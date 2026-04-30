import asyncio
import copy
import datetime
import hashlib
import html as html_lib
import ipaddress
import json
import os
import re
import socket
import time
from difflib import SequenceMatcher
from urllib import robotparser
from urllib.parse import quote, quote_plus, urldefrag, urljoin, urlparse, urlunparse

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
            msg += f"\nHint: {self.recovery}"
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
        wait = max(1, int(window - (now - calls[0]) + 0.999))
        return (
            f"Rate limit: {engine} engine ({len(calls)}/{max_per_minute} per minute). "
            f"Wait {wait}s or try engine='auto'."
        )
    calls.append(now)
    return None


# Shared annotations for tool mutability.
_READONLY_TOOL = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
_MUTATING_TOOL = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)

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
MAX_REDIRECTS = 5
MAX_FETCH_BYTES = 2 * 1024 * 1024
CRAWL_CONCURRENCY = 5

STRIP_TAGS = [
    "script", "style", "nav", "footer", "header",
    "svg", "form", "iframe", "video",
    "audio", "canvas", "embed", "object", "select",
    "button", "input", "textarea",
]

NON_HTML_EXTENSIONS = (
    ".7z", ".avi", ".bmp", ".bz2", ".csv", ".doc", ".docx", ".exe", ".gif",
    ".gz", ".ico", ".iso", ".jpeg", ".jpg", ".mov", ".mp3", ".mp4", ".odp",
    ".ods", ".odt", ".pdf", ".png", ".ppt", ".pptx", ".rar", ".tar", ".tgz",
    ".webm", ".webp", ".xls", ".xlsx", ".zip",
)

ALLOWED_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/ld+json",
    "application/xml",
    "application/rss+xml",
    "application/atom+xml",
    "application/xhtml+xml",
    "application/javascript",
    "application/x-javascript",
)

VALID_SAFESEARCH = {"on", "moderate", "off"}
VALID_TIMELIMITS = {None, "d", "w", "m", "y"}
VALID_OUTPUT_FORMATS = {"full", "compact", "links"}

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
    "doc.rust-lang.org", "golang.org/pkg", "ruby-doc.org",
    "api.rubyonrails.org", "docs.djangoproject.com", "react.dev",
    "vuejs.org", "angular.io", "kubernetes.io/docs",
    "docker.com/docs", "terraform.io/docs", "ansible.com/docs",
    "postgresql.org/docs", "mysql.com/docs", "mongodb.com/docs",
]

_PAPER_DOMAINS = [
    "arxiv.org", "dl.acm.org", "scholar.google.com",
    "semanticscholar.org", "paperswithcode.com", "openreview.net",
    "ieeexplore.ieee.org", "usenix.org", "researchgate.net",
    "aclanthology.org", "jmlr.org", "neurips.cc",
    "proceedings.mlr.press", "dl.acm.org/doi", "dlnext.acm.org",
]

