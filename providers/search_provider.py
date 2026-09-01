import requests
import os
import json
import re
from search_retrieval import execute_live_research

class BrowserSearchProvider:
    def is_available(self) -> bool:
        # Browser Search is available as long as network HTTP requests succeed
        return True

    def search(self, query: str, archetype: str = "GENERAL_ANALYTICAL") -> tuple:
        return execute_live_research(query, archetype=archetype)
