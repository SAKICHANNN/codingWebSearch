# DevLog

## v0.6.0 — 2026-04-29 (current)

### New Features

- **`search_crawl`** — Batch URL crawler. Two modes: (1) comma-separated URL list,
  (2) base URL mode that follows same-domain links. Parallel fetch via asyncio.gather.
  Extracts readable content with title and excerpt from each page.

- **Startup diagnostics** — `_startup_diagnostics()` runs on server start, printing
  engine status, tool count, Python version, and missing optional API keys to stderr.

- **Expanded domain coverage**:
  - Docs: +15 domains (AWS, GCP, Azure, PostgreSQL, MySQL, MongoDB, Rust, Go, Ruby)
  - Papers: +5 domains (ACL Anthology, JMLR, NeurIPS, MLR Press, ACM DL Next)
  - Code forges: +3 domains (sr.ht, Launchpad, Salsa)
  - New: Advisory domains (NVD, CVE, OSV, Snyk, RustSec, GitHub Advisories)

- **Expanded authority scores**: +12 entries for cloud providers, security sites,
  framework docs.

- **Documentation**:
  - `TROUBLESHOOTING.md` — 12+ common issues with solutions and diagnostic commands
  - `CONFIGURING.md` — Complete setup guide with engine config, Claude Desktop config
    examples, performance tuning, rate limit tables

- **Pre-commit hooks**: `.pre-commit-config.yaml` with flake8, black, and general
  file checks.

### Changed

- Tool count: 21 (was 20)
- Authority scores: 35 entries (was 23)

## v0.5.0 — 2026-04-29

### New Features

- **SearXNG engine** (7th engine) — Self-hosted metasearch via `SEARXNG_URL` env var.
  Privacy-respecting alternative that aggregates dozens of search sources. JSON API at
  `{instance}/search?format=json`.

- **`search_rss`** — RSS/Atom feed reader. Accepts direct feed URL or topic search.
  Auto-detects RSS 2.0 and Atom formats. Parses title, link, description, and date
  for each entry. Uses BeautifulSoup for robust XML parsing.

- **GitHub Actions CI/CD** — `.github/workflows/ci.yml` runs flake8 lint, syntax check,
  and tool count verification on Python 3.10–3.13. `docker-publish.yml` builds and
  pushes Docker images to ghcr.io on tag push.

- **Rate limit tracking** — `_check_rate_limit()` tracks per-engine API call frequency
  with a 60-second sliding window. Prevents silent failures by warning before hitting
  Brave/Google/Bing rate limits.

### Changed

- Tool count: 20 (was 19)
- Search engines: 7 (was 6)
- `SearchError` now supports `recovery` hint for actionable error guidance

## v0.4.0 — 2026-04-29

### New Tools (19 total)

- **`search_github_issues`** — Search GitHub issues/PRs across repos via REST API.
  No API key required for public repos (60 req/hr). Filter by repo, state, labels.
  Returns issue number, title, status, labels, author, comment count, body excerpt.

- **`search_security`** — Check packages for known CVEs via the OSV (Open Source
  Vulnerabilities) API. Covers PyPI, npm, crates.io, Go, Maven, RubyGems. Auto-detects
  ecosystem. Essential for dependency security review before adding new packages.

- **`search_tutorial`** — Find getting-started tutorials by technology and skill level
  (beginner/intermediate/advanced). Scoped to tutorial platforms and official docs
  quickstart pages. Authority-ranked.

### Enhanced

- **`search_deep`** — Major upgrade:
  - Parallel content fetching via `asyncio.gather` (was sequential)
  - Cross-source synthesis: common topic extraction, code example collection, coverage
  - Extracted code blocks shown in dedicated "Code Examples" section
  - Term frequency analysis across sources to identify common themes

### Infrastructure

- **Docker** — `Dockerfile` (Python 3.12-slim) and `docker-compose.yml`. Proper OCI
  labels, stdin_open for MCP stdio transport. Environment variable pass-through for
  API keys.
- **Tool count**: 19 tools (was 16)

### Documentation

- README: Docker section, 19-tool table, security features, deep research enhancements
- CHANGELOG: v0.4.0 entry
- pyproject.toml: version 0.4.0

## v0.3.0 — 2026-04-29

### New Tools

- **`search_package`** — Direct package registry lookup via PyPI, npm, crates.io, and
  pkg.go.dev APIs. Auto-detects registry or takes explicit selection. Falls back to web
  search if direct API fails. Returns version, license, description, and project URL.

- **`search_news`** — Tech news search scoped to HN, TechCrunch, ArsTechnica, The Verge,
  dev.to, The Register, ZDNet, The New Stack, InfoQ, LWN. Supports time filtering
  (d/w/m/y). Ranked by source authority.

### Performance & Architecture

