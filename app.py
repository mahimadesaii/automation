from flask import Flask, render_template, request, Response, jsonify, stream_with_context
import time
import json
import requests

app = Flask(__name__)

# API key is NOT stored server-side — it is passed per-request from the browser (stored in localStorage)
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# Model limits mapping
# Note: Whisper models are transcription models, so we query groq/compound while simulating Whisper limits.
MODELS = {
    "canopylabs/orpheus-arabic-saudi": {
        "name": "Canopy Labs Orpheus Arabic (Saudi)",
        "rpm": 10, "rpd": 100, "tpm": 1200, "tpd": 3600,
        "is_audio": False
    },
    "canopylabs/orpheus-v1-english": {
        "name": "Canopy Labs Orpheus V1 English",
        "rpm": 10, "rpd": 100, "tpm": 1200, "tpd": 3600,
        "is_audio": False
    },
    "groq/compound": {
        "name": "Groq Compound (Beta)",
        "rpm": 30, "rpd": 250, "tpm": 70000, "tpd": None,
        "is_audio": False
    },
    "groq/compound-mini": {
        "name": "Groq Compound Mini",
        "rpm": 30, "rpd": 250, "tpm": 70000, "tpd": None,
        "is_audio": False
    },
    "meta-llama/llama-prompt-guard-2-22m": {
        "name": "Llama Prompt Guard 2 22M",
        "rpm": 30, "rpd": 14400, "tpm": 15000, "tpd": 500000,
        "is_audio": False
    },
    "meta-llama/llama-prompt-guard-2-86m": {
        "name": "Llama Prompt Guard 2 86M",
        "rpm": 30, "rpd": 14400, "tpm": 15000, "tpd": 500000,
        "is_audio": False
    },
    "openai/gpt-oss-120b": {
        "name": "GPT OSS 120B",
        "rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000,
        "is_audio": False
    },
    "openai/gpt-oss-20b": {
        "name": "GPT OSS 20B",
        "rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000,
        "is_audio": False
    },
    "openai/gpt-oss-safeguard-20b": {
        "name": "Safety GPT OSS 20B",
        "rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000,
        "is_audio": False
    },
    "qwen/qwen3.6-27b": {
        "name": "Qwen 3.6 27B",
        "rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000,
        "is_audio": False
    },
    "qwen/qwen3.8-27b": {
        "name": "Qwen 3.8 27B",
        "rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 2000000,
        "is_audio": False
    },
    "whisper-large-v3": {
        "name": "Whisper Large V3 (Audio)",
        "rpm": 20, "rpd": 2000, "ash": 7200, "asd": 28800,
        "is_audio": True
    },
    "whisper-large-v3-turbo": {
        "name": "Whisper Large V3 Turbo (Audio)",
        "rpm": 20, "rpd": 2000, "ash": 7200, "asd": 28800,
        "is_audio": True
    }
}

class QueueRateLimiter:
    def __init__(self, rpm, tpm=None):
        self.rpm = rpm
        self.tpm = tpm
        self.request_times = []
        self.token_usage = []  # items are tuples (timestamp, tokens)

    def acquire_slot(self, estimated_tokens, send_log_fn):
        now = time.time()
        # Clean older than 60s
        self.request_times = [t for t in self.request_times if now - t < 60]
        self.token_usage = [item for item in self.token_usage if now - item[0] < 60]

        # Check RPM
        if len(self.request_times) >= self.rpm:
            wait_time = 60 - (now - self.request_times[0]) + 0.5
            if wait_time > 0:
                send_log_fn(f"RPM limit of {self.rpm} reached. Pausing queue for {wait_time:.2f} seconds...")
                time.sleep(wait_time)
                return self.acquire_slot(estimated_tokens, send_log_fn)

        # Check TPM
        if self.tpm:
            current_tpm_usage = sum(tokens for ts, tokens in self.token_usage)
            if current_tpm_usage + estimated_tokens > self.tpm:
                # Find wait time to clean up enough tokens
                self.token_usage.sort(key=lambda x: x[0])
                wait_time = 0
                temp_usage = current_tpm_usage
                for ts, tokens in self.token_usage:
                    temp_usage -= tokens
                    if temp_usage + estimated_tokens <= self.tpm:
                        wait_time = 60 - (now - ts) + 0.5
                        break
                if wait_time > 0:
                    send_log_fn(f"Estimated TPM limit ({self.tpm}) would be exceeded. Current minute usage: {current_tpm_usage} tokens. Pausing queue for {wait_time:.2f} seconds...")
                    time.sleep(wait_time)
                    return self.acquire_slot(estimated_tokens, send_log_fn)

        # Approved
        self.request_times.append(time.time())
        return True

    def record_usage(self, actual_tokens):
        self.token_usage.append((time.time(), actual_tokens))

    def get_wait_time(self, estimated_tokens):
        now = time.time()
        # Clean older than 60s
        request_times = [t for t in self.request_times if now - t < 60]
        token_usage = [item for item in self.token_usage if now - item[0] < 60]

        wait_time = 0
        
        # Check RPM
        if len(request_times) >= self.rpm:
            wait_time = max(wait_time, 60 - (now - request_times[0]) + 0.5)

        # Check TPM
        if self.tpm:
            current_tpm_usage = sum(tokens for ts, tokens in token_usage)
            if current_tpm_usage + estimated_tokens > self.tpm:
                token_usage.sort(key=lambda x: x[0])
                tpm_wait = 0
                temp_usage = current_tpm_usage
                for ts, tokens in token_usage:
                    temp_usage -= tokens
                    if temp_usage + estimated_tokens <= self.tpm:
                        tpm_wait = 60 - (now - ts) + 0.5
                        break
                wait_time = max(wait_time, tpm_wait)
                
        return wait_time

