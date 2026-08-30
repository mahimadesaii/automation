from flask import Flask, render_template, request, Response, jsonify, stream_with_context
import time
import json
import requests
import re
import os
import io
import concurrent.futures
from dotenv import load_dotenv

# Safe & Robust import for PDF Reader (pypdf)
PYPDF_AVAILABLE = False
PdfReader = None

try:
    import pypdf
    PdfReader = pypdf.PdfReader
    PYPDF_AVAILABLE = True
except Exception:
    PYPDF_AVAILABLE = False
    PdfReader = None

# Load environment variables from .env if present
load_dotenv()

app = Flask(__name__)

# System Configurations & Endpoints
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"

# Built-In System Cloud Key (from .env or Vercel environment variables)
BUILTIN_SYSTEM_TOKEN = os.environ.get("GROQ_API_KEY", "").strip()

# Compute Engine Profiles (Official Active Groq Cloud Models)
ONLINE_COMPUTE_ENGINES = {
    "qwen/qwen3.6-27b": {
        "name": "Groq Qwen 3.6 27B Engine",
        "rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000
    },
    "openai/gpt-oss-20b": {
        "name": "Groq Fast Synthesizer Engine",
        "rpm": 30, "rpd": 14400, "tpm": 18000, "tpd": 500000
    },
    "qwen/qwen3.8-27b": {
        "name": "Groq Qwen 3.8 Deep Engine",
        "rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000
    },
    "openai/gpt-oss-120b": {
        "name": "Groq Ultra-Capacity Engine",
        "rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000
    }
}

class SystemCapacityTracker:
    def __init__(self):
        self.request_times = []
        self.token_history = []
        self.daily_tokens = 0
        self.last_reset = time.time()

    def record_usage(self, tokens):
        now = time.time()
        self.request_times.append(now)
        self.token_history.append((now, tokens))
        self.daily_tokens += tokens

    def get_capacity_metrics(self):
        now = time.time()
        self.request_times = [t for t in self.request_times if now - t < 60]
        self.token_history = [item for item in self.token_history if now - item[0] < 60]

        current_minute_tokens = sum(tok for ts, tok in self.token_history)
        max_tpm_capacity = 8000
        capacity_pct = min(100, max(0, int((current_minute_tokens / max_tpm_capacity) * 100)))

        deep_dive_locked = capacity_pct >= 80 or len(self.request_times) >= 25
        cooldown_seconds = 0
        if deep_dive_locked and len(self.token_history) > 0:
            oldest_ts = self.token_history[0][0]
            cooldown_seconds = max(1, int(60 - (now - oldest_ts)))

        return {
            "capacity_utilized_pct": capacity_pct,
            "current_tpm": current_minute_tokens,
            "rpm_count": len(self.request_times),
            "deep_dive_locked": deep_dive_locked,
            "cooldown_seconds": cooldown_seconds
        }

capacity_tracker = SystemCapacityTracker()

OLLAMA_CACHE = {"timestamp": 0, "models": []}

def probe_local_ollama_engines(host=DEFAULT_OLLAMA_HOST):
    global OLLAMA_CACHE
    now = time.time()

    if OLLAMA_CACHE["models"] and (now - OLLAMA_CACHE["timestamp"] < 15):
        return OLLAMA_CACHE["models"]

    clean_host = (host or DEFAULT_OLLAMA_HOST).rstrip('/')
    try:
        res = requests.get(f"{clean_host}/api/tags", timeout=3.0)
        if res.status_code == 200:
            models_data = res.json().get("models", [])
            model_names = [m["name"] for m in models_data]
            OLLAMA_CACHE = {"timestamp": now, "models": model_names}
            return model_names
    except Exception:
        if OLLAMA_CACHE["models"] and (now - OLLAMA_CACHE["timestamp"] < 120):
            return OLLAMA_CACHE["models"]
            
    return []