_GITHUB_DOMAINS = [
    "github.com", "gitlab.com", "bitbucket.org",
    "gitee.com", "sourceforge.net", "codeberg.org",
    "git.sr.ht", "launchpad.net", "salsa.debian.org",
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
    # Community & News
    "dev.to": 0.6, "medium.com": 0.55, "reddit.com": 0.5,
    "news.ycombinator.com": 0.7,
    # Framework & additional docs
    "react.dev": 1.0, "vuejs.org": 1.0,
    "angular.io": 1.0, "docs.djangoproject.com": 1.0, "golang.org": 0.95,
    # Cloud providers
    "docs.aws.amazon.com": 0.95, "cloud.google.com": 0.95,
    # Security
    "nvd.nist.gov": 1.0, "cve.mitre.org": 1.0, "osv.dev": 0.95, "snyk.io": 0.85,
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
        "top_urls": [_as_text(r.get("href")) for r in results[:3]],
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
        return copy.deepcopy(entry[1])
    if entry:
        del _search_cache[key]
    return None

def _cache_set(key: str, results: list[dict]) -> None:
    _search_cache[key] = (time.time(), copy.deepcopy(results))
    if len(_search_cache) > 200:
        stale = [k for k, v in _search_cache.items() if time.time() - v[0] >= CACHE_TTL]
        for k in stale:
            del _search_cache[k]
        if len(_search_cache) > 200:
            oldest = sorted(_search_cache.items(), key=lambda item: item[1][0])
            for k, _ in oldest[:len(_search_cache) - 200]:
                del _search_cache[k]

# ===========================================================================
# Helpers
# ===========================================================================

def _as_text(value, default: str = "") -> str:
    if value is None:
        return default
    return str(value)

def _clean_one_line(value, default: str = "") -> str:
    return re.sub(r"[\r\n]+", " ", _as_text(value, default)).strip()


def _escape_markdown(value, default: str = "") -> str:
    text = _clean_one_line(value, default)
    return text.translate(str.maketrans({
        "\\": "\\\\", "`": "\\`", "*": "\\*", "_": "\\_", "{": "\\{",
        "}": "\\}", "[": "\\[", "]": "\\]", "(": "\\(", ")": "\\)",
        "#": "\\#", "+": "\\+", "-": "\\-", ".": "\\.", "!": "\\!",
        "|": "\\|", ">": "\\>",
    }))


def _clean_result_url(value) -> str:
    return re.sub(r"[\r\n\t ]+", "%20", _as_text(value).strip())


def _is_safe_result_url(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _normalize_host(host: str) -> str:
    host = (host or "").rstrip(".").lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    return re.sub(r"^www\.", "", host)


def _effective_port(parsed) -> int | None:
    try:
        return parsed.port or (443 if parsed.scheme == "https" else 80 if parsed.scheme == "http" else None)
    except ValueError:
        return None


def _normalize_url_for_compare(url: str) -> str:
    parsed = urlparse(url)
    host = _normalize_host(parsed.hostname or "")
    port = _effective_port(parsed)
    netloc = host if port in (80, 443, None) else f"{host}:{port}"
    path = parsed.path or "/"
    return urlunparse((parsed.scheme.lower(), netloc, path, "", parsed.query, ""))


def _same_site(url: str, base_url: str) -> bool:
    parsed = urlparse(url)
    base = urlparse(base_url)
    return (
        _normalize_host(parsed.hostname or "") == _normalize_host(base.hostname or "")
        and _effective_port(parsed) == _effective_port(base)
    )


def _is_probably_html_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return not any(path.endswith(ext) for ext in NON_HTML_EXTENSIONS)


def _split_url_list(urls: str) -> list[str]:
    """Split URL lists without breaking commas inside URLs."""
    parts = re.split(r"\n+|,\s*(?=https?://)", urls.strip())
    return [_clean_one_line(part) for part in parts if _clean_one_line(part)]


def _content_type_supported(content_type: str) -> bool:
    if not content_type:
        return True
    media_type = content_type.split(";", 1)[0].strip().lower()
    return any(media_type.startswith(prefix) for prefix in ALLOWED_CONTENT_TYPES)


def _provider_config_fingerprint(engines: list[str]) -> str:
    names = []
    for eng in engines:
        key_name = _ENGINE_INFO.get(eng, {}).get("key")
        if not key_name:
            continue
        for env_key in key_name.split("+"):
            value = os.environ.get(env_key, "").strip()
            digest = hashlib.sha256(value.encode()).hexdigest()[:12] if value else ""
            names.append(f"{env_key}:{digest}")
    return ",".join(sorted(names))


def _strip_package_spec(package: str) -> str:
    pkg = package.strip()
    if pkg.startswith("@"):
        match = re.match(r"(@[^@\s]+/[^@\s]+)(?:@.+)?$", pkg)
        if match:
            return match.group(1).strip()
    pkg = re.split(r"\s*(?:==|>=|<=|~=|!=|>|<)\s*", pkg, maxsplit=1)[0]
    if "@" in pkg:
        pkg = pkg.split("@", 1)[0]
    return pkg.strip()


def _sanitize_query_terms(query: str) -> str:
    return re.sub(r"\s+", " ", query.replace("\r", " ").replace("\n", " ")).strip()


def _normalize_registry(registry: str) -> str:
    key = registry.strip().lower()
    aliases = {"crate": "crates", "crates.io": "crates", "golang": "go", "pkg.go.dev": "go"}
    return aliases.get(key, key)


def _tool_count() -> int:
    return len([
        n for n, obj in globals().items()
        if callable(obj) and (n.startswith("search_") or n.startswith("web_") or n == "list_engines")
    ])


def _retry_sleep(attempt: int) -> float:
    return min(2 ** attempt, 8.0)


def _retry_after_sleep(headers, attempt: int) -> float:
    value = ""
    if headers:
        value = headers.get("retry-after", headers.get("Retry-After", ""))
    try:
        return min(max(float(value), 0.0), 8.0)
    except (TypeError, ValueError):
        return _retry_sleep(attempt) + 1


def _truncate_output(prefix: str, body: str, max_length: int, close_fence: bool = False) -> str:
    max_length = max(0, max_length)
    result = prefix + body
    if len(result) <= max_length:
        return result
    notice = f"\n\n> [... truncated to {max_length} chars ...]"
    if max_length <= len(notice):
        return notice[:max_length]
    budget = max_length - len(prefix) - len(notice)
    if budget <= 0:
        return (prefix[:max_length - len(notice)] + notice)[:max_length]
    trimmed = body[:budget].rstrip()
    if close_fence and trimmed.count("```") % 2:
        closure = "\n```"
        trimmed = body[:max(0, budget - len(closure))].rstrip() + closure
    return (prefix + trimmed + notice)[:max_length]


def _title_similar(a: str, b: str) -> float:
    if not a.strip() or not b.strip():
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

# Pre-sorted by domain length (longest first) so subdomain matches are precise
_SORTED_AUTHORITY = sorted(_AUTHORITY_SCORES.items(), key=lambda x: -len(x[0]))


def _source_authority(url: str) -> float:
    """Score a URL's authority for coding-related searches. Higher = more trusted."""
    try:
        host = _normalize_host(urlparse(url).hostname or "")
        for domain, score in _SORTED_AUTHORITY:
            # Dot-aware matching: domain must match end of host with a dot boundary
            if host == domain or host.endswith("." + domain):
                return score
    except Exception:
        pass
    return 0.4  # default for unknown sources

def _source_freshness(body: str, title: str = "") -> float:
    """Estimate content freshness from snippet text. Returns a boost factor (0-0.2).
    More recent = higher score. Detects year mentions and relative time indicators."""
    score = 0.0
    text = (_as_text(body) + " " + _as_text(title)).lower()

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
    if re.search(r'\b(today|just now|minutes ago|hours ago)\b|今天|刚刚|小时前|分钟前', text):
        score += 0.2
    elif re.search(r'\b(yesterday|this week|days ago)\b|昨天|本周|日前', text):
        score += 0.15
    elif re.search(r'\b(this month|weeks ago|recently)\b|本月|最近|周前', text):
        score += 0.1
    elif re.search(r'\b(this year|months ago)\b|今年|月前', text):
        score += 0.05

    return min(score, 0.2)


def _relevance_score(result: dict, query: str) -> float:
    """Score how relevant a result is to the query. Returns a boost factor (0-0.3).
    Measures keyword overlap between query terms and result title + snippet."""
    if not query:
        return 0.0
    query_terms = set(re.findall(r'\w+', query.lower(), flags=re.UNICODE))
    if not query_terms:
        return 0.0
    result_text = (_as_text(result.get("title")) + " " + _as_text(result.get("body"))).lower()
    result_terms = set(re.findall(r'\w+', result_text + " " + _as_text(result.get("href")), flags=re.UNICODE))
    meaningful_terms = {term for term in query_terms if len(term) > 2}
    if not meaningful_terms:
        meaningful_terms = query_terms
    denominator = max(1, min(len(meaningful_terms), 8))
    overlap = min(len(meaningful_terms & result_terms), denominator) / denominator
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
        original_q = q
        # Strip timestamps, hex addresses, file paths with line numbers
        q = re.sub(r'\b0x[0-9a-fA-F]+\b', '', q)
        q = re.sub(r'[\/\w]+\.(py|js|ts|go|rs|java|cpp|c|h):\d+', '', q)
        q = re.sub(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}', '', q)
        q = re.sub(r'at\s+\w+\([^\n]*\)', '', q)
        q = re.sub(r'\s{2,}', ' ', q).strip()
        if not q:
            q = original_q

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
    clean_domains = []
    for d in domains:
        parsed = urlparse(d if "://" in d else f"https://{d}")
        host = parsed.hostname or d.split("/", 1)[0]
        if host and host not in clean_domains:
            clean_domains.append(host)
    sites = " OR ".join(f"site:{d}" for d in clean_domains[:5])
    return f"({sites}) {query}"

def _build_result(index: int, result: dict) -> str:
    title = _escape_markdown(result.get("title"), "No title")
    if not title:
        title = "No title"
    if len(title) > 150:
        title = title[:147] + "..."
    href = _clean_result_url(result.get("href"))
    if not _is_safe_result_url(href):
        href = "(no URL)"
    body = _escape_markdown(result.get("body"))
    engine = _escape_markdown(result.get("engine"))
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

    header = f"## {_escape_markdown(label)}: {_escape_markdown(query)}"
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
    header = f"## {_escape_markdown(label)}: {_escape_markdown(query)}"
    meta = f"_{len(results)} results"
    if elapsed_ms:
        meta += f" in {elapsed_ms:.0f}ms"
    meta += "_"
    lines = [header, meta, ""]
    for i, r in enumerate(results, 1):
        title = _escape_markdown(r.get("title"), "No title")[:120] or "No title"
        href = _clean_result_url(r.get("href"))
        if not _is_safe_result_url(href):
            href = ""
        engine = _escape_markdown(r.get("engine"))
        tag = f" [{engine}]" if engine else ""
        lines.append(f"{i}. [{title}]({href}){tag}" if href else f"{i}. {title} (no URL){tag}")
    return "\n".join(lines)


def _format_links(query: str, results: list[dict], label: str, elapsed_ms: float = 0) -> str:
    """URL-only output: just the links."""
    header = f"## {_escape_markdown(label)}: {_escape_markdown(query)}"
    meta = f"_{len(results)} results"
    if elapsed_ms:
        meta += f" in {elapsed_ms:.0f}ms"
    meta += "_"
    lines = [header, meta, ""]
    for i, r in enumerate(results, 1):
        href = _clean_result_url(r.get("href"))
        if not _is_safe_result_url(href):
            href = ""
        lines.append(f"{i}. {href}" if href else f"{i}. (no URL)")
    return "\n".join(lines)


def _is_blocked_address(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any((
        addr.is_loopback,
        addr.is_private,
        addr.is_link_local,
        addr.is_multicast,
        addr.is_reserved,
        addr.is_unspecified,
    ))


def _validate_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except Exception:
        return f"Invalid URL: {url}."
    if parsed.scheme not in ("http", "https"):
        return f"Unsupported protocol '{parsed.scheme}'."
    if not parsed.netloc or not parsed.hostname:
        return "URL has no hostname."
    if parsed.username or parsed.password:
        return "URL credentials are not allowed."
    host = parsed.hostname
    if _is_blocked_address(host):
        return f"Blocked private or local address: {host}."
    try:
        resolved = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return f"Could not resolve hostname: {host}."
    except OSError as exc:
        return f"Could not validate hostname '{host}': {exc}."
    for info in resolved:
        resolved_ip = info[4][0]
        if _is_blocked_address(resolved_ip):
            return f"Blocked private or local address: {resolved_ip}."
    if parsed.scheme == "http":
        return "Plaintext HTTP URLs are not allowed. Use HTTPS."
    return None

async def _extract_text(html: str) -> str:
    """Extract readable text from HTML. CPU-bound parsing runs in thread pool."""
    def _parse():
        soup = BeautifulSoup(html, "html.parser")
        meta_desc = ""
        meta_el = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
        if meta_el:
            meta_desc = _clean_one_line(meta_el.get("content"))
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
        for table in soup.find_all("table"):
            rows = []
            for tr in table.find_all("tr"):
                cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])]
                if cells:
                    rows.append(" | ".join(cells))
            if rows:
                table.replace_with("\n" + "\n".join(rows) + "\n")
        for a in soup.find_all("a", href=True):
            text = a.get_text(" ", strip=True)
            href = _clean_result_url(a.get("href"))
            if href:
                a.replace_with(f"{text} ({href})" if text else href)
        for tag in STRIP_TAGS:
            for el in soup.find_all(tag):
                el.decompose()
        main = soup.find("article") or soup.find("main") or soup.find("body")
        if not main:
            return meta_desc
        text = main.get_text(separator="\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = "\n".join(line.strip() for line in text.splitlines()).strip()
        if meta_desc and meta_desc not in text:
            text = f"{meta_desc}\n\n{text}" if text else meta_desc
        return text
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
                timeout=httpx.Timeout(timeout), follow_redirects=False,
                headers=default_headers,
                trust_env=False,
            ) as client:
                current_url = url
                redirects = 0
                while True:
                    resp = await client.get(current_url)
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = getattr(resp, "headers", {}).get("location")
                        if not location:
                            return None, f"Redirect {resp.status_code} without Location"
                        if redirects >= MAX_REDIRECTS:
                            return None, f"Too many redirects (>{MAX_REDIRECTS})"
                        next_url = urljoin(current_url, location)
                        err = _validate_url(next_url)
                        if err:
                            return None, f"Redirect blocked: {err}"
                        current_url = next_url
                        redirects += 1
                        continue
                    break
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError, httpx.TooManyRedirects):
            if attempt < MAX_RETRIES:
                await asyncio.sleep(_retry_sleep(attempt))
                continue
            return None, f"Network error after {MAX_RETRIES + 1} attempts"
        except Exception as exc:
            return None, f"Fetch failed: {exc}"
        if resp.status_code == 429 and attempt < MAX_RETRIES:
            await asyncio.sleep(_retry_after_sleep(getattr(resp, "headers", {}), attempt))
            continue
        if 500 <= resp.status_code < 600 and attempt < MAX_RETRIES:
            await asyncio.sleep(_retry_sleep(attempt))
            continue
        if resp.status_code >= 400:
            detail = _clean_one_line(getattr(resp, "text", ""))[:300]
            return None, f"HTTP {resp.status_code}" + (f": {detail}" if detail else "")
        headers = getattr(resp, "headers", {}) or {}
        content_type = headers.get("content-type", headers.get("Content-Type", ""))
        if not _content_type_supported(content_type):
            return None, f"Unsupported content type: {content_type}"
        content_length = headers.get("content-length", headers.get("Content-Length"))
        try:
            if content_length and int(content_length) > MAX_FETCH_BYTES:
                return None, f"Response too large: {content_length} bytes"
        except ValueError:
            pass
        raw = getattr(resp, "content", None)
        if raw is not None and len(raw) > MAX_FETCH_BYTES:
            return None, f"Response too large: {len(raw)} bytes"
        return resp.text, None
    return None, "Max retries exceeded"