# Shared cache for rate limiters per model selected
rate_limiters = {}

def get_rate_limiter(model_id):
    if model_id not in rate_limiters:
        config = MODELS.get(model_id, {"rpm": 10, "tpm": 1200})
        rate_limiters[model_id] = QueueRateLimiter(config["rpm"], config.get("tpm"))
    return rate_limiters[model_id]


# BATCH PROMPTS — 5-Stage Analyst Research Pipeline
# Each batch covers a distinct research dimension. The model runs all 5 stages
# internally and outputs ONLY the final synthesised answer (Stage 5).
BATCHES = [
    {
        "id": 1,
        "name": "Foundations & Core Concepts",
        "prompt": """You are an expert research analyst. Work through the five stages below for the topic: "{topic}"

---
STAGE 1 — UNDERSTAND THE TOPIC
Identify the user's likely intent, scope, and key concepts. Determine the type of answer needed. Note any important ambiguity.
Focus this stage on: core definitions, key actors, and foundational principles.

---
STAGE 2 — EXTRACT RELEVANT EVIDENCE
From your knowledge, extract only information directly relevant to this topic's foundations.
For each important finding, note: the finding, the supporting evidence, and the source.
Remove weak, irrelevant, or unsupported information. Do not invent facts or URLs.

---
STAGE 3 — ANALYZE AND SYNTHESIZE
Identify the most important patterns, relationships, and conclusions from the extracted evidence.
Do not simply list facts. Explain what the evidence collectively shows about this topic's foundations and key actors.

---
STAGE 4 — VALIDATE AND ADD CONTEXT
Check for: unsupported claims, contradictions, missing context, important limitations.
Flag or correct issues using only available evidence. Add relevant caveats.

---
STAGE 5 — GENERATE THE FINAL ANSWER
Using the validated findings above, write a complete, professional answer covering:
- WHO: the key stakeholders, actors, pioneers, and target groups — and their significance
- WHAT: a precise definition, core components, and how they interrelate

Rules:
- Answer the topic directly at the start. Do not open with a dictionary definition.
- Synthesise and analyse — do not list facts without explanation.
- Use a structure appropriate to this specific topic.
- Explain why findings matter, not just what they are.
- Be clear, concise, and professional. No filler, no repetition.
- Do not mention these stages, the research process, or internal reasoning.
- Do not invent statistics, rankings, or URLs.
- Include only sources that genuinely support your answer, formatted as: [Author/Organisation — Title](URL)

Output only the final answer from Stage 5."""
    },
    {
        "id": 2,
        "name": "Context, Applications & Timeline",
        "prompt": """You are an expert research analyst. Work through the five stages below for the topic: "{topic}"

---
STAGE 1 — UNDERSTAND THE TOPIC
Identify the user's likely intent, scope, and key concepts for this topic.
Focus this stage on: where this topic applies, when it emerged, and its adoption trajectory.

---
STAGE 2 — EXTRACT RELEVANT EVIDENCE
Extract only information directly relevant to this topic's real-world applications and historical context.
For each important finding, note: the finding, the supporting evidence, and the source.
Remove duplicate, weak, or unsupported information. Do not invent facts or URLs.

---
STAGE 3 — ANALYZE AND SYNTHESIZE
Identify patterns, adoption timelines, causal relationships, and sector-specific insights.
Explain what the evidence collectively shows — not just where and when, but why those applications emerged.

---
STAGE 4 — VALIDATE AND ADD CONTEXT
Check for: unsupported claims, over-generalisation, missing context, important edge cases.
Flag or correct issues. Note where this topic does not apply or has limitations.

---
STAGE 5 — GENERATE THE FINAL ANSWER
Using the validated findings above, write a complete, professional answer covering:
- WHERE: industries, sectors, environments, and geographies where this applies — and why
- WHEN: meaningful milestones, adoption phases, and forward trajectory

Rules:
- Answer the topic directly at the start.
- Use concrete, real-world examples. Avoid vague generalisations.
- Analyse causality and patterns — not just a list of dates or sectors.
- Be clear, concise, and professional. No filler, no repetition.
- Do not mention these stages, the research process, or internal reasoning.
- Do not invent statistics, rankings, or URLs.
- Include only sources that genuinely support your answer, formatted as: [Author/Organisation — Title](URL)

Output only the final answer from Stage 5."""
    },
    {
        "id": 3,
        "name": "Evaluation, Trade-offs & Methodology",
        "prompt": """You are an expert research analyst. Work through the five stages below for the topic: "{topic}"

---
STAGE 1 — UNDERSTAND THE TOPIC
Identify the user's likely intent and what type of evaluative answer is needed.
Focus this stage on: why this topic matters, its advantages and disadvantages, and how it works in practice.

---
STAGE 2 — EXTRACT RELEVANT EVIDENCE
Extract only information relevant to the significance, trade-offs, critiques, and implementation of this topic.
For each important finding, note: the finding, the supporting evidence, and the source.
Where evidence conflicts, capture both sides. Do not invent facts or URLs.

---
STAGE 3 — ANALYZE AND SYNTHESIZE
Identify the most important trade-offs, evaluative criteria, and methodological insights.
Where evidence conflicts, explain why. Go beyond description — offer a synthesised perspective on what matters most.

---
STAGE 4 — VALIDATE AND ADD CONTEXT
Check for: unsupported claims, one-sided analysis, missing caveats, contested evidence.
Correct or flag issues. Do not present subjective conclusions as objective facts.

---
STAGE 5 — GENERATE THE FINAL ANSWER
Using the validated findings above, write a complete, professional answer covering:
- WHY: significance, advantages, disadvantages, and balanced critiques
- HOW: methodology, workflow, or implementation — with meaningful comparisons where multiple approaches exist

Rules:
- Answer the topic directly at the start.
- State criteria clearly when comparing or ranking. Do not present subjective conclusions as facts.
- Acknowledge uncertainty or contested evidence where it exists.
- Be clear, concise, and professional. No filler, no repetition.
- Do not mention these stages, the research process, or internal reasoning.
- Do not invent statistics, rankings, or URLs.
- Include only sources that genuinely support your answer, formatted as: [Author/Organisation — Title](URL)

Output only the final answer from Stage 5."""
    },
    {
        "id": 4,
        "name": "Advanced Analysis & Emerging Dimensions",
        "prompt": """You are an expert research analyst. Work through the five stages below for the topic: "{topic}"

---
STAGE 1 — UNDERSTAND THE TOPIC
Identify the adjacent dimensions, peripheral technologies, and secondary factors a practitioner or decision-maker needs to understand about this topic.
Focus this stage on: what lies beyond the core — related concepts, industry context, open questions.

---
STAGE 2 — EXTRACT RELEVANT EVIDENCE
Extract only information relevant to the broader landscape of this topic — adjacent technologies, trends, challenges, and open debates.
For each important finding, note: the finding, the supporting evidence, and the source.
Prioritise quality over quantity. Do not invent facts or URLs.

---
STAGE 3 — ANALYZE AND SYNTHESIZE
Synthesise the connections between the main topic and its periphery.
Explain why these connections matter — not just what they are.
Highlight emerging trends, unresolved challenges, and open research questions.

---
STAGE 4 — VALIDATE AND ADD CONTEXT
Check for: unsupported claims, overstated trends, contested findings, gaps in current knowledge.
Flag or correct issues. Add important caveats where the landscape is uncertain.

---
STAGE 5 — GENERATE THE FINAL ANSWER
Using the validated findings above, write a comprehensive deep-dive covering:
- Adjacent technologies, related concepts, and industry context
- Emerging trends and their implications
- Unresolved challenges and open questions
- What this means for practitioners or decision-makers

Rules:
- Choose the structure that best fits this specific topic.
- Prefer coherent, evidence-backed prose over bullet-point fact lists.
- Synthesise connections — explain significance, not just existence.
- Be clear, concise, and professional. No filler, no repetition.
- Do not mention these stages, the research process, or internal reasoning.
- Do not invent statistics, rankings, or URLs.
- Include only sources that genuinely support your answer, formatted as: [Author/Organisation — Title](URL)

Output only the final answer from Stage 5."""
    },
    {
        "id": 5,
        "name": "Solutions, Tools & Recommendations",
        "prompt": """You are an expert research analyst. Work through the five stages below for the topic: "{topic}"

---
STAGE 1 — UNDERSTAND THE TOPIC
Identify what type of solutions, tools, vendors, or recommendations the user needs.
Determine the relevant evaluation criteria for this specific topic (e.g. cost, scalability, maturity, use-case fit, ecosystem).

---
STAGE 2 — EXTRACT RELEVANT EVIDENCE
Extract only information relevant to the leading solutions, tools, vendors, or approaches for this topic.
For each important finding, note: the option, its key characteristics, and the source.
Do not invent product features, pricing, or performance benchmarks.

---
STAGE 3 — ANALYZE AND SYNTHESIZE
Compare options based on the criteria identified in Stage 1.
Highlight meaningful differentiators, trade-offs, and use-case fits.
Do not present rankings as absolute facts — make the basis for comparison explicit.

---
STAGE 4 — VALIDATE AND ADD CONTEXT
Check for: unsupported claims, marketing bias, missing alternatives, important selection risks.
Add caveats a decision-maker should know. Include open-source or alternative options where relevant.

---
STAGE 5 — GENERATE THE FINAL ANSWER
Using the validated findings above, write a structured, actionable answer covering:
- The leading solutions, tools, or approaches — with clear evaluation criteria stated upfront
- For each: core strengths, primary use-cases, and ideal fit
- Meaningful differentiators and trade-offs between options
- Important limitations, vendor risks, or selection considerations
- Open-source or alternative options where relevant

Rules:
- Answer directly at the start: what are the top options and on what basis?
- Be specific — avoid generic marketing language.
- State criteria clearly when ranking. Do not present subjective conclusions as facts.
- Be clear, concise, and professional. No filler, no repetition.
- Do not mention these stages, the research process, or internal reasoning.
- Do not invent statistics, rankings, or URLs.
- Include only sources that genuinely support your answer, formatted as: [Author/Organisation — Title](URL)

Output only the final answer from Stage 5."""
    }
]



