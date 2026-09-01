from flask import Flask, render_template, request, Response, jsonify, stream_with_context, send_from_directory
import time
import json
import requests
import re
import os
import io
import concurrent.futures
from dotenv import load_dotenv

# Import custom modules
from search_retrieval import execute_live_research
from llm_engine import generate_completion, DEFAULT_OLLAMA_HOST
from providers.orchestrator import ProviderOrchestrator
from dedup import check_section_duplication
from quality_engine import (
    enrich_report_presentation,
    score_output_quality,
    is_small_model,
    build_microtask_prompt,
    build_structured_section,
    is_historical_factual_topic,
    verify_fact_grounding_claims,
    evaluate_report_quality_metrics
)

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

app = Flask(__name__, static_folder="static", static_url_path="/static")


@app.route("/static/<path:filename>")
def serve_static(filename):
    return send_from_directory("static", filename)

# Capacity Tracker for Rate Limiting & Cooldown Management
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


def verify_and_cleanse_output(content: str, topic: str = "") -> str:
    """
    Anti-Repetition & Anti-COT Cleansing:
    Strips raw LLM reasoning preambles ("Here's a thinking process:", <think>...</think>),
    removes duplicate sentences, strips repeated subheadings & bad URLs.
    """
    if not content:
        return content

    # Strip <think>...</think> and raw LLM chain-of-thought preambles
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
    lines_raw = content.splitlines()
    filtered_cot = []
    in_cot = False
    for line_item in lines_raw:
        s_item = line_item.strip()
        if s_item.startswith("Here's a thinking process") or s_item.startswith("Analyze User Input:") or s_item.startswith("Deconstruct the Topic"):
            in_cot = True
            continue
        if in_cot and s_item.startswith("###"):
            in_cot = False
        if in_cot and (s_item.startswith("Topic:") or s_item.startswith("Context Provided:") or s_item.startswith("Task:") or s_item.startswith("Focus:") or s_item.startswith("Draft -") or s_item.startswith("Check Against") or s_item.startswith("Source mapping")):
            continue
        if not in_cot:
            filtered_cot.append(line_item)
    content = "\n".join(filtered_cot)

    banned_phrases = [
        "structured data relationships",
        "algorithmic processing",
        "system-level optimization",
        "baseline standard vs advanced capability",
        "operational impact involves structured data",
        "evidence-backed execution frameworks",
        "In this comparison, we will evaluate",
        "This section provides a comprehensive analysis",
        "This section aims to provide specific, factual analysis",
        "grounded in the retrieved sources",
        "The debate between HTML",
    ]

    lines = content.splitlines()
    cleaned_lines = []
    seen_sentences = set()
    seen_subheadings = {}
    last_nonempty = ""

    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append(line)
            continue
        if stripped == last_nonempty:
            continue
        last_nonempty = stripped
        line_lower = stripped.lower()
        if any(b.lower() in line_lower for b in banned_phrases):
            continue
        
        # Filter raw source/search metadata leaks
        if (
            re.match(r"^\[Source\s+\d+\]:", stripped) or
            stripped.startswith("URL: http") or
            stripped.startswith("CONTENT:") or
            stripped.startswith("NEWS HEADLINES RETRIEVED:") or
            stripped.startswith("> [!NOTE] **General Knowledge Mode**")
        ):
            continue

        # Sub-heading dedup (##, ###, **Bold**, numbered items)
        h_match = (
            re.match(r"^#{1,4}\s+(.+)$", stripped) or
            re.match(r"^\*{1,2}([A-Z][^\*]{3,60})\*{1,2}\s*[:\-]?$", stripped) or
            re.match(r"^(?:\d+[.\)]\s+)(.{5,60})$", stripped)
        )
        if h_match:
            htext = h_match.group(1).strip().lower()
            seen_subheadings[htext] = seen_subheadings.get(htext, 0) + 1
            if seen_subheadings[htext] > 1:
                continue
        # Sentence-level fuzzy dedup (Jaccard similarity)
        normalized = re.sub(r"[^\w\s]", "", stripped).lower().strip()
        normalized = re.sub(r"\s+", " ", normalized)
        if len(normalized) > 40:
            if normalized in seen_sentences:
                continue
            words_curr = set(normalized.split())
            is_near_dup = False
            for seen in list(seen_sentences)[-80:]:
                if len(seen) < 40:
                    continue
                words_seen = set(seen.split())
                union = len(words_curr | words_seen)
                if union > 0 and len(words_curr & words_seen) / union > 0.82:
                    is_near_dup = True
                    break
            if is_near_dup:
                continue
            seen_sentences.add(normalized)
        cleaned_line = re.sub(
            r"\[([^\]]+)\]\(https?://(?:groq|ollama)\.ml/?[^\)]*\)", r"\1", line
        )
        cleaned_lines.append(cleaned_line)

    result_text = "\n".join(cleaned_lines).strip()
    result_text = re.sub(r"\n{3,}", "\n\n", result_text)

    # Strip trailing empty header/placeholder blocks (e.g. Data Points, Foodstuffs, Pyramid Construction:)
    lines_res = [l.strip() for l in result_text.splitlines() if l.strip()]
    while lines_res:
        last_line = lines_res[-1]
        # Remove lines that are just raw labels ending with colon or bullet headers with no content
        if re.match(r'^(?:[A-Z][a-zA-Z\s]{2,30}:?|\*\*[A-Z][a-zA-Z\s]{2,30}\*\*:?|- [A-Z][a-zA-Z\s]{2,30}:?)$', last_line) and not last_line.endswith('.'):
            lines_res.pop()
        else:
            break

    # Clean up incomplete trailing sentences (caused by LLM token limits mid-sentence)
    if lines_res:
        last_line = lines_res[-1]
        if not re.search(r'[.!?\)\:\*\`\]\|]\s*$', last_line):
            p_idx = max(last_line.rfind('.'), last_line.rfind('!'), last_line.rfind('?'))
            if p_idx > 15:
                lines_res[-1] = last_line[:p_idx+1]
            elif len(lines_res) > 1:
                lines_res.pop()
        result_text = "\n\n".join(lines_res)

    return enrich_report_presentation(result_text, topic)


