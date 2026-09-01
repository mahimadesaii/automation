def normalize_search_query(topic: str) -> str:
    """Simplifies conversational queries like 'How was Pagani invented' to core search terms."""
    t_clean = topic.strip()
    simplified = re.sub(r'^(?:why\s+did|why\s+was|why|how\s+was|how\s+did|how|what\s+is|what\s+are)\s+', '', t_clean, flags=re.IGNORECASE)
    if re.search(r'\b(?:invented|created)\b', simplified, re.IGNORECASE):
        simplified = re.sub(r'\b(?:invented|created)\b', 'history founding', simplified, flags=re.IGNORECASE)
    return simplified.strip() or topic

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
    if not raw_html:
        return ""
    if BS4_AVAILABLE:
        soup = BeautifulSoup(raw_html, "html.parser")
        for element in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "form", "svg", "button", "iframe"]):
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
    url_lower = (url or "").lower()
    query_lower = (query or "").lower()
    if "duckduckgo.com/y.js" in url_lower or "/aclick?" in url_lower or "bing.com/aclick" in url_lower:
        return False
    if ("company" in query_lower or "work" in query_lower or "employer" in query_lower or "job" in query_lower):
        irrelevant = ["myntra.com", "flipkart.com", "amazon.com/dp", "meesho.com", "ajio.com", "ebay.com"]
        if any(d in url_lower for d in irrelevant):
            return False
    return True


def follow_google_news_redirect(gn_url: str, timeout: int = 6) -> str:
    """Follow Google News redirect to get the real article URL."""
    if not gn_url or "news.google.com" not in gn_url:
        return gn_url
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    try:
        res = requests.get(gn_url, headers=headers, timeout=timeout, allow_redirects=True)
        final_url = res.url
        if "news.google.com" not in final_url and final_url.startswith("http"):
            return final_url
    except Exception:
        pass
    return gn_url


def fetch_page_content(url: str, timeout: int = 8) -> str:
    """Fetch and clean page content. Follows Google News redirects automatically."""
    if not url or not url.startswith("http"):
        return ""

    real_url = url
    if "news.google.com" in url:
        real_url = follow_google_news_redirect(url, timeout=timeout)

    if "news.google.com" in real_url:
        return ""  # Redirect didn't resolve to a real article

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
    }
    try:
        res = requests.get(real_url, headers=headers, timeout=timeout)
        if res.status_code == 200 and "text/html" in res.headers.get("Content-Type", ""):
            cleaned = clean_html_content(res.text)
            if len(cleaned) > 150:
                return cleaned[:3500]
    except Exception:
        pass
    return ""


def search_tavily(query: str, api_key: str, max_results: int = 8) -> list:
    if not api_key:
        return []
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "include_answer": True,
        "include_raw_content": False,
        "max_results": max_results
    }
    try:
        response = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=12)
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
        print(f"[Tavily] Error: {e}")
    return []


def search_brave(query: str, api_key: str, max_results: int = 8) -> list:
    if not api_key:
        return []
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept": "application/json", "Accept-Encoding": "gzip", "X-Subscription-Token": api_key}
    try:
        response = requests.get(url, headers=headers, params={"q": query, "count": max_results}, timeout=10)
        if response.status_code == 200:
            results = []
            for item in response.json().get("web", {}).get("results", []):
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
        print(f"[Brave] Error: {e}")
    return []


def search_google_news_rss(query: str, max_results: int = 8) -> list:
    """Fetch Google News RSS results and follow redirects to get real article content."""
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
                title = title_elem.text.strip() if title_elem is not None else ""
                gn_link = link_elem.text.strip() if link_elem is not None else ""
                pub_date = pub_elem.text.strip() if pub_elem is not None else ""
                # Clean title (remove " - Source Name" suffix for cleaner display)
                clean_title = re.sub(r'\s*-\s*[^-]+$', '', title).strip() if title else title
                if title and is_relevant_source(gn_link, title, query):
                    results.append({
                        "title": clean_title or title,
                        "full_title": title,
                        "url": gn_link,
                        "pub_date": pub_date,
                        "snippet": "",    # Will be populated by content fetch
                        "content": "",    # Will be populated by content fetch
                        "source": "google_news"
                    })
    except Exception as e:
        print(f"[Google News RSS] Error: {e}")
    return results


def search_wikipedia(query: str, max_results: int = 5) -> list:
    results = []
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        wiki_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(query)}&format=json"
        res = requests.get(wiki_url, headers=headers, timeout=8)
        if res.status_code == 200:
            for item in res.json().get("query", {}).get("search", [])[:max_results]:
                title = item.get("title", "")
                clean_snippet = re.sub(r'<[^>]+>', '', item.get("snippet", "")).strip()
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
        print(f"[Wikipedia] Error: {e}")
    return results


def search_duckduckgo(query: str, max_results: int = 8) -> list:
    results = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
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
        print(f"[DuckDuckGo] Error: {e}")

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
            print(f"[DuckDuckGo IA] Error: {e}")
    return results


def deduplicate_sources(results: list, similarity_threshold: float = 0.78) -> list:
    """Drops sources over ~78% word-overlap similarity to an already included source."""
    deduped = []
    for r in results:
        content_r = (r.get("content") or r.get("snippet") or "").strip().lower()
        if not content_r:
            continue
        words_r = set(re.findall(r'\b\w{4,}\b', content_r))
        if not words_r:
            deduped.append(r)
            continue
        is_dup = False
        for d in deduped:
            content_d = (d.get("content") or d.get("snippet") or "").strip().lower()
            words_d = set(re.findall(r'\b\w{4,}\b', content_d))
            if not words_d:
                continue
            union = len(words_r | words_d)
            if union > 0 and (len(words_r & words_d) / union) > similarity_threshold:
                is_dup = True
                break
        if not is_dup:
            deduped.append(r)
    return deduped