def verify_and_cleanse_output(content, topic=""):
    """
    Automated Quality Verification & Anti-Loop Cleansing System:
    - Detects and removes degenerate repeating heading loops.
    - Cleans fake/hallucinated domain URLs.
    - Sanitizes Markdown formatting.
    """
    if not content:
        return content

    lines = content.splitlines()
    cleaned_lines = []
    seen_headings = {}
    consecutive_repeat_count = 0
    last_line = ""

    for line in lines:
        stripped = line.strip()

        # Skip consecutive duplicate lines
        if stripped and stripped == last_line:
            consecutive_repeat_count += 1
            if consecutive_repeat_count >= 2:
                continue
        else:
            consecutive_repeat_count = 0
            last_line = stripped

        # Detect degenerate repetitive heading patterns
        heading_match = re.match(r'^(?:\d+\.)+\d*\s+(.+)$', stripped)
        if heading_match:
            heading_text = heading_match.group(1).lower().strip()
            seen_headings[heading_text] = seen_headings.get(heading_text, 0) + 1
            if seen_headings[heading_text] > 2:
                continue

        # Clean fake/hallucinated URLs like groq.ml or ollama.ml
        cleaned_line = re.sub(r'\[([^\]]+)\]\(https?://(?:groq|ollama)\.ml/?[^\)]*\)', r'\1', line)
        cleaned_lines.append(cleaned_line)

    result_text = "\n".join(cleaned_lines).strip()
    return result_text


