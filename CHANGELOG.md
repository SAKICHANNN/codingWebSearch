# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Fixed

- URL fetches now block loopback, private, link-local, reserved, multicast, and
  DNS-resolved private addresses, and redirects are followed only after each
  target URL is revalidated.
- Error-query optimization now preserves the original query when stripping stack
  trace noise would otherwise produce an empty search.
- Yahoo result relabeling now copies result dictionaries instead of mutating
  DDGS results in place; cache reads and writes also use defensive copies.
- Search core now wraps per-engine coroutines with `asyncio.create_task()` before
  passing them to `asyncio.wait`, fixing runtime failures on modern Python.
- Engine selection now treats `engine="ALL"` the same as `engine="all"`.
- Runtime status and error strings avoid emoji so Windows GBK consoles can print
  diagnostics without `UnicodeEncodeError`.
- `web_fetch_code` truncates extracted code content without dropping the response
  header or leaving unbalanced markdown code fences.
- `search_package` registry parsing now handles nullable metadata explicitly and
  only suppresses network/JSON failures during registry fallback.
- Search cache keys now include `safesearch`, `timelimit`, and resolved engine
  chain, preventing stale results across different search filters.
- Cached search results now update `search_session` history when a `session_id`
  is provided.
- Google engine configuration checks now require both `GOOGLE_API_KEY` and
  `GOOGLE_CSE_ID` before reporting the engine as usable.
- Startup diagnostics now report the actual registered tool count and complete
  engine key-set status.
- `search_crawl` elapsed time now includes the real fetch and extraction work.
- Docker metadata now reflects the current 21-tool, 7-engine release surface.
- `search_security(ecosystem="auto")` now checks all supported ecosystems before
  reporting no vulnerabilities, avoiding false PyPI-only OK results for npm/Go/etc.
- `search_security` now extracts fixed versions from OSV affected ranges and
  handles nullable vulnerability fields.
- `search_github_issues` now expands comma-separated labels into valid GitHub
  search filters and handles issues with null bodies.
- Search result formatting and deduplication now tolerate nullable `title`,
  `href`, `body`, and `engine` fields from upstream providers.
- Package/security lookup now rejects empty package names and treats registry or
  ecosystem selectors case-insensitively.
- Scoped npm package names are URL-encoded correctly for direct registry lookup.
- GitHub issue `state` and tutorial `level` selectors are now case-insensitive.
- `web_fetch` and `web_fetch_code` normalize negative `max_length` values to zero
  before truncating output.
- Troubleshooting diagnostics now use ASCII status text instead of emoji markers.
- GitHub issue rendering now tolerates nullable metadata such as missing user,
  title, labels, state, URL, comment count, and update timestamp.

## [0.6.0] — 2026-04-29

### Added

- **`search_crawl`** — Batch URL crawler. Crawl a list of URLs or an entire site
  (follow same-domain links). All pages fetched in parallel via `asyncio.gather`.
  Extracts readable content from each page with title and excerpt.
- **Startup diagnostics** — Prints engine status, tool count, and missing optional
  API keys to stderr at startup. Helps users verify configuration.
- **Expanded domain lists** — Added 15+ docs domains (AWS, GCP, Azure, PostgreSQL,
  MySQL, MongoDB), security advisory domains (NVD, CVE, OSV, Snyk, RustSec), 5
  paper domains (ACL, JMLR, NeurIPS), 3 code forge domains.
- **Expanded authority scores** — Added scores for cloud providers, security sites,
  and additional language/framework docs.
- **Pre-commit config** — `.pre-commit-config.yaml` with trailing-whitespace,
  end-of-file-fixer, check-yaml, check-json, check-ast, flake8, black.
- **Troubleshooting guide** — `TROUBLESHOOTING.md` with solutions for 12+ common
  issues, diagnostic commands, and rate limit info.
- **Configuration guide** — `CONFIGURING.md` with engine setup, Claude Desktop config
  examples, performance tuning, and rate limit tables.

### Changed

- Tool count: 20→21 tools
- Domain lists: docs (10→25), papers (8→13), repos (6→9), added advisory (6)
- Authority scores: 23→35 entries
- README updated with new tools and docs references

## [0.5.0] — 2026-04-29

### Added

- **SearXNG search engine** — 7th engine. Self-hosted, privacy-respecting metasearch.
  Set `SEARXNG_URL=http://localhost:8080` to use. Aggregates dozens of sources.
- **`search_rss`** — Fetch and parse RSS/Atom feeds by URL or topic search.
  Auto-detects RSS 2.0 and Atom formats. Returns title, link, description, date.
- **GitHub Actions CI/CD** — `.github/workflows/ci.yml` (lint, syntax, tool count on
  Python 3.10-3.13) and `docker-publish.yml` (ghcr.io on tag push).
- **Rate limit tracking** — Per-engine rate limit tracking with `_check_rate_limit()`.
  Warns before hitting limits on Brave, Google, Bing APIs.

### Changed

- Tool count: 19→20 tools
- Search engines: 6→7 (added SearXNG)
- `SearchError` now carries optional `recovery` hint for actionable guidance

## [0.4.0] — 2026-04-29

### Added

- **`search_github_issues`** — Search GitHub issues and PRs across repos via GitHub REST
  API. Filter by repo, state, labels. No API key required for public repos.
- **`search_security`** — Check for known CVEs in packages via the OSV API. Covers
  PyPI, npm, crates.io, Go, Maven, RubyGems. Auto-detects ecosystem.
- **`search_tutorial`** — Find getting-started tutorials for any technology. Scoped to
  tutorial platforms and official docs. Skill-level filtering.
- **Enhanced `search_deep`** — Parallel multi-source fetching via `asyncio.gather`.
  Cross-source synthesis: common topic extraction, code example collection, source
  coverage metrics. Extracted code blocks shown in dedicated section.
- **Docker support** — `Dockerfile` and `docker-compose.yml` for easy deployment.
  Python 3.12-slim base image, proper labels, stdin_open for MCP stdio transport.

### Changed

- Tool count: 16→19 tools
- `search_deep`: Content fetching now parallel (was sequential)
- `list_engines`: Updated tool table with all 19 tools

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
