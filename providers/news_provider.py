import requests
import xml.etree.ElementTree as ET
import urllib.parse

class NewsProvider:
    def is_available(self) -> bool:
        return True

    def fetch_news(self, query: str, max_results: int = 5) -> list:
        try:
            encoded_query = urllib.parse.quote(query)
            rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-US&gl=US&ceid=US:en"
            resp = requests.get(rss_url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                return []
            
            root = ET.fromstring(resp.content)
            items = []
            for item in root.findall(".//item")[:max_results]:
                title = item.findtext("title", "")
                link = item.findtext("link", "")
                pub_date = item.findtext("pubDate", "")
                if title and link:
                    items.append({
                        "title": title,
                        "url": link,
                        "pub_date": pub_date
                    })
            return items
        except Exception as e:
            print(f"[NewsProvider] News RSS fetch failed: {e}")
            return []
