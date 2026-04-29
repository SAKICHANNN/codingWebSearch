import asyncio
import datetime
import hashlib
import json
import os
import re
import time
from difflib import SequenceMatcher
from urllib.parse import quote_plus, urlparse

import httpx
from bs4 import BeautifulSoup
from ddgs import DDGS
from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations, Annotations as ResourceAnnotations

# ===========================================================================
# MCP Server
# ===========================================================================
mcp = FastMCP("codingWebSearch")

class SearchError(Exception):
    """Raised when a search operation fails.
    FastMCP converts this to an isError=true tool result.

    Subclasses carry a `recovery` hint for actionable guidance."""
    def __init__(self, message: str, recovery: str = ""):
        super().__init__(message)
        self.recovery = recovery

    def __str__(self):
        msg = super().__str__()
        if self.recovery:
            msg += f"\n💡 {self.recovery}"
        return msg


_RATE_LIMIT_TRACKER: dict[str, list[float]] = {}


def _check_rate_limit(engine: str, max_per_minute: int = 10) -> str | None:
    """Track per-engine rate limits. Returns error message if rate limited."""
    now = time.time()
    window = 60
    calls = _RATE_LIMIT_TRACKER.get(engine, [])
    calls = [t for t in calls if now - t < window]
    _RATE_LIMIT_TRACKER[engine] = calls
    if len(calls) >= max_per_minute:
        wait = int(window - (now - calls[0]))
        return (
            f"Rate limit: {engine} engine ({len(calls)}/{max_per_minute} per minute). "
            f"Wait {wait}s or try engine='auto'."
        )
    calls.append(now)
    return None


# Shared annotations — all tools in this server are read-only
_READONLY_TOOL = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)

# ===========================================================================
# Constants
# ===========================================================================
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

SEARCH_TIMEOUT = 20
FETCH_TIMEOUT = 30
SEARCH_OVERALL_TIMEOUT = 60  # hard cap for multi-engine search
MAX_RESULTS = 50
DEFAULT_MAX_LENGTH = 12000
CACHE_TTL = 300
MAX_RETRIES = 2

STRIP_TAGS = [
    "script", "style", "nav", "footer", "header", "aside",
    "noscript", "svg", "form", "iframe", "img", "video",
    "audio", "canvas", "embed", "object", "select",
    "button", "input", "textarea",
]

# Domain lists with authority weights (higher = more trusted)
_CODE_DOMAINS = [
    "stackoverflow.com",
    "github.com",
    "stackexchange.com",
    "medium.com",
    "dev.to",
    "reddit.com/r/programming",
    "news.ycombinator.com",
    "serverfault.com",
    "superuser.com",
]

_DOCS_DOMAINS = [
    "readthedocs.io", "docs.python.org", "developer.mozilla.org",
    "pypi.org", "npmjs.com", "crates.io", "pkg.go.dev",
    "learn.microsoft.com", "man7.org", "nodejs.org",
]

_PAPER_DOMAINS = [
    "arxiv.org", "dl.acm.org", "scholar.google.com",
    "semanticscholar.org", "paperswithcode.com", "openreview.net",
    "ieeexplore.ieee.org", "usenix.org",
]

_GITHUB_DOMAINS = [
    "github.com", "gitlab.com", "bitbucket.org",
    "gitee.com", "sourceforge.net", "codeberg.org",
]

# Source authority scoring — used to re-rank results for coding agents
_AUTHORITY_SCORES: dict[str, float] = {
    # Official docs and specs
    "docs.python.org": 1.0, "developer.mozilla.org": 1.0, "learn.microsoft.com": 1.0,
    "nodejs.org": 0.95, "pkg.go.dev": 0.95, "doc.rust-lang.org": 1.0,
    "man7.org": 0.9, "readthedocs.io": 0.85, "kubernetes.io": 0.95,
    # Code platforms
    "github.com": 0.9, "gitlab.com": 0.8, "bitbucket.org": 0.75,
    # Q&A
    "stackoverflow.com": 0.85, "stackexchange.com": 0.8, "serverfault.com": 0.75,
    "superuser.com": 0.7,
    # Academic
    "arxiv.org": 0.9, "dl.acm.org": 0.85, "ieeexplore.ieee.org": 0.85,
    "semanticscholar.org": 0.8, "paperswithcode.com": 0.8,
    # Package registries
    "pypi.org": 0.8, "npmjs.com": 0.8, "crates.io": 0.8,
    # Community
    "dev.to": 0.6, "medium.com": 0.55, "reddit.com": 0.5,
    # News
    "news.ycombinator.com": 0.7,
}

ENV_BING_KEY = "BING_SEARCH_API_KEY"
ENV_GOOGLE_KEY = "GOOGLE_API_KEY"
ENV_GOOGLE_CX = "GOOGLE_CSE_ID"
ENV_BRAVE_KEY = "BRAVE_SEARCH_API_KEY"
ENV_SEARXNG_URL = "SEARXNG_URL"

# Search session context — enables multi-turn search refinement
_search_sessions: dict[str, dict] = {}

def _session_add(session_id: str, query: str, results: list[dict]) -> None:
    # Prune old sessions (>50 sessions or >1h idle)
    if len(_search_sessions) > 50:
        stale_sessions = [
            sid for sid, s in _search_sessions.items()
            if not s["history"] or time.time() - s["history"][-1]["time"] > 3600
        ]
        for sid in stale_sessions:
            del _search_sessions[sid]
        # Fallback: if still over limit, remove oldest by last activity
        if len(_search_sessions) > 50:
            oldest = sorted(
                [(sid, s["history"][-1]["time"] if s["history"] else 0) for sid, s in _search_sessions.items()],
                key=lambda x: x[1],
            )
            for sid, _ in oldest[:len(_search_sessions) - 50]:
                del _search_sessions[sid]

    if session_id not in _search_sessions:
        _search_sessions[session_id] = {"history": [], "context": {}}
    _search_sessions[session_id]["history"].append({
        "query": query, "count": len(results),
        "time": time.time(),
        "top_urls": [r["href"] for r in results[:3]],
    })
    # Keep last 20 queries per session
    if len(_search_sessions[session_id]["history"]) > 20:
        _search_sessions[session_id]["history"] = _search_sessions[session_id]["history"][-20:]

def _session_context(session_id: str) -> str:
    """Build a context summary for the coding agent."""
    s = _search_sessions.get(session_id)
    if not s or not s["history"]:
        return "No search history for this session."
    lines = [f"## Search Session: {session_id}\n"]
    lines.append(f"_{len(s['history'])} queries so far_\n")
    for i, h in enumerate(s["history"][-10:], 1):
        lines.append(f"{i}. `{h['query'][:100]}` → {h['count']} results")
    return "\n".join(lines)

# ===========================================================================
# Cache
# ===========================================================================
_search_cache: dict[str, tuple[float, list[dict]]] = {}

def _cache_key(prefix: str, query: str, engine: str, **kw) -> str:
    raw = f"{prefix}|{query}|{engine}|{sorted(kw.items())}"
    return hashlib.md5(raw.encode()).hexdigest()

def _cache_get(key: str) -> list[dict] | None:
    entry = _search_cache.get(key)
    if entry and time.time() - entry[0] < CACHE_TTL:
        return entry[1]
    if entry:
        del _search_cache[key]
    return None

def _cache_set(key: str, results: list[dict]) -> None:
    _search_cache[key] = (time.time(), results)
    if len(_search_cache) > 200:
        stale = [k for k, v in _search_cache.items() if time.time() - v[0] >= CACHE_TTL]
        for k in stale:
            del _search_cache[k]

# ===========================================================================
# Helpers
# ===========================================================================

def _retry_sleep(attempt: int) -> float:
    return min(2 ** attempt, 8.0)

def _title_similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

# Pre-sorted by domain length (longest first) so subdomain matches are precise
_SORTED_AUTHORITY = sorted(_AUTHORITY_SCORES.items(), key=lambda x: -len(x[0]))


def _source_authority(url: str) -> float:
    """Score a URL's authority for coding-related searches. Higher = more trusted."""
    try:
        host = urlparse(url).netloc.lower()
        host = re.sub(r'^www\.', '', host)
        for domain, score in _SORTED_AUTHORITY:
            if domain in host:
                return score
    except Exception:
        pass
    return 0.4  # default for unknown sources