def _is_duplicate(result: dict, seen: list[dict], title_threshold: float = 0.85) -> bool:
    url = _clean_result_url(result.get("href"))
    norm_url = _normalize_url_for_compare(url) if url else ""
    title = _clean_one_line(result.get("title"))
    for s in seen:
        seen_url = _clean_result_url(s.get("href"))
        seen_norm_url = _normalize_url_for_compare(seen_url) if seen_url else ""
        if norm_url and seen_norm_url and seen_norm_url == norm_url:
            return True
        seen_title = _clean_one_line(s.get("title"))
        if not title or not seen_title:
            continue
        same_host = (
            bool(url and seen_url)
            and _normalize_host(urlparse(url).hostname or "") == _normalize_host(urlparse(seen_url).hostname or "")
        )
        threshold = title_threshold if same_host else 0.97
        if len(title) < 8 or len(seen_title) < 8:
            continue
        if _title_similar(seen_title, title) >= threshold:
            return True
    return False

# ===========================================================================
# Search Engines
# ===========================================================================

async def _search_ddgs(query: str, region="wt-wt", safesearch="off",
                       timelimit: str | None = None, max_results=10) -> list[dict]:
    if safesearch not in VALID_SAFESEARCH:
        raise DDGSException(f"Invalid safesearch '{safesearch}'. Use on, moderate, or off.")
    if timelimit not in VALID_TIMELIMITS:
        raise DDGSException(f"Invalid timelimit '{timelimit}'. Use d, w, m, y, or None.")

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

async def _search_brave_api(
    query: str, max_results: int, region="wt-wt", safesearch="off", timelimit: str | None = None,
) -> list[dict]:
    api_key = os.environ.get(ENV_BRAVE_KEY, "").strip()
    if not api_key:
        raise DDGSException("Brave requires BRAVE_SEARCH_API_KEY. Get free key at brave.com/search/api/")
    params = [
        f"q={quote_plus(query)}",
        f"count={min(max_results, 20)}",
        f"safesearch={quote_plus(safesearch)}",
    ]
    if region and region != "wt-wt":
        params.append(f"country={quote_plus(region.split('-', 1)[0].upper())}")
    if timelimit:
        params.append(f"freshness={quote_plus(timelimit)}")
    url = "https://api.search.brave.com/res/v1/web/search?" + "&".join(params)
    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT, trust_env=False) as c:
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

async def _search_google_api(
    query: str, max_results: int, region="wt-wt", safesearch="off", timelimit: str | None = None,
) -> list[dict]:
    api_key, cx = os.environ.get(ENV_GOOGLE_KEY, "").strip(), os.environ.get(ENV_GOOGLE_CX, "").strip()
    if not api_key or not cx:
        raise DDGSException("Google needs GOOGLE_API_KEY + GOOGLE_CSE_ID.")
    params = [
        f"key={quote_plus(api_key)}",
        f"cx={quote_plus(cx)}",
        f"q={quote_plus(query)}",
        f"num={min(max_results, 10)}",
        f"safe={'active' if safesearch in ('on', 'moderate') else 'off'}",
    ]
    if region and region != "wt-wt":
        params.append(f"gl={quote_plus(region.split('-', 1)[0].lower())}")
    if timelimit:
        params.append(f"dateRestrict={quote_plus(timelimit + '1')}")
    url = "https://www.googleapis.com/customsearch/v1?" + "&".join(params)
    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT, trust_env=False) as c:
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