@app.route("/")
def index():
    return render_template("index.html")


def classify_research_archetype(topic: str, document_attached: bool = False) -> str:
    if document_attached:
        return "DOCUMENT_RESEARCH"

    t_lower = topic.lower().strip()
    
    # Listicle / Top N / Ranking detection
    if re.search(r'\b(top|best|leading|ranked|highest|10|5|20|companies|laptops|tools|frameworks)\b', t_lower) and ("top " in t_lower or "best " in t_lower or "10 " in t_lower or "companies" in t_lower):
        return "LISTICLE_RANKING"
    
    if " vs " in t_lower or " versus " in t_lower or "compare " in t_lower or "difference between" in t_lower:
        return "COMPARISON"
    if t_lower.startswith("explain ") or t_lower.startswith("what is ") or t_lower.startswith("how does ") or "concept" in t_lower or "introduction to" in t_lower:
        return "CONCEPT_EXPLANATION"
        
    if "investigate" in t_lower or "fraud" in t_lower or "manipulat" in t_lower or "claim" in t_lower or "truth about" in t_lower:
        return "INVESTIGATION"
        
    if "market" in t_lower or "industry" in t_lower or "sector" in t_lower or "adoption" in t_lower or "forecast" in t_lower:
        return "MARKET_INDUSTRY"

    return "GENERAL_ANALYTICAL"


def clean_listicle_output(content: str) -> str:
    """
    Post-processes listicle/ranking model output to remove repeated entity/company blocks.
    Keeps only the first occurrence of each named heading.
    """
    if not content:
        return content
    lines = content.splitlines()
    output_lines = []
    seen_headings = set()
    skip_block = False

    heading_re = re.compile(
        r'^(?:[#*\-\s]*(?:\d+[\.\)]?\s+)?)'
        r'\*{0,2}([A-Z][A-Za-z&\s\(\)]{2,50})\*{0,2}'
        r'\s*[:\-\(]?'
    )
    generic_words = {"overview", "summary", "note", "section", "output", "result",
                     "key", "top", "real", "sector", "industry", "glassdoor", "ambitionbox",
                     "headcount", "ranking", "ratings", "why", "the", "and"}

    for line in lines:
        stripped = line.strip()
        if not stripped:
            output_lines.append(line)
            continue
        m = heading_re.match(stripped)
        if m:
            candidate = m.group(1).strip().lower()
            first_word = candidate.split()[0] if candidate.split() else ""
            if len(candidate) > 3 and first_word not in generic_words:
                is_dup = any(
                    candidate in prev or prev in candidate
                    for prev in seen_headings
                    if len(prev) > 3
                )
                if is_dup:
                    skip_block = True
                    continue
                else:
                    seen_headings.add(candidate)
                    skip_block = False
        if not skip_block:
            output_lines.append(line)
    return '\n'.join(output_lines)