def _source_freshness(body: str, title: str = "") -> float:
    """Estimate content freshness from snippet text. Returns a boost factor (0-0.2).
    More recent = higher score. Detects year mentions and relative time indicators."""
    score = 0.0
    text = (body + " " + title).lower()

    # Explicit year mentions
    current_year = datetime.datetime.now().year
    years = re.findall(r'\b(20\d{2})\b', text)
    if years:
        latest = max(int(y) for y in years)
        if latest >= current_year:
            score += 0.2
        elif latest >= current_year - 1:
            score += 0.15
        elif latest >= current_year - 2:
            score += 0.1
        elif latest >= current_year - 3:
            score += 0.05

    # Relative time indicators
    if re.search(r'\b(today|just now|minutes ago|hours ago)\b', text):
        score += 0.2
    elif re.search(r'\b(yesterday|this week|days ago)\b', text):
        score += 0.15
    elif re.search(r'\b(this month|weeks ago|recently)\b', text):
        score += 0.1
    elif re.search(r'\b(this year|months ago)\b', text):
        score += 0.05

    return min(score, 0.2)


def _relevance_score(result: dict, query: str) -> float:
    """Score how relevant a result is to the query. Returns a boost factor (0-0.3).
    Measures keyword overlap between query terms and result title + snippet."""
    if not query:
        return 0.0
    query_terms = set(re.findall(r'[a-zA-Z0-9]+', query.lower()))
    if not query_terms:
        return 0.0
    result_text = (result.get("title", "") + " " + result.get("body", "")).lower()
    result_terms = set(re.findall(r'[a-zA-Z0-9]+', result_text))
    overlap = len(query_terms & result_terms) / len(query_terms)
    return round(overlap * 0.3, 2)


def _sort_by_authority(results: list[dict], query: str = "") -> list[dict]:
    """Sort results: authority + freshness + relevance, then original position."""
    scored = [
        (
            r,
            _source_authority(r.get("href", ""))
            + _source_freshness(r.get("body", ""), r.get("title", ""))
            + _relevance_score(r, query),
        )
        for r in results
    ]
    scored.sort(key=lambda x: -x[1])
    return [r for r, _ in scored]

def _optimize_query(query: str, category: str = "code") -> str:
    """
    Optimize a search query for technical precision.
    - Adds relevant technical operators for code searches
    - Strips noise words for error searches
    - Preserves exact phrases in quotes
    """
    q = query.strip()

    if category == "error":
        # Strip timestamps, hex addresses, file paths with line numbers
        q = re.sub(r'\b0x[0-9a-fA-F]+\b', '', q)
        q = re.sub(r'[\/\w]+\.(py|js|ts|go|rs|java|cpp|c|h):\d+', '', q)
        q = re.sub(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}', '', q)
        q = re.sub(r'at\s+\w+\(.*?\)', '', q)
        q = re.sub(r'\s{2,}', ' ', q).strip()

    if category == "api":
        # Ensure the query has clear API reference intent
        if not any(kw in q.lower() for kw in ["api", "function", "method", "class", "param", "return", "signature", "doc"]):
            q = f"{q} API reference documentation"

    if category == "compare":
        # Ensure comparison format — use word-boundary regex to catch edge positions
        if " vs " not in q.lower() and " versus " not in q.lower():
            q = re.sub(r'\bcompare\b', 'vs', q, flags=re.IGNORECASE)
            q = re.sub(r'\bcomparison\b', 'vs', q, flags=re.IGNORECASE)

    return q

def _build_site_query(query: str, domains: list[str]) -> str:
    sites = " OR ".join(f"site:{d}" for d in domains[:5])
    return f"({sites}) {query}"

def _build_result(index: int, result: dict) -> str:
    title = result.get("title", "No title")
    if len(title) > 150:
        title = title[:147] + "..."
    href = result.get("href", "")
    body = result.get("body", "")
    engine = result.get("engine", "")
    authority = result.get("_authority", 0)
    tags = []
    if engine:
        tags.append(engine)
    if authority >= 0.9:
        tags.append("official")
    elif authority >= 0.8:
        tags.append("trusted")
    tag_str = f" [{']/['.join(tags)}]" if tags else ""
    return f"{index}. **{title}**{tag_str}\n   URL: {href}\n   {body or '(no snippet)'}"

def _format_results(
    query: str, results: list[dict], label: str = "Search",
    elapsed_ms: float = 0, total_found: int = 0, show_authority: bool = False,
) -> str:
    if not results:
        return f"No results found for '{query}'."

    if show_authority:
        results = _sort_by_authority(results, query)

    header = f"## {label}: {query}"
    meta = f"_{len(results)} results"
    if total_found:
        meta += f" of ~{total_found}"
    if elapsed_ms:
        meta += f" in {elapsed_ms:.0f}ms"
    meta += "_"
    lines = [header, meta, ""]
    for i, r in enumerate(results, 1):
        lines.append(_build_result(i, r))
        lines.append("")
    return "\n".join(lines)

def _format_compact(query: str, results: list[dict], label: str, elapsed_ms: float = 0) -> str:
    """Compact output: one line per result, URL + title only."""
    header = f"## {label}: {query}"
    meta = f"_{len(results)} results"
    if elapsed_ms:
        meta += f" in {elapsed_ms:.0f}ms"
    meta += "_"
    lines = [header, meta, ""]
    for i, r in enumerate(results, 1):
        title = r.get("title", "No title")[:120]
        href = r.get("href", "")
        engine = r.get("engine", "")
        tag = f" [{engine}]" if engine else ""
        lines.append(f"{i}. [{title}]({href}){tag}")
    return "\n".join(lines)


def _format_links(query: str, results: list[dict], label: str, elapsed_ms: float = 0) -> str:
    """URL-only output: just the links."""
    header = f"## {label}: {query}"
    meta = f"_{len(results)} results"
    if elapsed_ms:
        meta += f" in {elapsed_ms:.0f}ms"
    meta += "_"
    lines = [header, meta, ""]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.get('href', '')}")
    return "\n".join(lines)


def _validate_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except Exception:
        return f"Invalid URL: {url}."
    if parsed.scheme not in ("http", "https"):
        return f"Unsupported protocol '{parsed.scheme}'."
    if not parsed.netloc:
        return "URL has no hostname."
    return None

async def _extract_text(html: str) -> str:
    """Extract readable text from HTML. CPU-bound parsing runs in thread pool."""
    def _parse():
        soup = BeautifulSoup(html, "html.parser")
        for pre in soup.find_all("pre"):
            code = pre.find("code")
            lang = ""
            if code:
                cls = code.get("class", [])
                for c in cls:
                    if c.startswith("language-") or c.startswith("lang-"):
                        lang = c.split("-", 1)[1]
                        break
                content = code.get_text()
            else:
                content = pre.get_text()
            lang_marker = f" {lang}" if lang else ""
            pre.replace_with(f"\n```{lang_marker}\n{content.strip()}\n```\n")
        for code_tag in soup.find_all("code"):
            code_tag.replace_with(f"`{code_tag.get_text()}`")
        for tag in STRIP_TAGS:
            for el in soup.find_all(tag):
                el.decompose()
        main = soup.find("article") or soup.find("main") or soup.find("body")
        if not main:
            return ""
        text = main.get_text(separator="\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return "\n".join(line.strip() for line in text.splitlines()).strip()
    return await asyncio.to_thread(_parse)

async def _fetch(url: str, timeout: int, headers: dict | None = None) -> tuple[str | None, str | None]:
    err = _validate_url(url)
    if err:
        return None, err
    default_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    }
    if headers:
        default_headers.update(headers)
    for attempt in range(MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout), follow_redirects=True,
                headers=default_headers,
            ) as client:
                resp = await client.get(url)
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError):
            if attempt < MAX_RETRIES:
                await asyncio.sleep(_retry_sleep(attempt))
                continue
            return None, f"Network error after {MAX_RETRIES+1} attempts"
        except Exception as exc:
            return None, f"Fetch failed: {exc}"
        if resp.status_code == 429 and attempt < MAX_RETRIES:
            await asyncio.sleep(_retry_sleep(attempt) + 1)
            continue
        if resp.status_code >= 400:
            return None, f"HTTP {resp.status_code}"
        return resp.text, None
    return None, "Max retries exceeded"

def _is_duplicate(result: dict, seen: list[dict], title_threshold: float = 0.85) -> bool:
    url = result.get("href", "")
    title = result.get("title", "")
    for s in seen:
        if s["href"] == url:
            return True
        if _title_similar(s["title"], title) >= title_threshold:
            return True
    return False

# ===========================================================================
# Search Engines
# ===========================================================================

async def _search_ddgs(query: str, region="wt-wt", safesearch="off",
                       timelimit: str | None = None, max_results=10) -> list[dict]:
    def _sync_search():
        with DDGS(timeout=SEARCH_TIMEOUT) as ddgs:
            return list(ddgs.text(
                query, region=region, safesearch=safesearch,
                timelimit=timelimit, max_results=max_results,
            ))
    raw = await asyncio.to_thread(_sync_search)
    return [
        {"title": r.get("title", ""), "href": r.get("href", ""),
         "body": r.get("body", ""), "engine": "ddgs",
         "_authority": _source_authority(r.get("href", ""))}
        for r in raw
    ]