async def _search_bing_api(
    query: str, max_results: int, region="wt-wt", safesearch="off", timelimit: str | None = None,
) -> list[dict]:
    api_key = os.environ.get(ENV_BING_KEY, "").strip()
    if not api_key:
        raise DDGSException("Bing needs BING_SEARCH_API_KEY.")
    mkt = region if re.fullmatch(r"[a-z]{2}-[a-z]{2}", region or "", re.I) else "en-US"
    safe_map = {"on": "Strict", "moderate": "Moderate", "off": "Off"}
    freshness_map = {"d": "Day", "w": "Week", "m": "Month"}
    params = [
        f"q={quote_plus(query)}",
        f"count={min(max_results, 15)}",
        f"mkt={quote_plus(mkt)}",
        f"safeSearch={safe_map.get(safesearch, 'Off')}",
    ]
    if timelimit in freshness_map:
        params.append(f"freshness={freshness_map[timelimit]}")
    url = "https://api.bing.microsoft.com/v7.0/search?" + "&".join(params)
    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT, trust_env=False) as c:
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
        for container in soup.select("div.result, div.c-container, div.c-result, div[srcid]"):
            if len(results) >= max_results:
                break
            title_el = container.find("h3")
            link_el = container.find("a", href=True)
            if not title_el or not link_el:
                continue
            # Try to extract real URL from Baidu's redirect wrapper
            real_href = link_el.get("data-url") or link_el.get("mu") or link_el["href"]
            parsed_href = urlparse(real_href)
            if _normalize_host(parsed_href.hostname or "").endswith("baidu.com") and parsed_href.path.startswith("/link"):
                continue
            snippet_el = container.select_one(
                "div.c-abstract, span.c-font-normal, div.c-span-last, span[class*='content-right']"
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
    def _sync_search():
        with DDGS(timeout=SEARCH_TIMEOUT) as ddgs:
            return list(ddgs.text(query, backend="yahoo", max_results=max_results))

    try:
        raw = await asyncio.to_thread(_sync_search)
    except TypeError:
        raw = await _search_ddgs(query, max_results=max_results)
    return [
        {"title": r.get("title", ""), "href": r.get("href", ""),
         "body": r.get("body", ""), "engine": "yahoo",
         "_authority": _source_authority(r.get("href", ""))}
        for r in raw
    ]


async def _search_searxng(
    query: str, max_results: int, region="wt-wt", safesearch="off", timelimit: str | None = None,
) -> list[dict]:
    """Search via a self-hosted SearXNG instance. Privacy-respecting metasearch."""
    searxng_url = os.environ.get(ENV_SEARXNG_URL, "").rstrip("/")
    if not searxng_url:
        raise DDGSException(
            "SearXNG requires SEARXNG_URL env var. Set it to your instance URL.\n"
            "Example: export SEARXNG_URL=http://localhost:8080"
        )
    base = searxng_url if searxng_url.endswith("/search") else f"{searxng_url}/search"
    safe_map = {"off": "0", "moderate": "1", "on": "2"}
    time_map = {"d": "day", "w": "week", "m": "month", "y": "year"}
    params = [
        "format=json",
        f"q={quote_plus(query)}",
        "categories=general",
        "pageno=1",
        f"safesearch={safe_map.get(safesearch, '0')}",
    ]
    if region and region != "wt-wt":
        params.append(f"language={quote_plus(region)}")
    if timelimit in time_map:
        params.append(f"time_range={time_map[timelimit]}")
    url = f"{base}?" + "&".join(params)
    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT, trust_env=False) as c:
        resp = await c.get(url, headers={"User-Agent": USER_AGENT})
        if resp.status_code != 200:
            raise DDGSException(f"SearXNG HTTP {resp.status_code} — is the instance running?")
        data = resp.json()
    if not isinstance(data, dict):
        raise DDGSException("SearXNG returned malformed JSON.")
    results = []
    for r in (data.get("results") or [])[:max_results]:
        if not isinstance(r, dict):
            continue
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
_ENGINE_PRIORITY = ["searxng", "auto", "brave", "google", "bing", "yahoo", "baidu"]

_NO_KEY_MSGS = {
    "brave": "Engine 'brave' needs BRAVE_SEARCH_API_KEY. Free key: https://brave.com/search/api/",
    "google": "Engine 'google' needs GOOGLE_API_KEY + GOOGLE_CSE_ID.",
    "bing": "Engine 'bing' needs BING_SEARCH_API_KEY. Note: 'auto' already includes Bing.",
    "searxng": "Engine 'searxng' needs SEARXNG_URL. Set to your instance URL (e.g. http://localhost:8080).",
}

def _required_keys_present(required_key: str | None) -> bool:
    """Return True when an engine has no key requirement or all required env vars are set."""
    if not required_key:
        return True
    return all(os.environ.get(k, "").strip() for k in required_key.split("+"))

async def _search_with_engine(
    query, engine, max_results=10, region="wt-wt", safesearch="off", timelimit=None
) -> list[dict]:
    if engine == "auto":
        return await _search_ddgs(query, region, safesearch, timelimit, max_results)
    if engine == "brave":
        return await _search_brave_api(query, max_results, region, safesearch, timelimit)
    if engine == "google":
        return await _search_google_api(query, max_results, region, safesearch, timelimit)
    if engine == "bing":
        return await _search_bing_api(query, max_results, region, safesearch, timelimit)
    if engine == "baidu":
        return await _search_baidu(query, max_results)
    if engine == "yahoo":
        return await _search_yahoo(query, max_results)
    if engine == "searxng":
        return await _search_searxng(query, max_results, region, safesearch, timelimit)
    raise DDGSException(f"Unknown engine '{engine}'. Options: {', '.join(_ENGINE_INFO.keys())}")