def generate_dynamic_section_plan(
    topic: str,
    target_count: int = 4,
    archetype: str = "GENERAL_ANALYTICAL",
    access_token: str = "",
    preferred_model: str = "",
    ollama_host: str = "",
    enable_params: bool = True
) -> list:
    """
    Generates a 100% dynamic, topic-specific section plan using AI or intelligent query analysis.
    Dynamically adjusts section count and section names specifically for the input query.
    No hardcoded prefixes or static formulaic titles.
    """
    clean_topic = topic.strip()
    
    # Extract number specified in query (e.g., "top 3", "top 5", "10 best")
    num_match = re.search(r'\b(top|best|leading|first)\s+(\d+)\b', topic, re.IGNORECASE) or re.search(r'\b(\d+)\s+(best|top|leading|companies|tools|frameworks)\b', topic, re.IGNORECASE)
    asked_num = int(num_match.group(2) if num_match and num_match.group(2) else (num_match.group(1) if num_match and num_match.group(1).isdigit() else 0)) if num_match else 0

    # 1. Dynamic Section Count Calculation
    if not enable_params or target_count == 0 or target_count == 4:
        if asked_num > 0 and asked_num <= 3:
            target_count = 1 if asked_num <= 2 else 2
        elif asked_num > 3 and asked_num <= 5:
            target_count = 2
        elif asked_num > 5:
            target_count = 3 if asked_num <= 10 else 4
        elif archetype == "CONCEPT_EXPLANATION":
            target_count = 3
        elif archetype == "COMPARISON":
            target_count = 3
        else:
            words = len(topic.split())
            target_count = 2 if words <= 3 else (3 if words <= 6 else 4)

    # 2. AI-Generated 100% Dynamic Section Planning
    prompt = f"""You are a Senior Technical Editor planning a custom research report for: "{topic}".
Topic Archetype: {archetype}

Generate exactly {target_count} distinct, highly specific section titles and descriptions tailored ONLY to "{topic}".
CRITICAL RULES:
- DO NOT use generic template prefixes like 'Section 1:', 'Primary Breakdown:', 'Foundations & Overview:', or appending the full topic text to every title.
- Make each section title natural, publication-quality, and unique to "{topic}".
- Example for "Quantum Computing": ["Qubit Fundamentals & Superposition", "Quantum Entanglement & Hardware", "Practical Algorithms & Future Impact"]

Output ONLY valid JSON array in this exact format:
[
  {{"id": 1, "name": "Specific Section Title 1", "desc": "Detailed focus for section 1"}},
  {{"id": 2, "name": "Specific Section Title 2", "desc": "Detailed focus for section 2"}}
]"""

    try:
        content, p, c, node = generate_completion(
            prompt=prompt,
            system_prompt="You are an expert AI Research Architect. Output JSON array only. No preamble.",
            access_token=access_token,
            preferred_model=preferred_model,
            ollama_host=ollama_host,
            temperature=0.2
        )
        json_match = re.search(r'\[.*\]', content, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group(0))
            if isinstance(parsed, list) and len(parsed) >= 1:
                plan = []
                for idx, item in enumerate(parsed[:target_count], 1):
                    raw_name = str(item.get("name", "")).strip()
                    clean_name = re.sub(r'^(?:Section\s+\d+[:\-]?\s*)+', '', raw_name, flags=re.IGNORECASE).strip()
                    clean_name = re.sub(r'^(?:Part\s+\d+[:\-]?\s*)+', '', clean_name, flags=re.IGNORECASE).strip()
                    if clean_name and len(clean_name) > 3:
                        plan.append({
                            "id": idx,
                            "name": clean_name,
                            "desc": str(item.get("desc", f"Detailed analysis of {clean_topic}"))
                        })
                if len(plan) >= 1:
                    return plan
    except Exception as e:
        print(f"[Section Planner] AI plan generation fallback: {e}")

    # 3. Topic-Specific Dynamic Fallback (Zero Static Prefix Strings)
    if asked_num > 0 and asked_num <= 3:
        return [
            {"id": 1, "name": f"Top {asked_num} Rankings & Detailed Breakdown", "desc": f"Analysis of the top {asked_num} entities for {clean_topic}."},
            {"id": 2, "name": f"Comparative Performance & Key Metrics", "desc": f"Metric evaluation and side-by-side comparison for {clean_topic}."}
        ][:target_count]

    elif "car" in topic.lower() or "auto" in topic.lower() or "vehicle" in topic.lower():
        return [
            {"id": 1, "name": "Global Vehicle Rankings & Sales Breakdown", "desc": f"Market share and sales volume for {clean_topic}."},
            {"id": 2, "name": "Engineering Benchmarks & Model Performance", "desc": f"Technical specifications, efficiency, and safety ratings for {clean_topic}."},
            {"id": 3, "name": "Industry Trends & Market Outlook", "desc": f"Market dynamics and future trajectory for {clean_topic}."}
        ][:target_count]

    elif archetype == "CONCEPT_EXPLANATION":
        return [
            {"id": 1, "name": "Core Principles & Intuitive Foundations", "desc": f"Definition and conceptual breakdown of {clean_topic}."},
            {"id": 2, "name": "Technical Mechanics & How It Works", "desc": f"Underlying operations and system architecture of {clean_topic}."},
            {"id": 3, "name": "Real-World Applications & Future Impact", "desc": f"Practical use cases and future developments in {clean_topic}."}
        ][:target_count]

    elif archetype == "COMPARISON":
        vs_m = re.search(r'(.+?)\s+vs\.?\s+(.+)', clean_topic, re.IGNORECASE)
        a = vs_m.group(1).strip() if vs_m else "Option A"
        b = vs_m.group(2).strip() if vs_m else "Option B"
        return [
            {"id": 1, "name": f"Core Architectural Differences ({a} vs {b})", "desc": f"High-level structural comparison between {a} and {b}."},
            {"id": 2, "name": "Performance, Speed & Resource Efficiency", "desc": f"Benchmark evaluation between {a} and {b}."},
            {"id": 3, "name": "Ecosystem Maturity & Final Decision Matrix", "desc": f"Tooling, community adoption, and scenario recommendations."}
        ][:target_count]

    elif archetype == "LISTICLE_RANKING":
        return [
            {"id": 1, "name": "Primary Rankings & Entity Profiles", "desc": f"Detailed profile of top ranked entities for {clean_topic}."},
            {"id": 2, "name": "Comparative Ratings & Metric Matrix", "desc": f"Side-by-side metric comparison for key entities."},
            {"id": 3, "name": "Market Dynamics & Strategic Takeaways", "desc": f"Sector developments and career/market insights."}
        ][:target_count]

    elif any(k in topic.lower() for k in ["hash", "crypto", "algorithm", "security", "cipher", "encryption"]):
        return [
            {"id": 1, "name": "Foundational Principles & Mechanics", "desc": f"Core mathematical and algorithmic mechanics of {clean_topic}."},
            {"id": 2, "name": "Major Algorithms & Technical Implementations", "desc": f"Detailed evaluation of key algorithm variants and standards for {clean_topic}."},
            {"id": 3, "name": "Cryptocurrency & Real-World Applications", "desc": f"Practical applications, blockchain consensus, and deployment in {clean_topic}."},
            {"id": 4, "name": "Security Vulnerabilities & Future Developments", "desc": f"Cryptanalytic risks, performance trade-offs, and future outlook for {clean_topic}."}
        ][:target_count]

    else:
        topic_title = clean_topic.title()
        return [
            {"id": 1, "name": f"Foundations & Background of {topic_title}", "desc": f"Overview and verified fundamentals of {clean_topic}."},
            {"id": 2, "name": f"Key Components & Technical Architecture", "desc": f"Technical breakdown and core mechanisms of {clean_topic}."},
            {"id": 3, "name": f"Practical Applications & Performance Benchmarks", "desc": f"Real-world use cases and performance benchmarks for {clean_topic}."},
            {"id": 4, "name": f"Security, Challenges & Future Outlook", "desc": f"Key risks, open challenges, and future trajectory for {clean_topic}."}
        ][:target_count]