async def _search_brave_api(query: str, max_results: int) -> list[dict]:
    api_key = os.environ.get(ENV_BRAVE_KEY)
    if not api_key:
        raise DDGSException("Brave requires BRAVE_SEARCH_API_KEY. Get free key at brave.com/search/api/")
    url = f"https://api.search.brave.com/res/v1/web/search?q={quote_plus(query)}&count={min(max_results, 20)}"
    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT) as c:
        resp = await c.get(url, headers={
            "Accept": "application/json", "Accept-Encoding": "gzip",
            "X-Subscription-Token": api_key,
        })
        if resp.status_code != 200:
            raise DDGSException(f"Brave HTTP {resp.status_code}")
        data = resp.json()
    web = data.get("web") or data.get("webPages") or {}
    return [
        {"title": i.get("title", ""), "href": i.get("url", ""),
         "body": i.get("description", "") or i.get("snippet", ""),
         "engine": "brave", "_authority": _source_authority(i.get("url", ""))}
        for i in (web.get("results") or web.get("value") or [])[:max_results]
    ]

async def _search_google_api(query: str, max_results: int) -> list[dict]:
    api_key, cx = os.environ.get(ENV_GOOGLE_KEY), os.environ.get(ENV_GOOGLE_CX)
    if not api_key or not cx:
        raise DDGSException("Google needs GOOGLE_API_KEY + GOOGLE_CSE_ID.")
    url = (f"https://www.googleapis.com/customsearch/v1"
           f"?key={api_key}&cx={cx}&q={quote_plus(query)}&num={min(max_results, 10)}")
    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT) as c:
        resp = await c.get(url)
        if resp.status_code != 200:
            raise DDGSException(f"Google HTTP {resp.status_code}")
        data = resp.json()
    return [
        {"title": i.get("title", ""), "href": i.get("link", ""),
         "body": i.get("snippet", ""), "engine": "google",
         "_authority": _source_authority(i.get("link", ""))}
        for i in data.get("items", [])[:max_results]
    ]

async def _search_bing_api(query: str, max_results: int) -> list[dict]:
    api_key = os.environ.get(ENV_BING_KEY)
    if not api_key:
        raise DDGSException("Bing needs BING_SEARCH_API_KEY.")
    url = (f"https://api.bing.microsoft.com/v7.0/search"
           f"?q={quote_plus(query)}&count={min(max_results, 15)}&mkt=en-US")
    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT) as c:
        resp = await c.get(url, headers={"Ocp-Apim-Subscription-Key": api_key})
        if resp.status_code != 200:
            raise DDGSException(f"Bing HTTP {resp.status_code}")
        data = resp.json()
    return [
        {"title": i.get("name", ""), "href": i.get("url", ""),
         "body": i.get("snippet", ""), "engine": "bing",
         "_authority": _source_authority(i.get("url", ""))}
        for i in data.get("webPages", {}).get("value", [])[:max_results]
    ]

async def _search_baidu(query: str, max_results: int) -> list[dict]:
    url = f"https://www.baidu.com/s?wd={quote_plus(query)}&rn={min(max_results, 20)}"
    html, err = await _fetch(url, SEARCH_TIMEOUT)
    if err:
        raise DDGSException(f"Baidu: {err}")

    def _parse_baidu():
        soup = BeautifulSoup(html, "html.parser")
        results = []
        for container in soup.select("div.result, div.c-container, div.c-result"):
            if len(results) >= max_results:
                break
            title_el = container.find("h3")
            link_el = container.find("a", href=True)
            if not title_el or not link_el:
                continue
            # Try to extract real URL from Baidu's redirect wrapper
            real_href = link_el.get("data-url") or link_el.get("mu") or link_el["href"]
            snippet_el = container.select_one(
                "span.content-right_8Zs40, div.c-abstract, span.c-font-normal, div.c-span-last"
            )
            results.append({
                "title": title_el.get_text(strip=True),
                "href": real_href,
                "body": (snippet_el.get_text(strip=True) if snippet_el else "")[:500],
                "engine": "baidu",
                "_authority": _source_authority(real_href),
            })
        return results

    results = await asyncio.to_thread(_parse_baidu)
    if not results:
        raise DDGSException("Baidu returned no parseable results.")
    return results

async def _search_yahoo(query: str, max_results: int) -> list[dict]:
    results = await _search_ddgs(query, max_results=max_results)
    for r in results:
        r["engine"] = "yahoo"
    return results


async def _search_searxng(query: str, max_results: int) -> list[dict]:
    """Search via a self-hosted SearXNG instance. Privacy-respecting metasearch."""
    searxng_url = os.environ.get(ENV_SEARXNG_URL, "").rstrip("/")
    if not searxng_url:
        raise DDGSException(
            "SearXNG requires SEARXNG_URL env var. Set it to your instance URL.\n"
            "Example: export SEARXNG_URL=http://localhost:8080"
        )
    url = f"{searxng_url}/search?format=json&q={quote_plus(query)}&categories=general&pageno=1"
    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT) as c:
        resp = await c.get(url, headers={"User-Agent": USER_AGENT})
        if resp.status_code != 200:
            raise DDGSException(f"SearXNG HTTP {resp.status_code} — is the instance running?")
        data = resp.json()
    results = []
    for r in (data.get("results") or [])[:max_results]:
        results.append({
            "title": r.get("title", ""),
            "href": r.get("url", ""),
            "body": (r.get("content", "") or r.get("snippet", ""))[:500],
            "engine": "searxng",
            "_authority": _source_authority(r.get("url", "")),
        })
    if not results:
        raise DDGSException("SearXNG returned no results.")
    return results


_ENGINE_INFO = {
    "auto":    {"source": "DuckDuckGo", "key": None},
    "brave":   {"source": "Brave Search API", "key": ENV_BRAVE_KEY},
    "google":  {"source": "Google CSE API", "key": f"{ENV_GOOGLE_KEY}+{ENV_GOOGLE_CX}"},
    "bing":    {"source": "Bing API v7", "key": ENV_BING_KEY},
    "baidu":   {"source": "Baidu scraping", "key": None},
    "yahoo":   {"source": "Yahoo via DDGS", "key": None},
    "searxng": {"source": "SearXNG (self-hosted)", "key": ENV_SEARXNG_URL},
}
_ENGINE_PRIORITY = ["auto", "brave", "google", "bing", "yahoo", "baidu", "searxng"]

_NO_KEY_MSGS = {
    "brave": "Engine 'brave' needs BRAVE_SEARCH_API_KEY. Free key: https://brave.com/search/api/",
    "google": "Engine 'google' needs GOOGLE_API_KEY + GOOGLE_CSE_ID.",
    "bing": "Engine 'bing' needs BING_SEARCH_API_KEY. Note: 'auto' already includes Bing.",
    "searxng": "Engine 'searxng' needs SEARXNG_URL. Set to your instance URL (e.g. http://localhost:8080).",
}

async def _search_with_engine(query, engine, max_results=10, region="wt-wt",
                               safesearch="off", timelimit=None) -> list[dict]:
    if engine == "auto":
        return await _search_ddgs(query, region, safesearch, timelimit, max_results)
    if engine == "brave":
        return await _search_brave_api(query, max_results)
    if engine == "google":
        return await _search_google_api(query, max_results)
    if engine == "bing":
        return await _search_bing_api(query, max_results)
    if engine == "baidu":
        return await _search_baidu(query, max_results)
    if engine == "yahoo":
        return await _search_yahoo(query, max_results)
    if engine == "searxng":
        return await _search_searxng(query, max_results)
    raise DDGSException(f"Unknown engine '{engine}'. Options: {', '.join(_ENGINE_INFO.keys())}")

def _resolve_engines(requested: str) -> list[str]:
    env_override = os.environ.get("SEARCH_ENGINES", "").strip()
    if env_override:
        engines = [e for e in re.split(r"[,\s]+", env_override.lower()) if e in _ENGINE_INFO]
        if engines:
            return engines
    if requested == "all":
        return _ENGINE_PRIORITY.copy()
    requested_lower = requested.lower()
    if requested_lower in _ENGINE_INFO:
        return [requested_lower]
    if requested:
        # Unknown engine — log and fall back to auto
        pass
    return ["auto"]

# ===========================================================================
# Unified search core
# ===========================================================================