def execute_groq_cloud_node(prompt, access_token, preferred_model="qwen/qwen3.6-27b", temperature=0.2):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": "Aethelgard-Compute-Engine/2.0"
    }

    target_model = preferred_model if preferred_model in ONLINE_COMPUTE_ENGINES else "qwen/qwen3.6-27b"
    
    payload = {
        "model": target_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature
    }

    res = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=45)
    
    if res.status_code == 401 or res.status_code == 403:
        raise Exception("Authentication Error (401): The provided Groq Access Key is invalid or expired. Enter a valid free Groq API Key (gsk_...) or uncheck 'Enable Groq Key' to use local Ollama.")

    # Dynamic model fallback if requested model ID is 404 or rate-limited
    if res.status_code == 404 or res.status_code == 429:
        time.sleep(1)
        try:
            m_res = requests.get(GROQ_MODELS_URL, headers=headers, timeout=10)
            if m_res.status_code == 200:
                active_list = [m['id'] for m in m_res.json().get('data', []) if not m['id'].startswith('whisper') and not m['id'].startswith('meta-llama/llama-prompt-guard')]
                for active_m in active_list:
                    payload["model"] = active_m
                    res = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=45)
                    if res.status_code == 200:
                        target_model = active_m
                        break
        except Exception:
            pass

    res.raise_for_status()
    res_data = res.json()
    content = res_data["choices"][0]["message"]["content"]
    usage = res_data.get("usage", {})
    p_tokens = usage.get("prompt_tokens", len(prompt) // 4)
    c_tokens = usage.get("completion_tokens", len(content) // 4)
    engine_name = ONLINE_COMPUTE_ENGINES.get(target_model, {}).get("name", f"Groq Node ({target_model})")
    return content, p_tokens, c_tokens, engine_name


def execute_compute_node(prompt, mode="auto", access_token=None, preferred_model="qwen/qwen3.6-27b", ollama_host=DEFAULT_OLLAMA_HOST, temperature=0.2):
    token_to_use = (access_token or "").strip()
    
    # Priority 1: Groq API Cloud Engine (If token is explicitly provided starting with gsk_)
    if token_to_use and token_to_use.startswith("gsk_"):
        return execute_groq_cloud_node(prompt, access_token=token_to_use, preferred_model=preferred_model, temperature=temperature)

    # Priority 2: Local / Network Ollama Node
    local_engines = probe_local_ollama_engines(ollama_host)
    if mode == "ollama" or len(local_engines) > 0:
        if len(local_engines) > 0:
            target_model = preferred_model if preferred_model in local_engines else local_engines[0]
            url = f"{ollama_host.rstrip('/')}/api/chat"
            payload = {
                "model": target_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_ctx": 2048,
                    "num_predict": 768,
                    "repeat_penalty": 1.22,
                    "presence_penalty": 0.5,
                    "frequency_penalty": 0.5
                }
            }
            try:
                res = requests.post(url, json=payload, timeout=600)
                if res.status_code == 200:
                    data = res.json()
                    content = data.get("message", {}).get("content", "")
                    p_tokens = data.get("prompt_eval_count", len(prompt) // 4)
                    c_tokens = data.get("eval_count", len(content) // 4)
                    return content, p_tokens, c_tokens, f"Local Ollama ({target_model})"
            except Exception as e:
                raise Exception(f"Local Ollama Connection Error: {str(e)}. Ensure 'ollama serve' is running locally.")

    # Fallback to system key if available and no explicit access key was passed
    if BUILTIN_SYSTEM_TOKEN and BUILTIN_SYSTEM_TOKEN.startswith("gsk_"):
        return execute_groq_cloud_node(prompt, access_token=BUILTIN_SYSTEM_TOKEN, preferred_model=preferred_model, temperature=temperature)

    raise Exception("No active compute node available. Run 'ollama serve' locally, or enter a free Groq API Key (gsk_...) in the Access Key panel.")


def classify_research_archetype(topic, document_attached=False):
    if document_attached:
        return "DOCUMENT_RESEARCH"

    t_lower = topic.lower().strip()
    
    if " vs " in t_lower or " versus " in t_lower or "compare " in t_lower or "difference between" in t_lower:
        return "COMPARISON"
        
    if t_lower.startswith("explain ") or t_lower.startswith("what is ") or t_lower.startswith("how does ") or "concept" in t_lower or "introduction to" in t_lower:
        return "CONCEPT_EXPLANATION"
        
    if "investigate" in t_lower or "fraud" in t_lower or "manipulat" in t_lower or "claim" in t_lower or "truth about" in t_lower or "whistleblower" in t_lower:
        return "INVESTIGATION"
        
    if "market" in t_lower or "industry" in t_lower or "sector" in t_lower or "adoption" in t_lower or "forecast" in t_lower:
        return "MARKET_INDUSTRY"
        
    if "company" in t_lower or "analyze " in t_lower or "annual report" in t_lower or "business model" in t_lower:
        return "CORPORATE_COMPANY"

    return "GENERAL_ANALYTICAL"


def build_dynamic_plan(topic, depth="standard", domain_focus="auto", tone="analyst", document_attached=False):
    target_count = 5 if depth == "deep" else (3 if depth == "quick" else 4)
    archetype = classify_research_archetype(topic, document_attached)

    tone_instruction = {
        "analyst": "Objective, analytical, evidence-backed evaluation.",
        "technical": "Deep technical breakdown with architecture details, mechanics, and trade-offs.",
        "executive": "Strategic summaries, executive insights, cost-benefit analyses, and key takeaways.",
        "educational": "Clear, accessible explanations with conceptual definitions and practical examples."
    }.get(tone, "Objective research analysis.")

    domain_prompt = f"Lens focus: {domain_focus.upper()}." if domain_focus != "auto" else ""

    plan_prompt = f"""You are a Principal AI Research Analyst. Design a customized, domain-specific research plan containing exactly {target_count} sections for the following research topic:

QUERY: "{topic}"
QUERY ARCHETYPE: {archetype}
TARGET SECTIONS: {target_count}
{domain_prompt}
TONE EXPECTATION: {tone_instruction}

IMPORTANT STRUCTURAL GUIDELINES:
1. DO NOT use a single fixed template for all topics. Match the structure to the nature of the topic:
   - For COMPARISON queries ("React vs Vue", "Groq vs Ollama"): Include Comparison Matrix, Performance & DX, Ecosystem, Trade-Offs, and Best Use Case Verdict.
   - For CONCEPT EXPLANATION queries ("Explain Quantum Computing"): Include Plain-Language Intuition, Technical Mechanics, Classical vs Quantum Comparison, Real-World Applications, and Misconceptions.
   - For INVESTIGATION queries ("Investigate financial manipulation"): Include Known Facts, Primary Evidence, Financial/Technical Indicators, Contradictions, and Assessment.
   - For MARKET/INDUSTRY queries ("India's EV market"): Include Market Size & Growth, Key Players, Drivers & Infrastructure, Regulations, and Future Outlook.
   - For DOCUMENT RESEARCH: Include Document Findings, Financial/Technical Table Breakdown, External Verification, and Synthesized Verdict.
2. Output strictly a JSON array format:
[
  {{"id": 1, "name": "Section Title", "description": "Scope of what this section investigates"}},
  ...
]"""

    return plan_prompt, target_count, archetype


def fallback_sections(topic, target_count=4, archetype="GENERAL_ANALYTICAL"):
    if archetype == "COMPARISON":
        sections = [
            {"id": 1, "name": "Executive Verdict & Side-by-Side Matrix", "desc": "High-level comparative summary and structural criteria comparison."},
            {"id": 2, "name": "Architecture & Performance Benchmark", "desc": "Core mechanics, speed, memory footprint, and scalability."},
            {"id": 3, "name": "Developer Experience & Ecosystem", "desc": "Tooling, library support, community adoption, and learning curve."},
            {"id": 4, "name": "Trade-offs & Optimal Use Cases", "desc": "Critical trade-offs, cost factors, and clear decision matrix."}
        ]
    elif archetype == "CONCEPT_EXPLANATION":
        sections = [
            {"id": 1, "name": "Plain-Language Intuition & Core Concept", "desc": "Fundamental explanation, conceptual analogies, and core definition."},
            {"id": 2, "name": "Technical Mechanics & Under the Hood", "desc": "Underlying principles, mathematical/physical foundations, and operation."},
            {"id": 3, "name": "Real-World Applications & Industry Impact", "desc": "Practical implementations, current breakthroughs, and active use cases."},
            {"id": 4, "name": "Limitations & Common Misconceptions", "desc": "Technical constraints, common myths, and current state of research."}
        ]
    elif archetype == "INVESTIGATION":
        sections = [
            {"id": 1, "name": "Question Statement & Established Facts", "desc": "Primary inquiry, verified timeline, and uncontested facts."},
            {"id": 2, "name": "Primary Evidence & Technical Indicators", "desc": "Detailed evidence, anomalies, metrics, and official filings."},
            {"id": 3, "name": "Counter-Arguments & Alternative Explanations", "desc": "Defense arguments, alternative interpretations, and contradictory data."},
            {"id": 4, "name": "Definitive Assessment & Remaining Unknowns", "desc": "Final verdict, what can be established vs what remains unproven."}
        ]
    elif archetype == "MARKET_INDUSTRY":
        sections = [
            {"id": 1, "name": "Executive Summary & Market Dynamics", "desc": "Current market size, CAGR projections, and structural state."},
            {"id": 2, "name": "Competitive Landscape & Key Players", "desc": "Market share breakdown, leading companies, and moat analysis."},
            {"id": 3, "name": "Regulatory Environment & Drivers", "desc": "Government policy, infrastructure availability, and demand catalysts."},
            {"id": 4, "name": "Structural Risks & Future Outlook", "desc": "Supply chain vulnerabilities, adoption hurdles, and 5-year outlook."}
        ]
    elif archetype == "DOCUMENT_RESEARCH":
        sections = [
            {"id": 1, "name": "Core Document Findings & Key Evidence", "desc": "Direct extraction of primary facts, metrics, and claims from the file."},
            {"id": 2, "name": "Data & Table Breakdown", "desc": "Structured breakdown of quantitative tables, figures, and financial data."},
            {"id": 3, "name": "External Verification & Context", "desc": "Cross-checking document findings against external industry benchmarks."},
            {"id": 4, "name": "Critical Analysis & Final Synthesis", "desc": "Gaps, contradictions, and authoritative conclusions."}
        ]
    else:
        sections = [
            {"id": 1, "name": "Foundations & Core Principles", "desc": "Direct definition, mechanics, and fundamental significance."},
            {"id": 2, "name": "Context, Evolution & Applications", "desc": "Historical trajectory, adoption milestones, and real-world implementations."},
            {"id": 3, "name": "Evaluation & Trade-offs", "desc": "Strengths, technical constraints, risks, and comparative analysis."},
            {"id": 4, "name": "Innovations & Future Horizon", "desc": "Emerging developments, ecosystem tools, and strategic recommendations."}
        ]
    return sections[:target_count]


@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/capacity")
def get_capacity():
    return jsonify(capacity_tracker.get_capacity_metrics())

@app.route("/api/ollama/models")
def get_ollama_models():
    host = request.args.get("host", DEFAULT_OLLAMA_HOST)
    models = probe_local_ollama_engines(host)
    if models:
        return jsonify({"available": True, "models": models})
    return jsonify({"available": False, "models": [], "message": "Local compute node offline."})


@app.route("/api/upload", methods=["POST"])
def upload_document():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
        
    uploaded_file = request.files['file']
    filename = uploaded_file.filename
    if not filename:
        return jsonify({"error": "Empty filename"}), 400

    extracted_text = ""
    try:
        if filename.lower().endswith('.pdf'):
            if not PYPDF_AVAILABLE or PdfReader is None:
                try:
                    pdf_bytes = uploaded_file.read()
                    extracted_text = pdf_bytes.decode('utf-8', errors='ignore')
                except Exception:
                    return jsonify({"error": "PDF parser (pypdf) is unavailable on server. Upload a .txt or .md file instead."}), 400
            else:
                pdf_bytes = uploaded_file.read()
                reader = PdfReader(io.BytesIO(pdf_bytes))
                for page in reader.pages[:20]:
                    text = page.extract_text()
                    if text:
                        extracted_text += text + "\n"
        else:
            extracted_text = uploaded_file.read().decode('utf-8', errors='ignore')

        clean_text = extracted_text.strip()
        if len(clean_text) > 8000:
            clean_text = clean_text[:8000] + "\n...[Document text truncated for processing]"

        return jsonify({
            "success": True,
            "filename": filename,
            "character_count": len(extracted_text),
            "text": clean_text
        })
    except Exception as e:
        return jsonify({"error": f"Failed to parse document: {str(e)}"}), 500


@app.route("/api/research/stream", methods=["GET", "POST"])
def stream_research():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        topic = data.get("topic", "").strip()
        compute_mode = data.get("mode", "auto").strip().lower()
        access_token = data.get("access_token", "").strip()
        preferred_model = data.get("model", "qwen/qwen3.6-27b").strip()
        ollama_host = data.get("ollama_host", DEFAULT_OLLAMA_HOST).strip()
        depth = data.get("depth", "standard").strip().lower()
        domain_focus = data.get("domain", "auto").strip().lower()
        tone = data.get("tone", "analyst").strip().lower()
        document_text = data.get("document_text", "").strip()
    else:
        topic = request.args.get("topic", "").strip()
        compute_mode = request.args.get("mode", "auto").strip().lower()
        access_token = request.args.get("access_token", "").strip()
        preferred_model = request.args.get("model", "qwen/qwen3.6-27b").strip()
        ollama_host = request.args.get("ollama_host", DEFAULT_OLLAMA_HOST).strip()
        depth = request.args.get("depth", "standard").strip().lower()
        domain_focus = request.args.get("domain", "auto").strip().lower()
        tone = request.args.get("tone", "analyst").strip().lower()
        document_text = request.args.get("document_text", "").strip()

    if not topic:
        return jsonify({"error": "Topic is required"}), 400

    capacity_info = capacity_tracker.get_capacity_metrics()
    if depth == "deep" and capacity_info["deep_dive_locked"]:
        depth = "standard"

    target_batch_count = 5 if depth == "deep" else (3 if depth == "quick" else 4)
    document_attached = bool(document_text)

    def event_stream():
        yield f"data: {json.dumps({'type': 'log', 'message': f'Analyzing research query & domain intent for: \"{topic}\"...'})}\n\n"

        plan_prompt, batch_cnt, archetype = build_dynamic_plan(topic, depth, domain_focus, tone, document_attached)
        yield f"data: {json.dumps({'type': 'log', 'message': f'Detected research archetype: [{archetype}]. Planning customized section structure.'})}\n\n"

        section_plan = []

        try:
            plan_res, _, _, _ = execute_compute_node(
                plan_prompt, mode=compute_mode, access_token=access_token,
                preferred_model=preferred_model, ollama_host=ollama_host, temperature=0.1
            )
            
            clean_json = plan_res.strip()
            if "```" in clean_json:
                match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', clean_json, re.DOTALL)
                clean_json = match.group(1) if match else clean_json.replace("```json", "").replace("```", "").strip()

            parsed = json.loads(clean_json)
            if isinstance(parsed, list) and len(parsed) > 0:
                for idx, sec in enumerate(parsed[:target_batch_count]):
                    section_plan.append({
                        "id": idx + 1,
                        "name": sec.get("name", f"Section {idx+1}"),
                        "desc": sec.get("description", "Detailed analytical evaluation")
                    })
                yield f"data: {json.dumps({'type': 'log', 'message': f'Synthesized {len(section_plan)} dynamic research sections.'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'log', 'message': f'Engine notice: ({str(e)}). Using adaptive archetype plan.'})}\n\n"
            section_plan = []

        if not section_plan:
            fb = fallback_sections(topic, target_batch_count, archetype)
            section_plan = [{"id": f["id"], "name": f["name"], "desc": f["desc"]} for f in fb]

        batch_meta = [{"id": s["id"], "name": s["name"]} for s in section_plan]
        yield f"data: {json.dumps({'type': 'init', 'total_batches': len(section_plan), 'batch_metadata': batch_meta})}\n\n"

        accumulated_findings = []
        total_tokens = 0
        total_p_tokens = 0
        total_c_tokens = 0
        start_time_all = time.time()

        doc_context_snippet = ""
        if document_text:
            doc_context_snippet = f"\n\n--- ATTACHED PRIMARY DOCUMENT CONTENT ---\n{document_text[:3500]}\n--- END DOCUMENT CONTENT ---\n\n"

        # Fact grounding guidance for common tech terms to prevent 0.5B model hallucination
        fact_grounding_prompt = ""
        t_lower = topic.lower()
        if "groq" in t_lower or "ollama" in t_lower:
            fact_grounding_prompt = """\nFACTUAL DOMAIN KNOWLEDGE (MUST OBEY):
- Groq: High-performance LPU (Language Processing Unit) AI cloud inference service and hardware API for fast LLM response generation.
- Ollama: Open-source framework for running local open-source LLMs (Llama, Qwen, Mistral) on personal computers and local servers.
- Groq is NOT a Python ML library; Ollama is NOT a Java library.\n"""

        for idx, sec in enumerate(section_plan):
            batch_id = sec["id"]
            batch_name = sec["name"]
            batch_desc = sec["desc"]

            yield f"data: {json.dumps({'type': 'status', 'batch_id': batch_id, 'status': 'running', 'message': f'Researching Section {batch_id}: {batch_name}...'})}\n\n"

            context_summary = ""
            if accumulated_findings:
                context_summary = "\n\n--- PREVIOUS SECTION FINDINGS (FOR CONTEXT CONTINUITY) ---\n"
                for prev in accumulated_findings[-2:]:
                    short_sum = prev['summary'][:180].replace('\n', ' ')
                    context_summary += f"• Section '{prev['name']}': {short_sum}...\n"
                context_summary += "--- END PREVIOUS FINDINGS ---\n\n"

            section_prompt = f"""You are a Principal AI Research Analyst conducting Section {batch_id} for the research query: "{topic}".

SECTION TITLE: {batch_name}
SECTION SCOPE: {batch_desc}
ARCHETYPE: {archetype}
TONE: {tone.capitalize()} research style.
{fact_grounding_prompt}
{doc_context_snippet}
{context_summary}
ANALYTICAL & REPORTING RULES:
1. DO NOT REPEAT HEADING TITLES OR NUMBERS OVER AND OVER. State your points clearly and stop when complete.
2. TRUTH & EVIDENCE: Explicitly separate FACT from ANALYSIS and UNCERTAINTY. Use clear phrasing ("Facts show...", "Evidence indicates...", "Estimates suggest...").
3. QUANTITATIVE ACCURACY: Include relevant percentages, growth rates, CAGR, cost trade-offs, or numbers where applicable. Avoid false precision.
4. DOMAIN SOURCE HIERARCHY: Format citations clearly in markdown as: [Source Title — Author/Publisher](URL). Never invent fake URLs.
5. STRUCTURE: Use clear GFM markdown headings, bullet points, and markdown tables for comparisons or metrics when helpful. Do not mention internal prompt instructions or phase numbers.
6. CONTINUITY: Build upon prior findings without repeating introductory definitions."""

            t0 = time.time()
            try:
                # Threaded execution with heartbeat pinging to prevent connection drops
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        execute_compute_node, section_prompt, compute_mode, access_token, preferred_model, ollama_host, 0.2
                    )
                    while not future.done():
                        time.sleep(2.5)
                        if not future.done():
                            yield ": heartbeat\n\n"
                    
                    content, p_tok, c_tok, node_used = future.result()

                # Run Automated Quality Verification & Cleansing
                verified_content = verify_and_cleanse_output(content, topic)

                batch_tokens = p_tok + c_tok
                total_tokens += batch_tokens
                total_p_tokens += p_tok
                total_c_tokens += c_tok

                capacity_tracker.record_usage(batch_tokens)

                concise_summary = verified_content[:250].replace('\n', ' ') + "..."
                accumulated_findings.append({
                    "id": batch_id,
                    "name": batch_name,
                    "summary": concise_summary
                })

                t1 = time.time()
                time_taken = t1 - t0
                metrics = capacity_tracker.get_capacity_metrics()

                success_msg = f"Section {batch_id} ({batch_name}) verified & synthesized via {node_used} in {time_taken:.1f}s ({batch_tokens} tokens)."
                
                yield f"data: {json.dumps({'type': 'result', 'batch_id': batch_id, 'batch_name': batch_name, 'content': verified_content, 'prompt_tokens': p_tok, 'completion_tokens': c_tok, 'tokens': batch_tokens, 'time_taken': f'{time_taken:.1f}', 'node_name': node_used, 'capacity_pct': metrics['capacity_utilized_pct'], 'deep_dive_locked': metrics['deep_dive_locked'], 'cooldown_seconds': metrics['cooldown_seconds']})}\n\n"
                yield f"data: {json.dumps({'type': 'log', 'message': success_msg})}\n\n"

            except Exception as e:
                err_text = str(e)
                yield f"data: {json.dumps({'type': 'error', 'batch_id': batch_id, 'message': f'Execution error: {err_text}'})}\n\n"
                return

            time.sleep(0.4)

        total_time = time.time() - start_time_all
        final_metrics = capacity_tracker.get_capacity_metrics()

        yield f"data: {json.dumps({'type': 'done', 'total_tokens': total_tokens, 'total_prompt_tokens': total_p_tokens, 'total_completion_tokens': total_c_tokens, 'total_time': f'{total_time:.1f}', 'final_capacity_pct': final_metrics['capacity_utilized_pct']})}\n\n"

    res = Response(stream_with_context(event_stream()), mimetype="text/event-stream")
    res.headers["X-Accel-Buffering"] = "no"
    res.headers["Cache-Control"] = "no-cache"
    return res


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
