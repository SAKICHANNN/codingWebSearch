# Troubleshooting

## Common Issues

### "No results found" for every search

1. Check your internet connection
2. Try a different engine: set `engine="brave"` or `engine="google"` if configured
3. Check if DuckDuckGo is accessible from your location:
   ```bash
   curl "https://duckduckgo.com/html/?q=test"
   ```
4. Some networks block DuckDuckGo — try adding a Brave API key (free):
   ```bash
   export BRAVE_SEARCH_API_KEY=your_key
   ```

### "BRAVE_SEARCH_API_KEY not set" error

1. Get a free key at https://brave.com/search/api/ (2000 queries/month)
2. Set it: `export BRAVE_SEARCH_API_KEY=your_key`
3. Or use `engine="auto"` for DuckDuckGo (no key needed)

### "HTTP 429" or rate limit errors

- **Brave**: 2000/month free tier. Wait or upgrade.
- **Google**: 100/day. Reduce frequency or get GCP billing.
- **Bing**: 1000/month. Reduce frequency.
- **GitHub API** (search_github_issues): 60/hr unauthenticated. Set `GITHUB_TOKEN` for 5000/hr.
- Use `engine="auto"` for unlimited free searches.

### "No readable text found" from web_fetch

Some sites block automated access or require JavaScript. Try:
1. A different URL (same content on another site)
2. `search_deep` with the topic (fetches multiple sources)
3. The URL may be a JavaScript SPA — these can't be scraped without Playwright

### Baidu returns no results

Baidu HTML scraping may break if Baidu changes their page structure. Use `engine="auto"` or `engine="brave"` instead.

### SearXNG "HTTP 404" or connection refused

1. Verify your SearXNG instance is running: `curl http://localhost:8080/search?format=json&q=test`
2. Check `SEARXNG_URL` is set correctly: `echo $SEARXNG_URL`
3. Ensure the SearXNG instance has JSON format enabled: `search: formats: - html - json`

### "Search query is empty" error

All search tools require a non-empty query. For news without a specific topic, use:
```
search_news(topic="programming", period="d")
```

### Python import errors

```bash
# Reinstall dependencies
pip install --upgrade -r requirements.txt
# Check versions
pip show mcp ddgs httpx beautifulsoup4
```

Minimum versions: mcp>=1.27.0, ddgs>=9.14.0, httpx>=0.28.0, bs4>=4.14.0

### Docker: server starts but Claude can't connect

Make sure `stdin_open: true` is set in docker-compose or use `-i` with docker run:
```bash
docker run -i --rm codingwebsearch  # correct
docker run --rm codingwebsearch     # wrong - missing -i
```

## Diagnostic Commands

```bash
# Check which engines are configured
python -c "
import os
keys = {
    'auto': 'always available',
    'brave': bool(os.environ.get('BRAVE_SEARCH_API_KEY')),
    'google': bool(os.environ.get('GOOGLE_API_KEY') and os.environ.get('GOOGLE_CSE_ID')),
    'bing': bool(os.environ.get('BING_SEARCH_API_KEY')),
    'baidu': 'always available (scraping)',
    'yahoo': 'always available',
    'searxng': bool(os.environ.get('SEARXNG_URL')),
}
for k, v in keys.items():
    status = 'OK' if v else 'MISSING'
    print(f'  {k:10s}: {status:7s} {v if isinstance(v, str) else \"\"} ')
"

# Test a search directly
python -c "
import asyncio, os
os.environ['PYTHONIOENCODING'] = 'utf-8'
from server import _search_ddgs
async def test():
    results = await _search_ddgs('python programming', max_results=3)
    for r in results:
        print(r['title'][:80])
asyncio.run(test())
"
```

## Getting Help

- GitHub Issues: https://github.com/SAKICHANNN/codingWebSearch/issues
- MCP Specification: https://modelcontextprotocol.io
- FastMCP Docs: https://gofastmcp.com