async def _do_search(
    query: str, label: str, max_results: int, engine: str,
    region: str, safesearch: str, timelimit: str | None,
    scoped_domains: list[str] | None = None,
    query_category: str = "code",
    sort_by_authority: bool = False,
    session_id: str | None = None,
    output_format: str = "full",
) -> str:
    if not query or not query.strip():
        raise SearchError("Search query is empty. Please provide a search query.")

    t_start = time.time()
    max_results = max(1, min(max_results, MAX_RESULTS))

    # Optimize query for coding context
    search_query = _optimize_query(query, query_category)

    # Domain scoping
    if scoped_domains:
        if engine == "auto":
            search_query = _build_site_query(search_query, scoped_domains)
        else:
            sites = " OR ".join(f"site:{d}" for d in scoped_domains[:3])
            search_query = f"({sites}) {search_query}"

    # Cache check — key includes max_results so different sizes don't share stale entries
    cache_key = _cache_key(label, search_query, engine, region=region, max_results=max_results, fmt=output_format)
    cached = _cache_get(cache_key)
    if cached:
        if output_format == "compact":
            return _format_compact(query, cached, label)
        elif output_format == "links":
            return _format_links(query, cached, label)
        return _format_results(query, cached, label, show_authority=sort_by_authority)

    engines_to_try = _resolve_engines(engine)

    # API key check for single engine
    if len(engines_to_try) == 1:
        eng = engines_to_try[0]
        required_key = _ENGINE_INFO.get(eng, {}).get("key")
        if required_key and not os.environ.get(required_key.split("+")[0]):
            if isinstance(required_key, str) and "+" in required_key:
                keys = required_key.split("+")
                if not all(os.environ.get(k) for k in keys):
                    return _NO_KEY_MSGS.get(eng, f"'{eng}' needs API keys. Use 'auto' instead. (Engine '{eng}' falls back to auto)")
            elif not os.environ.get(required_key):
                return _NO_KEY_MSGS.get(eng, f"'{eng}' needs an API key. Use 'auto' instead. (Engine '{eng}' falls back to auto)")

    # Try engines in parallel (with overall time budget)
    last_errors = []
    all_results = []
    seen = []
    tasks = []

    async def _try_one_engine(eng: str) -> tuple[str, list[dict], str | None]:
        """Search one engine with retries. Returns (engine_name, results, error)."""
        info = _ENGINE_INFO.get(eng, {})
        required_key = info.get("key")
        if required_key:
            if isinstance(required_key, str) and "+" in required_key:
                if not all(os.environ.get(k) for k in required_key.split("+")):
                    return (eng, [], f"[{eng}] keys not configured, skip")
            elif not os.environ.get(required_key or ""):
                return (eng, [], f"[{eng}] key not set, skip")

        # Rate limit check for public APIs
        if eng in ("brave", "google", "bing"):
            limit_err = _check_rate_limit(eng)
            if limit_err:
                return (eng, [], f"[{eng}] {limit_err}")

        for attempt in range(MAX_RETRIES + 1):
            try:
                results = await _search_with_engine(
                    search_query, eng, max_results, region, safesearch, timelimit
                )
                return (eng, results, None)
            except RatelimitException:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(_retry_sleep(attempt) + 1)
                    continue
                return (eng, [], f"[{eng}] rate limited")
            except TimeoutException:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(_retry_sleep(attempt))
                    continue
                return (eng, [], f"[{eng}] timeout")
            except DDGSException as exc:
                return (eng, [], f"[{eng}] {exc}")
            except Exception as exc:
                return (eng, [], f"[{eng}] {exc}")
        return (eng, [], f"[{eng}] max retries exceeded")

    # Gather valid engine search tasks
    for eng in engines_to_try:
        tasks.append(_try_one_engine(eng))

    # Run all engines concurrently with overall timeout
    done, pending = await asyncio.wait(
        tasks,
        timeout=SEARCH_OVERALL_TIMEOUT,
        return_when=asyncio.ALL_COMPLETED,
    )
    # Cancel any tasks that didn't finish in time
    for p in pending:
        p.cancel()

    # Collect and deduplicate results
    for task in done:
        eng, results, error = task.result()
        if error:
            last_errors.append(error)
            continue
        for r in results:
            if not _is_duplicate(r, seen):
                seen.append(r)
                all_results.append(r)
                if len(all_results) >= max_results:
                    break
        if len(all_results) >= max_results:
            break

    if all_results:
        _cache_set(cache_key, all_results[:max_results])  # cache_key already scopes to this max_results
        # Record in session
        if session_id:
            _session_add(session_id, query, all_results[:max_results])

    elapsed = (time.time() - t_start) * 1000

    if all_results:
        used = engines_to_try[0] if len(engines_to_try) == 1 else "multi"
        engine_label = f"{label} [{used}]"

        final = all_results[:max_results]

        if output_format == "compact":
            # Snippet-less, URL-focused output
            return _format_compact(query, final, engine_label, elapsed)
        elif output_format == "links":
            # Just URLs
            return _format_links(query, final, engine_label, elapsed)

        return _format_results(query, final, engine_label, elapsed, show_authority=sort_by_authority)

    if last_errors:
        raise SearchError(
            "All search engines failed.\n" + "\n".join(last_errors[:3]) +
            "\nTry engine='auto' or check API key configuration with list_engines()."
        )

    return f"No results found for '{query}'."

# ===========================================================================
# Core Tools
# ===========================================================================

@mcp.tool(annotations=_READONLY_TOOL)
async def web_search(
    query: str, engine: str = "auto", region: str = "wt-wt",
    safesearch: str = "off", timelimit: str | None = None, max_results: int = 10,
    output_format: str = "full",
    session_id: str = "",
) -> str:
    """General web search. Best for broad technical research, technology overviews,
    and finding multiple perspectives on a topic.

    Args:
        query: Search query. Use English for best results. Be specific.
        engine: "auto"(free), "brave"(free key), "google", "bing", "baidu", "yahoo", "all".
        region: Region for DuckDuckGo: wt-wt, us-en, cn-zh.
        safesearch: "on", "moderate", "off".
        timelimit: "d"=day, "w"=week, "m"=month, "y"=year, or None.
        max_results: 1-50.
        output_format: "full" (title+URL+snippet), "compact" (title+URL), "links" (URL only).
        session_id: Track search history for multi-turn research (e.g. "debug-session-1").
    """
    return await _do_search(
        query=query, label="Web", max_results=max_results, engine=engine,
        region=region, safesearch=safesearch, timelimit=timelimit,
        query_category="code", output_format=output_format,
        session_id=session_id or None,
    )

@mcp.tool(annotations=_READONLY_TOOL)
async def search_code(
    query: str, engine: str = "auto", max_results: int = 10,
    timelimit: str | None = None,
) -> str:
    """Search programming Q&A, tutorials, and code examples.
    Scoped to Stack Overflow, GitHub, Reddit, dev.to, Medium, Hacker News.

    Best for: debugging help, code patterns, "how do I...", library usage examples.

    Args:
        query: Error message, programming question, or technique name.
        engine: "auto", "brave", "google", "bing", "baidu", "yahoo", "all".
        max_results: 1-50.
        timelimit: "d", "w", "m", "y", or None.
    """
    return await _do_search(
        query=query, label="Code & Q&A", max_results=max_results, engine=engine,
        region="wt-wt", safesearch="off", timelimit=timelimit,
        scoped_domains=_CODE_DOMAINS, query_category="code",
    )

@mcp.tool(annotations=_READONLY_TOOL)
async def search_docs(
    query: str, engine: str = "auto", max_results: int = 10,
) -> str:
    """Search official documentation and API references.
    Scoped to MDN, readthedocs, docs.python.org, learn.microsoft.com, PyPI, npm, etc.

    Best for: API method signatures, config options, language specs, migration guides.

    Args:
        query: Library name, method name, config key, or technology.
        engine: "auto", "brave", "google", "bing", "baidu", "yahoo", "all".
        max_results: 1-50.
    """
    return await _do_search(
        query=query, label="Docs", max_results=max_results, engine=engine,
        region="wt-wt", safesearch="off", timelimit=None,
        scoped_domains=_DOCS_DOMAINS, query_category="api",
    )

@mcp.tool(annotations=_READONLY_TOOL)
async def search_paper(
    query: str, engine: str = "auto", max_results: int = 10,
    timelimit: str | None = None,
) -> str:
    """Search CS research papers and academic references.
    Scoped to arXiv, ACM DL, Semantic Scholar, IEEE, Usenix, PapersWithCode.

    Best for: algorithm details, state-of-the-art surveys, theoretical foundations.

    Args:
        query: Paper title, algorithm name, research topic, or author.
        engine: "auto", "brave", "google", "bing", "baidu", "yahoo", "all".
        max_results: 1-50.
        timelimit: "d", "w", "m", "y", or None.
    """
    return await _do_search(
        query=query, label="Paper", max_results=max_results, engine=engine,
        region="wt-wt", safesearch="off", timelimit=timelimit,
        scoped_domains=_PAPER_DOMAINS, query_category="code",
    )

