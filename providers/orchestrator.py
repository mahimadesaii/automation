import os
from .groq_provider import GroqProvider
from .ollama_provider import OllamaProvider
from .search_provider import BrowserSearchProvider
from .news_provider import NewsProvider
from .document_provider import LocalDocumentProvider

class ProviderOrchestrator:
    def __init__(self, groq_key: str = ""):
        self.groq_provider = GroqProvider(api_key=groq_key)
        self.ollama_provider = OllamaProvider()
        self.search_provider = BrowserSearchProvider()
        self.news_provider = NewsProvider()
        self.doc_provider = LocalDocumentProvider()

    def detect_capabilities(self) -> dict:
        """
        Probe all system providers and return live status map.
        """
        groq_available = self.groq_provider.is_available()
        ollama_available = self.ollama_provider.is_available()
        search_available = self.search_provider.is_available()
        news_available = self.news_provider.is_available()
        doc_available = self.doc_provider.is_available()

        mode = "ONLINE_GROQ" if groq_available else "FALLBACK"

        return {
            "mode": mode,
            "mode_badge": "ONLINE RESEARCH — Groq + Web Research" if mode == "ONLINE_GROQ" else "FALLBACK RESEARCH — Using available local/browser/news capabilities",
            "capabilities": {
                "groq": groq_available,
                "ollama": ollama_available,
                "browser_search": search_available,
                "news": news_available,
                "local_documents": doc_available
            }
        }

    def generate_llm_completion(self, prompt: str, system_prompt: str = "", preferred_model: str = "llama-3.3-70b-versatile", temperature: float = 0.2) -> tuple:
        """
        Tiered LLM completion execution with automatic fallback cascading:
        1. Groq Cloud (if API key available)
        2. Ollama (if local node running)
        3. Dynamic Grounded Synthesizer (fallback zero-failure engine)
        """
        # Tier 1: Groq Cloud
        if self.groq_provider.is_available():
            try:
                return self.groq_provider.generate(prompt, system_prompt=system_prompt, model=preferred_model, temperature=temperature)
            except Exception as e:
                print(f"[Orchestrator] Groq Provider failed: {e}")

        # Tier 2: Ollama Local Node
        if self.ollama_provider.is_available():
            try:
                return self.ollama_provider.generate(prompt, system_prompt=system_prompt, temperature=temperature)
            except Exception as e:
                print(f"[Orchestrator] Ollama Provider failed: {e}")

        # Tier 3: Grounded Web Fact Synthesizer Fallback
        from llm_engine import synthesize_grounded_web_facts
        print("[Orchestrator] Falling back to Dynamic Grounded Synthesizer")
        return synthesize_grounded_web_facts(prompt)
