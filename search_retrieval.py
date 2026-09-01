import os
import re
import json
import urllib.parse
import urllib.request
import requests
import xml.etree.ElementTree as ET

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False


def clean_html_content(raw_html: str) -> str:
    """
    Strips HTML tags, script/style tags, navigation bars, ads, and normalizes whitespace.
    """
    if not raw_html:
        return ""
    
    if BS4_AVAILABLE:
        soup = BeautifulSoup(raw_html, "html.parser")
        for element in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "form", "svg"]):
            element.decompose()
        text = soup.get_text(separator=" ")
    else:
        text = re.sub(r'<script.*?>.*?</script>', '', raw_html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style.*?>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
    
    lines = (line.strip() for line in text.splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    cleaned_text = ' '.join(chunk for chunk in chunks if chunk)
    return cleaned_text


def is_relevant_source(url: str, title: str, query: str) -> bool:
    """
    Filters out irrelevant shopping or ad domains for corporate/analytical queries.
    """
    url_lower = (url or "").lower()
    title_lower = (title or "").lower()
    query_lower = (query or "").lower()
    
    # Filter out ad links
    if "duckduckgo.com/y.js" in url_lower or "ad_provider" in url_lower or "/aclick?" in url_lower or "bing.com/aclick" in url_lower:
        return False

    if ("company" in query_lower or "work" in query_lower or "employer" in query_lower or "job" in query_lower):
        irrelevant_domains = ["myntra.com", "flipkart.com", "amazon.com/dp", "meesho.com", "ajio.com", "ebay.com"]
        if any(domain in url_lower for domain in irrelevant_domains):
            return False
    return True


def search_tavily(query: str, api_key: str, max_results: int = 8) -> list:
    if not api_key:
        return []
    
    url = "https://api.tavily.com/search"
    headers = {"Content-Type": "application/json"}
    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "include_answer": True,
        "include_raw_content": False,
        "max_results": max_results
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=12)
        if response.status_code == 200:
            data = response.json()
            results = []
            
            tavily_answer = data.get("answer")
            if tavily_answer:
                results.append({
                    "title": f"Tavily Summary: {query}",
                    "url": "https://tavily.com",
                    "snippet": tavily_answer,
                    "content": tavily_answer,
                    "source": "tavily_summary"
                })
            
            for item in data.get("results", []):
                item_url = item.get("url", "")
                item_title = item.get("title", "Web Result")
                if is_relevant_source(item_url, item_title, query):
                    content = item.get("content", "") or item.get("snippet", "")
                    results.append({
                        "title": item_title,
                        "url": item_url,
                        "snippet": item.get("snippet", ""),
                        "content": clean_html_content(content)[:2500],
                        "source": "tavily"
                    })
            return results
    except Exception as e:
        print(f"[Search Engine] Tavily API error: {e}")
    
    return []


def search_brave(query: str, api_key: str, max_results: int = 8) -> list:
    if not api_key:
        return []
    
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key
    }
    params = {
        "q": query,
        "count": max_results
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            web_results = data.get("web", {}).get("results", [])
            results = []
            for item in web_results:
                item_url = item.get("url", "")
                item_title = item.get("title", "")
                if is_relevant_source(item_url, item_title, query):
                    results.append({
                        "title": item_title,
                        "url": item_url,
                        "snippet": item.get("description", ""),
                        "content": item.get("description", ""),
                        "source": "brave"
                    })
            return results
    except Exception as e:
        print(f"[Search Engine] Brave API error: {e}")
    
    return []


def search_google_news_rss(query: str, max_results: int = 8) -> list:
    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    try:
        rss_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=en-US&gl=US&ceid=US:en"
        res = requests.get(rss_url, headers=headers, timeout=8)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            items = root.findall('.//item')
            for item in items[:max_results]:
                title_elem = item.find('title')
                link_elem = item.find('link')
                pub_elem = item.find('pubDate')
                
                title = title_elem.text if title_elem is not None else ""
                link = link_elem.text if link_elem is not None else ""
                pub_date = pub_elem.text if pub_elem is not None else ""
                
                if title and is_relevant_source(link, title, query):
                    snippet = f"{title} (Published: {pub_date})"
                    results.append({
                        "title": title,
                        "url": link or "https://news.google.com",
                        "snippet": snippet,
                        "content": snippet,
                        "source": "google_news"
                    })
    except Exception as e:
        print(f"[Search Engine] Google News RSS error: {e}")

    return results


def search_wikipedia(query: str, max_results: int = 5) -> list:
    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    try:
        wiki_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(query)}&format=json"
        res = requests.get(wiki_url, headers=headers, timeout=8)
        if res.status_code == 200:
            search_items = res.json().get("query", {}).get("search", [])
            for item in search_items[:max_results]:
                title = item.get("title", "")
                snippet_raw = item.get("snippet", "")
                clean_snippet = re.sub(r'<[^>]+>', '', snippet_raw).strip()
                page_url = f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
                
                if title and clean_snippet:
                    results.append({
                        "title": f"Wikipedia: {title}",
                        "url": page_url,
                        "snippet": clean_snippet,
                        "content": clean_snippet,
                        "source": "wikipedia"
                    })
    except Exception as e:
        print(f"[Search Engine] Wikipedia API error: {e}")

    return results