@mcp.tool(annotations=_READONLY_TOOL)
async def search_github(
    query: str, engine: str = "auto", max_results: int = 10,
    timelimit: str | None = None,
) -> str:
    """Search open-source code repositories.
    Scoped to GitHub, GitLab, Bitbucket, Gitee, SourceForge, Codeberg.

    Best for: finding example projects, library source code, starter templates.

    Args:
        query: Repo name, tech stack keywords, or "X framework example".
        engine: "auto", "brave", "google", "bing", "baidu", "yahoo", "all".
        max_results: 1-50.
        timelimit: "d", "w", "m", "y", or None.
    """
    return await _do_search(
        query=query, label="Repo", max_results=max_results, engine=engine,
        region="wt-wt", safesearch="off", timelimit=timelimit,
        scoped_domains=_GITHUB_DOMAINS, query_category="code",
    )

# ===========================================================================
# Advanced Coding-Agent Tools
# ===========================================================================

@mcp.tool(annotations=_READONLY_TOOL)
async def search_error(
    error_message: str,
    language: str = "",
    engine: str = "auto",
    max_results: int = 10,
    session_id: str = "",
) -> str:
    """Search for solutions to an error message. Automatically strips noise
    like timestamps, file paths, hex addresses, and version numbers before searching.

    Use this FIRST when debugging — it's optimized for error resolution.

    Args:
        error_message: The exact error message or stack trace you're seeing.
        language: Optional language/framework hint (e.g. "Python", "React", "Go").
        engine: "auto", "brave", "google", "bing", "baidu", "yahoo", "all".
        max_results: 1-50.
        session_id: Track debug session across multiple error searches.
    """
    query = error_message.strip()
    if language:
        query = f"{language} {query}"

    # Detect known error code patterns to improve search precision
    error_hints = []
    # MongoDB/Node.js error codes: E11000, ERR_MODULE_NOT_FOUND, etc.
    for pattern, hint in [
        (r'\bE\d{4,6}\b', 'MongoDB'), (r'\bERR_\w+\b', 'Node.js'),
        (r'\b0x[0-9a-fA-F]{8,16}\b', 'Windows'), (r'\bPANIC\b', 'Rust/Go'),
        (r'\b(?:segfault|SIGSEGV|SIGABRT)\b', 'C/C++'),
        (r'\b(?:ORA-\d{4,5}|SQL\d{4})\b', 'Oracle/SQL'),
        (r'\b(?:TypeError|ReferenceError|SyntaxError|RangeError)\b', 'JavaScript'),
        (r'\b(?:ImportError|ModuleNotFoundError|AttributeError|KeyError|ValueError)\b', 'Python'),
        (r'\b(?:NullPointerException|ClassCastException|IllegalArgument)\b', 'Java'),
        (r'\b(?:panic|defer|goroutine)\b', 'Go'),
        (r'\bHTTP\s*(?:4\d{2}|5\d{2})\b', 'HTTP status'),
    ]:
        if re.search(pattern, query):
            error_hints.append(hint)
    if error_hints:
        query = f"{query} {' '.join(dict.fromkeys(error_hints))}"

    return await _do_search(
        query=query, label="Error Resolution", max_results=max_results,
        engine=engine, region="wt-wt", safesearch="off", timelimit=None,
        query_category="error", session_id=session_id or f"debug-{hashlib.md5(query.encode()).hexdigest()[:8]}",
    )


@mcp.tool(annotations=_READONLY_TOOL)
async def search_api(
    library: str,
    method: str = "",
    engine: str = "auto",
    max_results: int = 10,
) -> str:
    """Search for API documentation, method signatures, parameters, and usage.

    Use this when you need the exact signature, return type, or parameter
    documentation for a library method or class.

    Args:
        library: Library/framework name (e.g. "FastAPI", "React", "Express").
        method: Specific method/class/function name (e.g. "Depends", "useState").
        engine: "auto", "brave", "google", "bing", "baidu", "yahoo", "all".
        max_results: 1-50.
    """
    if method:
        query = f"{library} {method} API reference documentation parameters"
    else:
        query = f"{library} API reference documentation getting started"

    return await _do_search(
        query=query, label="API Reference", max_results=max_results,
        engine=engine, region="wt-wt", safesearch="off", timelimit=None,
        scoped_domains=_DOCS_DOMAINS, query_category="api",
        sort_by_authority=True,
    )


@mcp.tool(annotations=_READONLY_TOOL)
async def search_compare(
    tech_a: str,
    tech_b: str,
    aspect: str = "",
    engine: str = "auto",
    max_results: int = 10,
) -> str:
    """Compare two technologies, libraries, or approaches side by side.

    Searches for direct comparisons and authoritative analyses to help you
    choose the right tool for your use case.

    Args:
        tech_a: First technology name (e.g. "React", "FastAPI", " PostgreSQL").
        tech_b: Second technology name (e.g. "Vue", "Django", "MySQL").
        aspect: Optional comparison angle (e.g. "performance", "learning curve", "ecosystem").
        engine: "auto", "brave", "google", "bing", "baidu", "yahoo", "all".
        max_results: 1-50.
    """
    query = f"{tech_a} vs {tech_b}"
    if aspect:
        query += f" {aspect}"
    query += " comparison"

    return await _do_search(
        query=query, label=f"Compare", max_results=max_results,
        engine=engine, region="wt-wt", safesearch="off", timelimit=None,
        query_category="compare", sort_by_authority=True,
    )


@mcp.tool(annotations=_READONLY_TOOL)
async def search_deep(
    topic: str,
    engine: str = "auto",
    max_results: int = 5,
    fetch_top: int = 2,
) -> str:
    """Deep research on a topic: searches, then fetches the most relevant pages
    and extracts their key content. Returns both search results and fetched summaries.

    Use this when you need comprehensive understanding of a topic, not just links.
    Automatically fetches the top results' content for deeper analysis.

    Args:
        topic: The research topic or question.
        engine: "auto", "brave", "google", "bing", "baidu", "yahoo", "all".
        max_results: Search results to return (1-20).
        fetch_top: How many of the top results to fetch and summarize (1-5).
    """
    t_start = time.time()
    fetch_top = max(0, min(fetch_top, 5))
    max_results = max(1, min(max_results, 20))

    # Step 1: Search
    try:
        search_result = await _do_search(
            query=topic, label="Deep Research", max_results=max_results,
            engine=engine, region="wt-wt", safesearch="off", timelimit=None,
        )
    except SearchError as e:
        raise SearchError(f"Deep research search failed: {e}") from e

    if "No results" in search_result:
        return search_result

    # Step 2: Parse URLs from search results (match URL: prefix in any format)
    urls = []
    for line in search_result.split("\n"):
        # Match both full format "   URL: https://..." and compact "[title](url)"
        m1 = re.match(r'\s+URL:\s+(https?://\S+)', line)
        if m1:
            urls.append(m1.group(1))
        else:
            m2 = re.findall(r'\]\((https?://[^)]+)\)', line)
            urls.extend(m2)
        if len(urls) >= fetch_top:
            break
    urls = list(dict.fromkeys(urls))  # dedup preserving order

    if not urls:
        return search_result + "\n\n_(No URLs found to fetch for deep research)_"

    # Step 3: Fetch content from top results (parallel fetch for speed)
    async def _fetch_one(i: int, url: str):
        html, err = await _fetch(url, FETCH_TIMEOUT)
        if err:
            return (i, url, None, None, None, err)
        text = await _extract_text(html)
        if not text:
            return (i, url, None, None, None, "No readable content")
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else "Untitled"
        # Extract code blocks for synthesis
        code_blocks = re.findall(r'```(?:\w+)?\n(.*?)```', text, re.DOTALL)
        return (i, url, title, text, code_blocks, None)

    fetch_tasks = [_fetch_one(i, url) for i, url in enumerate(urls, 1)]
    fetched_raw = await asyncio.gather(*fetch_tasks)

    # Step 4: Build output with cross-source synthesis
    fetched = []
    all_code = []
    all_keywords = {}
    for i, url, title, text, code_blocks, err in fetched_raw:
        if err:
            fetched.append(f"### [{i}] {url}\n> ⚠ {err}\n")
            continue
        excerpt = text[:2000]
        if len(text) > 2000:
            excerpt += f"\n\n> [... {len(text)} total chars, showing first 2000 ...]"
        fetched.append(f"### [{i}] {title}\n> {url}\n\n{excerpt}\n")
        all_code.extend(code_blocks[:5])
        # Count keyword frequency across sources (simple term extraction)
        for term in re.findall(r'\b[A-Z][a-zA-Z]{3,}(?:\s+[A-Z][a-zA-Z]{3,})?\b', text[:1000]):
            all_keywords[term] = all_keywords.get(term, 0) + 1

    # Cross-source synthesis section
    synthesis_parts = []
    if len(fetched) >= 2:
        # Common terms across sources
        common_terms = [t for t, c in sorted(all_keywords.items(), key=lambda x: -x[1]) if c >= 2][:10]
        if common_terms:
            synthesis_parts.append(
                "**Common topics across sources:** " + " · ".join(common_terms)
            )
        # Code examples found
        all_code = list(dict.fromkeys(all_code))[:6]  # dedup, top 6
        if all_code:
            synthesis_parts.append(
                f"**{len(all_code)} code example(s) extracted** — see source sections below for full context"
            )
        synthesis_parts.append(
            f"**Coverage:** {len(fetched)} sources fetched from {len(urls)} URLs searched"
        )

    elapsed = (time.time() - t_start) * 1000

    parts = [f"## Deep Research: {topic}\n_Searched + fetched {len(fetched)}/{fetch_top} pages in {elapsed:.0f}ms_\n"]

    if synthesis_parts:
        parts.append("### Synthesis\n" + "\n".join(f"- {s}" for s in synthesis_parts))
        parts.append("")

    parts.append("---")
    parts.extend(fetched)

    if all_code:
        parts.append("---")
        parts.append("### Extracted Code Examples\n")
        for j, code in enumerate(all_code, 1):
            parts.append(f"**Example {j}:**\n```\n{code.strip()[:800]}\n```\n")

    parts.append("---")
    parts.append("### Search Results\n")
    parts.append(search_result)

    return "\n".join(parts)


