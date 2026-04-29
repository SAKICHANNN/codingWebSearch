# codingWebSearch

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-1.0%2B-purple)](https://modelcontextprotocol.io/)

**The ultimate open-source, locally-deployed web search MCP server for coding agents.**
16 specialized tools, 6 search engines, parallel execution. Designed for programmers,
researchers, and engineers who need reliable, high-signal search integrated into their
AI coding workflow. No API key required by default.

---

## Quick Start

### Install

```bash
git clone https://github.com/SAKICHANNN/codingWebSearch.git
cd codingWebSearch
pip install -r requirements.txt
```

### Claude Desktop

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

### Optional: Brave Search API (free tier, better results)

```json
{
  "mcpServers": {
    "codingWebSearch": {
      "command": "python",
      "args": ["path/to/codingWebSearch/server.py"],
      "env": {
        "BRAVE_SEARCH_API_KEY": "your-key-from-brave.com/search/api/"
      }
    }
  }
}
```

---

## Tools Reference

### Core Search (5 tools)

| Tool | Purpose | Scoped To |
|------|---------|-----------|
| `web_search` | General search, 3 output formats, session tracking | Whole web |
| `search_code` | Programming Q&A and code examples | Stack Overflow, GitHub, Reddit, dev.to, HN |
| `search_docs` | API references and official documentation | MDN, readthedocs, PyPI, npm, MS Learn, docs sites |
| `search_paper` | CS research papers and academic references | arXiv, ACM DL, IEEE, Semantic Scholar, Usenix |
| `search_github` | Open-source repository search | GitHub, GitLab, Bitbucket, Gitee, SourceForge |

### Coding Agent Tools (6 tools)

| Tool | Purpose | Example |
|------|---------|---------|
| `search_error` | Debug errors, auto-detects error codes + strips noise | `search_error("E11000 duplicate key", language="MongoDB")` |
| `search_api` | API method signatures and parameter docs | `search_api("FastAPI", "Depends")` |
| `search_compare` | Side-by-side technology comparison | `search_compare("Rust", "Go", aspect="performance")` |
| `search_deep` | Search + auto-fetch top results' content | `search_deep("Python async patterns", fetch_top=3)` |
| `search_similar_repos` | Find repos by feature description | `search_similar_repos("async HTTP client", language="Rust")` |
| `search_package` | **NEW** — Direct package registry lookup (PyPI/npm/crates/go) | `search_package("serde", registry="crates")` |

### Content & News (3 tools)

| Tool | Purpose |
|------|---------|
| `search_news` | **NEW** — Tech news: HN, TechCrunch, ArsTechnica, dev.to. Time-filtered (d/w/m/y). |
| `web_fetch` | Extract readable content from any URL, preserves code blocks |
| `web_fetch_code` | Extract only code blocks from a URL |

### Utility (2 tools)

| Tool | Purpose |
|------|---------|
| `search_session` | Manage multi-turn search context across queries |
| `list_engines` | Show engine status, API key config, and setup instructions |

---

## Output Formats

Every `web_search` supports three formats via `output_format`:

| Format | Output |
|--------|--------|
| `full` (default) | Title + URL + snippet + authority tags `[official]`/`[trusted]` |
| `compact` | `[title](url)` markdown links |
| `links` | URL only |

---

## Search Engines

| Engine | Backend | Free Tier | API Key |
|--------|---------|-----------|---------|
| `auto` | DuckDuckGo (aggregates Bing+Yahoo+Brave+Yandex) | **Unlimited** | None |
| `brave` | Brave Search API (independent index) | 2000/mo | `BRAVE_SEARCH_API_KEY` |
| `google` | Google Custom Search API | 100/day | `GOOGLE_API_KEY` + `GOOGLE_CSE_ID` |
| `bing` | Bing Web Search API v7 | 1000/mo | `BING_SEARCH_API_KEY` |
| `baidu` | Baidu.com scraping | Unlimited | None |
| `yahoo` | Yahoo via DuckDuckGo backend | Unlimited | None |

Set `engine="all"` to try every configured engine with **parallel execution** and automatic deduplication.

Set `SEARCH_ENGINES=brave,auto` env var for custom fallback chains.

---

## Key Features

### Search Intelligence
- **3D result ranking** — combines source authority, content freshness, and keyword
  relevance for optimal result ordering
- **Source authority scoring** — official docs (1.0) > Stack Overflow (0.85) > blogs
  (0.5), each result tagged `[official]` or `[trusted]`
- **Freshness scoring** — detects year mentions and relative time indicators for
  recency boost
- **Parallel engine execution** — all configured engines run concurrently via
  `asyncio.wait` when using `engine="all"`, with 60s overall timeout
- **Smart dedup** — URL exact match + title similarity (85%) across multi-engine results

### Error Debugging
- **Error code detection** — recognizes patterns across MongoDB, Node.js, Python,
  JavaScript, Java, Go, C/C++, HTTP, Oracle/SQL, and Windows
- **Noise stripping** — auto-removes timestamps, hex addresses, file paths,
  stack trace frames before searching

### Performance & Reliability
- **5-minute result caching** — no repeated network calls for identical queries
- **Auto-retry** — exponential backoff (2s→4s→8s) on rate limits and transient failures
- **Session tracking** — multi-turn search context, 50 sessions, 1h idle TTL
- **Async-safe** — all CPU-bound parsing runs in thread pool, no event loop blocking
- **MCP protocol compliant** — `isError: true` for failures, tool/resource annotations,
  JSON MIME types on resources

---

## MCP Resources

| Resource URI | Content |
|-------------|---------|
| `search://domains/code` | Code Q&A domain list |
| `search://domains/docs` | Documentation domain list |
| `search://domains/paper` | Academic paper domain list |
| `search://domains/github` | Repository domain list |
| `search://authority` | Source authority scores |

---

## Requirements

- Python 3.10+
- [`mcp`](https://pypi.org/project/mcp/) >= 1.27.0
- [`ddgs`](https://pypi.org/project/ddgs/) >= 9.14.0
- [`httpx`](https://pypi.org/project/httpx/) >= 0.28.0
- [`beautifulsoup4`](https://pypi.org/project/beautifulsoup4/) >= 4.14.0

---

## Documentation

- [CHANGELOG.md](CHANGELOG.md) — version history
- [devlog.md](devlog.md) — detailed development log
- [references.md](references.md) — competitive analysis and research sources

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

## Related

- [Model Context Protocol](https://modelcontextprotocol.io/) — official specification
- [Official MCP servers](https://github.com/modelcontextprotocol/servers) — reference implementations
- [FastMCP](https://github.com/prefecthq/fastmcp) — Pythonic MCP framework
