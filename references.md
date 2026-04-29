# References

Projects researched during the development of codingWebSearch to understand the
competitive landscape and identify feature gaps. Listed in order of relevance.

## Direct Competitors (Web Search MCP Servers)

| # | Project | Stars | Key Features | What We Learned |
|---|---------|-------|-------------|-----------------|
| 1 | [mcp-searxng](https://github.com/SecretiveShell/MCP-searxng) | ~200 | Single tool, SearXNG backend, Docker, MIT | Privacy-first approach; self-hosted metasearch. Simple but limited to one tool. |
| 2 | [tavily-mcp](https://github.com/tavily-ai/tavily-mcp) | ~500 | 4 tools (search, extract, map, crawl), Tavily API | Multi-tool research pipeline. But proprietary API, not self-hostable. |
| 3 | [pskill9/web-search](https://github.com/pskill9/web-search) | ~100 | Google scraping, no API keys, 10 results max | API-key-free approach is valuable. HTML scraping is fragile. |
| 4 | [mcp-searxng-enhanced](https://github.com/OvertliDS/mcp-searxng-enhanced) | 37 | Category-aware search, web scraping, datetime tool | Search categories and structured scraping add real value. |
| 5 | [web-research-assistant](https://github.com/elad12390/web-research-assistant) | 9 | 13 tools, SearXNG, error translation, API docs, GitHub | Broadest tool set among Python competitors. Error translation and API doc tools are smart. |
| 6 | [intercept-mcp](https://github.com/bighippoman/intercept-mcp) | ~50 | 9 fallback strategies, markdown extraction, multi-format | Multi-tier fetch fallback is a robust pattern. |
| 7 | [olostep-mcp-server](https://github.com/olostep/olostep-mcp-server) | ~50 | Web scraping, crawling, batch 10k URLs, markdown/JSON | Batch processing and structured extraction at scale. |
| 8 | [freshcontext-mcp](https://github.com/PrinceGabriel-lgtm/freshcontext-mcp) | ~20 | Real-time freshness timestamps, GitHub, HN, Scholar, arXiv | Freshness tracking across multiple sources is useful for research agents. |
| 9 | [iwanghc/mcp_web_search](https://github.com/iwanghc/mcp_web_search) | 6 | Playwright-based, anti-bot bypass, Google scraping | Playwright for anti-bot is effective but heavy. |
| 10 | [agent-scraper-mcp](https://github.com/aparajithn/agent-scraper-mcp) | ~30 | 6 tools, clean content extraction, Google search, metadata | Structured scraping with metadata extraction complements search well. |

## Research & Deep Search Projects (Not Direct Competitors)

| # | Project | Stars | Key Features |
|---|---------|-------|-------------|
| 11 | [gpt-researcher](https://github.com/assafelovic/gpt-researcher) | 26.8k | Autonomous deep research agent, LLM-driven, web scraping |
| 12 | [Scrapling](https://github.com/D4Vinci/Scrapling) | 39.2k | Adaptive web scraping framework, crawling, data extraction |
| 13 | [sylex-search](https://github.com/MastadoonPrime/sylex-search) | ~20 | 10 MCP tools, universal search, zero LLM calls |
| 14 | [a2asearch-mcp](https://github.com/tadas-github/a2asearch-mcp) | ~30 | Searches 4800+ MCP servers specifically (meta-search) |

## Reference Specifications

| Resource | URL |
|----------|-----|
| MCP Specification (Draft) | https://modelcontextprotocol.io/specification/draft/ |
| MCP Tools Specification | https://modelcontextprotocol.io/specification/draft/server/tools |
| MCP Resources Specification | https://modelcontextprotocol.io/specification/draft/server/resources |
| FastMCP Documentation | https://gofastmcp.com |
| Awesome MCP Servers | https://github.com/punkpeye/awesome-mcp-servers |
| DuckDuckGo Instant Answer API | https://duckduckgo.com/api |
| Brave Search API | https://brave.com/search/api/ |
| Google Custom Search API | https://developers.google.com/custom-search |
| Bing Web Search API | https://www.microsoft.com/en-us/bing/apis/bing-web-search-api |

## Feature Gap Analysis (As Of v0.3.0)

What codingWebSearch has that competitors don't (combined):

| Feature | codingWebSearch | Others |
|---------|:---:|:---:|
| Multi-engine (6 engines) | ✅ | ❌ (max 1-2) |
| Parallel engine execution | ✅ | ❌ |
| Domain-scoped search (4 categories) | ✅ | ❌ (1 project) |
| Source authority ranking | ✅ | ❌ |
| Relevance + freshness scoring | ✅ | ❌ |
| Direct package registry APIs | ✅ | ❌ |
| Error code pattern detection | ✅ | ❌ (1 project) |
| Deep research (search + fetch) | ✅ | ✅ (Tavily) |
| Session tracking | ✅ | ❌ |
| 3 output formats | ✅ | ❌ |
| 16 specialized tools | ✅ | ❌ (max 13) |
| Resource exposure (domains, authority) | ✅ | ❌ |
| 5-min result caching | ✅ | ❌ |
| Auto-retry with backoff | ✅ | ❌ |
| API key optional (free by default) | ✅ | ✅ (3 projects) |
| MCP annotations (tools + resources) | ✅ | ❌ |
| isError protocol compliance | ✅ | ❌ |
| Tool annotations (readOnlyHint etc.) | ✅ | ❌ |

Areas where competitors still lead:

| Feature | codingWebSearch | Leader |
|---------|:---:|--------|
| SearXNG integration | ❌ | mcp-searxng |
| Playwright/anti-bot | ❌ | mcp_web_search |
| LLM-powered summarization | ❌ | gpt-researcher |
| Docker packaging | ❌ | mcp-searxng |
| OAuth authentication | ❌ | tavily-mcp |
| Batch URL crawling | ❌ | olostep-mcp-server |
| RSS/feed monitoring | ❌ | TrendRadar |