@mcp.tool(annotations=_READONLY_TOOL)
async def search_similar_repos(
    repo_description: str,
    language: str = "",
    engine: str = "auto",
    max_results: int = 10,
) -> str:
    """Find open-source repositories similar to a description or existing project.

    Searches for projects matching a feature description, tech stack, or use case.
    Results are ranked by source authority (official repos > community).

    Args:
        repo_description: What the project does (e.g. "async HTTP client library Python").
        language: Programming language to filter by.
        engine: "auto", "brave", "google", "bing", "baidu", "yahoo", "all".
        max_results: 1-50.
    """
    query = f"{repo_description}"
    if language:
        query = f"{language} {query}"
    query += " github repository"

    return await _do_search(
        query=query, label="Similar Repos", max_results=max_results,
        engine=engine, region="wt-wt", safesearch="off", timelimit=None,
        scoped_domains=_GITHUB_DOMAINS, query_category="code",
        sort_by_authority=True,
    )


@mcp.tool(annotations=_READONLY_TOOL)
async def search_package(
    package: str,
    registry: str = "auto",
    engine: str = "auto",
    max_results: int = 5,
) -> str:
    """Look up package/library info directly from registries (PyPI, npm, crates.io, pkg.go.dev).
    Uses direct registry APIs — much faster than web search for version/license/dependency info.

    Args:
        package: Package name (e.g. "requests", "react", "serde", "gin").
        registry: "pypi", "npm", "crates", "go", or "auto" (auto-detect).
        engine: Fallback web search engine if direct API fails.
        max_results: Results if falling back to web search (1-10).
    """
    pkg = package.strip().lower()
    registries_to_try = []

    if registry == "auto":
        registries_to_try = ["pypi", "npm", "crates", "go"]
    elif registry in ("pypi", "npm", "crates", "go"):
        registries_to_try = [registry]
    else:
        return f"Unknown registry '{registry}'. Use: pypi, npm, crates, go, or auto."

    REGISTRY_URLS = {
        "pypi": f"https://pypi.org/pypi/{pkg}/json",
        "npm": f"https://registry.npmjs.org/{pkg}/latest",
        "crates": f"https://crates.io/api/v1/crates/{pkg}",
        "go": f"https://api.pkg.go.dev/packages/{pkg}",
    }

    results = []
    for reg in registries_to_try:
        url = REGISTRY_URLS[reg]
        try:
            async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as c:
                resp = await c.get(url, headers={"User-Agent": USER_AGENT})
                if resp.status_code != 200:
                    continue
                data = resp.json()

            if reg == "pypi":
                info = data.get("info", {})
                results.append(
                    f"### PyPI: {info.get('name', pkg)} v{info.get('version', '?')}\n"
                    f"- **License**: {info.get('license', 'N/A')}\n"
                    f"- **Summary**: {info.get('summary', 'N/A')[:300]}\n"
                    f"- **URL**: {info.get('project_url', f'https://pypi.org/project/{pkg}/')}\n"
                    f"- **Requires Python**: {info.get('requires_python', 'N/A')}\n"
                )
            elif reg == "npm":
                results.append(
                    f"### npm: {data.get('name', pkg)} v{data.get('version', '?')}\n"
                    f"- **License**: {data.get('license', 'N/A')}\n"
                    f"- **Description**: {str(data.get('description', 'N/A'))[:300]}\n"
                    f"- **URL**: https://www.npmjs.com/package/{pkg}\n"
                )
            elif reg == "crates":
                crate = data.get("crate", data)
                results.append(
                    f"### crates.io: {crate.get('name', pkg)} v{crate.get('max_stable_version', crate.get('newest_version', '?'))}\n"
                    f"- **License**: {crate.get('license', 'N/A')}\n"
                    f"- **Description**: {crate.get('description', 'N/A')[:300]}\n"
                    f"- **URL**: https://crates.io/crates/{pkg}\n"
                )
            elif reg == "go":
                results.append(
                    f"### pkg.go.dev: {data.get('name', pkg)} v{data.get('version', '?')}\n"
                    f"- **URL**: https://pkg.go.dev/{pkg}\n"
                    f"- **Synopsis**: {data.get('synopsis', 'N/A')[:300]}\n"
                )
            break  # stop after first successful registry
        except Exception:
            continue

    if results:
        return "\n\n".join(results)

    # Fallback to web search
    return await _do_search(
        query=f"{package} package version license", label="Package Info [web]",
        max_results=max_results, engine=engine, region="wt-wt",
        safesearch="off", timelimit=None, query_category="api",
    )


@mcp.tool(annotations=_READONLY_TOOL)
async def search_news(
    topic: str = "",
    engine: str = "auto",
    max_results: int = 10,
    period: str = "w",
) -> str:
    """Search tech and programming news from Hacker News, TechCrunch, ArsTechnica,
    The Verge, dev.to, and other tech news sources. Time-filtered for recent content.

    Args:
        topic: Tech topic, language, framework, or company (e.g. "Rust", "React", "OpenAI").
               Leave empty for top tech headlines.
        engine: "auto", "brave", "google", "bing", "baidu", "yahoo", "all".
        max_results: 1-50.
        period: Time filter — "d"=day, "w"=week, "m"=month, "y"=year.
    """
    _NEWS_DOMAINS = [
        "news.ycombinator.com", "techcrunch.com", "arstechnica.com",
        "theverge.com", "dev.to", "theregister.com", "zdnet.com",
        "thenewstack.io", "infoq.com", "lwn.net",
    ]
    query = topic.strip() if topic.strip() else "latest programming technology news"
    query = f"{query} news"

    return await _do_search(
        query=query, label="Tech News", max_results=max_results,
        engine=engine, region="wt-wt", safesearch="off", timelimit=period,
        scoped_domains=_NEWS_DOMAINS, query_category="code",
        sort_by_authority=True,
    )


@mcp.tool(annotations=_READONLY_TOOL)
async def search_github_issues(
    repo: str = "",
    query: str = "",
    state: str = "all",
    labels: str = "",
    max_results: int = 10,
) -> str:
    """Search GitHub issues and pull requests across repos. Uses GitHub's REST API
    directly — no search engine latency. Requires no API key for public repos
    (rate limit: 60 req/hr unauthenticated, 5000 with GITHUB_TOKEN).

    Args:
        repo: Repo name (e.g. "python/cpython" or "facebook/react"). Empty = search all.
        query: Search keywords, error messages, or issue titles.
        state: "open", "closed", or "all".
        labels: Comma-separated label filter (e.g. "bug,good first issue").
        max_results: 1-30.
    """
    if not query.strip():
        raise SearchError("GitHub issue search requires a query.")

    gh_token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": USER_AGENT}
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    max_results = max(1, min(max_results, 30))
    search_parts = [query.strip()]
    if repo.strip():
        search_parts.append(f"repo:{repo.strip()}")
    if state in ("open", "closed"):
        search_parts.append(f"state:{state}")
    if labels.strip():
        search_parts.append(f"label:{labels.strip()}")
    search_q = " ".join(search_parts)

    url = f"https://api.github.com/search/issues?q={quote_plus(search_q)}&per_page={max_results}&sort=updated&order=desc"

    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as c:
            resp = await c.get(url, headers=headers)
            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                return (
                    "GitHub API rate limit exceeded (60/hr unauthenticated).\n"
                    "Set GITHUB_TOKEN env var for 5000/hr:\n"
                    "  export GITHUB_TOKEN=ghp_xxxx  # or ghp_xxxx from github.com/settings/tokens"
                )
            if resp.status_code == 422:
                raise SearchError(f"GitHub search query invalid. Check repo name format (owner/repo).")
            if resp.status_code != 200:
                raise SearchError(f"GitHub API HTTP {resp.status_code}")
            data = resp.json()
    except httpx.RequestError as e:
        raise SearchError(f"GitHub API request failed: {e}") from e

    items = data.get("items", [])
    if not items:
        return f"No GitHub issues/PRs found for '{search_q}'."

    lines = [
        f"## GitHub Issues: {search_q}",
        f"_{len(items)} results of ~{data.get('total_count', '?')}_\n",
    ]
    for i, item in enumerate(items, 1):
        item_type = "PR" if "pull_request" in item else "Issue"
        state_icon = "🟢" if item["state"] == "open" else ("🟣" if item["state"] == "merged" else "🔴")
        labels_str = ""
        if item.get("labels"):
            labels_str = " [" + ", ".join(l["name"] for l in item["labels"][:3]) + "]"
        lines.append(
            f"### {i}. [{item_type} #{item['number']}]({item['html_url']}) {state_icon}{labels_str}\n"
            f"**{item['title']}**\n"
            f"> {item.get('repository_url', '').replace('https://api.github.com/repos/', '')} | "
            f"by [{item['user']['login']}]({item['user']['html_url']}) "
            f"| {item.get('comments', 0)} comments | "
            f"updated {item.get('updated_at', '?')[:10]}\n\n"
            f"{item.get('body', '(no description)')[:500]}\n"
        )
    return "\n".join(lines)


