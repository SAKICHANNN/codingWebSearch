# codingWebSearch

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-1.0%2B-purple)](https://modelcontextprotocol.io/)
[![Docker](https://img.shields.io/badge/Docker-supported-blue)](https://docs.docker.com/)

**The ultimate open-source, locally-deployed web search MCP server for coding agents.**
21 specialized tools, 7 search engines, SearXNG, batch crawling, RSS, CVE checks, Docker, CI/CD.
Designed for programmers, researchers, and engineers. No API key required by default.

---

## Quick Start

### pip Install

```bash
git clone https://github.com/SAKICHANNN/codingWebSearch.git
cd codingWebSearch
pip install -r requirements.txt
```

### Docker

```bash
docker build -t codingwebsearch .
docker run -i --rm codingwebsearch
# Or: docker-compose up
```

### Claude Desktop Config

```json
{
  "mcpServers": {
    "codingWebSearch": {
      "command": "python",
      "args": ["path/to/codingWebSearch/server.py"]
    }
  }
}
```

With Brave Search (recommended free upgrade):

```json
{
  "mcpServers": {
    "codingWebSearch": {
      "command": "python",
      "args": ["path/to/codingWebSearch/server.py"],
      "env": { "BRAVE_SEARCH_API_KEY": "your-key-from-brave.com/search/api/" }
    }
  }
}
```

---

## Tools Reference (21 total)

### Core Search

| Tool | Scope |
|------|-------|
| `web_search` | General web search, 3 output formats, session tracking |
| `search_code` | Stack Overflow, GitHub, Reddit, dev.to, HN |
| `search_docs` | MDN, readthedocs, PyPI, npm, MS Learn, docs sites |
| `search_paper` | arXiv, ACM DL, IEEE, Semantic Scholar, Usenix |
| `search_github` | GitHub, GitLab, Bitbucket, Gitee, SourceForge |

### Coding Agent Tools

| Tool | Purpose |
|------|---------|
| `search_error` | Debug errors — auto-strips noise, detects 10+ error code patterns |
| `search_api` | API method signatures, parameters, return types |
| `search_compare` | Side-by-side tech comparison with aspect filtering |
| `search_deep` | Search + parallel fetch + cross-source synthesis + code extraction |
| `search_similar_repos` | Find repos by feature description and language |
| `search_package` | Direct registry lookup: PyPI, npm, crates.io, pkg.go.dev |
| `search_github_issues` | Search issues/PRs across GitHub — filter by repo, state, labels |
| `search_security` | Check CVEs via OSV API — PyPI, npm, crates, Go, Maven, RubyGems |

### Content & News (5 tools)

| Tool | Purpose |
|------|---------|
| `search_news` | Tech news from HN, TechCrunch, ArsTechnica, dev.to (time-filtered) |
| `search_tutorial` | Find tutorials by tech and skill level (beginner to advanced) |
| `search_rss` | Fetch/parse RSS/Atom feeds by URL or topic search |
| `web_fetch` | Extract readable content from any URL, preserves code blocks |
| `search_crawl` | **NEW** — Batch crawl multiple URLs or entire site in parallel |
| `web_fetch_code` | Extract only code blocks from a URL with language detection |

### Utility

| Tool | Purpose |
|------|---------|
| `search_session` | Multi-turn search context across queries |
| `list_engines` | Engine status, API key config, setup instructions |

---

## Key Features

### Search Intelligence
- **3D result ranking** — authority + freshness + relevance scoring
- **Source authority** — official docs (1.0) > Stack Overflow (0.85) > blogs (0.5)
- **Parallel engine execution** — all engines run concurrently via `asyncio.wait`
- **Smart dedup** — URL exact + title similarity (85%) across multi-engine results

### Security
- **CVE checking** — `search_security` queries OSV API before you add dependencies
- **No API key required** — works out of the box with DuckDuckGo
- **URL validation** — blocks `file://`, `javascript:`, and other dangerous protocols

### Debugging
- **Error code detection** — MongoDB, Node.js, Python, JS, Java, Go, C/C++, HTTP, SQL
- **Noise stripping** — removes timestamps, hex addresses, file paths, stack frames

### Deep Research
- **Cross-source synthesis** — common topic extraction, code collection, coverage metrics
- **Parallel content fetching** — all source URLs fetched concurrently

### Performance
- **5-min result caching** — no repeated calls for identical queries
- **Auto-retry** — exponential backoff (2s→4s→8s)
- **Async-safe** — all CPU parsing in thread pool, zero event loop blocking
- **MCP compliant** — `isError: true`, tool/resource annotations, JSON MIME types

---

## Output Formats

| Format | Output |
|--------|--------|
| `full` (default) | Title + URL + snippet + `[official]`/`[trusted]` tags |
| `compact` | `[title](url)` markdown links |
| `links` | URL only |

---

## Search Engines

| Engine | Backend | Free Tier | API Key |
|--------|---------|-----------|---------|
| `auto` | DuckDuckGo (Bing+Yahoo+Brave+Yandex) | **Unlimited** | None |
| `brave` | Brave Search API (independent index) | 2000/mo | `BRAVE_SEARCH_API_KEY` |
| `google` | Google Custom Search API | 100/day | `GOOGLE_API_KEY` + `GOOGLE_CSE_ID` |
| `bing` | Bing Web Search API v7 | 1000/mo | `BING_SEARCH_API_KEY` |
| `baidu` | Baidu.com scraping | Unlimited | None |
| `yahoo` | Yahoo via DuckDuckGo | Unlimited | None |
| `searxng` | SearXNG (self-hosted metasearch) | **Unlimited** | `SEARXNG_URL` |

Set `engine="all"` for parallel multi-engine search with auto-deduplication.
Set `SEARCH_ENGINES=brave,auto` env var for custom fallback chains.

---

## MCP Resources

| URI | Content |
|-----|---------|
| `search://domains/code` | Code Q&A domain list |
| `search://domains/docs` | Documentation domain list |
| `search://domains/paper` | Academic paper domain list |
| `search://domains/github` | Repository domain list |
| `search://authority` | Source authority scores |

---

## Docker

```bash
docker build -t codingwebsearch .
docker run -i --rm codingwebsearch
docker run -i --rm -e BRAVE_SEARCH_API_KEY=key codingwebsearch
docker-compose up
```

---

## Requirements

- Python 3.10+
- [`mcp`](https://pypi.org/project/mcp/) >= 1.27.0
- [`ddgs`](https://pypi.org/project/ddgs/) >= 9.14.0
- [`httpx`](https://pypi.org/project/httpx/) >= 0.28.0
- [`beautifulsoup4`](https://pypi.org/project/beautifulsoup4/) >= 4.14.0

---

## Documentation

- [CONFIGURING.md](CONFIGURING.md) — engine setup, API keys, Claude Desktop config
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — common issues and diagnostic commands
- [CHANGELOG.md](CHANGELOG.md) — version history
- [devlog.md](devlog.md) — detailed development log
- [references.md](references.md) — competitive analysis and research sources

---

## License

MIT — see [LICENSE](LICENSE) for details.
