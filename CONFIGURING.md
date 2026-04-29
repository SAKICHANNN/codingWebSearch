# Configuration Guide

## Engine Configuration

codingWebSearch supports 7 search engines. No configuration is required — it works
out of the box with DuckDuckGo. Adding API keys unlocks more engines with better results.

### Quick Setup (Recommended)

```bash
# 1. Brave Search (free, 2000 queries/month, independent index)
export BRAVE_SEARCH_API_KEY=your_key_here

# 2. Optional: SearXNG (self-hosted, unlimited, privacy-respecting)
export SEARXNG_URL=http://your-searxng-instance:8080
```

With just Brave, you get the `auto` (DuckDuckGo) + `brave` engines. Set `engine="all"`
to use both in parallel.

### Full Setup

```bash
# Brave Search (recommended)
export BRAVE_SEARCH_API_KEY=your_key    # https://brave.com/search/api/

# Google Custom Search
export GOOGLE_API_KEY=your_key          # https://developers.google.com/custom-search
export GOOGLE_CSE_ID=your_cse_id        # Create at https://programmablesearchengine.google.com/

# Bing Web Search
export BING_SEARCH_API_KEY=your_key     # https://www.microsoft.com/bing/apis

# SearXNG (self-hosted, no API key)
export SEARXNG_URL=http://localhost:8080  # Your SearXNG instance URL

# GitHub (for search_github_issues, optional)
export GITHUB_TOKEN=ghp_xxxx           # https://github.com/settings/tokens
```

### Engine Fallback Chain

Set `SEARCH_ENGINES` to control which engines are tried and in what order:

```bash
# Try Brave first, fall back to DuckDuckGo
export SEARCH_ENGINES=brave,auto

# Use only DuckDuckGo (never try API engines)
export SEARCH_ENGINES=auto

# Try all free engines first
export SEARCH_ENGINES=searxng,auto,brave

# All engines (parallel when using engine="all")
export SEARCH_ENGINES=brave,google,bing,auto,searxng
```

### Per-Query Engine Selection

Each search tool accepts an `engine` parameter:
- `engine="auto"` — DuckDuckGo (default, free, unlimited)
- `engine="brave"` — Brave Search API (requires key)
- `engine="google"` — Google CSE (requires key + CSE ID)
- `engine="bing"` — Bing API (requires key)
- `engine="baidu"` — Baidu scraping (free, no key)
- `engine="yahoo"` — Yahoo via DDGS (free, no key)
- `engine="searxng"` — Self-hosted SearXNG (requires SEARXNG_URL)
- `engine="all"` — All configured engines in parallel

## Claude Desktop Configuration

### Basic (stdio transport)

```json
{
  "mcpServers": {
    "codingWebSearch": {
      "command": "python",
      "args": ["path/to/codingWebSearch/server.py"],
      "env": {
        "BRAVE_SEARCH_API_KEY": "your_key"
      }
    }
  }
}
```

### Docker

```json
{
  "mcpServers": {
    "codingWebSearch": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-e", "BRAVE_SEARCH_API_KEY=your_key", "codingwebsearch"]
    }
  }
}
```

### Multiple API Keys

```json
{
  "mcpServers": {
    "codingWebSearch": {
      "command": "python",
      "args": ["path/to/codingWebSearch/server.py"],
      "env": {
        "BRAVE_SEARCH_API_KEY": "your_brave_key",
        "GOOGLE_API_KEY": "your_google_key",
        "GOOGLE_CSE_ID": "your_cse_id",
        "SEARCH_ENGINES": "brave,auto"
      }
    }
  }
}
```

## Output Format Configuration

The `web_search` tool supports three formats via `output_format`:

- `"full"` — Title, URL, snippet, authority tags `[official]`/`[trusted]` (default)
- `"compact"` — `[title](url)` markdown links
- `"links"` — URL only

## Region & Language

- `region="wt-wt"` — Worldwide (default)
- `region="us-en"` — United States English
- `region="cn-zh"` — China Chinese

The region parameter only applies to DuckDuckGo (`engine="auto"`). API-based engines
(Brave, Google, Bing) use their own region settings.

## SafeSearch

- `safesearch="off"` — No filtering (default)
- `safesearch="moderate"` — Moderate filtering
- `safesearch="on"` — Strict filtering

## Time Filtering

Several tools support `timelimit` or `period` parameters:
- `"d"` — Past day
- `"w"` — Past week
- `"m"` — Past month
- `"y"` — Past year

## Performance Tuning

### Cache TTL

Results are cached for 5 minutes by default. Modify `CACHE_TTL` in `server.py` to change.

### Parallel Engines

When using `engine="all"`, all configured engines run concurrently. The overall timeout
is 60 seconds (`SEARCH_OVERALL_TIMEOUT`). Engines that don't finish in time are cancelled.

### Rate Limits

| Engine | Free Tier | Authenticated |
|--------|-----------|---------------|
| DuckDuckGo (auto) | Unlimited | — |
| Brave Search | 2000/month | — |
| Google CSE | 100/day | 10000/day (billed) |
| Bing | 1000/month | — |
| Baidu | Unlimited (scraping) | — |
| Yahoo | Unlimited (via DDGS) | — |
| SearXNG | Unlimited (self-hosted) | — |
| GitHub API | 60/hr | 5000/hr (with token) |
| OSV (CVE) | Unlimited | — |
| PyPI/npm/crates | Unlimited | — |

A built-in rate limit tracker warns before hitting Brave/Google/Bing limits.