@mcp.tool(annotations=_READONLY_TOOL)
async def search_security(
    package: str,
    ecosystem: str = "auto",
    max_results: int = 10,
) -> str:
    """Check for known security vulnerabilities in a package/library.
    Uses the OSV (Open Source Vulnerabilities) API — covers PyPI, npm, crates.io,
    Go, Maven, RubyGems, and more. Essential before adding new dependencies.

    Args:
        package: Package name (e.g. "requests", "lodash", "serde").
        ecosystem: "PyPI", "npm", "crates.io", "Go", "Maven", "RubyGems", or "auto".
        max_results: Max vulnerabilities to show (1-20).
    """
    pkg = package.strip()
    max_results = max(1, min(max_results, 20))

    # Ecosystem auto-detection and mapping
    ECOSYSTEM_MAP = {
        "pypi": "PyPI",
        "npm": "npm",
        "crates": "crates.io",
        "go": "Go",
        "maven": "Maven",
        "rubygems": "RubyGems",
    }
    ecosystems_to_try = []
    if ecosystem == "auto":
        ecosystems_to_try = ["PyPI", "npm", "crates.io", "Go", "Maven", "RubyGems"]
    elif ecosystem.lower() in ECOSYSTEM_MAP:
        ecosystems_to_try = [ECOSYSTEM_MAP[ecosystem.lower()]]
    else:
        ecosystems_to_try = [ecosystem]

    results = []
    for eco in ecosystems_to_try:
        try:
            async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as c:
                resp = await c.post(
                    "https://api.osv.dev/v1/query",
                    json={"package": {"name": pkg, "ecosystem": eco}},
                )
                if resp.status_code != 200:
                    continue
                data = resp.json()
        except Exception:
            continue

        vulns = data.get("vulns", [])
        if not vulns:
            results.append(f"### {eco}: {pkg} — ✅ No known vulnerabilities")
            break  # found the right ecosystem

        lines = [f"### {eco}: {pkg} — ⚠️ {len(vulns)} vulnerabilities\n"]
        for v in vulns[:max_results]:
            vid = v.get("id", "unknown")
            aliases = ", ".join(v.get("aliases", [])[:3])
            summary = v.get("summary", "No description")[:300]
            severity = ""
            for sev in v.get("severity", []):
                if sev.get("type") == "CVSS_V3":
                    severity = f" | CVSS: {sev.get('score', '?')}"
            fixed = v.get("fixed", "not yet fixed")
            lines.append(
                f"- **{vid}**{severity}\n"
                f"  {summary}\n"
                f"  Aliases: {aliases or 'none'} | Fixed in: {fixed}\n"
            )
        results.append("\n".join(lines))
        break  # found the right ecosystem

    if results:
        return "\n\n".join(results)

    return (
        f"No vulnerability data found for '{pkg}'.\n"
        f"Try specifying ecosystem explicitly: {', '.join(ECOSYSTEM_MAP.keys())}."
    )


@mcp.tool(annotations=_READONLY_TOOL)
async def search_tutorial(
    technology: str,
    level: str = "beginner",
    engine: str = "auto",
    max_results: int = 8,
) -> str:
    """Find getting-started tutorials and learning resources for a technology.
    Scoped to tutorial platforms, official docs quickstart pages, and trusted
    learning sites. Authority-ranked for quality.

    Args:
        technology: Language, framework, or tool (e.g. "Rust", "React", "Docker").
        level: "beginner", "intermediate", or "advanced".
        engine: "auto", "brave", "google", "bing", "baidu", "yahoo", "all".
        max_results: 1-30.
    """
    _TUTORIAL_DOMAINS = [
        "realpython.com", "freecodecamp.org", "codecademy.com",
        "docs.python.org", "developer.mozilla.org", "learn.microsoft.com",
        "react.dev", "vuejs.org", "angular.io", "svelte.dev",
        "rust-lang.org", "golang.org", "nodejs.org", "kubernetes.io",
    ]
    level_q = {"beginner": "getting started tutorial for beginners",
               "intermediate": "intermediate guide tutorial",
               "advanced": "advanced deep dive guide"}
    level_part = level_q.get(level, level_q["beginner"])
    query = f"{technology} {level_part}"

    return await _do_search(
        query=query, label="Tutorial", max_results=max_results,
        engine=engine, region="wt-wt", safesearch="off", timelimit="y",
        scoped_domains=_TUTORIAL_DOMAINS, query_category="code",
        sort_by_authority=True,
    )


@mcp.tool(annotations=_READONLY_TOOL)
async def search_rss(
    feed_url: str = "",
    topic: str = "",
    max_results: int = 15,
) -> str:
    """Fetch and parse RSS/Atom feeds. Provide a feed URL to read a specific feed,
    or a topic to find relevant feeds via web search first, then fetch entries.

    Args:
        feed_url: Direct RSS/Atom feed URL (e.g. "https://hnrss.org/frontpage").
        topic: Topic to find feeds for if no feed_url given (e.g. "Rust releases").
        max_results: Max entries to return (1-50).
    """
    max_results = max(1, min(max_results, 50))

    # If no feed URL, search for feeds matching the topic
    if not feed_url.strip():
        if not topic.strip():
            raise SearchError("Provide either a feed_url or a topic to search for feeds.")
        # Search for RSS feeds
        search_result = await _do_search(
            query=f"{topic} RSS feed", label="Feed Search", max_results=3,
            engine="auto", region="wt-wt", safesearch="off", timelimit=None,
            output_format="links",
        )
        urls = re.findall(r'https?://[^\s\n]+', search_result)
        if not urls:
            return f"No RSS feeds found for '{topic}'."
        feed_url = urls[0]

    # Fetch and parse the feed
    html, err = await _fetch(feed_url.strip(), FETCH_TIMEOUT)
    if err:
        raise SearchError(f"Failed to fetch feed: {err}")

    soup = BeautifulSoup(html, "html.parser")

    # RSS 2.0
    items = soup.find_all("item")
    # Atom
    if not items:
        items = soup.find_all("entry")

    if not items:
        raise SearchError("No RSS/Atom entries found in the feed.")

    # Feed title
    feed_title = ""
    title_tag = soup.find("title")
    if title_tag:
        feed_title = title_tag.get_text(strip=True)

    lines = [f"## RSS: {feed_title or feed_url.strip()}", f"> {feed_url.strip()}\n"]
    for i, item in enumerate(items[:max_results], 1):
        title = ""
        link = ""
        desc = ""
        date = ""

        title_el = item.find("title")
        if title_el:
            title = title_el.get_text(strip=True)

        link_el = item.find("link")
        if link_el:
            link = link_el.get("href") or link_el.get_text(strip=True)

        desc_el = item.find("description") or item.find("summary") or item.find("content")
        if desc_el:
            desc = BeautifulSoup(desc_el.get_text(strip=True)[:300], "html.parser").get_text()

        for dtag in ("published", "updated", "pubDate", "dc:date"):
            date_el = item.find(dtag)
            if date_el:
                date = date_el.get_text(strip=True)[:25]
                break

        lines.append(f"### {i}. [{title}]({link})\n" if link else f"### {i}. {title}\n")
        if date:
            lines.append(f"_{date}_\n")
        if desc:
            lines.append(f"{desc}\n")

    return "\n".join(lines)


# ===========================================================================
# Content Fetching
# ===========================================================================

