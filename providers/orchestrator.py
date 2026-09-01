import os

class ProviderOrchestrator:
    def __init__(self, groq_key: str = ""):
        self.groq_key = groq_key or os.environ.get("GROQ_API_KEY", "").strip()

    def detect_capabilities(self) -> dict:
        groq_available = bool(self.groq_key and (self.groq_key.startswith("gsk_") or self.groq_key.startswith("sk-")))
        tavily_key = os.environ.get("TAVILY_API_KEY", "").strip()
        brave_key = os.environ.get("BRAVE_API_KEY", "").strip()
        search_available = bool(tavily_key or brave_key)
        
        mode = "ONLINE_GROQ" if groq_available else "FALLBACK"

        return {
            "mode": mode,
            "mode_badge": "ONLINE RESEARCH — Groq + Web Research" if mode == "ONLINE_GROQ" else "FALLBACK RESEARCH — Local/Web capabilities",
            "groq_available": groq_available,
            "search_available": search_available
        }