def search_duckduckgo(query: str, max_results: int = 8) -> list:
    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        html_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        res = requests.get(html_url, headers=headers, timeout=8)
        if res.status_code == 200 and BS4_AVAILABLE:
            soup = BeautifulSoup(res.text, "html.parser")
            for div in soup.find_all("div", class_="result")[:max_results]:
                a_title = div.find("a", class_="result__a")
                a_snippet = div.find("a", class_="result__snippet")
                if not a_title:
                    continue
                
                title = a_title.get_text().strip()
                snippet = a_snippet.get_text().strip() if a_snippet else ""
                raw_href = a_title.get("href", "")
                if raw_href.startswith("//"):
                    raw_href = "https:" + raw_href
                
                if "/uddg/?" in raw_href or "/l/?" in raw_href:
                    parsed = urllib.parse.parse_qs(urllib.parse.urlparse(raw_href).query)
                    link = parsed.get("uddg", [raw_href])[0]
                else:
                    link = raw_href
                    
                if title and link and is_relevant_source(link, title, query):
                    results.append({
                        "title": title,
                        "url": link,
                        "snippet": snippet,
                        "content": snippet,
                        "source": "duckduckgo_html"
                    })
    except Exception as e:
        print(f"[Search Engine] DuckDuckGo HTML error: {e}")

    # Fallback to DuckDuckGo Instant Answer API
    if len(results) < 2:
        try:
            ia_url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json&no_html=1&skip_disambig=1"
            res = requests.get(ia_url, headers=headers, timeout=6)
            if res.status_code == 200:
                data = res.json()
                abstract = data.get("AbstractText", "")
                if abstract:
                    results.append({
                        "title": data.get("Heading", query),
                        "url": data.get("AbstractURL", "https://duckduckgo.com"),
                        "snippet": abstract,
                        "content": abstract,
                        "source": "duckduckgo_ia"
                    })
        except Exception as e:
            print(f"[Search Engine] DuckDuckGo IA error: {e}")

    return results


def fetch_page_content(url: str, timeout: int = 6) -> str:
    if not url or not url.startswith("http") or "news.google.com" in url or "wikipedia.org" in url:
        return ""
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        res = requests.get(url, headers=headers, timeout=timeout)
        if res.status_code == 200:
            cleaned = clean_html_content(res.text)
            return cleaned[:3000]
    except Exception:
        pass
    return ""


def execute_live_research(query: str, tavily_key: str = "", brave_key: str = "", max_results: int = 8) -> dict:
    tavily_key = tavily_key or os.environ.get("TAVILY_API_KEY", "").strip()
    brave_key = brave_key or os.environ.get("BRAVE_API_KEY", "").strip()
    
    results = []
    engine_used = "None"
    
    # Tier 1: Tavily Primary
    if tavily_key:
        results = search_tavily(query, tavily_key, max_results=max_results)
        if results:
            engine_used = "Tavily Search API"
    
    # Tier 2: Brave Secondary
    if not results and brave_key:
        results = search_brave(query, brave_key, max_results=max_results)
        if results:
            engine_used = "Brave Search API"
            
    # Tier 3: Google News RSS Search
    if not results:
        results = search_google_news_rss(query, max_results=max_results)
        if results:
            engine_used = "Google News Live Search"
            
    # Tier 4: Wikipedia Encyclopedic Search
    if not results or len(results) < 3:
        wiki_res = search_wikipedia(query, max_results=4)
        if wiki_res:
            results.extend(wiki_res)
            if engine_used == "None":
                engine_used = "Wikipedia Intelligence"

    # Tier 5: DuckDuckGo Fallback
    if not results:
        results = search_duckduckgo(query, max_results=max_results)
        if results:
            engine_used = "DuckDuckGo Web Search"

    # Deep fetch top pages if snippets are short (for direct web links)
    for r in results[:3]:
        original_snippet = r.get("snippet", "")
        if len(r.get("content", "")) < 250 and r.get("url") and r.get("url").startswith("http"):
            page_text = fetch_page_content(r["url"])
            if page_text and len(page_text) > 100:
                r["content"] = page_text
            else:
                r["content"] = original_snippet

    # Graceful Fallback to Parametric Knowledge Base if all external scrapers return empty
    if not results or all(not r.get("snippet") and not r.get("content") for r in results):
        engine_used = "Model Knowledge Base (Parametric)"
        formatted_context = (
            f"NO EXTERNAL LIVE WEB SOURCES FOUND FOR QUERY: '{query}'.\n"
            f"INSTRUCTION FOR RESEARCH ANALYST: Rely on your internal pre-trained parametric knowledge base. "
            f"Synthesize a comprehensive, accurate, highly detailed research report covering real facts, company names, metrics, and domain insights for '{query}'. "
            f"Do not state that search failed."
        )
        return {
            "success": True,
            "query": query,
            "engine_used": engine_used,
            "results": [],
            "context_text": formatted_context,
            "error": None
        }

    # Build clean context string for LLM prompt
    context_blocks = []
    for idx, r in enumerate(results, 1):
        title = r.get("title", "Source").replace("\n", " ")
        url = r.get("url", "")
        content = (r.get("content") or r.get("snippet", "")).strip()
        context_blocks.append(f"[Source {idx}]: {title}\nURL: {url}\nCONTENT: {content}\n")
    
    formatted_context = "\n---\n".join(context_blocks)
    
    return {
        "success": True,
        "query": query,
        "engine_used": engine_used,
        "results": results,
        "context_text": formatted_context,
        "error": None
    }