@mcp.tool(annotations=_READONLY_TOOL)
async def web_fetch(
    url: str, max_length: int = DEFAULT_MAX_LENGTH,
    timeout: int = FETCH_TIMEOUT,
) -> str:
    """Fetch a web page and extract readable content. Code blocks in <pre>/<code>
    are preserved as markdown fences with language detection. Navigation, ads,
    and boilerplate are stripped.

    Args:
        url: Target URL (http:// or https://).
        max_length: Max characters to return (default 12000).
        timeout: Request timeout in seconds (default 30).
    """
    html, err = await _fetch(url, timeout)
    if err:
        raise SearchError(err)

    text = await _extract_text(html)
    if not text:
        raise SearchError("No readable text found on this page.")

    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else "Untitled"

    prefix = f"# {title}\n> {url}\n\n"

    if len(text) > max_length:
        text = text[:max_length]
        text += f"\n\n> [... truncated to {max_length} chars ...]"

    return prefix + text


@mcp.tool(annotations=_READONLY_TOOL)
async def web_fetch_code(
    url: str, max_length: int = DEFAULT_MAX_LENGTH,
    timeout: int = FETCH_TIMEOUT,
) -> str:
    """Fetch a web page and extract ONLY the code blocks from it.
    Useful for reading source code, configuration examples, or code-heavy documentation.

    Returns all code blocks found on the page as markdown fences with language labels.

    Args:
        url: Target URL.
        max_length: Max characters to return (default 12000).
        timeout: Request timeout (default 30).
    """
    html, err = await _fetch(url, timeout)
    if err:
        raise SearchError(err)

    def _parse_code_blocks():
        soup = BeautifulSoup(html, "html.parser")
        blocks = []
        for pre in soup.find_all("pre"):
            code = pre.find("code")
            lang = ""
            if code:
                cls = code.get("class", [])
                for c in cls:
                    if c.startswith("language-") or c.startswith("lang-"):
                        lang = c.split("-", 1)[1]
                        break
                content = code.get_text()
            else:
                content = pre.get_text()
            lang_marker = f" {lang}" if lang else ""
            blocks.append(f"```{lang_marker}\n{content.strip()}\n```")
        return blocks

    code_blocks = await asyncio.to_thread(_parse_code_blocks)

    if not code_blocks:
        raise SearchError("No code blocks found on this page.")

    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else "Untitled"

    result = f"# {title} — Code Blocks\n> {url}\n\n"
    result += f"_{len(code_blocks)} code block(s) found_\n\n"
    result += "\n\n".join(code_blocks)

    if len(result) > max_length:
        result = result[:max_length]
        result += f"\n\n> [... truncated to {max_length} chars ...]"

    return result


# ===========================================================================
# Meta tools
# ===========================================================================

@mcp.tool(annotations=_READONLY_TOOL)
async def search_session(
    session_id: str = "default",
    action: str = "view",
) -> str:
    """Manage search session context for multi-turn research.

    Coding agents can use this to track search history across multiple queries,
    enabling context-aware follow-up searches and avoiding redundant lookups.

    Args:
        session_id: Session identifier (default "default"). Use different IDs
                    for different coding tasks or projects.
        action: "view" to see search history, "clear" to reset the session.
    """
    if action == "clear":
        if session_id in _search_sessions:
            del _search_sessions[session_id]
            return f"Session '{session_id}' cleared."
        return f"Session '{session_id}' was already empty."

    return _session_context(session_id)


@mcp.tool(annotations=_READONLY_TOOL)
async def list_engines() -> str:
    """Show all available search engines, their config status, and setup instructions."""
    brave_status = "✅ configured" if os.environ.get(ENV_BRAVE_KEY) else "❌ not set"
    google_status = "✅ configured" if (os.environ.get(ENV_GOOGLE_KEY) and os.environ.get(ENV_GOOGLE_CX)) else "❌ not set"
    bing_status = "✅ configured" if os.environ.get(ENV_BING_KEY) else "❌ not set"
    searxng_status = "✅ configured" if os.environ.get(ENV_SEARXNG_URL) else "❌ not set"

    return f"""
## Available Search Engines

| Engine   | Backend                           | Free Tier           | Status      |
|----------|-----------------------------------|---------------------|-------------|
| auto     | DuckDuckGo (Bing+Yahoo+Brave)     | **Unlimited, free** | ✅ always   |
| brave    | Brave Search (independent index)  | 2000/mo             | {brave_status} |
| google   | Google Custom Search              | 100/day             | {google_status} |
| bing     | Bing Web Search v7                | 1000/mo             | {bing_status} |
| baidu    | Baidu scraping                    | Unlimited (China)   | ✅ always   |
| yahoo    | Yahoo via DDGS                    | Unlimited           | ✅ always   |
| searxng  | SearXNG (self-hosted metasearch)  | **Unlimited**       | {searxng_status} |

## Coding-Agent Tools (20 total)

| Tool | Purpose |
|------|---------|
| `web_search` | General web search with any engine, 3 output formats, session tracking |
| `search_code` | Programming Q&A: Stack Overflow, GitHub, Reddit, dev.to, HN |
| `search_docs` | Official docs: MDN, readthedocs, PyPI, npm, MS Learn, crates.io |
| `search_paper` | Research papers: arXiv, ACM DL, IEEE, Semantic Scholar, Usenix |
| `search_github` | Code repos: GitHub, GitLab, Bitbucket, Gitee, SourceForge |
| `search_github_issues` | **NEW** — Search issues/PRs across GitHub repos (no API key needed) |
| `search_error` | **Debug errors** — auto-strips noise, detects 10+ error code patterns |
| `search_api` | **API signatures** — method, parameter, and return type documentation |
| `search_compare` | **Compare X vs Y** — side-by-side technology analysis |
| `search_deep` | **Deep research** — search + parallel fetch + cross-source synthesis |
| `search_similar_repos` | **Find repos** by feature description and language |
| `search_package` | **Package lookup** — PyPI, npm, crates.io, pkg.go.dev direct API |
| `search_security` | **NEW** — Check for CVEs via OSV API (PyPI, npm, crates, Go, Maven) |
| `search_news` | **Tech news** — HN, TechCrunch, ArsTechnica, dev.to, time-filtered |
| `search_tutorial` | **NEW** — Find tutorials by tech and skill level (beginner to advanced) |
| `search_rss` | **NEW** — Fetch and parse RSS/Atom feeds by URL or topic search |
| `web_fetch` | Extract readable content from any URL, preserves code blocks |
| `web_fetch_code` | Extract **only code blocks** from a URL with language detection |
| `search_session` | Manage multi-turn search context across queries |
| `list_engines` | Show engine status, API key config, and setup instructions |

## Setup

```bash
# Brave (recommended free addition)
export BRAVE_SEARCH_API_KEY=your_key  # https://brave.com/search/api/

# Google
export GOOGLE_API_KEY=your_key
export GOOGLE_CSE_ID=your_cse_id

# Bing
export BING_SEARCH_API_KEY=your_key

# SearXNG (self-hosted, privacy-respecting)
export SEARXNG_URL=http://localhost:8080

# Fallback chain
export SEARCH_ENGINES=brave,auto
```
"""


# ===========================================================================
# MCP Resources — expose domain lists and templates
# ===========================================================================

_RESOURCE_ANNOTATIONS = ResourceAnnotations(audience=["assistant"], priority=0.7)

@mcp.resource("search://domains/code", mime_type="application/json", annotations=_RESOURCE_ANNOTATIONS)
def resource_code_domains() -> str:
    """Code Q&A domains used by search_code."""
    return json.dumps(_CODE_DOMAINS, indent=2)

@mcp.resource("search://domains/docs", mime_type="application/json", annotations=_RESOURCE_ANNOTATIONS)
def resource_docs_domains() -> str:
    """Documentation domains used by search_docs."""
    return json.dumps(_DOCS_DOMAINS, indent=2)

@mcp.resource("search://domains/paper", mime_type="application/json", annotations=_RESOURCE_ANNOTATIONS)
def resource_paper_domains() -> str:
    """Academic paper domains used by search_paper."""
    return json.dumps(_PAPER_DOMAINS, indent=2)

@mcp.resource("search://domains/github", mime_type="application/json", annotations=_RESOURCE_ANNOTATIONS)
def resource_github_domains() -> str:
    """Repository domains used by search_github."""
    return json.dumps(_GITHUB_DOMAINS, indent=2)

@mcp.resource("search://authority", mime_type="application/json", annotations=_RESOURCE_ANNOTATIONS)
def resource_authority() -> str:
    """Source authority scores for result ranking."""
    return json.dumps(_AUTHORITY_SCORES, indent=2)


# ===========================================================================
# Entry point
# ===========================================================================

def main():
    mcp.run()


if __name__ == "__main__":
    main()
