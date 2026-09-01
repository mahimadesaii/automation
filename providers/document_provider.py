import os

class LocalDocumentProvider:
    def __init__(self, doc_dir: str = "knowledge_docs"):
        self.doc_dir = doc_dir

    def is_available(self) -> bool:
        if os.path.exists(self.doc_dir) and os.path.isdir(self.doc_dir):
            files = os.listdir(self.doc_dir)
            return len(files) > 0
        return False

    def retrieve_context(self, query: str) -> str:
        if not self.is_available():
            return ""
        
        extracted_texts = []
        try:
            for fname in os.listdir(self.doc_dir):
                fpath = os.path.join(self.doc_dir, fname)
                if os.path.isfile(fpath) and fname.endswith(('.txt', '.md')):
                    with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                        text = f.read()
                        if query.lower() in text.lower():
                            extracted_texts.append(f"[Local Document: {fname}]\n{text[:1500]}")
        except Exception as e:
            print(f"[LocalDocumentProvider] Document retrieval error: {e}")

        return "\n\n".join(extracted_texts)