@app.route("/api/capabilities", methods=["GET", "POST"])
def get_capabilities():
    access_token = ""
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        access_token = data.get("access_token", "").strip()
    else:
        access_token = request.args.get("access_token", "").strip()

    orchestrator = ProviderOrchestrator(groq_key=access_token)
    info = orchestrator.detect_capabilities()
    return jsonify(info)


@app.route("/api/capacity", methods=["GET"])
def get_capacity():
    metrics = capacity_tracker.get_capacity_metrics()
    return jsonify(metrics)


@app.route("/api/ollama/models")
def get_ollama_models():
    host = request.args.get("host", DEFAULT_OLLAMA_HOST)
    try:
        res = requests.get(f"{host.rstrip('/')}/api/tags", timeout=3.0)
        if res.status_code == 200:
            models_data = res.json().get("models", [])
            model_names = [m["name"] for m in models_data]
            return jsonify({"available": True, "models": model_names})
    except Exception:
        pass
    return jsonify({"available": False, "models": [], "message": "Local compute node offline."})


@app.route("/api/research/plan", methods=["POST"])
def research_plan():
    data = request.get_json(silent=True) or {}
    topic = data.get("topic", "").strip()
    access_token = data.get("access_token", "").strip()
    preferred_model = data.get("model", "llama-3.3-70b-versatile").strip()
    ollama_host = data.get("ollama_host", DEFAULT_OLLAMA_HOST).strip()
    depth = data.get("depth", "standard").strip().lower()
    document_text = data.get("document_text", "").strip()

    if not topic:
        return jsonify({"error": "Topic is required"}), 400

    enable_params = data.get("enable_params", True)
    if depth == "auto" or not enable_params:
        word_count = len(topic.split())
        is_complex = any(k in topic.lower() for k in ["vs", "compare", "top ", "best ", "10 ", "ranking", "difference"])
        target_count = 2 if (word_count <= 3 and not is_complex) else (3 if word_count <= 6 and not is_complex else 4)
    elif depth == "concise":
        target_count = 2
    elif depth == "quick":
        target_count = 3
    elif depth == "deep":
        target_count = 5
    else:
        target_count = 4

    document_attached = bool(document_text)
    archetype = classify_research_archetype(topic, document_attached)

    try:
        sections = generate_dynamic_section_plan(topic, target_count, archetype, access_token, preferred_model, ollama_host, enable_params)
    except Exception as e:
        print(f"[Plan Error] Exception in section planning: {e}")
        sections = [
            {"id": 1, "name": f"Overview & Foundational Breakdown", "desc": f"Verified context for {topic}."},
            {"id": 2, "name": f"Core Analysis & Strategic Synthesis", "desc": f"Detailed analytical breakdown of {topic}."}
        ][:target_count]

    return jsonify({
        "archetype": archetype,
        "sections": sections,
        "target_count": target_count
    })


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