def execute_live_research(query: str, tavily_key: str = "", brave_key: str = "", max_results: int = 12) -> dict:
    tavily_key = tavily_key or os.environ.get("TAVILY_API_KEY", "").strip()
    brave_key = brave_key or os.environ.get("BRAVE_API_KEY", "").strip()

    results = []
    engine_used = "None"

    # Tier 1: Tavily (best quality)
    if tavily_key:
        results = search_tavily(query, tavily_key, max_results=max_results)
        if results:
            engine_used = "Tavily Search API"

    # Tier 2: Brave
    if not results and brave_key:
        results = search_brave(query, brave_key, max_results=max_results)
        if results:
            engine_used = "Brave Search API"

    # Tier 3: Google News RSS
    if not results or sum(len(r.get("content","") or r.get("snippet","")) for r in results) < 2000:
        gn_results = search_google_news_rss(query, max_results=max_results)
        if gn_results:
            results.extend(gn_results)
            if engine_used == "None":
                engine_used = "Google News Live Search"

    # Tier 4: Wikipedia
    if not results or sum(len(r.get("content","") or r.get("snippet","")) for r in results) < 2000:
        wiki_res = search_wikipedia(query, max_results=5)
        if wiki_res:
            results.extend(wiki_res)
            if engine_used == "None":
                engine_used = "Wikipedia Intelligence"

    # Tier 5: DuckDuckGo
    if not results or sum(len(r.get("content","") or r.get("snippet","")) for r in results) < 2000:
        ddg_res = search_duckduckgo(query, max_results=max_results)
        if ddg_res:
            results.extend(ddg_res)
            if engine_used == "None":
                engine_used = "DuckDuckGo Web Search"

    # === PARALLEL CONTENT ENRICHMENT (Fast Multi-Threaded Scraping) ===
    thin_items = [r for r in results[:8] if len(r.get("content", "") or r.get("snippet", "")) < 400 and r.get("url", "").startswith("http")]
    def _enrich_single(r):
        url = r.get("url", "")
        page_text = fetch_page_content(url, timeout=3.5)
        if page_text and len(page_text) > 200:
            r["content"] = page_text
            r["snippet"] = page_text[:300]

    if thin_items:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(_enrich_single, r) for r in thin_items]
            concurrent.futures.wait(futures, timeout=4.5)

    # === SOURCE DEDUPLICATION (Fix #7) ===
    results = deduplicate_sources(results, similarity_threshold=0.78)

    # Query Normalization retry if initial search was conversational and returned thin context (<2000 chars)
    total_content_chars = sum(len(r.get("content", "") or r.get("snippet", "")) for r in results)
    if total_content_chars < 2000:
        norm_q = normalize_search_query(query)
        if norm_q.lower() != query.lower():
            print(f"[Search Retrieval] Retrying retrieval with normalized query terms: '{norm_q}'")
            extra_gn = search_google_news_rss(norm_q, max_results=8)
            extra_wiki = search_wikipedia(norm_q, max_results=4)
            extra_ddg = search_duckduckgo(norm_q, max_results=8)
            results.extend(extra_gn + extra_wiki + extra_ddg)
            results = deduplicate_sources(results, similarity_threshold=0.78)

    # === PARAMETRIC KNOWLEDGE FALLBACK (Fix #1) ===
    total_content_chars = sum(len(r.get("content", "") or r.get("snippet", "")) for r in results)
    use_parametric = (not results) or (total_content_chars < 2000)

    if use_parametric:
        engine_used = "Model Parametric Knowledge Base"
        formatted_context = (
            f"> [!NOTE]\n"
            f"> **General Knowledge Mode**: External live web sources returned limited content ({total_content_chars} chars) for '{query}'. "
            f"This section is synthesized based on general AI model knowledge, not live-verified web sources.\n\n"
            f"INSTRUCTION: You are an expert Senior Research Analyst. Write a detailed, accurate, well-structured "
            f"research section on '{query}'. Include real entity names, specific metrics, ratings, and comparisons.\n\n"
            f"NEWS HEADLINES RETRIEVED:\n"
            + "\n".join(f"- {r.get('full_title') or r.get('title', '')} ({r.get('pub_date', '')})" for r in results[:5] if r.get("title"))
        )
        return {
            "success": True,
            "query": query,
            "engine_used": engine_used,
            "results": results,
            "context_text": formatted_context,
            "error": None
        }

    # === BUILD RICH CONTEXT STRING ===
    context_blocks = []
    for idx, r in enumerate(results[:10], 1):
        title = (r.get("title") or "Source").replace("\n", " ")
        url = r.get("url", "")
        content = (r.get("content") or r.get("snippet", "")).strip()
        pub_date = r.get("pub_date", "")
        date_str = f" (Published: {pub_date})" if pub_date else ""
        context_blocks.append(f"[Source {idx}]: {title}{date_str}\nURL: {url}\nCONTENT: {content}\n")

    formatted_context = "\n---\n".join(context_blocks)

    return {
        "success": True,
        "query": query,
        "engine_used": engine_used,
        "results": results,
        "context_text": formatted_context,
        "error": None
    }


