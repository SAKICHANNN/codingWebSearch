# codingmcp

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-1.0%2B-purple)](https://modelcontextprotocol.io/)

**MCP server for coding agents** — intelligent web search, documentation lookup, error debugging, and deep research. Built specifically for AI coding assistants that need structured, high-signal search results.

14 tools, 5 resources, 6 search engines. No API key required by default.

---

## Quick Start

### Install

```bash
git clone https://github.com/SAKICHANNN/codingmcp.git
cd codingmcp
pip install -r requirements.txt
```

### Claude Desktop

```json
{
  "mcpServers": {
    "codingmcp": {
      "command": "python",
      "args": ["path/to/codingmcp/server.py"]
    }
  }
}
```

### Optional: Brave Search API (free tier, better results)

```json
{
  "mcpServers": {
    "codingmcp": {
      "command": "python",
      "args": ["path/to/codingmcp/server.py"],
      "env": {
        "BRAVE_SEARCH_API_KEY": "your-key-from-brave.com/search/api/"
      }
    }
  }
}
```

---

## Tools Reference

### Core Search

| Tool | Purpose | Scoped To |
|------|---------|-----------|
| `web_search` | General search, 3 output formats, session tracking | Whole web |
| `search_code` | Programming Q&A and code examples | Stack Overflow, GitHub, Reddit, dev.to, HN |
| `search_docs` | API references and official documentation | MDN, readthedocs, PyPI, npm, MS Learn, docs sites |
| `search_paper` | CS research papers and academic references | arXiv, ACM DL, IEEE, Semantic Scholar, Usenix |
| `search_github` | Open-source repository search | GitHub, GitLab, Bitbucket, Gitee, SourceForge |

### Coding Agent Tools

| Tool | Purpose | Example |
|------|---------|---------|
| `search_error` | Debug error messages (auto-strips noise) | `search_error("TypeError: undefined is not a function", language="React")` |
| `search_api` | API method signatures and parameter docs | `search_api("FastAPI", "Depends")` |
| `search_compare` | Side-by-side technology comparison | `search_compare("Rust", "Go", aspect="performance")` |
| `search_deep` | Search + auto-fetch top results' content | `search_deep("Python async patterns", fetch_top=2)` |
| `search_similar_repos` | Find repos by feature description | `search_similar_repos("async HTTP client", language="Rust")` |

### Content & Utility

| Tool | Purpose |
|------|---------|
| `web_fetch` | Extract readable content from any URL, preserves code blocks |
| `web_fetch_code` | Extract only code blocks from a URL |
| `search_session` | Manage multi-turn search context across queries |
| `list_engines` | Show engine status, API key config, and setup instructions |

---

## Output Formats

Every `web_search` supports three formats via `output_format`:

| Format | Output |
|--------|--------|
| `full` (default) | Title + URL + snippet + authority tags |
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

Set `engine="all"` to try every configured engine with automatic deduplication.

Set `SEARCH_ENGINES=brave,auto` env var for custom fallback chains.

---

## Features for Coding Agents

- **Source authority ranking** — official docs > Stack Overflow > blogs, each result tagged `[official]` or `[trusted]`
- **Freshness scoring** — recent content (year mentions, time indicators) gets ranking boost
- **Query optimization** — error messages auto-strip hex addresses, timestamps, file paths
- **Smart dedup** — URL exact + title similarity (85%) across multi-engine results
- **Result caching** — 5-minute TTL, no repeated network calls for identical queries
- **Auto-retry** — exponential backoff (2s→4s→8s) on rate limits and transient failures
- **Session tracking** — multi-turn search context, avoid redundant lookups

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
- [`mcp`](https://pypi.org/project/mcp/) >= 1.0.0
- [`ddgs`](https://pypi.org/project/ddgs/) >= 9.0.0
- [`httpx`](https://pypi.org/project/httpx/) >= 0.27.0
- [`beautifulsoup4`](https://pypi.org/project/beautifulsoup4/) >= 4.12.0

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

## Related

- [Model Context Protocol](https://modelcontextprotocol.io/) — official specification
- [Official MCP servers](https://github.com/modelcontextprotocol/servers) — reference implementations
- [FastMCP](https://github.com/prefecthq/fastmcp) — Pythonic MCP framework