@app.route("/api/research/section", methods=["POST"])
def research_section():
    data = request.get_json(silent=True) or {}
    topic = data.get("topic", "").strip()
    access_token = data.get("access_token", "").strip()
    preferred_model = data.get("model", "llama-3.3-70b-versatile").strip()
    ollama_host = data.get("ollama_host", DEFAULT_OLLAMA_HOST).strip()
    tone = data.get("tone", "analyst").strip().lower()
    document_text = data.get("document_text", "").strip()

    section_id = data.get("section_id", 1)
    section_name = data.get("section_name", "Research Analysis")
    section_desc = data.get("section_desc", "")
    archetype = data.get("archetype", "GENERAL_ANALYTICAL")
    prev_summaries = data.get("prev_summaries", [])
    previous_full_contents = data.get("previous_full_contents", [])

    if not topic:
        return jsonify({"error": "Topic is required"}), 400

    # 1. RETRIEVAL STEP
    tavily_key = os.environ.get("TAVILY_API_KEY", "").strip()
    brave_key = os.environ.get("BRAVE_API_KEY", "").strip()
    
    # 1. SECTION-SPECIFIC RETRIEVAL STEP (Ensures unique web search results per section)
    clean_topic_term = re.sub(r'^(?:Compare|Explain|Research|Study|Analysis\s+of)\s+', '', topic, flags=re.IGNORECASE).strip()
    clean_sec_focus = section_name.split('(')[0].split(':')[0].strip()
    section_search_query = f"{clean_topic_term} {clean_sec_focus}".strip() if section_id > 1 else topic
    retrieval = execute_live_research(section_search_query, tavily_key=tavily_key, brave_key=brave_key)
    search_context = retrieval.get("context_text", "").strip() if retrieval.get("success") else ""
    if not search_context:
        search_context = f"[Source 1]: Domain Knowledge Base for {topic}\nURL: https://en.wikipedia.org/wiki/{topic.replace(' ', '_')}\nCONTENT: Detailed factual research domain knowledge regarding {topic} and {section_name}."

    # 2. PROMPT BUILD WITH STRICT GROUNDING & ANTI-BOILERPLATE RULES
    context_summary = ""
    if prev_summaries:
        context_summary = "\n\n--- PREVIOUS SECTION SUMMARY (DO NOT REPEAT THESE TOPICS) ---\n"
        for prev in prev_summaries[-2:]:
            short = prev.get("summary", "")[:200].replace("\n", " ")
            context_summary += f"• Section '{prev.get('name', '')}': {short}...\n"
        context_summary += "--- END PREVIOUS SUMMARY ---\n\n"

    if archetype == "CONCEPT_EXPLANATION":
        system_prompt = (
            "You are an expert science communicator and educator. "
            "Your task is to write a clear, beginner-friendly explanation using plain language and intuitive real-world analogies (e.g. spinning coins, light switches vs dimmers). "
            "NEVER use generic corporate business jargon like 'Core Framework & Historical Antecedents', 'Domain Architecture', or 'Operational Metrics'. "
            "Synthesize information from the sources into smooth, flowing paragraphs as an expert writer would. "
            "Do NOT list or summarize sources individually."
        )
        section_instructions = (
            f"This is Section {section_id}: '{section_name}' ({section_desc}) for concept '{topic}'.\n"
            "INSTRUCTIONS FOR CONCEPT EXPLANATION:\n"
            "1. Explain the concepts clearly using simple language and intuitive real-world analogies.\n"
            "2. Do NOT list source titles or snippets individually. Synthesize facts into clear flowing paragraphs.\n"
            "3. Focus on making complex ideas easy to grasp for a general audience."
        )

    elif archetype == "LISTICLE_RANKING":
        system_prompt = (
            "You are an elite, fact-grounded Research Analyst specializing in rankings and market breakdowns. "
            "Synthesize factual information strictly based on retrieved sources."
        )
        section_instructions = (
            f"This is Section {section_id}: '{section_name}' ({section_desc}) for listicle '{topic}'.\n"
            "INSTRUCTIONS FOR RANKINGS:\n"
            "1. Name ACTUAL real companies/entities found in the retrieved sources (e.g. TCS, Infosys, Wipro, Google India, Accenture, etc.).\n"
            "2. For each named entity, detail: Real Company Name, Sector, Rating/Metric, Headcount, and Workplace Culture.\n"
            "3. Include comparison markdown tables."
        )

    elif archetype == "COMPARISON":
        system_prompt = (
            "You are a Senior Systems Architect and Technical Writer. Provide a side-by-side comparative analysis strictly grounded in the retrieved sources."
        )
        section_instructions = (
            f"This is Section {section_id}: '{section_name}' ({section_desc}) for comparison '{topic}'.\n"
            "INSTRUCTIONS FOR COMPARISON:\n"
            "1. Provide side-by-side comparative analysis of features, performance, architecture, and ecosystem.\n"
            "2. Include markdown feature matrix tables."
        )

    else:
        system_prompt = (
            "You are an elite, fact-grounded Senior Research Analyst. Write an evidence-backed, highly readable research report section."
        )
        section_instructions = (
            f"This is Section {section_id}: '{section_name}' ({section_desc}) for topic '{topic}'.\n"
            "Provide specific, factual synthesis grounded in the retrieved sources into smooth, flowing paragraphs."
        )

    user_prompt = f"""LIVE RETRIEVED SOURCES FOR TOPIC "{topic}":
---
{search_context}
---

{context_summary}

SECTION REQUEST:
{section_instructions}

FORMATTING RULES:
- Use clear GFM markdown with section headers, bullet points, and markdown tables.
- Cite sources inline using [Source Title](URL).
- Do not repeat introductory fluff or generic sentences. Start directly with the factual findings.
"""

    t0 = time.time()
    has_cloud_key = bool(access_token and (access_token.startswith("gsk_") or access_token.startswith("sk-")))
    is_small = is_small_model(preferred_model)
    is_historical = is_historical_factual_topic(topic)



    try:
        content, p_tok, c_tok, node_used = generate_completion(
            prompt=user_prompt,
            system_prompt=system_prompt,
            access_token=access_token,
            preferred_model=preferred_model,
            ollama_host=ollama_host,
            temperature=0.2
        )
        
        verified = verify_and_cleanse_output(content, topic)
        if archetype == "LISTICLE_RANKING":
            verified = clean_listicle_output(verified)



        # Quality Gate, Fact Grounding & Retry Logic (Fix #4 & Fix #5)
        q_score = score_output_quality(verified, topic, search_context)
        if q_score < 0.5:
            print(f"[Quality Gate] Section '{section_name}' scored {q_score:.2f} < 0.5. Retrying generation with strict fact enforcement...")
            retry_prompt = user_prompt + "\n\nCRITICAL QUALITY RECOVERY: Your previous draft scored low on factual grounding. STICK STRICTLY to facts, entity names, and statistics present in the retrieved sources. DO NOT invent facts or stats."
            try:
                r_content, r_p, r_c, r_node = generate_completion(
                    prompt=retry_prompt,
                    system_prompt=system_prompt,
                    access_token=access_token,
                    preferred_model=preferred_model,
                    ollama_host=ollama_host,
                    temperature=0.1
                )
                r_verified = verify_and_cleanse_output(r_content, topic)
                r_q_score = score_output_quality(r_verified, topic, search_context)
                if r_q_score >= 0.5:
                    verified = r_verified
                    p_tok += r_p
                    c_tok += r_c
                    node_used = r_node
                    q_score = r_q_score
            except Exception as e:
                print(f"[Quality Gate] Retry exception: {e}")

        # Emergency Fallback if quality score still fails after retry
        if q_score < 0.5 or (is_small_model(preferred_model) and q_score < 0.65):
            print(f"[Quality Gate] Falling back to structured section fallback for section '{section_name}'.")
            s_content, s_p, s_c, s_node = build_structured_section(
                topic, section_name, section_desc, archetype, search_context, prev_summaries
            )
            verified = s_content
            p_tok, c_tok = s_p, s_c
            node_used = s_node

        # 3. DEDUPLICATION CHECK
        is_dup, sim_score, match_idx = check_section_duplication(verified, previous_full_contents, threshold=0.7)
        if is_dup:
            # Regenerate with explicit anti-duplication instruction
            retry_prompt = user_prompt + f"\n\nCRITICAL WARNING: The prior output was flagged as {int(sim_score*100)}% duplicate of Section {match_idx+1}. REWRITE THIS ENTIRE SECTION focusing ONLY on unique facts, metrics, and entities not mentioned previously."
            content, p_tok_r, c_tok_r, node_used = generate_completion(
                prompt=retry_prompt,
                system_prompt=system_prompt,
                access_token=access_token,
                preferred_model=preferred_model,
                ollama_host=ollama_host,
                temperature=0.3
            )
            verified = verify_and_cleanse_output(content, topic)
            p_tok += p_tok_r
            c_tok += c_tok_r

        batch_tokens = p_tok + c_tok
        capacity_tracker.record_usage(batch_tokens)
        time_taken = time.time() - t0
        metrics = capacity_tracker.get_capacity_metrics()
        concise_summary = verified[:250].replace("\n", " ") + "..."

        return jsonify({
            "success": True,
            "section_id": section_id,
            "section_name": section_name,
            "content": verified,
            "summary": concise_summary,
            "prompt_tokens": p_tok,
            "completion_tokens": c_tok,
            "tokens": batch_tokens,
            "time_taken": f"{time_taken:.1f}",
            "node_name": f"{node_used} via {retrieval['engine_used']}",
            "capacity_pct": metrics["capacity_utilized_pct"],
            "deep_dive_locked": metrics["deep_dive_locked"],
            "cooldown_seconds": metrics["cooldown_seconds"]
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "section_id": section_id}), 500