@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/models")
def get_models():
    return jsonify(MODELS)

@app.route("/api/research/stream")
def stream_research():
    topic = request.args.get("topic", "").strip()
    api_key = request.args.get("api_key", "").strip()

    if not topic:
        return jsonify({"error": "Topic is required"}), 400

    if not api_key or not api_key.startswith("gsk_"):
        return jsonify({"error": "A valid Groq API key (starting with gsk_) is required."}), 401

    TEXT_MODEL_POOL = [
        "qwen/qwen3.6-27b",
        "openai/gpt-oss-20b",
        "qwen/qwen3.8-27b",
        "openai/gpt-oss-safeguard-20b"
    ]

    def event_stream():
        yield f"data: {json.dumps({'type': 'init', 'total_batches': len(BATCHES), 'model_name': 'Dynamic Router (Load Balanced)'})}\n\n"
        
        total_tokens = 0
        start_time_all = time.time()

        for idx, batch in enumerate(BATCHES):
            batch_id = batch["id"]
            batch_name = batch["name"]
            raw_prompt = batch["prompt"].format(topic=topic)

            yield f"data: {json.dumps({'type': 'status', 'batch_id': batch_id, 'status': 'running', 'message': f'Processing Batch {batch_id}: {batch_name}...'})}\n\n"

            # 1. Estimate tokens (char count / 4)
            estimated_tokens = len(raw_prompt) // 4 + 1000 # Expecting ~1000 token output
            
            # Select model dynamically from the pool based on minimum wait time
            actual_model = "qwen/qwen3.6-27b"  # Default
            min_wait = float('inf')
            
            for m_id in TEXT_MODEL_POOL:
                lim = get_rate_limiter(m_id)
                w_time = lim.get_wait_time(estimated_tokens)
                if w_time < min_wait:
                    min_wait = w_time
                    actual_model = m_id
                    if min_wait == 0:
                        break # Perfect fit!
            
            limiter = get_rate_limiter(actual_model)
            model_config = MODELS[actual_model]
            
            # Log routing decision
            routing_msg = f"Routing: Batch {batch_id} routed to {model_config['name']} (estimated wait: {min_wait:.1f}s)."
            yield f"data: {json.dumps({'type': 'log', 'message': routing_msg})}\n\n"

            if min_wait > 0:
                pause_msg = f"Rate Limit: Pausing queue for {min_wait:.1f}s to respect {model_config['name']} capacity limits."
                yield f"data: {json.dumps({'type': 'log', 'message': pause_msg})}\n\n"
                time.sleep(min_wait)

            # Record slot
            limiter.request_times.append(time.time())

            # 2. Call API
            t0 = time.time()
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0"
            }
            payload = {
                "model": actual_model,
                "messages": [{"role": "user", "content": raw_prompt}],
                "temperature": 0.2
            }

            try:
                # Query Groq
                res = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=45)
                
                if res.status_code == 401 or res.status_code == 403:
                    raise Exception(f"API Authentication Failed ({res.status_code}): {res.text}")
                
                if res.status_code == 429:
                    retry_msg = f"Groq API returned HTTP 429 (Rate Limit) for {model_config['name']}. Waiting 10 seconds before retry..."
                    yield f"data: {json.dumps({'type': 'log', 'message': retry_msg})}\n\n"
                    time.sleep(10)
                    res = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=45)
                
                res.raise_for_status()
                res_data = res.json()
                
                content = res_data["choices"][0]["message"]["content"]
                
                # Parse usage info
                usage = res_data.get("usage", {})
                prompt_tokens = usage.get("prompt_tokens", len(raw_prompt) // 4)
                completion_tokens = usage.get("completion_tokens", len(content) // 4)
                batch_tokens = prompt_tokens + completion_tokens
                total_tokens += batch_tokens

                limiter.record_usage(batch_tokens)
                t1 = time.time()
                time_taken = t1 - t0

                success_msg = f"Success: Batch {batch_id} completed via {model_config['name']} in {time_taken:.1f}s. Consumed {batch_tokens} tokens."
                yield f"data: {json.dumps({'type': 'result', 'batch_id': batch_id, 'content': content, 'tokens': batch_tokens, 'time_taken': f'{time_taken:.1f}', 'model_name': model_config['name']})}\n\n"
                yield f"data: {json.dumps({'type': 'log', 'message': success_msg})}\n\n"
                
            except Exception as e:
                # Fallback to qwen/qwen3.6-27b if model call fails
                if actual_model != "qwen/qwen3.6-27b":
                    yield f"data: {json.dumps({'type': 'log', 'message': f'Warning: Query using model {actual_model} failed ({str(e)}). Falling back to qwen/qwen3.6-27b to complete research.'})}\n\n"
                    actual_model = "qwen/qwen3.6-27b"
                    fallback_config = MODELS[actual_model]
                    try:
                        t0 = time.time()
                        payload["model"] = "qwen/qwen3.6-27b"
                        res = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=45)
                        res.raise_for_status()
                        res_data = res.json()
                        content = res_data["choices"][0]["message"]["content"]
                        usage = res_data.get("usage", {})
                        batch_tokens = usage.get("prompt_tokens", len(raw_prompt)//4) + usage.get("completion_tokens", len(content)//4)
                        total_tokens += batch_tokens
                        get_rate_limiter(actual_model).record_usage(batch_tokens)
                        t1 = time.time()
                        time_taken = t1 - t0
                        yield f"data: {json.dumps({'type': 'result', 'batch_id': batch_id, 'content': content, 'tokens': batch_tokens, 'time_taken': f'{time_taken:.1f}', 'model_name': fallback_config['name']})}\n\n"
                        yield f"data: {json.dumps({'type': 'log', 'message': f'Success: Batch {batch_id} completed via fallback in {time_taken:.1f}s. Consumed {batch_tokens} tokens.'})}\n\n"
                    except Exception as fallback_error:
                        yield f"data: {json.dumps({'type': 'error', 'batch_id': batch_id, 'message': f'Error in batch {batch_id}: {str(fallback_error)}'})}\n\n"
                        return
                else:
                    yield f"data: {json.dumps({'type': 'error', 'batch_id': batch_id, 'message': f'Error in batch {batch_id}: {str(e)}'})}\n\n"
                    return

            time.sleep(1.0)

        total_time = time.time() - start_time_all
        yield f"data: {json.dumps({'type': 'done', 'total_tokens': total_tokens, 'total_time': f'{total_time:.1f}'})}\n\n"

    return Response(stream_with_context(event_stream()), mimetype="text/event-stream")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
