import requests
import os
import json

DEFAULT_OLLAMA_HOST = "http://localhost:11434"

class OllamaProvider:
    def __init__(self, host: str = DEFAULT_OLLAMA_HOST):
        self.host = (os.environ.get("OLLAMA_HOST") or host).rstrip('/')

    def is_available(self) -> bool:
        if bool(os.environ.get("VERCEL")) or os.environ.get("DISABLE_LOCAL_OLLAMA", "").lower() == "true":
            return False
        try:
            r = requests.get(f"{self.host}/api/tags", timeout=1.5)
            return r.status_code == 200
        except Exception:
            return False

    def generate(self, prompt: str, system_prompt: str = "", model: str = "", temperature: float = 0.2, timeout: int = 45) -> tuple:
        if not self.is_available():
            raise ValueError("Ollama service unavailable.")

        # Probe available local models
        try:
            r = requests.get(f"{self.host}/api/tags", timeout=2)
            models_list = [m.get("name") for m in r.json().get("models", [])]
        except Exception:
            models_list = []

        chosen_model = model
        if not chosen_model or chosen_model not in models_list:
            if models_list:
                chosen_model = models_list[0]
            else:
                chosen_model = "qwen2.5:0.5b"

        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt

        payload = {
            "model": chosen_model,
            "prompt": full_prompt,
            "stream": False,
            "options": {"temperature": temperature}
        }

        res = requests.post(f"{self.host}/api/generate", json=payload, timeout=timeout)
        if res.status_code == 200:
            content = res.json().get("response", "")
            p_tok = len(full_prompt) // 4
            c_tok = len(content) // 4
            return content, p_tok, c_tok, f"Local Ollama ({chosen_model})"
        else:
            raise Exception(f"Ollama API returned HTTP {res.status_code}")
