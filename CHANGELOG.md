# Changelog

All notable changes to this project will be documented in this file.

## [0.3.0] — 2026-04-29

### Added

- **`search_package`** — Direct package registry lookup via PyPI, npm, crates.io, and
  pkg.go.dev APIs. Auto-detects registry. Falls back to web search.
- **`search_news`** — Tech news search scoped to HN, TechCrunch, ArsTechnica, The Verge,
  dev.to, and more. Time-filtered (d/w/m/y). Authority-ranked.
- **Parallel engine execution** — Multi-engine searches now run all engines
  concurrently via `asyncio.wait` instead of sequentially. 60s overall timeout.
- **Result relevance scoring** — Keyword overlap between query and result title/snippet
  combined with authority and freshness into 3D ranking.
- **Error code detection** — `search_error` now recognizes 10+ patterns: MongoDB E-codes,
  Node.js ERR_ codes, Python/JS/Java/Go error types, HTTP codes, Windows hex codes.
- **Tool/resource annotations** — All 16 tools declare `readOnlyHint=True`; all 5
  resources declare `audience=["assistant"], priority=0.7`.
- **References documentation** — `references.md` with competitive analysis of 10+
  MCP web search projects and feature gap analysis.
- **DevLog** — `devlog.md` with detailed version history.

### Changed

- Tool count: 14→16 tools
- README completely rewritten with new tool tables, feature descriptions
- pyproject.toml: version 0.2.0→0.3.0, updated description

### Fixed (v0.2.1 backport)

- DDGS event loop blocking (wrapped in `asyncio.to_thread`)
- Session memory leak (fallback pruning by oldest activity)
- Cache key missing `max_results` (different page sizes share cache)
- Compare word-boundary in `_optimize_query` (regex instead of replace)
- Engine name case-sensitive matching
- BeautifulSoup CPU blocking (all parsing runs in thread pool)
- Baidu redirect URL extraction (checks `data-url` and `mu` attributes)
- Multi-engine missing overall timeout
- MCP protocol: `isError` compliance, resource MIME types, server name

## [0.2.0] — 2026-04-29

### Added

- **10 new coding-agent tools**: `search_error`, `search_api`, `search_compare`, `search_deep`, `search_similar_repos`, `web_fetch_code`, `search_session`, `list_engines`, plus enhanced `web_search` and `search_*` variants
- **Multi-engine support**: DuckDuckGo (default, free), Brave Search API (free tier), Google CSE API, Bing Web Search API, Baidu scraping, Yahoo via DDGS
- **Engine fallback chain**: `SEARCH_ENGINES=brave,auto` env var for automatic failover
- **3 output formats**: `full` (title+URL+snippet), `compact` (markdown links), `links` (URL only)
- **Source authority ranking**: results tagged `[official]` or `[trusted]` based on domain authority scores
- **Freshness scoring**: year detection and time-indicator analysis for ranking boost
- **Query optimization**: automatic noise stripping for error messages (hex addresses, timestamps, file paths)
- **Session tracking**: multi-turn search context via `search_session` tool
- **Smart deduplication**: URL exact match + title similarity (85%) across multi-engine results
- **Result caching**: 5-minute TTL with automatic pruning
- **Auto-retry**: exponential backoff (2s→4s→8s) on rate limits and transient failures
- **5 MCP Resources**: domain lists and authority scores exposed as `search://` resources
- **Comprehensive error handling**: empty query checks, invalid URL blocking (`file://`, `javascript:`), missing API key messages

### Changed

- Renamed project from `websearch-mcp` to `codingWebSearch`
- Switched search backend from `duckduckgo_search` to `ddgs` (v9 API)
- Restructured codebase with clear sections: Constants, Cache, Helpers, Engines, Unified Search Core, Tools, Resources
- Improved `web_fetch` code block preservation with language detection
- Authority tags use plain text (`[official]`, `[trusted]`) for terminal compatibility

### Fixed

- Removed unused `functools.lru_cache` import
- Moved `datetime` import to module level
- Pre-computed `_SORTED_AUTHORITY` for O(1) lookup
- Added title truncation (150 chars) to prevent excessively long output
- Added session memory limit (50 sessions, 1h idle TTL)
- `search_deep` URL extraction now supports both full and compact formats

## [0.1.0] — 2026-04-28

### Added

- Initial MCP server with `web_search` and `web_fetch` tools
- DuckDuckGo search backend via `ddgs` library
- HTML content extraction with code block preservation
- URL validation and security filtering
- Basic error handling for search and fetch operations