def _resolve_engines(requested: str) -> list[str]:
    requested_lower = (requested or "auto").lower().strip()
    env_override = os.environ.get("SEARCH_ENGINES", "").strip()
    if env_override and requested_lower in ("", "auto"):
        engines = []
        for e in re.split(r"[,\s]+", env_override.lower()):
            if e == "all":
                engines.extend(_ENGINE_PRIORITY)
            elif e in _ENGINE_INFO:
                engines.append(e)
            elif e:
                raise SearchError(f"Unknown search engine '{e}'. Options: {', '.join(_ENGINE_INFO.keys())}, all.")
        engines = list(dict.fromkeys(engines))
        if engines:
            return engines
    if requested_lower == "all":
        return _ENGINE_PRIORITY.copy()
    if requested_lower in _ENGINE_INFO:
        return [requested_lower]
    raise SearchError(f"Unknown search engine '{requested}'. Options: {', '.join(_ENGINE_INFO.keys())}, all.")

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
    safesearch = (safesearch or "off").lower().strip()
    if safesearch not in VALID_SAFESEARCH:
        raise SearchError("Invalid safesearch. Use 'on', 'moderate', or 'off'.")
    if timelimit not in VALID_TIMELIMITS:
        raise SearchError("Invalid timelimit. Use 'd', 'w', 'm', 'y', or None.")
    output_format = (output_format or "full").lower().strip()
    if output_format not in VALID_OUTPUT_FORMATS:
        raise SearchError("Invalid output_format. Use 'full', 'compact', or 'links'.")

    # Optimize query for coding context
    search_query = _optimize_query(query, query_category)

    # Domain scoping
    if scoped_domains:
        if engine == "auto":
            search_query = _build_site_query(search_query, scoped_domains)
        else:
            sites = " OR ".join(f"site:{d}" for d in scoped_domains[:3])
            search_query = f"({sites}) {search_query}"

    engines_to_try = _resolve_engines(engine)

    # Cache key includes every request option that can change engine results.
    cache_key = _cache_key(
        label,
        search_query,
        engine,
        engines=",".join(engines_to_try),
        region=region,
        safesearch=safesearch,
        timelimit=timelimit,
        max_results=max_results,
        fmt=output_format,
        provider_config=_provider_config_fingerprint(engines_to_try),
    )
    cached = _cache_get(cache_key)
    if cached:
        if sort_by_authority:
            cached = _sort_by_authority(cached, search_query)
        if session_id:
            _session_add(session_id, query, cached)
        used = engines_to_try[0] if len(engines_to_try) == 1 else "multi"
        engine_label = f"{label} [{used}, cached]"
        elapsed = (time.time() - t_start) * 1000
        if output_format == "compact":
            return _format_compact(query, cached, engine_label, elapsed)
        elif output_format == "links":
            return _format_links(query, cached, engine_label, elapsed)
        return _format_results(query, cached, engine_label, elapsed, show_authority=sort_by_authority)

    # API key check for single engine
    if len(engines_to_try) == 1:
        eng = engines_to_try[0]
        required_key = _ENGINE_INFO.get(eng, {}).get("key")
        if required_key and not _required_keys_present(required_key):
            raise SearchError(_NO_KEY_MSGS.get(eng, f"'{eng}' needs API keys. Use 'auto' instead."))

    # Try engines in parallel (with overall time budget)
    last_errors = []
    all_results = []
    seen = []
    tasks = []

    async def _try_one_engine(eng: str) -> tuple[str, list[dict], str | None]:
        """Search one engine with retries. Returns (engine_name, results, error)."""
        info = _ENGINE_INFO.get(eng, {})
        required_key = info.get("key")
        if required_key and not _required_keys_present(required_key):
            return (eng, [], f"[{eng}] keys not configured, skip")

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
                if attempt < MAX_RETRIES and re.search(r"json|timeout|temporar|5\d\d|connection", str(exc), re.I):
                    await asyncio.sleep(_retry_sleep(attempt))
                    continue
                return (eng, [], f"[{eng}] {exc}")
            except Exception as exc:
                transient = isinstance(exc, (json.JSONDecodeError, httpx.RequestError, ValueError))
                if attempt < MAX_RETRIES and (
                    transient or re.search(r"json|timeout|temporar|5\d\d|connection", str(exc), re.I)
                ):
                    await asyncio.sleep(_retry_sleep(attempt))
                    continue
                return (eng, [], f"[{eng}] {exc}")
        return (eng, [], f"[{eng}] max retries exceeded")

    # Gather valid engine search tasks
    for eng in engines_to_try:
        tasks.append(asyncio.create_task(_try_one_engine(eng)))

    # Run all engines concurrently with overall timeout
    done, pending = await asyncio.wait(
        tasks,
        timeout=SEARCH_OVERALL_TIMEOUT,
        return_when=asyncio.ALL_COMPLETED,
    )
    # Cancel any tasks that didn't finish in time
    for p in pending:
        p.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    # Collect and deduplicate results
    task_order = {task: index for index, task in enumerate(tasks)}
    for task in sorted(done, key=lambda t: task_order[t]):
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
        if sort_by_authority:
            final = _sort_by_authority(final, search_query)

        if output_format == "compact":
            # Snippet-less, URL-focused output
            return _format_compact(query, final, engine_label, elapsed)
        elif output_format == "links":
            # Just URLs
            return _format_links(query, final, engine_label, elapsed)

        return _format_results(query, final, engine_label, elapsed, show_authority=sort_by_authority)

    if last_errors:
        if pending:
            raise SearchError(
                f"Search timed out after {SEARCH_OVERALL_TIMEOUT}s before all engines completed.\n"
                + "\n".join(last_errors[:3])
            )
        raise SearchError(
            "All search engines failed.\n" + "\n".join(last_errors[:3])
            + "\nTry engine='auto' or check API key configuration with list_engines()."
        )

    if pending:
        raise SearchError(f"Search timed out after {SEARCH_OVERALL_TIMEOUT}s with no results.")

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
    timelimit: str | None = None,
) -> str:
    """Search official documentation and API references.
    Scoped to MDN, readthedocs, docs.python.org, learn.microsoft.com, PyPI, npm, etc.

    Best for: API method signatures, config options, language specs, migration guides.

    Args:
        query: Library name, method name, config key, or technology.
        engine: "auto", "brave", "google", "bing", "baidu", "yahoo", "all".
        max_results: 1-50.
        timelimit: "d", "w", "m", "y", or None.
    """
    return await _do_search(
        query=query, label="Docs", max_results=max_results, engine=engine,
        region="wt-wt", safesearch="off", timelimit=timelimit,
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
        query_category="error", session_id=session_id.strip() or None,
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
        query=query, label="Compare", max_results=max_results,
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

    if search_result.startswith("No results found"):
        return search_result

    # Step 2: Parse URLs from search results (match URL: prefix in any format)
    urls = []
    for line in search_result.split("\n"):
        # Match both full format "   URL: https://..." and compact "[title](url)"
        m1 = re.match(r'\s+URL:\s+(https?://\S+)', line)
        if m1:
            urls.append(m1.group(1))
        else:
            m2 = re.findall(r'\]\((https?://.+)\)', line)
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
        code_blocks = re.findall(r'```\s*(?:\w+)?\n(.*?)```', text, re.DOTALL)
        return (i, url, title, text, code_blocks, None)

    fetch_tasks = [_fetch_one(i, url) for i, url in enumerate(urls, 1)]
    fetched_raw = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    # Step 4: Build output with cross-source synthesis
    fetched = []
    all_code = []
    all_keywords = {}
    successful_fetches = 0
    for entry in fetched_raw:
        if isinstance(entry, Exception):
            fetched.append(f"### [failed]\n> Warning: {entry}\n")
            continue
        i, url, title, text, code_blocks, err = entry
        if err:
            fetched.append(f"### [{i}] {url}\n> Warning: {err}\n")
            continue
        successful_fetches += 1
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
    if successful_fetches >= 2:
        # Common terms across sources
        common_terms = [t for t, c in sorted(all_keywords.items(), key=lambda x: -x[1]) if c >= 2][:10]
        if common_terms:
            synthesis_parts.append(
                "**Common topics across sources:** " + " · ".join(common_terms)
            )
        # Code examples found
        all_code = sorted(set(all_code), key=lambda code: (-all_code.count(code), all_code.index(code)))[:6]
        if all_code:
            synthesis_parts.append(
                f"**{len(all_code)} code example(s) extracted** — see source sections below for full context"
            )
        synthesis_parts.append(
            f"**Coverage:** {successful_fetches} sources fetched from {len(urls)} URLs searched"
        )

    elapsed = (time.time() - t_start) * 1000

    parts = [f"## Deep Research: {topic}\n_Searched + fetched {successful_fetches}/{fetch_top} pages in {elapsed:.0f}ms_\n"]

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
    query += " repository"

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
    pkg = _strip_package_spec(package)
    if not pkg:
        raise SearchError("Package name is empty. Please provide a package name.")
    registry_key = _normalize_registry(registry)

    if registry_key == "auto":
        if pkg.startswith("@"):
            registries_to_try = ["npm", "pypi", "crates", "go"]
        elif "/" in pkg and "." in pkg.split("/", 1)[0]:
            registries_to_try = ["go", "npm", "pypi", "crates"]
        else:
            registries_to_try = ["npm", "pypi", "crates", "go"]
    elif registry_key in ("pypi", "npm", "crates", "go"):
        registries_to_try = [registry_key]
    else:
        raise SearchError(f"Unknown registry '{registry}'. Use: pypi, npm, crates, go, or auto.")

    pypi_pkg = quote(pkg.lower(), safe="")
    npm_pkg = quote(pkg.lower(), safe="@")
    crates_pkg = quote(pkg.lower(), safe="")
    go_pkg = quote(pkg, safe="/.-_~")
    REGISTRY_URLS = {
        "pypi": f"https://pypi.org/pypi/{pypi_pkg}/json",
        "npm": f"https://registry.npmjs.org/{npm_pkg}",
        "crates": f"https://crates.io/api/v1/crates/{crates_pkg}",
        "go": f"https://api.pkg.go.dev/packages/{go_pkg}",
    }

    results = []
    errors = []
    deadline = time.time() + FETCH_TIMEOUT
    for reg in registries_to_try:
        remaining = deadline - time.time()
        if remaining <= 0:
            errors.append("registry lookup timed out")
            break
        url = REGISTRY_URLS[reg]
        try:
            async with httpx.AsyncClient(timeout=remaining, trust_env=False) as c:
                resp = await c.get(url, headers={"User-Agent": USER_AGENT})
                if resp.status_code != 200:
                    errors.append(f"{reg}: HTTP {resp.status_code}")
                    continue
                data = resp.json()
            if not isinstance(data, dict):
                errors.append(f"{reg}: registry returned non-object JSON")
                continue

            if reg == "pypi":
                info = data.get("info", {})
                if not isinstance(info, dict):
                    errors.append("pypi: malformed info object")
                    continue
                summary = info.get("summary") or "N/A"
                license_text = info.get("license") or "N/A"
                results.append(
                    f"### PyPI: {info.get('name', pkg)} v{info.get('version', '?')}\n"
                    f"- **License**: {license_text}\n"
                    f"- **Summary**: {summary[:300]}\n"
                    f"- **URL**: {info.get('project_url', f'https://pypi.org/project/{pkg}/')}\n"
                    f"- **Requires Python**: {info.get('requires_python', 'N/A')}\n"
                )
            elif reg == "npm":
                if "versions" in data:
                    latest = (data.get("dist-tags") or {}).get("latest")
                    versions = data.get("versions") if isinstance(data.get("versions"), dict) else {}
                    if latest and latest in versions and isinstance(versions[latest], dict):
                        data = {**versions[latest], "name": data.get("name", pkg)}
                    elif versions:
                        version_key = sorted(versions)[-1]
                        data = {**versions[version_key], "name": data.get("name", pkg)}
                description = str(data.get("description") or "N/A")
                results.append(
                    f"### npm: {data.get('name', pkg)} v{data.get('version', '?')}\n"
                    f"- **License**: {data.get('license') or 'N/A'}\n"
                    f"- **Description**: {description[:300]}\n"
                    f"- **URL**: https://www.npmjs.com/package/{pkg}\n"
                )
            elif reg == "crates":
                crate = data.get("crate", data)
                if not isinstance(crate, dict):
                    errors.append("crates: malformed crate object")
                    continue
                description = crate.get("description") or "N/A"
                version = crate.get("max_stable_version") or crate.get("newest_version") or crate.get("newest_version_num") or "?"
                results.append(
                    f"### crates.io: {crate.get('name', pkg)} v{version}\n"
                    f"- **License**: {crate.get('license') or 'N/A'}\n"
                    f"- **Description**: {description[:300]}\n"
                    f"- **URL**: https://crates.io/crates/{quote(crate.get('name', pkg), safe='')}\n"
                )
            elif reg == "go":
                synopsis = data.get("synopsis") or "N/A"
                display_name = data.get("name") or pkg
                results.append(
                    f"### pkg.go.dev: {display_name} v{data.get('version', '?')}\n"
                    f"- **URL**: https://pkg.go.dev/{quote(display_name, safe='/.-_~')}\n"
                    f"- **Synopsis**: {synopsis[:300]}\n"
                )
            break  # stop after first successful registry
        except (httpx.RequestError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{reg}: {exc}")
            continue

    if results:
        return "\n\n".join(results)

    if registry_key != "auto":
        detail = "; ".join(errors[:3]) if errors else "no registry data returned"
        raise SearchError(f"{registry_key} lookup failed for '{pkg}': {detail}")

    # Fallback to web search
    return await _do_search(
        query=f"{_sanitize_query_terms(pkg)} package version license", label="Package Info [web]",
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
    query = topic.strip() if topic.strip() else "latest programming technology"
    if not re.search(r"\bnews\b", query, re.I):
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
    repo = repo.strip()
    query = query.strip()
    labels = labels.strip()
    state_key = state.strip().lower()
    if not query and not repo and not labels and state_key in ("", "all"):
        raise SearchError("GitHub issue search requires a query or at least one filter.")
    if state_key not in ("open", "closed", "all"):
        raise SearchError("Invalid GitHub issue state. Use open, closed, or all.")
    if repo and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise SearchError("Invalid repo format. Use owner/repo without spaces or extra qualifiers.")

    gh_token = os.environ.get("GITHUB_TOKEN", "").strip()
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": USER_AGENT}
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    max_results = max(1, min(max_results, 30))
    search_parts = [query] if query else []
    if repo:
        search_parts.append(f"repo:{repo}")
    if state_key in ("open", "closed"):
        search_parts.append(f"state:{state_key}")
    if labels:
        for label in [lb.strip() for lb in labels.split(",") if lb.strip()]:
            escaped_label = label.replace('"', '\\"')
            if re.search(r"\s", escaped_label):
                search_parts.append(f'label:"{escaped_label}"')
            else:
                search_parts.append(f"label:{escaped_label}")
    search_q = " ".join(search_parts)

    url = f"https://api.github.com/search/issues?q={quote_plus(search_q)}&per_page={max_results}&sort=updated&order=desc"

    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, trust_env=False) as c:
            resp = await c.get(url, headers=headers)
            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                return (
                    "GitHub API rate limit exceeded (60/hr unauthenticated).\n"
                    "Set GITHUB_TOKEN env var for 5000/hr:\n"
                    "  export GITHUB_TOKEN=ghp_xxxx  # or ghp_xxxx from github.com/settings/tokens"
                )
            if resp.status_code == 422:
                raise SearchError("GitHub search query invalid. Check repo name format (owner/repo).")
            if resp.status_code != 200:
                detail = _clean_one_line(resp.text)[:300]
                raise SearchError(f"GitHub API HTTP {resp.status_code}" + (f": {detail}" if detail else ""))
            data = resp.json()
    except httpx.RequestError as e:
        raise SearchError(f"GitHub API request failed: {e}") from e

    items = data.get("items", [])
    if not items:
        return f"No GitHub issues/PRs found for '{search_q}'."

    lines = [
        f"## GitHub Issues: {_escape_markdown(search_q)}",
        f"_{len(items)} results of ~{data.get('total_count', '?')}_\n",
    ]
    if data.get("incomplete_results"):
        lines.append("> GitHub marked these results incomplete; narrow the query for a more reliable list.\n")
    for i, item in enumerate(items, 1):
        item_type = "PR" if "pull_request" in item else "Issue"
        state = _as_text(item.get("state")).lower()
        state_label = "[open]" if state == "open" else ("[merged]" if state == "merged" else "[closed]")
        labels_str = ""
        label_names = [
            _as_text(lb.get("name") if isinstance(lb, dict) else lb)
            for lb in (item.get("labels") or [])[:3]
        ]
        label_names = [name for name in label_names if name]
        if label_names:
            labels_str = " [" + ", ".join(_escape_markdown(name) for name in label_names) + "]"
        user = item.get("user") or {}
        user_login = _as_text(user.get("login") if isinstance(user, dict) else None, "unknown")
        user_url = _as_text(user.get("html_url") if isinstance(user, dict) else None)
        user_text = f"[{_escape_markdown(user_login)}]({_clean_result_url(user_url)})" if user_url else _escape_markdown(user_login)
        body = _as_text(item.get("body"), "(no description)")[:500].rstrip()
        if body.count("```") % 2:
            body += "\n```"
        lines.append(
            f"### {i}. [{item_type} #{_as_text(item.get('number'), '?')}]"
            f"({_clean_result_url(item.get('html_url'))}) {state_label}{labels_str}\n"
            f"**{_escape_markdown(item.get('title'), '(no title)')}**\n"
            f"> {_escape_markdown(_as_text(item.get('repository_url')).replace('https://api.github.com/repos/', ''))} | "
            f"by {user_text} "
            f"| {_as_text(item.get('comments'), '0')} comments | "
            f"updated {_as_text(item.get('updated_at'), '?')[:10]}\n\n"
            f"{_escape_markdown(body)}\n"
        )
    return "\n".join(lines)


@mcp.tool(annotations=_READONLY_TOOL)
async def search_security(
    package: str,
    ecosystem: str = "auto",
    version: str = "",
    max_results: int = 10,
) -> str:
    """Check for known security vulnerabilities in a package/library.
    Uses the OSV (Open Source Vulnerabilities) API — covers PyPI, npm, crates.io,
    Go, Maven, RubyGems, and more. Essential before adding new dependencies.

    Args:
        package: Package name (e.g. "requests", "lodash", "serde").
        ecosystem: "PyPI", "npm", "crates.io", "Go", "Maven", "RubyGems", or "auto".
        version: Optional installed version to check.
        max_results: Max vulnerabilities to show (1-20).
    """
    pkg = _strip_package_spec(package)
    if not pkg:
        raise SearchError("Package name is empty. Please provide a package name.")
    max_results = max(1, min(max_results, 20))
    ecosystem_key = ecosystem.strip().lower()
    version = version.strip()
    if not version:
        version_match = re.search(r"(?:==|>=|<=|~=|!=|>|<)\s*([A-Za-z0-9_.+\-]+)", package)
        if not version_match and package.strip().startswith("@"):
            version_match = re.match(r"@[^@\s]+/[^@\s]+@([A-Za-z0-9_.+\-]+)$", package.strip())
        elif not version_match and "@" in package.strip():
            version_match = re.search(r"@([A-Za-z0-9_.+\-]+)$", package.strip())
        if version_match:
            version = version_match.group(1)

    # Ecosystem auto-detection and mapping
    ECOSYSTEM_MAP = {
        "pypi": "PyPI",
        "npm": "npm",
        "crates": "crates.io",
        "crates.io": "crates.io",
        "go": "Go",
        "golang": "Go",
        "maven": "Maven",
        "rubygems": "RubyGems",
        "ruby": "RubyGems",
    }
    ecosystems_to_try = []
    if ecosystem_key == "auto":
        if pkg.startswith("@"):
            ecosystems_to_try = ["npm", "PyPI", "crates.io", "Go", "Maven", "RubyGems"]
        elif "/" in pkg and "." in pkg.split("/", 1)[0]:
            ecosystems_to_try = ["Go", "npm", "PyPI", "crates.io", "Maven", "RubyGems"]
        else:
            ecosystems_to_try = ["npm", "PyPI", "crates.io", "Go", "Maven", "RubyGems"]
    elif ecosystem_key in ECOSYSTEM_MAP:
        ecosystems_to_try = [ECOSYSTEM_MAP[ecosystem_key]]
    else:
        raise SearchError(f"Unknown ecosystem '{ecosystem}'. Use: {', '.join(ECOSYSTEM_MAP.keys())}, or auto.")

    def _fixed_versions(vuln: dict) -> str:
        fixed_versions = []
        if vuln.get("fixed"):
            fixed_versions.append(_as_text(vuln.get("fixed")))
        for affected in vuln.get("affected", []) or []:
            if not isinstance(affected, dict):
                continue
            for rng in affected.get("ranges", []) or []:
                if not isinstance(rng, dict):
                    continue
                for event in rng.get("events", []) or []:
                    if isinstance(event, dict) and event.get("fixed"):
                        fixed_versions.append(_as_text(event.get("fixed")))
        fixed_versions = list(dict.fromkeys(fixed_versions))
        return ", ".join(fixed_versions) if fixed_versions else "not specified"

    def _aliases(vuln: dict) -> str:
        aliases = vuln.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        aliases = [_as_text(alias) for alias in aliases if _as_text(alias)]
        aliases = sorted(dict.fromkeys(aliases), key=lambda alias: (not alias.startswith("CVE-"), alias))
        return ", ".join(aliases[:5]) if aliases else "none"

    def _severity(vuln: dict) -> str:
        parts = []
        for sev in vuln.get("severity") or []:
            if not isinstance(sev, dict):
                continue
            stype = _as_text(sev.get("type"))
            score = _as_text(sev.get("score"))
            if stype and score:
                parts.append(f"{stype}: {score}")
        db_sev = vuln.get("database_specific", {})
        if isinstance(db_sev, dict) and db_sev.get("severity"):
            parts.append(f"database: {_as_text(db_sev.get('severity'))}")
        parts = list(dict.fromkeys(parts))
        return " | " + "; ".join(parts) if parts else ""

    auto_mode = ecosystem_key == "auto"
    checked_ecosystems = []
    results = []
    failures = []
    deadline = time.time() + FETCH_TIMEOUT
    for eco in ecosystems_to_try:
        remaining = deadline - time.time()
        if remaining <= 0:
            failures.append("OSV lookup timed out")
            break
        try:
            payload = {"package": {"name": pkg, "ecosystem": eco}}
            if version:
                payload["version"] = version
            async with httpx.AsyncClient(timeout=remaining, trust_env=False) as c:
                resp = await c.post(
                    "https://api.osv.dev/v1/query",
                    json=payload,
                )
                if resp.status_code != 200:
                    failures.append(f"{eco}: HTTP {resp.status_code}")
                    continue
                data = resp.json()
            if not isinstance(data, dict):
                failures.append(f"{eco}: malformed OSV response")
                continue
        except (httpx.RequestError, json.JSONDecodeError, ValueError) as exc:
            failures.append(f"{eco}: {exc}")
            continue

        checked_ecosystems.append(eco)
        vulns = data.get("vulns") or []
        if not isinstance(vulns, list):
            failures.append(f"{eco}: malformed vulnerability list")
            continue
        if not vulns:
            if not auto_mode:
                suffix = f" v{version}" if version else ""
                results.append(f"### {eco}: {pkg}{suffix} - OK: No known vulnerabilities")
                break
            continue

        suffix = f" v{version}" if version else ""
        lines = [f"### {eco}: {pkg}{suffix} - WARNING: {len(vulns)} vulnerabilities\n"]
        for v in vulns[:max_results]:
            if not isinstance(v, dict):
                continue
            vid = _as_text(v.get("id"), "unknown")
            aliases = _aliases(v)
            summary = _as_text(v.get("summary") or v.get("details"), "No description")[:300]
            severity = _severity(v)
            fixed = _fixed_versions(v)
            withdrawn = _as_text(v.get("withdrawn"))
            status = f" | Status: withdrawn {withdrawn[:10]}" if withdrawn else ""
            lines.append(
                f"- **{vid}**{severity}{status}\n"
                f"  {summary}\n"
                f"  Aliases: {aliases or 'none'} | Fixed in: {fixed}\n"
            )
        results.append("\n".join(lines))
        if not auto_mode or results:
            break

    if results:
        return "\n\n".join(results)

    if failures and (not auto_mode or not checked_ecosystems):
        raise SearchError("OSV lookup failed: " + "; ".join(failures[:3]))

    if auto_mode and checked_ecosystems:
        return (
            f"No known vulnerabilities found for '{pkg}' in checked ecosystems: "
            f"{', '.join(checked_ecosystems)}."
        )

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
    level_part = level_q.get(level.lower(), level_q["beginner"])
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
        urls = [_clean_result_url(url).rstrip(").,") for url in re.findall(r'https?://[^\s\n]+', search_result)]
        if not urls:
            return f"No RSS feeds found for '{topic}'."
        last_error = ""
        for candidate in urls[:3]:
            html, err = await _fetch(candidate, FETCH_TIMEOUT)
            if err:
                last_error = err
                continue
            if re.search(r"<(rss|feed)\b", html[:2000], re.I):
                feed_url = candidate
                break
            last_error = "candidate was not an RSS/Atom feed"
        if not feed_url.strip():
            raise SearchError(f"No valid RSS feeds found for '{topic}': {last_error}")

    if "html" not in locals():
        html, err = await _fetch(feed_url.strip(), FETCH_TIMEOUT)
        if err:
            raise SearchError(f"Failed to fetch feed: {err}")

    def _parse_feed():
        try:
            soup = BeautifulSoup(html, "xml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")
        items = soup.find_all("item")
        if not items:
            items = soup.find_all("entry")
        if not items:
            return None, "No RSS/Atom entries found in the feed."

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
            link_candidates = item.find_all("link", recursive=False)
            for link_el in link_candidates:
                rel = link_el.get("rel")
                rel_values = rel if isinstance(rel, list) else [_as_text(rel)] if rel else []
                href = link_el.get("href") or link_el.get_text(strip=True)
                if href and (not rel_values or "alternate" in rel_values):
                    link = href
                    break
            if not link:
                guid_el = item.find("guid") or item.find("id")
                if guid_el:
                    link = guid_el.get_text(strip=True)
            if link:
                link = urljoin(feed_url.strip(), link)
            desc_el = item.find("description") or item.find("summary") or item.find("content")
            if desc_el:
                desc_html = html_lib.unescape(desc_el.decode_contents())
                desc = BeautifulSoup(desc_html, "html.parser").get_text(" ", strip=True)[:300]
            for dtag in ("published", "updated", "pubDate", "dc:date"):
                date_el = item.find(dtag)
                if date_el:
                    date = date_el.get_text(strip=True)[:25]
                    break
            safe_title = _escape_markdown(title, "No title")
            safe_link = _clean_result_url(link)
            lines.append(f"### {i}. [{safe_title}]({safe_link})\n" if safe_link else f"### {i}. {safe_title}\n")
            if date:
                lines.append(f"_{date}_\n")
            if desc:
                lines.append(f"{desc}\n")
        return "\n".join(lines), None

    try:
        result, err = await asyncio.to_thread(_parse_feed)
    except Exception as exc:
        raise SearchError(f"RSS parse failed: {exc}") from exc
    if err:
        raise SearchError(err)
    return result


@mcp.tool(annotations=_READONLY_TOOL)
async def search_crawl(
    urls: str = "",
    base_url: str = "",
    max_pages: int = 10,
    max_length_per_page: int = 3000,
) -> str:
    """Crawl multiple URLs or an entire site, extracting readable content from each page.
    All URLs are fetched in parallel for speed. Use this for comprehensive research
    across multiple sources or when you need to extract content from an entire site.

    Args:
        urls: Comma-separated or newline-separated list of URLs to crawl.
        base_url: Alternatively, a single URL whose linked pages (same domain) to follow.
        max_pages: Max pages to crawl (1-30 for URLs, 1-50 for base_url mode).
        max_length_per_page: Max characters per page (default 3000).
    """
    t_start = time.time()
    target_urls = []
    base_html = None
    robots = None
    normalized_base = base_url.strip()
    if urls.strip():
        max_pages = max(1, min(max_pages, 30))
        target_urls = _split_url_list(urls)[:30]
    elif base_url.strip():
        # Crawl mode: fetch the base page, extract same-domain links
        max_pages = max(1, min(max_pages, 50))
        normalized_base = base_url.strip()
        html, err = await _fetch(normalized_base, FETCH_TIMEOUT)
        if err:
            raise SearchError(f"Failed to fetch base URL: {err}")
        base_html = html

        async def _load_robots():
            parsed = urlparse(normalized_base)
            robots_url = urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
            robots_txt, robots_err = await _fetch(robots_url, min(FETCH_TIMEOUT, 10))
            if robots_err or not robots_txt:
                return None
            parser = robotparser.RobotFileParser(robots_url)
            parser.parse(robots_txt.splitlines())
            return parser

        robots = await _load_robots()

        def _extract_links():
            soup = BeautifulSoup(html, "html.parser")
            base_el = soup.find("base", href=True)
            document_base = urljoin(normalized_base, base_el["href"]) if base_el else normalized_base
            links = []
            seen_links = {_normalize_url_for_compare(normalized_base)}
            for a in soup.find_all("a", href=True):
                href, _fragment = urldefrag(urljoin(document_base, a["href"]))
                if not href.startswith(("http://", "https://")):
                    continue
                if not _same_site(href, normalized_base) or not _is_probably_html_url(href):
                    continue
                normalized = _normalize_url_for_compare(href)
                if normalized not in seen_links:
                    seen_links.add(normalized)
                    links.append(href)
            return links

        target_urls = await asyncio.to_thread(_extract_links)
        target_urls.insert(0, normalized_base)
    else:
        raise SearchError("Provide either 'urls' (comma-separated list) or 'base_url' (site to crawl).")

    max_length_per_page = max(500, min(max_length_per_page, 10000))

    # Parallel fetch all URLs
    semaphore = asyncio.Semaphore(CRAWL_CONCURRENCY)

    async def _crawl_one(url: str):
        async with semaphore:
            if robots and not robots.can_fetch(USER_AGENT, url):
                return (url, None, None, "Blocked by robots.txt")
            page_html = base_html if base_html is not None and _normalize_url_for_compare(url) == _normalize_url_for_compare(normalized_base) else None
            if page_html is None:
                page_html, err = await _fetch(url, FETCH_TIMEOUT)
                if err:
                    return (url, None, None, err)
            try:
                text = await _extract_text(page_html)
            except Exception as exc:
                return (url, None, None, f"Parser error: {exc}")
            title_match = re.search(r"<title[^>]*>(.*?)</title>", page_html, re.IGNORECASE | re.DOTALL)
            title = html_lib.unescape(title_match.group(1).strip()) if title_match else url[:80]
            excerpt = text[:max_length_per_page] if text else ""
            if len(text or "") > max_length_per_page:
                excerpt += f"\n\n> [... {len(text)} chars total ...]"
            return (url, title, excerpt, None)

    tasks = [_crawl_one(u) for u in target_urls[:max_pages]]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    results = []
    for index, item in enumerate(raw_results):
        if isinstance(item, Exception):
            url = target_urls[index] if index < len(target_urls) else "(unknown URL)"
            results.append((url, None, None, str(item)))
        else:
            results.append(item)

    lines = [f"## Crawl Results: {len(results)} pages\n"]
    success = 0
    for url, title, content, err in results:
        if err:
            lines.append(f"### [failed] {url}\n> {err}\n")
        else:
            success += 1
            lines.append(f"### [{success}] {title}\n> {url}\n\n{content}\n")

    elapsed = (time.time() - t_start) * 1000
    lines.insert(1, f"_{success}/{len(results)} pages fetched in {elapsed:.0f}ms_\n")
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
    max_length = max(0, max_length)
    html, err = await _fetch(url, timeout)
    if err:
        raise SearchError(err)

    stripped = html.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            text = json.dumps(json.loads(stripped), indent=2, ensure_ascii=False)
        except json.JSONDecodeError as exc:
            raise SearchError(f"JSON parse failed: {exc}") from exc
    else:
        try:
            text = await _extract_text(html)
        except Exception as exc:
            raise SearchError(f"Page parse failed: {exc}") from exc
    if not text:
        raise SearchError("No readable text found on this page.")

    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else "Untitled"

    prefix = f"# {title}\n> {url}\n\n"

    return _truncate_output(prefix, text, max_length)


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
    max_length = max(0, max_length)
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

    try:
        code_blocks = await asyncio.to_thread(_parse_code_blocks)
    except Exception as exc:
        raise SearchError(f"Code parse failed: {exc}") from exc

    if not code_blocks:
        raise SearchError("No code blocks found on this page.")

    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else "Untitled"

    header = f"# {title} - Code Blocks\n> {url}\n\n"
    header += f"_{len(code_blocks)} code block(s) found_\n\n"
    body = "\n\n".join(code_blocks)
    return _truncate_output(header, body, max_length, close_fence=True)


# ===========================================================================
# Meta tools
# ===========================================================================

@mcp.tool(annotations=_MUTATING_TOOL)
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
    brave_status = "configured" if _required_keys_present(ENV_BRAVE_KEY) else "not set"
    google_status = "configured" if _required_keys_present(f"{ENV_GOOGLE_KEY}+{ENV_GOOGLE_CX}") else "not set"
    bing_status = "configured" if _required_keys_present(ENV_BING_KEY) else "not set"
    searxng_status = "configured" if _required_keys_present(ENV_SEARXNG_URL) else "not set"

    return f"""
## Available Search Engines

| Engine   | Backend                           | Free Tier           | Status      |
|----------|-----------------------------------|---------------------|-------------|
| auto     | DuckDuckGo (Bing+Yahoo+Brave)     | **Unlimited, free** | always      |
| brave    | Brave Search (independent index)  | 2000/mo             | {brave_status} |
| google   | Google Custom Search              | 100/day             | {google_status} |
| bing     | Bing Web Search v7                | 1000/mo             | {bing_status} |
| baidu    | Baidu scraping                    | Unlimited (China)   | always      |
| yahoo    | Yahoo via DDGS                    | Unlimited           | always      |
| searxng  | SearXNG (self-hosted metasearch)  | **Unlimited**       | {searxng_status} |

## Coding-Agent Tools ({_tool_count()} total)

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
| `search_crawl` | **NEW** — Batch crawl multiple URLs or entire sites in parallel |
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

def _startup_diagnostics():
    """Print startup info to stderr (not stdout — stdout is for JSON-RPC)."""
    import sys as _sys
    py_ver = f"{_sys.version_info.major}.{_sys.version_info.minor}.{_sys.version_info.micro}"
    engines_configured = [
        e for e, info in _ENGINE_INFO.items()
        if _required_keys_present(info.get("key"))
    ]
    tool_count = _tool_count()
    _sys.stderr.write(f"[codingWebSearch v0.6.0] Python {py_ver} | "
                      f"{len(engines_configured)}/{len(_ENGINE_INFO)} engines | "
                      f"{tool_count} tools\n")
    optional_keys = {ENV_BRAVE_KEY, ENV_GOOGLE_KEY, ENV_GOOGLE_CX, ENV_BING_KEY, ENV_SEARXNG_URL}
    missing = [k for k in optional_keys if not os.environ.get(k, "").strip()]
    if missing:
        _sys.stderr.write(f"[codingWebSearch] Optional: set {', '.join(missing)} for more engines\n")
    _sys.stderr.flush()


def main():
    _startup_diagnostics()
    mcp.run()


if __name__ == "__main__":
    main()