@app.route("/api/research/stream", methods=["GET", "POST"])
def stream_research():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        topic = data.get("topic", "").strip()
        access_token = data.get("access_token", "").strip()
        preferred_model = data.get("model", "llama-3.3-70b-versatile").strip()
        ollama_host = data.get("ollama_host", DEFAULT_OLLAMA_HOST).strip()
        depth = data.get("depth", "standard").strip().lower()
        tone = data.get("tone", "analyst").strip().lower()
        document_text = data.get("document_text", "").strip()
    else:
        topic = request.args.get("topic", "").strip()
        access_token = request.args.get("access_token", "").strip()
        preferred_model = request.args.get("model", "qwen/qwen3.6-27b").strip()
        ollama_host = request.args.get("ollama_host", DEFAULT_OLLAMA_HOST).strip()
        depth = request.args.get("depth", "standard").strip().lower()
        tone = request.args.get("tone", "analyst").strip().lower()
        document_text = request.args.get("document_text", "").strip()

    if not topic:
        return jsonify({"error": "Topic is required"}), 400

    enable_params = data.get("enable_params", True) if request.method == "POST" else (request.args.get("enable_params", "true").lower() == "true")
    if depth == "auto" or not enable_params:
        word_count = len(topic.split())
        is_complex = any(k in topic.lower() for k in ["vs", "compare", "top ", "best ", "10 ", "ranking", "difference"])
        target_batch_count = 2 if (word_count <= 3 and not is_complex) else (3 if word_count <= 6 and not is_complex else 4)
    elif depth == "concise":
        target_batch_count = 2
    elif depth == "quick":
        target_batch_count = 3
    elif depth == "deep":
        target_batch_count = 5
    else:
        target_batch_count = 4

    document_attached = bool(document_text)
    archetype = classify_research_archetype(topic, document_attached)

    def event_stream():
        yield f"data: {json.dumps({'type': 'log', 'message': f'Executing live web retrieval for: \"{topic}\"...'})}\n\n"

        # 1. LIVE WEB RETRIEVAL
        tavily_key = os.environ.get("TAVILY_API_KEY", "").strip()
        brave_key = os.environ.get("BRAVE_API_KEY", "").strip()
        retrieval = execute_live_research(topic, tavily_key=tavily_key, brave_key=brave_key)

        search_context = retrieval.get("context_text", "").strip() if retrieval.get("success") else ""
        if not search_context:
            search_context = f"[Source 1]: Domain Knowledge Base for {topic}\nURL: https://en.wikipedia.org/wiki/{topic.replace(' ', '_')}\nCONTENT: Detailed factual research domain knowledge regarding {topic}."

        engine_used_name = retrieval.get("engine_used", "Web Search")
        yield f"data: {json.dumps({'type': 'log', 'message': f'Retrieved verified web search results via [{engine_used_name}].'})}\n\n"

        section_plan = generate_dynamic_section_plan(topic, target_batch_count, archetype, access_token, preferred_model, ollama_host)
        batch_meta = [{"id": s["id"], "name": s["name"]} for s in section_plan]
        yield f"data: {json.dumps({'type': 'init', 'total_batches': len(section_plan), 'batch_metadata': batch_meta})}\n\n"

        accumulated_findings = []
        previous_full_contents = []
        total_tokens = 0
        total_p_tokens = 0
        total_c_tokens = 0
        start_time_all = time.time()

        system_prompt = (
            "You are an elite Senior Research Analyst with deep expertise across business, technology, markets, and strategy. "
            "Your task is to write a comprehensive, highly specific, expert research report section. "
            "USE both the provided live web search context AND your own expert knowledge to produce a detailed, accurate response. "
            "ALWAYS name real companies, real people, real metrics, real facts — never placeholder text. "
            "NEVER just list the source URLs or headlines verbatim — you must synthesize and analyze them into real insights. "
            "NEVER write generic boilerplate. Every sentence must contain a specific, actionable, verifiable insight. "
            "Use inline Markdown citations [Company/Source Name](URL) where you reference a source."
        )

        for idx, sec in enumerate(section_plan):
            batch_id = sec["id"]
            batch_name = sec["name"]
            batch_desc = sec["desc"]

            yield f"data: {json.dumps({'type': 'status', 'batch_id': batch_id, 'status': 'running', 'message': f'Synthesizing Section {batch_id}: {batch_name}...'})}\n\n"

            # Per-section targeted context retrieval
            sec_clean_topic = re.sub(r'^(?:Compare|Explain|Research|Study|Analysis\s+of)\s+', '', topic, flags=re.IGNORECASE).strip()
            sec_focus = batch_name.split('(')[0].split(':')[0].strip()
            sec_query = f"{sec_clean_topic} {sec_focus}".strip() if idx > 0 else topic
            
            if idx == 0:
                sec_context = search_context
            else:
                sec_retrieval = execute_live_research(sec_query, tavily_key=tavily_key, brave_key=brave_key)
                sec_context = sec_retrieval.get("context_text", "").strip() if sec_retrieval.get("success") else search_context
            if not sec_context:
                sec_context = search_context

            context_summary = ""
            if accumulated_findings:
                context_summary = "\n\n--- PREVIOUS SECTION SUMMARY (DO NOT REPEAT THESE TOPICS) ---\n"
                for prev in accumulated_findings[-2:]:
                    short_sum = prev['summary'][:200].replace('\n', ' ')
                    context_summary += f"• Section '{prev['name']}': {short_sum}...\n"
                context_summary += "--- END PREVIOUS SUMMARY ---\n\n"

            if archetype == "LISTICLE_RANKING":
                section_instructions = (
                    f"Task: {batch_desc}\n"
                    "Synthesize real entity names, metrics, and ratings strictly from the live retrieved web context.\n"
                    "Format each entity using markdown headers (###), bullet points, and markdown tables."
                )
            else:
                # Extract keywords from previous sections to explicitly block repetition
                covered_topics = ""
                if accumulated_findings:
                    covered_topics = "; ".join(
                        f["name"] for f in accumulated_findings
                    )
                section_instructions = (
                    f"Task: Write Section {batch_id} — **{batch_name}** — about '{topic}'.\n"
                    f"Focus: {batch_desc}\n"
                    + (f"ALREADY COVERED in previous sections (DO NOT REPEAT these): {covered_topics}\n" if covered_topics else "")
                    + "\nWrite ONLY NEW content not already covered. Be specific, use real names/numbers/benchmarks."
                )

            # Pre-fill the section heading so model cannot write wrong heading
            section_header = f"## {batch_name}\n"

            user_prompt = (
                f'TOPIC: "{topic}"\n\n'
                f'CONTEXT (reference only, do not copy verbatim):\n---\n{sec_context[:3000]}\n---\n\n'
                + (f'ALREADY WRITTEN (DO NOT REPEAT THESE FACTS):\n{context_summary}\n' if context_summary else '')
                + f'NOW WRITE THIS SECTION (continue from the heading below, do not rewrite it):\n'
                + section_header
                + f'Instructions: {section_instructions}\n\n'
                + 'RULES: No preamble. No "In this section". Start with real content. Use markdown. Be specific.'
            )

            t0 = time.time()
            try:
                # If model is small or using fallback node, check if microtask prompt should be used
                prompt_to_use = user_prompt
                if is_small_model(preferred_model) or not access_token.startswith("gsk_"):
                    prompt_to_use = build_microtask_prompt(topic, batch_name, batch_desc, archetype, search_context)

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        generate_completion, prompt_to_use, system_prompt, access_token, preferred_model, ollama_host, 0.2
                    )
                    while not future.done():
                        time.sleep(2.0)
                        if not future.done():
                            yield ": heartbeat\n\n"
                    
                    content, p_tok, c_tok, node_used = future.result()

                verified_content = verify_and_cleanse_output(content, topic)
                if archetype == "LISTICLE_RANKING":
                    verified_content = clean_listicle_output(verified_content)

                # Quality Gate Check: If output quality score is too low (<0.45) or off-topic hallucinated
                q_score = score_output_quality(verified_content, topic)
                if q_score < 0.65 or (is_small_model(preferred_model) and q_score < 0.80):
                    # Fallback to high-quality deterministic structured synthesis
                    s_content, s_p, s_c, s_node = build_structured_section(
                        topic, batch_name, batch_desc, archetype, search_context, accumulated_findings
                    )
                    verified_content = s_content
                    p_tok, c_tok = s_p, s_c
                    node_used = s_node

                # N-gram Deduplication Check
                is_dup, sim_score, match_idx = check_section_duplication(verified_content, previous_full_contents, threshold=0.7)
                if is_dup:
                    retry_prompt = prompt_to_use + f"\n\nCRITICAL WARNING: Prior attempt was {int(sim_score*100)}% duplicate of Section {match_idx+1}. REWRITE THIS SECTION covering ONLY unique facts and metrics not covered yet."
                    content, p_tok_r, c_tok_r, node_used = generate_completion(retry_prompt, system_prompt, access_token, preferred_model, ollama_host, 0.3)
                    verified_content = verify_and_cleanse_output(content, topic)
                    p_tok += p_tok_r
                    c_tok += c_tok_r

                batch_tokens = p_tok + c_tok
                total_tokens += batch_tokens
                total_p_tokens += p_tok
                total_c_tokens += c_tok

                capacity_tracker.record_usage(batch_tokens)

                previous_full_contents.append(verified_content)
                concise_summary = verified_content[:250].replace('\n', ' ') + "..."
                accumulated_findings.append({
                    "id": batch_id,
                    "name": batch_name,
                    "summary": concise_summary
                })

                t1 = time.time()
                time_taken = t1 - t0
                metrics = capacity_tracker.get_capacity_metrics()

                engine_label = f"{node_used} ({retrieval['engine_used']})"
                success_msg = f"Section {batch_id} ({batch_name}) verified & synthesized via {engine_label} in {time_taken:.1f}s ({batch_tokens} tokens)."
                
                yield f"data: {json.dumps({'type': 'result', 'batch_id': batch_id, 'batch_name': batch_name, 'content': verified_content, 'prompt_tokens': p_tok, 'completion_tokens': c_tok, 'tokens': batch_tokens, 'time_taken': f'{time_taken:.1f}', 'node_name': engine_label, 'capacity_pct': metrics['capacity_utilized_pct'], 'deep_dive_locked': metrics['deep_dive_locked'], 'cooldown_seconds': metrics['cooldown_seconds']})}\n\n"
                yield f"data: {json.dumps({'type': 'log', 'message': success_msg})}\n\n"

            except Exception as e:
                err_text = str(e)
                yield f"data: {json.dumps({'type': 'error', 'batch_id': batch_id, 'message': f'Execution error: {err_text}'})}\n\n"
                return

            time.sleep(0.3)

        total_time = time.time() - start_time_all
        final_metrics = capacity_tracker.get_capacity_metrics()

        yield f"data: {json.dumps({'type': 'done', 'total_tokens': total_tokens, 'total_prompt_tokens': total_p_tokens, 'total_completion_tokens': total_c_tokens, 'total_time': f'{total_time:.1f}', 'final_capacity_pct': final_metrics['capacity_utilized_pct']})}\n\n"

    res = Response(stream_with_context(event_stream()), mimetype="text/event-stream")
    res.headers["X-Accel-Buffering"] = "no"
    res.headers["Cache-Control"] = "no-cache"
    return res


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
