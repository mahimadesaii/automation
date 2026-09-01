import requests
import os
import json

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

class GroqProvider:
    def __init__(self, api_key: str = ""):
        self.api_key = (api_key or os.environ.get("GROQ_API_KEY", "")).strip()

    def is_available(self) -> bool:
        return bool(self.api_key and self.api_key.startswith("gsk_"))

    def generate(self, prompt: str, system_prompt: str = "", model: str = "llama-3.3-70b-versatile", temperature: float = 0.2, timeout: int = 25) -> tuple:
        if not self.is_available():
            raise ValueError("Groq API Key unavailable or invalid.")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Aethelgard-Compute-Engine/2.0"
        }

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        target_models = [model, "llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768", "gemma2-9b-it"]
        last_err = None

        for m in target_models:
            payload = {
                "model": m,
                "messages": messages,
                "temperature": temperature
            }
            try:
                res = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=timeout)
                if res.status_code == 200:
                    data = res.json()
                    content = data["choices"][0]["message"]["content"]
                    usage = data.get("usage", {})
                    p_tok = usage.get("prompt_tokens", len(prompt) // 4)
                    c_tok = usage.get("completion_tokens", len(content) // 4)
                    return content, p_tok, c_tok, f"Groq Cloud ({m})"
                elif res.status_code in (401, 403):
                    raise Exception("Authentication Error (401/403): Invalid or expired Groq API Key.")
            except Exception as e:
                last_err = e
                if "Authentication Error" in str(e):
                    raise e
                continue

        raise Exception(f"Groq Cloud API failure: {last_err}")