- **Parallel engine execution** — Multi-engine searches now run all engines concurrently
  via `asyncio.wait` instead of sequentially. Dramatically faster when using
  `engine="all"` or multi-engine fallback chains. Overall timeout (60s) enforced with
  automatic cancellation of slow engines.

- **Result relevance scoring** — New `_relevance_score()` function measures keyword
  overlap between query terms and result title/snippet. Combined with existing authority
  and freshness scores in `_sort_by_authority()` for intelligent ranking that considers
  all three dimensions: trust, recency, and topical match.

### Enhanced Tools

- **`search_error`** — Now detects 10+ error code patterns across languages
  (MongoDB E-codes, Node.js ERR_ codes, Python/JS/Java/Go error types, HTTP status
  codes, Windows hex codes, Oracle/SQL codes). Appends detected language/framework
  hints to improve search precision.

### Compliance & Polish

- Updated `list_engines` tool to show all 16 tools with descriptions
- Tool count in `list_engines` matches actual count
- All 16 tools have `ToolAnnotations(readOnlyHint=True, destructiveHint=False)`
- All 5 resources have `ResourceAnnotations(audience=["assistant"], priority=0.7)`

---

## v0.2.1 — 2026-04-29

### MCP Protocol Compliance

- **Server name**: `FastMCP("websearch")` → `FastMCP("codingWebSearch")` to match
  project name in `pyproject.toml` and `README.md`
- **Error protocol**: Added `SearchError` exception class. Search/fetch failures now
  raise exceptions, which FastMCP converts to `isError: true` tool results per the
  MCP spec. This lets clients distinguish errors from empty results.
- **Resource MIME types**: All 5 `@mcp.resource()` entries now declare
  `mime_type="application/json"` so clients can parse responses correctly.
- **Tool annotations**: All tools declare `readOnlyHint=True`,
  `destructiveHint=False`, `idempotentHint=True` via shared `_READONLY_TOOL` constant.

### Bug Fixes

- **DDGS event loop blocking**: `_search_ddgs()` was synchronous, calling DDGS's
  blocking HTTP library directly from async context. Wrapped in
  `asyncio.to_thread()` with an inner `_sync_search()` closure. All callers
  (`_search_with_engine`, `_search_yahoo`) now use `await`.

- **Session memory leak**: When 50+ sessions had recent activity, the stale-only
  pruning never fired. Added fallback: if still over limit after stale pruning,
  remove the oldest sessions by last-activity timestamp.

- **Cache key missing max_results**: Different `max_results` values for the same
  query shared cached results via identical cache keys. Added `max_results` to
  the cache key hash, so `max_results=5` and `max_results=20` get independent
  cache entries.

- **Compare word-boundary bug**: `_optimize_query()` used `.replace(" compare ", " vs ")`
  which missed "compare" at query start/end. Changed to `re.sub(r'\bcompare\b', ...)`
  with `flags=re.IGNORECASE`.

- **Engine name case sensitivity**: `_resolve_engines()` did exact-case matching
  against `_ENGINE_INFO`. Changed to case-insensitive lookup with `requested.lower()`.

- **BeautifulSoup CPU blocking**: `_extract_text()`, `web_fetch_code` parsing, and
  `_search_baidu` parsing were synchronous CPU-bound operations blocking the async
  event loop. All wrapped in `asyncio.to_thread()`.

- **Baidu redirect URLs**: Results often contained `baidu.com/link?url=...` redirect
  wrappers instead of real target URLs. Now checks `data-url` and `mu` attributes
  first, falling back to raw `href`.

- **Multi-engine timeout**: No overall time budget for `engine="all"`. Added
  `SEARCH_OVERALL_TIMEOUT=60s` constant with per-engine time budget check.

---

## v0.2.0 — 2026-04-29 (Initial Release)

### Core Architecture

- 14 tools, 5 resources, 6 search engines
- FastMCP-based MCP server (`server.py`, ~1246 lines)
- Search backends: DuckDuckGo (free), Brave, Google, Bing, Baidu (scraping), Yahoo
- Domain-scoped search categories: code, docs, papers, repos
- Source authority ranking with pre-computed domain scores
- Content freshness scoring from year mentions and time indicators

### Tools (Initial 14)

- `web_search`, `search_code`, `search_docs`, `search_paper`, `search_github`
- `search_error` (with noise stripping), `search_api`, `search_compare`
- `search_deep` (search + fetch), `search_similar_repos`
- `web_fetch`, `web_fetch_code`
- `search_session`, `list_engines`

### Infrastructure

- 5-min TTL result caching (200 entry limit, auto-pruning)
- Exponential backoff retry (2s→4s→8s, max 2 retries)
- Multi-engine deduplication (URL exact + title similarity 85%)
- Session tracking (50 sessions, 1h idle TTL, 20 queries each)
- 3 output formats: full, compact, links
- Engine fallback chain via `SEARCH_ENGINES` env var
- URL security validation (blocks `file://`, `javascript:`)
