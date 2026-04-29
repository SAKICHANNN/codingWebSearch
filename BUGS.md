# Bug Tracker

## Status Legend
- 🔴 **open** — confirmed, not yet fixed
- 🟡 **in progress** — fix in development
- 🟢 **fixed** — resolved and committed
- ⚪ **wontfix** — acknowledged, intentionally not fixed

---

## Tracked Bugs

### BUG-001 [high]: `_optimize_query("error")` strips all content from hex-only / timestamp-only queries
- **Status**: 🟢 fixed
- **Severity**: high
- **File**: `server.py`, `_optimize_query()` (~line 329)
- **Discovered**: 2026-04-29, final audit round
- **Reproduction**:
  ```python
  _optimize_query("0x1234abcd", "error")  # returns ""
  _optimize_query("2024-01-01T12:00:00", "error")  # returns ""
  ```
- **Root cause**: The `error` category strips hex addresses (`0x[0-9a-fA-F]+`),
  timestamps, and file paths via regex, then collapses whitespace. If the entire
  query matches one of these patterns, the result is an empty string.
- **Impact**: `search_error` throws a misleading "Search query is empty" error
  when a user pastes a raw hex address, timestamp, or file path as the error
  message. The user sees a validation error instead of search results.
- **Fix direction**: After stripping, if the result is empty or only whitespace,
  return the original query unchanged rather than an empty string.
- **Fixed**: `_optimize_query()` now falls back to the original error query when stripping would erase all searchable content.


### BUG-002 [high]: SSRF via URL fetch — three attack vectors
- **Status**: 🟢 fixed
- **Severity**: high
- **File**: `server.py`, `_validate_url()` (~line 423) and `_fetch()` (~line 466)
- **Discovered**: 2026-04-29, deep audit rounds 5 and 7 (merged from previous BUG-004/005/006)
- **Vectors**:
  1. **Direct access**: `_validate_url` does not reject loopback (`127.0.0.1`),
     link-local (`169.254.169.254`), or private-range (`10.x`, `192.168.x`) addresses.
  2. **Redirect bypass**: `follow_redirects=True` in `_fetch` follows 30x redirects
     without re-validating the redirect target. A public URL can 302 to an internal one.
  3. **DNS rebinding**: Validation inspects only the URL string, not resolved IPs.
     `attacker.example` passes validation even when DNS resolves to `127.0.0.1`.
- **Reproduction**:
  ```python
  _validate_url("http://127.0.0.1:8080/admin")       # vector 1: returns None (valid)
  _validate_url("http://169.254.169.254/metadata/")  # vector 1: returns None (valid)
  # vector 2: GET https://safe.com → 302 → http://169.254.169.254/ → fetched
  # vector 3: GET http://rebind.example → DNS → 127.0.0.1 → fetched
  ```
- **Impact**: `web_fetch` and `search_crawl` can be used as SSRF primitives to
  reach internal services, cloud metadata endpoints, and localhost admin interfaces.
- **Fix direction**: (1) Block private/link-local/loopback IPs by default with opt-in;
  (2) disable automatic redirects or validate every redirect target; (3) resolve
  hostnames before connecting and block resolved private IPs.
- **Fixed**: `_validate_url()` now blocks private/local IPs and private DNS resolutions, and `_fetch()` revalidates each redirect target before following it.


### BUG-003 [high]: `_search_yahoo` mutates cached result dicts in-place — cache poisoning
- **Status**: 🟢 fixed
- **Severity**: high
- **File**: `server.py`, `_search_yahoo()` and `_cache_get()`
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: DDGS search → results cached with `"engine": "ddgs"`. Yahoo
  search hits cache → `r["engine"] = "yahoo"` mutates the cached dict. Future
  DDGS searches return `"engine": "yahoo"`.
- **Root cause**: `_cache_get` returns mutable reference; `_search_yahoo` modifies
  it in-place.
- **Impact**: Cross-engine cache poisoning — DDGS results permanently relabeled.
- **Fix direction**: Deep-copy results before mutation.
- **Fixed**: `_search_yahoo()` now relabels copied results, and cache reads/writes return defensive copies.


### BUG-004 [medium]: `_is_duplicate` false-positively matches results with missing/empty `href`
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_is_duplicate()` (~line 501)
- **Discovered**: 2026-04-29, final audit round
- **Reproduction**:
  ```python
  r1 = {"title": "Article A", "href": None}
  r2 = {"title": "Completely Different Article B", "href": None}
  _is_duplicate(r2, [r1])  # returns True (WRONG)
  ```
- **Root cause**: `_as_text(s.get("href"))` returns `""` for both None and
  missing keys. The URL comparison `"" == ""` evaluates to True, marking
  completely different results as duplicates.
- **Impact**: When an engine returns results without `href` fields (rare but
  possible with scraping-based engines like Baidu), different results may be
  incorrectly deduplicated. Users see fewer results than they should.
- **Fix direction**: Skip the URL-based duplicate check when both URLs are
  empty strings. Only use title similarity in that case. Or, require at least
  one non-empty URL for the URL-match branch.

### BUG-005 [medium]: `search_crawl` reports failed fetches as successful pages
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_crawl()` → `_crawl_one()` (~line 1781)
- **Discovered**: 2026-04-29, crawl/RSS/backend audit round
- **Reproduction**:
  ```python
  async def fake_fetch(url, timeout, headers=None):
      return None, "HTTP 500"

  server._fetch = fake_fetch
  await search_crawl(urls="https://example.com/fail", max_pages=1)
  # Output includes: "1/1 pages fetched" and renders "HTTP 500" as page text.
  ```
- **Root cause**: `_crawl_one()` returns `(url, None, err)` when `_fetch()`
  fails. The output loop treats the third tuple item as page content, so the
  error string is formatted as a successful crawl result.
- **Impact**: Users get a false success count and no clear indication that the
  page fetch failed. Downstream consumers may treat error messages as crawled
  page content.
- **Fix direction**: Return a distinct error shape from `_crawl_one()` and
  count failed fetches separately. For failed pages, render an error section
  instead of a normal result body.

### BUG-006 [medium]: `search_deep` URL extraction truncates URLs containing parentheses
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_deep()` (~line 1148)
- **Discovered**: 2026-04-29, final audit round
- **Reproduction**:
  ```python
  # Compact format line: [Python](https://en.wikipedia.org/wiki/Python_(programming_language))
  # Regex: \]\((https?://[^)]+)\)
  # Captures: https://en.wikipedia.org/wiki/Python_(programming_language
  # Missing: )
  ```
- **Root cause**: The compact-format URL regex uses `[^)]+` which stops at
  the first `)`. URLs containing `)` (valid per RFC 3986, common on Wikipedia)
  are truncated.
- **Impact**: `search_deep` fetches a truncated/invalid URL and gets an
  HTTP error instead of the intended page. The user loses one source in
  their deep research results.
- **Fix direction**: Use a more robust URL extraction: match the full
  `[text](url)` pattern with balanced parentheses, or use `\S+` with
  a trailing `\)` anchor, or prefer the full-format `URL: ` prefix
  which uses `\S+` and doesn't have this issue.

### BUG-007 [medium]: `_source_authority` fails when URL has a non-standard port
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_source_authority()` (~line 246)
- **Discovered**: 2026-04-29, final audit round
- **Reproduction**:
  ```python
  _source_authority("https://docs.python.org:8080/")  # returns 0.4 (default)
  # Expected: 1.0 (docs.python.org is a known authority)
  ```
- **Root cause**: `urlparse(url).netloc` includes the port number, e.g.
  `"docs.python.org:8080"`. The authority matching logic does `host == domain or
  host.endswith("." + domain)`, neither of which matches when a port is present.
- **Impact**: Any search result URL with a non-standard port (common in
  development servers, self-hosted docs, and internal tools) loses its
  authority score, defaulting to 0.4. Results from authoritative sources
  may be ranked below less authoritative ones.
- **Fix direction**: Strip the port from the host before matching:
  `host = host.split(":")[0]`.

### BUG-008 [medium]: `search_crawl(base_url=...)` fetches the base page twice
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_crawl()` (~lines 1752 and 1793)
- **Discovered**: 2026-04-29, crawl/RSS/backend audit round
- **Reproduction**:
  ```python
  calls = []

  async def fake_fetch(url, timeout, headers=None):
      calls.append(url)
      return "<a href='/docs'>Docs</a>", None

  server._fetch = fake_fetch
  await search_crawl(base_url="https://example.com", max_pages=2)
  calls  # ["https://example.com", "https://example.com", "https://example.com/docs"]
  ```
- **Root cause**: `base_url` mode first fetches the page to extract links,
  inserts `base_url` into `target_urls`, then crawls every target URL including
  `base_url` again.
- **Impact**: The crawl wastes one network request, doubles latency for the
  first page, and can produce confusing results if the second fetch fails or
  returns different content.
- **Fix direction**: Reuse the first fetched `base_url` HTML for the crawl
  result, or skip the second fetch and crawl only discovered links after the
  already-fetched base page.

### BUG-009 [medium]: `search_crawl(max_pages=0/-1)` violates the documented minimum
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_crawl()` (~line 1751 and ~line 1793)
- **Discovered**: 2026-04-29, crawl/RSS/backend audit round
- **Reproduction**:
  ```python
  await search_crawl(urls="https://example.com/a", max_pages=0)
  # Output says "0/1 pages fetched".

  await search_crawl(
      urls="https://example.com/a,https://example.com/b",
      max_pages=-1,
  )
  # Python slicing drops the last URL instead of clamping to 1.
  ```
- **Root cause**: The docstring says `max_pages` is `1-30` for URL mode and
  `1-50` for `base_url` mode, but the implementation only applies `min()`.
  It never applies a lower bound before slicing `target_urls[:max_pages]`.
- **Impact**: Invalid user input causes surprising behavior: zero-page output
  or negative slicing semantics. This can silently skip pages the user asked
  to crawl.
- **Fix direction**: Clamp `max_pages` with both lower and upper bounds before
  building tasks, e.g. `max_pages = max(1, min(max_pages, limit))`.

### BUG-010 [medium]: `search_crawl` skips non-root relative links
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_crawl()` → `_extract_links()` (~line 1763)
- **Discovered**: 2026-04-29, crawl/RSS/backend audit round
- **Reproduction**:
  ```html
  <a href="page2.html">Page 2</a>
  <a href="./page3.html">Page 3</a>
  ```
  ```python
  await search_crawl(base_url="https://example.com/docs/index.html", max_pages=3)
  # Neither relative link is crawled.
  ```
- **Root cause**: `_extract_links()` only accepts absolute URLs and links that
  start with `/`. Relative links like `page2.html`, `./page3.html`, and
  `../guide.html` are ignored even though they are valid HTML links. Python's
  `urllib.parse.urljoin()` is the standard way to resolve them against a base
  URL.
- **Impact**: Many documentation and static sites use document-relative links.
  Crawling those sites misses valid internal pages.
- **Fix direction**: Resolve every candidate `href` with `urljoin(base_url,
  href)` before same-domain filtering.

### BUG-011 [medium]: `search_crawl` corrupts scheme-relative links
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_crawl()` → `_extract_links()` (~line 1763)
- **Discovered**: 2026-04-29, crawl/RSS/backend audit round
- **Reproduction**:
  ```html
  <a href="//example.com/a">A</a>
  ```
  ```python
  await search_crawl(base_url="https://example.com", max_pages=2)
  # Attempts to crawl: https://example.com//example.com/a
  ```
- **Root cause**: The code checks `href.startswith("/")` before handling
  scheme-relative URLs. A URL beginning with `//` is treated as root-relative
  text and concatenated onto the current origin.
- **Impact**: Valid same-site links become malformed URLs, causing fetch
  failures and missing crawl results.
- **Fix direction**: Use `urljoin()` for all links before same-domain checks.
  It correctly handles absolute, root-relative, document-relative, and
  scheme-relative URLs.

### BUG-012 [medium]: `search_rss` can lose RSS 2.0 `<link>text</link>` values
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_rss()` (~line 1683 and ~line 1705)
- **Discovered**: 2026-04-29, crawl/RSS/backend audit round
- **Reproduction**:
  ```xml
  <rss version="2.0">
    <channel>
      <item>
        <title>Post</title>
        <link>https://example.com/post</link>
      </item>
    </channel>
  </rss>
  ```
  ```python
  await search_rss(feed_url="https://example.com/feed.xml")
  # The item may be rendered without the expected link.
  ```
- **Root cause**: The feed is parsed with BeautifulSoup's `html.parser`.
  In HTML parsing mode, `<link>` is treated like an HTML void element, which
  can drop text content. RSS 2.0 uses `<link>...</link>` text as the item URL.
- **Impact**: Valid RSS feeds can produce entries with missing links, reducing
  the usefulness of feed output.
- **Fix direction**: Parse feeds with an XML parser when available, or use
  feed-specific parsing that preserves RSS `<link>` text nodes.

### BUG-013 [medium]: `search_rss` picks the first Atom link instead of `rel="alternate"`
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_rss()` (~line 1703)
- **Discovered**: 2026-04-29, crawl/RSS/backend audit round
- **Reproduction**:
  ```xml
  <entry>
    <title>Post</title>
    <link rel="self" href="https://example.com/feed/entry.xml" />
    <link rel="alternate" href="https://example.com/post" />
  </entry>
  ```
  ```python
  await search_rss(feed_url="https://example.com/atom.xml")
  # Output links to the feed entry XML instead of the article page.
  ```
- **Root cause**: The code uses `item.find("link")`, which selects the first
  link element. Atom feeds can include multiple links with different
  relations; `rel="alternate"` is the normal article URL, while `rel="self"`
  identifies the feed resource.
- **Impact**: Atom feed results can point users to machine-readable feed
  entries instead of the human-readable article.
- **Fix direction**: Prefer `link[rel="alternate"]`, then fall back to a link
  without `rel`, then to the first link only if no better candidate exists.

### BUG-014 [medium]: `_search_yahoo` does not use Yahoo-specific DDGS options
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_search_yahoo()` (~line 621)
- **Discovered**: 2026-04-29, crawl/RSS/backend audit round
- **Reproduction**:
  ```python
  async def fake_ddgs(query, *, region, safesearch, timelimit, max_results):
      return [(region, safesearch, timelimit, max_results)]

  server._search_ddgs = fake_ddgs
  await server._search_with_engine(
      "query", "yahoo", region="us-en", safesearch="moderate",
      timelimit="y", max_results=3,
  )
  # Recorded values are ("wt-wt", "off", None, 3), not the user settings.
  ```
- **Root cause**: `_search_yahoo()` delegates to `_search_ddgs(query,
  max_results=max_results)` without forwarding `region`, `safesearch`, or
  `timelimit`, and without selecting a Yahoo backend. DDGS documents these
  parameters, including backend selection.
- **Impact**: Users who choose `engine="yahoo"` get generic DDGS behavior and
  silently lose locale, safe-search, and time filters.
- **Fix direction**: Accept and forward the same search options as
  `_search_ddgs()`, and explicitly select Yahoo if the DDGS backend supports
  it. Otherwise, rename the engine label to avoid promising Yahoo-specific
  behavior.

### BUG-015 [medium]: `_do_search` overall timeout is reported as "No results found"
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_do_search()` (~line 821)
- **Discovered**: 2026-04-29, crawl/RSS/backend audit round
- **Reproduction**:
  ```python
  async def slow_search(*args, **kwargs):
      await asyncio.sleep(1)
      return [{"title": "late", "href": "https://example.com"}]

  server.SEARCH_OVERALL_TIMEOUT = 0.01
  server._search_with_engine = slow_search
  await web_search("slow")
  # Returns: "No results found for 'slow'."
  ```
- **Root cause**: `_do_search()` cancels pending tasks after
  `asyncio.wait(..., timeout=SEARCH_OVERALL_TIMEOUT)`, but it does not record
  timeout errors for those pending engines. If no task completed, `last_errors`
  remains empty and the function falls through to the generic no-results
  message.
- **Impact**: A timeout looks identical to a legitimate empty result set.
  Users and tests cannot distinguish network slowness from a successful search
  that found nothing.
- **Fix direction**: When pending tasks are canceled due to the overall
  timeout, append a clear timeout error per engine or return a dedicated
  overall-timeout message.

### BUG-016 [medium]: Unknown search engines silently fall back to `auto`
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_resolve_engines()` (~line 699)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**:
  ```python
  _resolve_engines("gogle")  # returns ["auto"]
  await web_search("python", engine="gogle")  # searches DuckDuckGo instead
  ```
- **Root cause**: `_resolve_engines()` ignores unknown engine names and returns
  `["auto"]` after a no-op `pass`.
- **Impact**: A typo in `engine` is not visible to the user. Results come from a
  different backend than requested, which can hide configuration mistakes.
- **Fix direction**: Reject unknown engines with a clear `SearchError` listing
  valid values.

### BUG-017 [medium]: `engine="all"` result order is nondeterministic
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_do_search()` (~line 831)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**: Mock two engines to return distinct first results, call
  `_do_search(..., engine="all", max_results=1)` repeatedly, and observe that
  whichever completed task is iterated first wins.
- **Root cause**: `asyncio.wait()` returns `done` as a set. The code iterates
  that set directly and stops once `max_results` is reached, so configured
  engine priority is not preserved.
- **Impact**: Multi-engine searches can return different top results across
  runs even with the same mocked engine outputs.
- **Fix direction**: Collect task results by engine name, then merge in
  `engines_to_try` order or apply an explicit score-based merge.

### BUG-018 [medium]: Single-engine searches promise `max_results=1-50` but several engines hard-cap lower
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_search_brave_api()`, `_search_google_api()`,
  `_search_bing_api()`, `_search_baidu()` (~lines 529-586)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**:
  ```python
  await web_search("python", engine="google", max_results=50)
  # The request asks Google for at most 10 and performs no pagination.
  ```
- **Root cause**: The public tool docs say `max_results` is `1-50`, but direct
  engine calls cap a single request at 10, 15, or 20 and do not page for the
  remainder.
- **Impact**: Users requesting 50 results from a single API engine may receive
  far fewer without an explanation.
- **Fix direction**: Either paginate supported APIs up to the requested count,
  or document and enforce engine-specific limits.

### BUG-019 [medium]: `max_length` does not cap total `web_fetch` / `web_fetch_code` output
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `web_fetch()` and `web_fetch_code()` (~lines 1815-1908)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**:
  ```python
  await web_fetch_code("https://example.com", max_length=0)
  # Still returns a title, URL, metadata, code, and truncation notice.
  ```
- **Root cause**: `web_fetch()` truncates only extracted text, then prepends the
  header. `web_fetch_code()` also forces a minimum body slice of 120 characters
  even when `max_length` is smaller than the header.
- **Impact**: Callers cannot rely on `max_length` as a hard output budget,
  which matters for context-limited clients.
- **Fix direction**: Apply the limit to the final assembled result, or rename
  the parameter to clarify that it only limits body text.

### BUG-020 [medium]: URL validation allows credentials and echoes them back to output
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_validate_url()` and `web_fetch()` (~lines 423 and 1838)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**:
  ```python
  await web_fetch("https://user:secret@example.com/page")
  # Output header includes: > https://user:secret@example.com/page
  ```
- **Root cause**: `_validate_url()` only checks scheme and hostname. It does
  not reject or redact `username:password@host` URL authority data.
- **Impact**: A caller can accidentally leak credentials into tool output,
  logs, or saved research notes.
- **Fix direction**: Reject URLs containing `parsed.username` or
  `parsed.password`, or redact credentials before fetch and display.

### BUG-021 [medium]: `_fetch` does not reject non-text content types
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_fetch()` (~line 466)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**: Fetch a PDF or image URL with `web_fetch()`. `_fetch()`
  reads `resp.text` and passes it to BeautifulSoup even though the response is
  not HTML or text.
- **Root cause**: The fetch layer never checks `Content-Type` before decoding
  the body as text.
- **Impact**: Binary content can become gibberish, waste CPU in the parser, or
  produce misleading "No readable text" errors.
- **Fix direction**: Reject unsupported media types before reading text, with a
  clear message such as `Unsupported content type: application/pdf`.

### BUG-022 [medium]: `_fetch` has no response-size guard before decoding
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_fetch()` (~line 466)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**: Point `web_fetch()` at a very large HTML/text response.
  The full response is downloaded and decoded before `max_length` is applied
  by the caller.
- **Root cause**: `_fetch()` uses `await client.get(url)` and returns
  `resp.text` without streaming or checking `Content-Length`.
- **Impact**: `max_length` does not protect memory usage. A large response can
  consume significant memory before any truncation happens.
- **Fix direction**: Enforce a maximum response byte size via `Content-Length`
  and streaming reads.

### BUG-023 [medium]: `search_deep` treats successful searches containing "No results" as empty
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_deep()` (~line 1143)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**:
  ```python
  # If the topic is "No results in compiler" and the search succeeds,
  # the header contains that phrase.
  await search_deep("No results in compiler")
  # Returns search output without fetching any result pages.
  ```
- **Root cause**: The code checks `if "No results" in search_result` instead
  of checking for the exact no-results response shape.
- **Impact**: Any successful search whose query/title/snippet contains
  `"No results"` can skip the deep-fetch phase.
- **Fix direction**: Return structured search status from `_do_search()`, or
  check `search_result.startswith("No results found for ")`.

### BUG-024 [medium]: `search_deep` never extracts code blocks produced by `_extract_text`
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_deep()` (~line 1166)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**:
  ```html
  <pre><code class="language-python">print(1)</code></pre>
  ```
  ```python
  # _extract_text() emits a fence like: ``` python
  # search_deep() looks for: ```(?:\w+)?\n
  ```
- **Root cause**: `_extract_text()` formats fenced code blocks with a space
  before the language name, while `search_deep()` expects the language to start
  immediately after the backticks.
- **Impact**: The "Extracted Code Examples" section can be missing even when
  fetched pages contain code blocks.
- **Fix direction**: Use a fence regex that allows optional whitespace and
  non-word language names, or standardize fence generation.

### BUG-025 [medium]: `search_deep` counts failed fetches as fetched sources
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_deep()` (~lines 1182-1208)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**: Mock one fetched URL to return an error. The warning entry
  is appended to `fetched`, and the summary can say `fetched 1/1 pages` even
  though no page content was extracted.
- **Root cause**: The same `fetched` list stores both successful page excerpts
  and warning sections for failed fetches. Summary and synthesis use
  `len(fetched)`.
- **Impact**: Deep research overstates source coverage and can claim successful
  fetches for failed URLs.
- **Fix direction**: Track successful and failed fetches separately.

### BUG-026 [medium]: `search_package` lowercases Go module paths
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_package()` (~line 1287)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**:
  ```python
  await search_package("github.com/Azure/azure-sdk-for-go", registry="go")
  # Requests: https://api.pkg.go.dev/packages/github.com/azure/azure-sdk-for-go
  ```
- **Root cause**: `pkg = package.strip().lower()` is applied to every
  registry. Go module paths can require escaped uppercase characters in module
  proxy paths, and blind lowercasing changes the requested package identity.
- **Impact**: Mixed-case Go module/package paths can be looked up incorrectly.
- **Fix direction**: Preserve the original package string for Go lookups and
  apply registry-specific normalization only where it is valid.

### BUG-027 [medium]: `search_package(registry="auto")` returns the first registry hit, not the intended ecosystem
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_package()` (~lines 1293 and 1353)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**:
  ```python
  await search_package("gin", registry="auto")
  # If PyPI has a package named "gin", auto mode stops before trying Go.
  ```
- **Root cause**: Auto mode tries `["pypi", "npm", "crates", "go"]` in a fixed
  order and breaks after the first HTTP 200 response. It does not infer the
  intended ecosystem from package syntax or user context.
- **Impact**: Ambiguous package names can return metadata for the wrong
  ecosystem.
- **Fix direction**: Detect package syntax first, allow returning multiple
  registry matches, or ask callers to specify the registry when ambiguous.

### BUG-028 [medium]: `search_github_issues` cannot list a repository's issues without a query
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_github_issues()` (~line 1420)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**:
  ```python
  await search_github_issues(repo="python/cpython", query="")
  # Raises: GitHub issue search requires a query.
  ```
- **Root cause**: The function requires `query.strip()` even when `repo` is
  provided. A GitHub search query containing only `repo:owner/name` and
  optional filters is valid and useful.
- **Impact**: Users cannot use the tool to view recent issues or PRs for a
  repository unless they invent a keyword.
- **Fix direction**: Permit blank `query` when `repo`, `labels`, or `state`
  filters are present.

### BUG-029 [medium]: `search_security(ecosystem="Crates.io")` sends the wrong ecosystem casing to OSV
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_security()` (~line 1521)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**:
  ```python
  await search_security("serde", ecosystem="Crates.io")
  # Posts package ecosystem "Crates.io" instead of canonical "crates.io".
  ```
- **Root cause**: The docstring lists `"crates.io"` as an accepted ecosystem,
  but `ECOSYSTEM_MAP` contains only `"crates": "crates.io"`. Case variants
  like `"Crates.io"` bypass the map and are sent as user-provided text.
- **Impact**: Case or spelling differences in OSV ecosystem identifiers can
  cause false "No vulnerability data" results for a documented value.
- **Fix direction**: Add `"crates.io": "crates.io"` and validate against known
  OSV ecosystem names.

### BUG-030 [medium]: `search_security` treats unknown ecosystems as real OSV ecosystems
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_security()` (~line 1536)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**:
  ```python
  await search_security("requests", ecosystem="Pip")
  # Sends ecosystem "Pip" to OSV, then reports no data.
  ```
- **Root cause**: Any ecosystem value not in `ECOSYSTEM_MAP` is accepted and
  sent directly to OSV.
- **Impact**: Typos are indistinguishable from genuinely missing vulnerability
  data, creating false reassurance.
- **Fix direction**: Reject unknown ecosystems and list supported aliases.

### BUG-031 [medium]: `search_rss` does not resolve relative Atom/RSS links
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_rss()` (~line 1703)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**:
  ```xml
  <feed>
    <entry>
      <title>Post</title>
      <link href="/post" />
    </entry>
  </feed>
  ```
  ```python
  await search_rss(feed_url="https://example.com/feed.xml")
  # Output link is /post, not https://example.com/post.
  ```
- **Root cause**: Feed item links are emitted exactly as found. The parser does
  not resolve relative URLs against the feed URL.
- **Impact**: Valid feeds with relative links produce output links that are not
  directly usable outside the feed's base URL context.
- **Fix direction**: Resolve item links with `urljoin(feed_url, link)` before
  rendering.

### BUG-032 [medium]: `_fetch` does not check response `Content-Type` (merged with BUG-052)
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_fetch()` (~line 466)
- **Discovered**: 2026-04-29, deep audit rounds 4 and 6 (merged from previous BUG-033/054)
- **Reproduction**: Fetch PDF/image → `resp.text` lossy-decodes binary via UTF-8
  fallback → garbled string → BeautifulSoup produces nonsense or "No readable text."
- **Root cause**: `_fetch()` never inspects `Content-Type` header. `resp.text`
  always decodes the response body, even for binary content types.
- **Impact**: Binary content wastes CPU, produces misleading output, and wastes
  context window with garbled text instead of giving a clear "unsupported type" error.
- **Fix direction**: Check `Content-Type` before calling `resp.text`; reject
  non-text/* types with a clear error message.

### BUG-033 [medium]: `_fetch` has no maximum response size guard
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_fetch()` (~line 466)
- **Discovered**: 2026-04-29, deep audit round 4 (overlaps with BUG-022)
- **Reproduction**: Point `web_fetch()` at multi-GB HTML response. Full response
  downloaded and decoded before `max_length` truncation.
- **Root cause**: No streaming or `Content-Length` check before `resp.text`.
- **Impact**: `max_length` does not protect memory; large responses risk OOM.
- **Fix direction**: Stream responses with byte budget or abort when
  `Content-Length` exceeds maximum.

### BUG-034 [medium]: `SEARCH_ENGINES=all` does not expand to all engines
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_resolve_engines()` (~line 700)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**:
  ```python
  os.environ["SEARCH_ENGINES"] = "all"
  _resolve_engines("auto")  # returns ["auto"]
  ```
- **Root cause**: Environment override parsing keeps only tokens already in
  `_ENGINE_INFO`. `"all"` is a valid user-facing engine option, but it is not a
  key in `_ENGINE_INFO`.
- **Impact**: Operators naturally setting `SEARCH_ENGINES=all` get only
  DuckDuckGo/auto behavior.
- **Fix direction**: Treat `"all"` in `SEARCH_ENGINES` the same way as
  `engine="all"` and expand to `_ENGINE_PRIORITY`.

### BUG-035 [medium]: Whitespace-only API key environment variables count as configured
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_required_keys_present()` (~line 674)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**:
  ```python
  os.environ["BRAVE_SEARCH_API_KEY"] = "   "
  _required_keys_present("BRAVE_SEARCH_API_KEY")  # returns True
  ```
- **Root cause**: Environment values are checked only for truthiness. Strings
  containing spaces are truthy.
- **Impact**: `list_engines()` can report an engine as configured, then API
  requests fail with authentication errors.
- **Fix direction**: Treat `not os.environ.get(key, "").strip()` as missing.

### BUG-036 [medium]: `_fetch` ignores `Retry-After` on HTTP 429
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_fetch()` (~line 491)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**: Mock a 429 response with `Retry-After: 60`; `_fetch()` sleeps
  `_retry_sleep(attempt) + 1` instead of the server-provided retry window.
- **Root cause**: The 429 branch never reads response headers.
- **Impact**: Retries are likely to happen too early and fail repeatedly.
- **Fix direction**: Honor `Retry-After` when present, with a reasonable maximum
  cap.

### BUG-037 [medium]: `_fetch` does not retry transient HTTP 5xx responses
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_fetch()` (~line 494)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**: Mock responses `[503, 200]`; `_fetch()` returns `HTTP 503`
  immediately instead of retrying.
- **Root cause**: Retry logic covers connection/read errors and 429 only. Any
  status code `>= 400` returns immediately.
- **Impact**: Temporary upstream failures are surfaced to users even when a
  retry would succeed.
- **Fix direction**: Retry selected transient 5xx statuses before returning an
  error.

### BUG-038 [medium]: `search_crawl(urls=...)` splits valid URLs containing commas
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_crawl()` (~line 1747)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**:
  ```python
  await search_crawl(urls="https://example.com/a,b")
  # Parsed as "https://example.com/a" and "b".
  ```
- **Root cause**: Direct URL mode splits on the regex `[,\n]+`, but commas are
  valid URL characters.
- **Impact**: Valid URLs are corrupted before fetch.
- **Fix direction**: Prefer newline-only splitting, JSON/list input, or a CSV
  parser with escaping rules.

### BUG-039 [medium]: `search_security(ecosystem=...)` does not strip whitespace
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_security()` (~line 1519)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**:
  ```python
  await search_security("requests", ecosystem=" npm ")
  # Sends ecosystem " npm " to OSV.
  ```
- **Root cause**: The code uses `ecosystem.lower()` for lookup but sends the
  original `ecosystem` string when lookup fails.
- **Impact**: Whitespace around a valid ecosystem name causes a false "No
  vulnerability data" style result instead of checking npm.
- **Fix direction**: Strip ecosystem names before lookup and before any direct
  API payload.

### BUG-040 [medium]: Explicit registry lookup failures silently fall back to web search
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_package()` (~line 1356)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**: With `registry="npm"` and a mocked npm registry timeout,
  the tool returns `Package Info [web]` search results instead of reporting that
  the npm registry lookup failed.
- **Root cause**: Request and JSON errors are swallowed with `continue`; if no
  direct result exists, the generic web fallback always runs.
- **Impact**: Callers who explicitly requested a registry cannot distinguish
  package-not-found from registry/network failure.
- **Fix direction**: For explicit registries, return a direct registry error or
  include the fallback reason in the output.

### BUG-041 [medium]: `search_package` can crash on non-object JSON responses
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_package()` (~line 1316)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**:
  ```python
  # Mock resp.json() to return [] with status_code = 200.
  await search_package("pkg", registry="pypi")
  # AttributeError: 'list' object has no attribute 'get'
  ```
- **Root cause**: After `data = resp.json()`, the formatter assumes `data` is a
  dict. The exception handler catches JSON decode errors, not wrong JSON types.
- **Impact**: Malformed or unexpected registry responses can crash the tool.
- **Fix direction**: Validate `isinstance(data, dict)` before accessing `.get()`.

### BUG-042 [medium]: `search_security` hides OSV API failures as missing data
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_security()` (~line 1555)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**: Mock OSV to return HTTP 500 or raise a network error for all
  ecosystems. The tool eventually returns `No vulnerability data found`.
- **Root cause**: Non-200 statuses, request errors, and JSON decode errors are
  swallowed while iterating ecosystems. No failure detail is retained.
- **Impact**: A service outage can look like a clean "no data" result, which is
  dangerous for dependency risk checks.
- **Fix direction**: Track API errors separately and surface them when no
  successful ecosystem query was completed.

### BUG-043 [medium]: `search_security` ignores OSV `CVSS_V4` and `CVSS_V2` severities
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_security()` (~line 1580)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**:
  ```python
  vuln = {"severity": [{"type": "CVSS_V4", "score": "CVSS:4.0/..."}]}
  # Rendered with no CVSS field.
  ```
- **Root cause**: The formatter only checks `sev.get("type") == "CVSS_V3"`.
  The OSV schema allows `CVSS_V2`, `CVSS_V3`, `CVSS_V4`, and other severity
  types.
- **Impact**: Newer advisories that publish only CVSS v4 vectors lose severity
  information in the output.
- **Fix direction**: Prefer the highest supported CVSS version, or render all
  severity entries.

### BUG-044 [medium]: Direct API engines ignore user region, SafeSearch, and freshness options
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_search_brave_api()`, `_search_google_api()`,
  `_search_bing_api()`, `_search_baidu()` (~lines 529-586)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**:
  ```python
  await web_search(
      "python", engine="bing", region="cn-zh",
      safesearch="on", timelimit="d",
  )
  # URL still uses mkt=en-US and no safeSearch/freshness parameter.
  ```
- **Root cause**: `_search_with_engine()` receives `region`, `safesearch`, and
  `timelimit`, but the direct API search functions only accept `query` and
  `max_results`. Google, Bing, and Brave all have documented query parameters
  for at least some of these controls.
- **Impact**: User-facing search filters silently work only for DDGS/auto, not
  for most named API engines.
- **Fix direction**: Thread supported filter options into each backend or
  document per-engine limitations in tool output.

### BUG-045 [medium]: `_title_similar` returns 1.0 for two empty titles causing false dedup
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_title_similar()` and `_is_duplicate()`
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: `SequenceMatcher(None, "", "").ratio()` → 1.0. Two results
  both missing titles are always deduplicated, even with different body snippets.
- **Root cause**: Two empty strings are trivially identical but represent
  unknown/missing data, not true content identity.
- **Impact**: Combined with BUG-004, href-less AND title-less results are
  guaranteed false-positive dedup.
- **Fix direction**: Short-circuit to 0.0 when both strings are empty.

### BUG-046 [medium]: SearXNG URL double `/search` path when `SEARXNG_URL` has path prefix
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_search_searxng()`
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: `SEARXNG_URL=https://host/searxng` → constructs
  `https://host/searxng/search` → 404 if SearXNG is rooted at `/searxng`.
- **Root cause**: `.rstrip("/")` only removes slashes, not path segments.
  `/search` is unconditionally appended.
- **Impact**: Reverse-proxy path-prefix deployments get 404 errors.
- **Fix direction**: Append `/search` only when path is empty or `/`.

### BUG-047 [medium]: "Needs API key" response cached; persists after key is set
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_do_search()` cache logic
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: Search with `engine="brave"` before setting key → cached
  "needs key". Set key, retry → cache hit → still shows "needs key".
- **Root cause**: Cache key does not include API key presence state.
- **Impact**: Users fixing API config see stale errors for CACHE_TTL (5 min).
- **Fix direction**: Skip caching for key-not-configured responses.

### BUG-048 [medium]: `search_rss` parses XML with `html.parser` losing namespace/CDATA info
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_rss()` → `_parse_feed()`
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: Atom feeds with `xml:base`, `<dc:creator>`, or CDATA sections
  parsed by `html.parser` — namespace semantics lost.
- **Root cause**: `BeautifulSoup(html, "html.parser")` used for XML content.
- **Impact**: Relative URLs in feeds may resolve incorrectly. Dublin Core metadata
  inaccessible.
- **Fix direction**: Use `lxml-xml` or `xml` parser; fall back to `html.parser`.

### BUG-049 [medium]: `search_crawl` skips `./` and `../` relative paths (distinct from BUG-010)
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_crawl()` → `_extract_links()`
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: `<a href="./intro.html">`, `<a href="../api/">` — silently
  skipped; only `/`-prefixed and `http`-prefixed links handled.
- **Root cause**: Resolution limited to two prefix checks, no `urljoin`.
- **Impact**: Most static doc sites using `./` or `../` miss internal pages.
- **Fix direction**: Use `urljoin(base_url, href)` for all link resolution.

### BUG-050 [medium]: `_cache_set` stores mutable reference — callers can corrupt cache
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_cache_set()`
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: Caller modifies result list after `_cache_set` → cached
  version also modified (same list object).
- **Root cause**: Direct list reference stored, no defensive copy.
- **Impact**: Silent cache corruption when callers mutate results.
- **Fix direction**: Store deep copy or document immutability requirement.

### BUG-051 [medium]: `search_deep` `asyncio.gather` without `return_exceptions=True` aborts all on one failure
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_deep()`
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: One `_fetch_one` raises unhandled exception → all other
  in-flight fetches cancelled.
- **Root cause**: `asyncio.gather(*fetch_tasks)` defaults to `return_exceptions=False`.
- **Impact**: Single malformed URL kills entire deep research operation.
- **Fix direction**: Use `asyncio.gather(*fetch_tasks, return_exceptions=True)`.

### BUG-052 [medium]: (MERGED -> see BUG-032) `_fetch` `resp.text` lossy-decodes binary content into garbled text
- **Status**: ⚪ merged into BUG-032
- **Severity**: medium

### BUG-053 [medium]: search_session(clear) is annotated as read-only and idempotent
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, _READONLY_TOOL and search_session()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: search_session("x", action="clear") deletes stored history while annotated readOnlyHint=True.
- **Root cause**: All tools share the same read-only annotation object.
- **Impact**: MCP clients may treat destructive state clearing as safe.
- **Fix direction**: Use separate annotations or split view and clear tools.

### BUG-054 [medium]: Docker image runs the MCP server as root
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `Dockerfile`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: The Dockerfile never creates a user or switches away from UID 0.
- **Root cause**: python:3.12-slim defaults to root.
- **Impact**: A compromised process has root privileges inside the container.
- **Fix direction**: Create an unprivileged user and set USER before ENTRYPOINT.

### BUG-055 [medium]: _fetch allows plaintext HTTP for arbitrary content fetches
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, _validate_url()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: _validate_url("http://example.com") returns None.
- **Root cause**: HTTP and HTTPS are accepted equally.
- **Impact**: Fetched content can be modified in transit.
- **Fix direction**: Default to HTTPS-only with explicit opt-in for HTTP.

### BUG-056 [medium]: _fetch advertises JSON support but web_fetch cannot parse JSON
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, _fetch(), _extract_text()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Accept includes application/json, but _extract_text looks only for HTML body/main/article.
- **Root cause**: Fetch and extraction layers disagree on supported media.
- **Impact**: Valid JSON endpoints produce confusing no-readable-text errors.
- **Fix direction**: Remove JSON from Accept or add JSON pretty-print extraction.

### BUG-057 [medium]: _fetch error messages discard useful HTTP response bodies
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, _fetch()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: A 403 page with explanatory text is returned as only HTTP 403.
- **Root cause**: Non-2xx responses are collapsed to status code only.
- **Impact**: Users lose actionable upstream diagnostics.
- **Fix direction**: Include a short sanitized text excerpt for text responses.

### BUG-058 [medium]: _do_search cancels pending tasks without awaiting cancellation
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, _do_search()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: On overall timeout, pending tasks are cancelled and ignored.
- **Root cause**: Cancelled tasks are not awaited with gather(return_exceptions=True).
- **Impact**: Background cleanup can produce warnings or lingering work.
- **Fix direction**: Await cancelled pending tasks before returning.

### BUG-059 [medium]: SEARCH_ENGINES overrides explicit per-call engine choices
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, _resolve_engines()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: With SEARCH_ENGINES=auto, web_search(..., engine="google") resolves to auto.
- **Root cause**: Environment override is applied before requested engine handling.
- **Impact**: Per-call engine selection can be silently ignored.
- **Fix direction**: Apply override only for engine="auto" or document it as absolute.

### BUG-060 [medium]: General search parameters are not validated before DDGS calls
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, web_search(), _do_search()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: safesearch="banana" and timelimit="decade" are forwarded to DDGS.
- **Root cause**: Docstring value sets are not enforced.
- **Impact**: Invalid options fail inconsistently by backend.
- **Fix direction**: Validate region, safesearch, and timelimit centrally.

### BUG-061 [medium]: Generic transient engine exceptions are not retried
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, _do_search()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: A temporary httpx.ReadError from an engine is returned immediately.
- **Root cause**: Retry logic handles only DDGS rate-limit and timeout exceptions.
- **Impact**: Avoidable transient failures surface to users.
- **Fix direction**: Retry known transient httpx and JSON errors.

### BUG-062 [medium]: Direct API engines do not retry malformed JSON responses
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, direct API engine functions`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: HTTP 200 with an HTML error page makes resp.json() fail once.
- **Root cause**: JSON decode errors are treated as final generic exceptions.
- **Impact**: Temporary provider glitches fail searches.
- **Fix direction**: Convert JSON decode failures into retryable engine errors.

### BUG-063 [medium]: SearXNG ignores search filters
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, _search_searxng()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: engine="searxng", safesearch="on", timelimit="d" still sends only q/categories/pageno.
- **Root cause**: The SearXNG function accepts only query and max_results.
- **Impact**: User filtering silently does nothing.
- **Fix direction**: Forward supported SearXNG safe-search, language, and time parameters.

### BUG-064 [medium]: SearXNG result parsing assumes every result is a dictionary
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, _search_searxng()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: data["results"] = ["bad"] causes AttributeError on .get.
- **Root cause**: Result item types are not validated.
- **Impact**: One malformed item can fail the engine.
- **Fix direction**: Skip non-dict result items.

### BUG-065 [medium]: Baidu parser can return Baidu redirect URLs instead of destination URLs
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, _search_baidu()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: If data-url and mu are absent, href may be a Baidu redirect URL.
- **Root cause**: Redirect wrappers are not resolved.
- **Impact**: Users get tracking/redirect URLs instead of source URLs.
- **Fix direction**: Resolve wrappers or label them as redirects.

### BUG-066 [medium]: Specialized search tools hardcode worldwide/off filters
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, search_code(), search_docs(), search_paper(), search_github()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: search_docs has no way to set region or safesearch.
- **Root cause**: Only web_search exposes those knobs.
- **Impact**: Users cannot localize or moderate scoped searches.
- **Fix direction**: Add optional region and safesearch parameters or document fixed behavior.

### BUG-067 [medium]: Search result snippets are not length-limited before formatting
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, _build_result()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: A mocked result body with 50000 chars is emitted in full format.
- **Root cause**: Only titles are truncated.
- **Impact**: One result can consume a large context window.
- **Fix direction**: Truncate snippets before rendering.

### BUG-068 [medium]: Full result formatter allows Markdown injection from result title and body
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, _build_result()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: A title containing a newline and heading syntax is rendered as Markdown.
- **Root cause**: Provider fields are interpolated without escaping.
- **Impact**: Malicious results can reshape rendered output.
- **Fix direction**: Escape title/body Markdown or render snippets in safe blocks.

### BUG-069 [medium]: _extract_text drops useful noscript content
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, STRIP_TAGS, _extract_text()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: A JS-heavy page with article text in <noscript> returns little or no text.
- **Root cause**: noscript is decomposed as boilerplate.
- **Impact**: Fallback content can be lost.
- **Fix direction**: Preserve meaningful noscript text or strip only duplicates.

### BUG-070 [medium]: web_fetch discards all hyperlink destinations
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, _extract_text()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: <a href="/install">install</a> becomes only install.
- **Root cause**: Anchors are flattened by get_text().
- **Impact**: Fetched docs lose navigation and citation URLs.
- **Fix direction**: Convert anchors to Markdown links before text extraction.

### BUG-071 [medium]: web_fetch flattens tables into ambiguous plain text
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, _extract_text()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Parameter tables lose row and column relationships.
- **Root cause**: Tables are not converted to Markdown or structured text.
- **Impact**: API docs become harder to interpret correctly.
- **Fix direction**: Convert simple tables to Markdown tables.

### BUG-072 [medium]: web_fetch has no meta-description fallback
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, _extract_text(), web_fetch()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: A page with only meta description and no body text returns no readable text.
- **Root cause**: Metadata fields are ignored.
- **Impact**: Sparse pages lose their only useful summary.
- **Fix direction**: Fall back to meta/OpenGraph descriptions.

### BUG-073 [medium]: PyPI package names are not URL-encoded in direct lookup URLs
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, search_package()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: package="bad/name" builds https://pypi.org/pypi/bad/name/json.
- **Root cause**: Raw package input is interpolated into the path.
- **Impact**: Invalid names can alter registry path structure.
- **Fix direction**: Validate and URL-encode path components.

### BUG-074 [medium]: npm lookup fails for packages without a latest dist-tag
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, search_package()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Existing package without latest dist-tag makes /latest return non-200.
- **Root cause**: Only the /latest endpoint is queried.
- **Impact**: Existing packages can fall back to imprecise web search.
- **Fix direction**: Query package root and resolve dist-tags.latest.

### BUG-075 [medium]: Package version specifiers are sent as package names
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, search_package(), search_security()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: search_security("requests==2.31.0") sends that whole string as the package name.
- **Root cause**: Requirement syntax is not parsed.
- **Impact**: Common dependency specifiers produce false no-data results.
- **Fix direction**: Parse ecosystem-specific requirement syntax.

### BUG-076 [medium]: Package fallback search can include raw newlines and search operators
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, search_package() fallback`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: package="requests
site:evil.example" can reach fallback search query.
- **Root cause**: Fallback uses original package argument instead of normalized name.
- **Impact**: Malformed input can alter fallback search scope.
- **Fix direction**: Use validated normalized names in fallback queries.

### BUG-077 [medium]: GitHub issue state is lowercased but not stripped
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, search_github_issues()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: state=" open " behaves like all.
- **Root cause**: Code uses state.lower() instead of state.strip().lower().
- **Impact**: Whitespace silently disables filtering.
- **Fix direction**: Strip and validate state.

### BUG-078 [medium]: GitHub incomplete_results flag is ignored
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, search_github_issues()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: GitHub can return incomplete_results=true, but output shows no warning.
- **Root cause**: Only items and total_count are rendered.
- **Impact**: Users may treat partial results as complete.
- **Fix direction**: Show a warning when incomplete_results is true.

### BUG-079 [medium]: GitHub issue body truncation can leave unclosed Markdown fences
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, search_github_issues()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: First 500 chars contain opening triple-backtick but no close.
- **Root cause**: Body is sliced blindly.
- **Impact**: One issue can corrupt rendering of following results.
- **Fix direction**: Close unbalanced fences or sanitize body Markdown.

### BUG-080 [medium]: GitHub issue titles and bodies are rendered without Markdown escaping
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, search_github_issues()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Issue title containing Markdown heading syntax is inserted directly.
- **Root cause**: GitHub fields are treated as trusted Markdown.
- **Impact**: Malicious issue content can reshape output.
- **Fix direction**: Escape titles and sanitize body excerpts.

### BUG-081 [medium]: Whitespace-only GITHUB_TOKEN is sent as Authorization
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, search_github_issues()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: GITHUB_TOKEN="   " sends Authorization: Bearer spaces.
- **Root cause**: Token is checked by truthiness only.
- **Impact**: Requests may fail instead of falling back unauthenticated.
- **Fix direction**: Strip token and omit header if empty.

### BUG-082 [medium]: search_security cannot determine whether a specific installed version is affected
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, search_security()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Tool has no version parameter for requests==2.31.0.
- **Root cause**: OSV version-aware queries are not exposed.
- **Impact**: Users may misread historical vulns as affecting their version.
- **Fix direction**: Add optional version parameter and query OSV with it.

### BUG-083 [medium]: Withdrawn OSV vulnerabilities are displayed as active
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, search_security()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: An OSV record with withdrawn timestamp is formatted as normal.
- **Root cause**: Formatter never checks withdrawn.
- **Impact**: Withdrawn advisories create false alarms.
- **Fix direction**: Hide or clearly label withdrawn records.

### BUG-084 [medium]: search_security can crash on malformed affected entries
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, _fixed_versions()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: affected=["bad"] causes .get on a string.
- **Root cause**: Nested OSV types are assumed to be dictionaries.
- **Impact**: One malformed advisory can fail the lookup.
- **Fix direction**: Validate nested item types.

### BUG-085 [medium]: search_security can crash on malformed severity entries
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, search_security()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: severity=["HIGH"] causes .get on a string.
- **Root cause**: Severity entries are assumed to be dictionaries.
- **Impact**: Unexpected JSON shape can crash the tool.
- **Fix direction**: Skip non-dict severity entries.

### BUG-086 [medium]: search_security mishandles string aliases
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, search_security()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: aliases="CVE-1" is iterated as characters.
- **Root cause**: Aliases are assumed to be a list.
- **Impact**: Alias output becomes misleading.
- **Fix direction**: Coerce strings to a single alias or require list.

### BUG-087 [medium]: search_security ignores database_specific severity
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, search_security()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: database_specific.severity is not rendered when top-level CVSS is absent.
- **Root cause**: Only the top-level severity list is inspected.
- **Impact**: Important advisory severity can be omitted.
- **Fix direction**: Fall back to known database-specific severity fields.

### BUG-088 [medium]: RSS descriptions are truncated before escaped HTML is sanitized
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, search_rss()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Escaped HTML description is sliced before parsing.
- **Root cause**: Truncation precedes cleanup.
- **Impact**: Output can contain broken entities or cut tags.
- **Fix direction**: Sanitize first, then truncate text.

### BUG-089 [medium]: RSS entries without link do not fall back to guid or id
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, search_rss()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: RSS item with guid isPermaLink=true but no link renders without a URL.
- **Root cause**: Only link is inspected for item destinations.
- **Impact**: Valid feed entries lose destinations.
- **Fix direction**: Fallback to RSS guid and Atom id where appropriate.

### BUG-090 [medium]: search_crawl ignores robots.txt
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, search_crawl()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Crawling a site with Disallow still fetches discovered disallowed paths.
- **Root cause**: Crawler has no robots.txt check.
- **Impact**: The tool can fetch paths a site asks bots not to crawl.
- **Fix direction**: Respect robots.txt or document noncompliance.

### BUG-091 [medium]: search_crawl has no concurrency limit
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, search_crawl()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: base_url mode can schedule 50 fetches at once.
- **Root cause**: All tasks go directly to asyncio.gather().
- **Impact**: A crawl can spike outbound connections and stress target sites.
- **Fix direction**: Add a semaphore limit.

### BUG-092 [medium]: search_crawl does not filter non-HTML assets
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, search_crawl()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Same-domain .pdf/.zip/.png links are crawled and parsed as text.
- **Root cause**: Link extraction does not filter assets.
- **Impact**: Crawl slots and bandwidth are wasted.
- **Fix direction**: Skip binary extensions and verify content type.

### BUG-093 [medium]: search_crawl ignores HTML base href
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py, search_crawl()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: A page with <base href="https://docs.example/v1/"> resolves links against original URL.
- **Root cause**: The crawler never inspects base tags.
- **Impact**: Valid links can resolve incorrectly.
- **Fix direction**: Honor document base URL when present.

### BUG-094 [medium]: README quick-start install does not install the console script
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `README.md, pyproject.toml`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: README uses pip install -r requirements.txt only.
- **Root cause**: The project itself is not installed.
- **Impact**: codingWebSearch console script is unavailable after quick start.
- **Fix direction**: Use pip install -e . or equivalent.

### BUG-095 [medium]: README and pyproject dependency minimums disagree
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `README.md, requirements.txt, pyproject.toml`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: README lists newer minimums than pyproject allows.
- **Root cause**: Dependency constraints are duplicated.
- **Impact**: Package installs can resolve versions below documented tested minimums.
- **Fix direction**: Use one source of truth for constraints.

### BUG-096 [medium]: Troubleshooting minimum versions disagree with package metadata
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `TROUBLESHOOTING.md, pyproject.toml`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Troubleshooting says mcp>=1.27/httpx>=0.28/bs4>=4.14 while pyproject allows older.
- **Root cause**: Docs and package metadata drifted.
- **Impact**: Users can install versions docs say are unsupported.
- **Fix direction**: Align docs, requirements, and pyproject.

### BUG-097 [medium]: Docker Compose advertises optional HTTP transport that server does not implement
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `docker-compose.yml, server.py`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Compose comments expose 8080 for HTTP transport, but main() starts stdio server.
- **Root cause**: Comment describes a mode not wired in code.
- **Impact**: Users can expose a dead port expecting HTTP MCP.
- **Fix direction**: Remove comment or implement HTTP transport.

### BUG-098 [medium]: Docker Compose restart policy can loop a stdio MCP container
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `docker-compose.yml`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Without an attached client, stdio process may exit and restart unless-stopped repeats.
- **Root cause**: Restart policy is for daemon-style services.
- **Impact**: Users may see noisy restart loops.
- **Fix direction**: Remove restart for stdio mode or document client attachment.

### BUG-099 [medium]: Docker publish workflow may lack package write permissions
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `.github/workflows/docker-publish.yml`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Restricted default GITHUB_TOKEN permissions can make GHCR push fail.
- **Root cause**: Workflow permissions are implicit.
- **Impact**: Tagged releases may fail to publish images.
- **Fix direction**: Declare contents: read and packages: write.

### BUG-100 [medium]: BUGS.md is ignored by git
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `.gitignore, BUGS.md`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: git status --ignored BUGS.md shows !! BUGS.md.
- **Root cause**: BUGS.md is listed under internal dev docs.
- **Impact**: The bug tracker will not be committed by normal workflows.
- **Fix direction**: Track BUGS.md or move persistent bugs to a tracked file.

### BUG-101 [medium]: `_is_duplicate` drops distinct pages with generic short titles
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_is_duplicate()` (~line 499)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**:
  ```python
  seen = [{"title": "Home", "href": "https://a.example/"}]
  _is_duplicate({"title": "Home", "href": "https://b.example/"}, seen)  # True
  ```
- **Root cause**: The title-similarity branch treats two identical generic
  titles as duplicates even when both URLs are non-empty and point to different
  hosts.
- **Impact**: Multi-engine searches can silently discard valid results from
  different sites whose titles are common labels such as "Home", "Docs", or
  "Overview".
- **Fix direction**: Require stronger evidence for very short/generic titles,
  or combine title similarity with host/path similarity before deduping.

### BUG-102 [medium]: Compact formatter renders unsafe upstream URLs as clickable markdown
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_format_compact()` (~line 393)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**:
  ```python
  _format_compact("q", [{"title": "x", "href": "javascript:alert(1)", "engine": "e"}], "L")
  # Emits: [x](javascript:alert(1))
  ```
- **Root cause**: Search-engine result URLs are rendered directly in markdown
  links without revalidating the scheme.
- **Impact**: A malicious or malformed upstream result can create clickable
  `javascript:` or similarly unsafe links in clients that render markdown.
- **Fix direction**: Validate or escape result URLs before rendering markdown;
  fall back to plain text for unsupported schemes.

### BUG-103 [medium]: Full formatter allows URL newlines to forge extra output lines
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_build_result()` (~line 353)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**:
  ```python
  _build_result(1, {"title": "T", "href": "https://ok\n2. forged", "body": "", "engine": "x"})
  ```
- **Root cause**: `href` is converted to text and interpolated into the
  `URL:` line without stripping control characters.
- **Impact**: Malformed provider data can forge additional result lines or
  confuse downstream parsers that rely on the formatted output structure.
- **Fix direction**: Normalize URLs by removing CR/LF characters before
  formatting, or render untrusted fields through a markdown-safe sanitizer.

### BUG-104 [medium]: Search cache is shared across API identities
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_do_search()` cache key construction (~line 748)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**:
  ```python
  # Run the same google query with GOOGLE_CSE_ID=cx1, then switch to cx2.
  # The second call can return cached cx1 results because the cache key has no
  # API identity or CSE identifier component.
  ```
- **Root cause**: Cache keys include query, engine, filters, and format, but not
  credential identity, Google CSE ID, or equivalent provider configuration.
- **Impact**: Long-running servers that rotate API keys or CSE IDs can return
  stale results from the previous provider configuration.
- **Fix direction**: Include a stable provider-configuration fingerprint in the
  cache key or clear cache when relevant environment variables change.

### BUG-105 [medium]: Missing API keys are returned as successful tool results
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_do_search()` (~line 774)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**:
  ```python
  await web_search("python", engine="google")
  # Returns a normal string asking for GOOGLE_API_KEY + GOOGLE_CSE_ID.
  ```
- **Root cause**: The single-engine key check returns a string from
  `_NO_KEY_MSGS` instead of raising `SearchError`.
- **Impact**: MCP clients see a successful tool result even though the requested
  engine could not run, making automated fallback/error handling unreliable.
- **Fix direction**: Raise `SearchError` for requested-engine configuration
  failures and keep human-readable recovery hints in the exception message.

### BUG-106 [medium]: Unknown package registries are returned as successful results
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_package()` (~line 1298)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**:
  ```python
  await search_package("requests", registry="pip")
  # Returns "Unknown registry 'pip'..." as a normal success string.
  ```
- **Root cause**: Invalid `registry` values return a plain string instead of
  raising `SearchError`.
- **Impact**: Callers cannot reliably distinguish a package lookup result from
  an invalid-argument failure.
- **Fix direction**: Validate registry values up front and raise `SearchError`
  with the supported values.

### BUG-107 [medium]: One crawl-page extraction exception aborts the whole batch
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_crawl()` (~line 1794)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**:
  ```python
  async def bad_extract(_html):
      raise ValueError("parse broke")
  server._extract_text = bad_extract
  await search_crawl(urls="https://a.example,https://b.example", max_pages=2)
  ```
- **Root cause**: `asyncio.gather(*tasks)` is used without
  `return_exceptions=True`, and `_crawl_one()` does not catch extraction
  exceptions.
- **Impact**: A single malformed page can fail an entire multi-URL crawl instead
  of being reported as one failed page.
- **Fix direction**: Catch per-page parsing failures inside `_crawl_one()` or
  gather exceptions and render them as failed page entries.

### BUG-108 [medium]: `web_fetch` leaks parser exceptions instead of `SearchError`
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `web_fetch()` (~line 1829)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**:
  ```python
  async def bad_extract(_html):
      raise ValueError("parse broke")
  server._extract_text = bad_extract
  await web_fetch("https://example.com")
  ```
- **Root cause**: The call to `_extract_text(html)` is not wrapped in
  `SearchError` conversion.
- **Impact**: Parser failures surface as raw exceptions, bypassing the tool's
  usual recovery-message path.
- **Fix direction**: Catch parsing exceptions and raise `SearchError("Failed to
  parse page content: ...")`.

### BUG-109 [medium]: `web_fetch_code` leaks parser exceptions instead of `SearchError`
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `web_fetch_code()` (~line 1865)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**:
  ```python
  # Make BeautifulSoup parsing or code-block extraction raise.
  await web_fetch_code("https://example.com/code")
  ```
- **Root cause**: `_parse_code_blocks()` runs in a thread and exceptions from
  `asyncio.to_thread()` propagate directly.
- **Impact**: Malformed HTML or parser edge cases can produce raw tool failures
  rather than a controlled "could not parse code blocks" error.
- **Fix direction**: Wrap `asyncio.to_thread(_parse_code_blocks)` and convert
  failures to `SearchError`.

### BUG-110 [medium]: `search_rss` parser exceptions bypass RSS error handling
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_rss()` (~line 1646)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**:
  ```python
  # A malformed or unusually large feed that triggers a BeautifulSoup/parser
  # exception propagates out of search_rss instead of returning a feed error.
  ```
- **Root cause**: Only the `(result, err)` return from `_parse_feed()` is
  handled. Exceptions raised inside the parser thread are not caught.
- **Impact**: Feed parsing can fail with raw exceptions rather than a stable MCP
  error result.
- **Fix direction**: Catch exceptions around `asyncio.to_thread(_parse_feed)` and
  raise `SearchError` with the feed URL and parse failure.

### BUG-111 [medium]: `search_package(auto)` has no overall registry timeout
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_package()` (~line 1294)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**:
  ```python
  await search_package("missing-package", registry="auto")
  # Can wait through PyPI, npm, crates.io, and pkg.go.dev sequential timeouts.
  ```
- **Root cause**: Registry probes are attempted sequentially, each with
  `FETCH_TIMEOUT`, and there is no total time budget for the operation.
- **Impact**: A slow registry chain can make a single package lookup take much
  longer than users expect.
- **Fix direction**: Add an overall timeout or run registry probes concurrently
  with deterministic result selection.

### BUG-112 [medium]: `search_security(auto)` has no overall OSV timeout
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_security()` (~line 1532)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**:
  ```python
  await search_security("package-name", ecosystem="auto")
  # Can wait through six sequential OSV requests.
  ```
- **Root cause**: Auto mode checks PyPI, npm, crates.io, Go, Maven, and RubyGems
  sequentially using `FETCH_TIMEOUT` for every request.
- **Impact**: Network slowness can turn a security check into a long blocking
  operation.
- **Fix direction**: Use a total OSV query budget or issue ecosystem probes
  concurrently while preserving deterministic output order.

### BUG-113 [medium]: `search_security(auto)` reports cross-ecosystem vulnerabilities for same-name packages
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_security()` (~line 1532)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**:
  ```python
  await search_security("shared-name", ecosystem="auto")
  # Reports vulnerabilities for every OSV ecosystem with that package name.
  ```
- **Root cause**: Auto mode does not identify the intended ecosystem. It queries
  every supported ecosystem and aggregates any hits.
- **Impact**: Packages with the same name in multiple ecosystems can produce
  irrelevant vulnerability warnings for the dependency the user actually meant.
- **Fix direction**: Ask for ecosystem when more than one ecosystem has matches,
  or add package-name heuristics only when confidence is high.

### BUG-114 [medium]: Multiple CVSS_V3 records overwrite each other in OSV output
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_security()` (~line 1580)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**:
  ```python
  vuln = {"severity": [
      {"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/..."},
      {"type": "CVSS_V3", "score": "CVSS:3.1/AV:L/..."},
  ]}
  ```
- **Root cause**: The loop assigns `severity = ...` for every CVSS_V3 entry, so
  the last value wins.
- **Impact**: The displayed severity can be lower priority or otherwise
  misleading when OSV provides multiple CVSS vectors.
- **Fix direction**: Render all severity entries or choose the highest-scoring
  vector after parsing CVSS scores.

### BUG-115 [medium]: OSV alias truncation can hide the primary CVE identifier
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_security()` (~line 1577)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**:
  ```python
  vuln = {"aliases": ["GHSA-a", "PYSEC-b", "RUSTSEC-c", "CVE-2026-1234"]}
  # Only the first three aliases are rendered.
  ```
- **Root cause**: Alias output slices `aliases[:3]` without prioritizing CVE or
  ecosystem-primary identifiers.
- **Impact**: Users may miss the CVE identifier they need for compliance,
  scanner correlation, or advisory lookup.
- **Fix direction**: Prefer CVE aliases when present, then include remaining
  aliases up to a reasonable output limit.

### BUG-116 [medium]: GitHub issue search allows qualifier injection through `repo`
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_github_issues()` (~line 1432)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**:
  ```python
  await search_github_issues(repo="owner/repo state:closed", query="bug", state="open")
  # Query includes both "state:closed" and "state:open".
  ```
- **Root cause**: `repo` is appended as `repo:{repo.strip()}` without validating
  the `owner/repo` grammar or escaping search qualifiers.
- **Impact**: User input can override or conflict with intended filters, causing
  GitHub results to come from the wrong scope or state.
- **Fix direction**: Prevalidate `repo` as `owner/repo` and reject whitespace or
  qualifier characters.

### BUG-117 [medium]: `search_deep` trusts formatted search output when selecting fetch URLs
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_deep()` (~line 1154)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**:
  ```python
  # A search result snippet containing a line like "   URL: https://attacker.example"
  # can be parsed as a fetch target from the formatted markdown output.
  ```
- **Root cause**: `search_deep` runs `_do_search()` to produce markdown, then
  reparses URLs from that markdown instead of using structured result objects.
- **Impact**: Untrusted title/snippet/body text can influence which pages are
  fetched during deep research.
- **Fix direction**: Keep structured search results for `search_deep` and format
  them only after the fetch target list is selected.

### BUG-118 [medium]: `search_crawl` loses same-site links after canonical host redirects
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_crawl()` (~line 1758)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**:
  ```python
  await search_crawl(base_url="https://example.com", max_pages=5)
  # If the fetched HTML is from https://www.example.com, same-site links are skipped.
  ```
- **Root cause**: Same-domain filtering uses the original `base_url` netloc
  instead of the final response URL after redirects or canonical host changes.
- **Impact**: Crawl mode can return only the base page for sites that redirect
  between apex and `www` hosts.
- **Fix direction**: Track the final URL from `_fetch()` or normalize canonical
  host aliases before filtering links.

### BUG-119 [medium]: `search_crawl` treats default ports as different domains
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_crawl()` (~line 1768)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**:
  ```python
  # base_url host is "example.com"; page links to "https://example.com:443/docs".
  # The link is skipped because netloc strings differ.
  ```
- **Root cause**: Same-domain comparison uses raw `urlparse(...).netloc`, which
  includes explicit ports.
- **Impact**: Valid same-site links using `:443` or `:80` are missed.
- **Fix direction**: Compare normalized `(scheme, hostname, effective_port)`
  tuples instead of raw netloc strings.

### BUG-120 [medium]: HTTP clients trust environment proxies while sending API tokens
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `httpx.AsyncClient(...)` calls (~lines 534, 574, 1447)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**:
  ```powershell
  $env:HTTPS_PROXY = "http://proxy.example:8080"
  $env:GITHUB_TOKEN = "ghp_xxxx"
  # GitHub API traffic is routed through the configured proxy by default.
  ```
- **Root cause**: HTTPX uses proxy-related environment variables by default
  unless `trust_env=False` is set.
- **Impact**: In environments with inherited proxy settings, token-bearing
  Brave, Bing, or GitHub requests may traverse an unexpected proxy.
- **Fix direction**: Set `trust_env=False` for token-bearing provider clients or
  document proxy behavior and provide an explicit opt-in.

### BUG-121 [medium]: `_optimize_query("error")` non-greedy regex leaves dangling parentheses
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_optimize_query()` (~line 332)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: `_optimize_query("at foo(bar(baz))", "error")` strips `at foo(bar(baz`
  and leaves `)` in the query -- a dangling unmatched parenthesis.
- **Root cause**: `at\s+\w+\(.*?\)` uses non-greedy `.*?` which stops at the first `)`.
  Nested function calls in stack traces break at the wrong parenthesis.
- **Impact**: Stack traces with nested calls produce queries with orphaned `)` characters,
  potentially confusing search engine syntax parsers.
- **Fix direction**: Use a balanced-parenthesis pattern or strip only the `at ...` prefix
  without trying to match the full call expression.

### BUG-122 [medium]: `search_docs` missing `timelimit` parameter - inconsistent with sibling tools
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_docs()` (~line 931)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: `search_code`, `search_paper`, `search_github` all accept `timelimit`;
  `search_docs` hardcodes `timelimit=None`. Users searching for recent API changes cannot
  filter by recency.
- **Root cause**: Parameter omission during tool design - no technical reason to exclude it.
- **Impact**: Documentation searches for rapidly-evolving frameworks (React 19, Python 3.13)
  return outdated results mixed with current ones.
- **Fix direction**: Add `timelimit: str | None = None` parameter to `search_docs`.

### BUG-123 [medium]: `_source_freshness` relative-time indicators are English-only
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_source_freshness()` (~line 278)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: Snippet text "vor 3 Stunden aktualisiert" (German), "3jikan mae ni koushin"
  (Japanese), or "il y a 3 heures" (French) all get zero freshness boost.
- **Root cause**: Regex alternation only matches English phrases: `today|yesterday|hours ago|...`
- **Impact**: Non-English search results are systematically scored lower on freshness,
  creating a ranking bias toward English-language sources.
- **Fix direction**: Add common non-English time indicators or rely on year-detection alone
  for non-English snippets.

### BUG-124 [medium]: `_relevance_score` keyword extraction excludes non-ASCII scripts
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_relevance_score()` (~line 295)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: Query with CJK/Cyrillic/Arabic characters -> regex `[a-zA-Z0-9]+` finds
  zero terms -> `query_terms` is empty -> relevance = 0.0 for all results.
- **Root cause**: The word-splitting regex only matches ASCII letters and digits.
  CJK characters, Cyrillic, Arabic, and other scripts are invisible to the tokenizer.
- **Impact**: Non-English queries receive zero relevance scoring. All results get
  the same relevance boost (0.0), making the sort purely authority+freshness based.
- **Fix direction**: Use `\w+` with Unicode flag (`re.UNICODE`) or a broader character class.

### BUG-125 [medium]: `_format_compact` produces empty-link markdown when title value is empty string
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_format_compact()` (~line 402)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: Result with `{"title": "", "href": "https://a.com"}` -> `_as_text(r.get("title"), "No title")`
  returns `""` (default only applies to missing key, not empty-string value) -> output
  `1. [](https://a.com)` - valid but zero-width link text.
- **Root cause**: `_as_text` only replaces None; empty-string values pass through unchanged.
  The "No title" default is never reached for empty-string titles.
- **Impact**: Markdown renderers produce invisible or unclickable links. Users cannot
  identify the result without reading the URL directly.
- **Fix direction**: Treat empty-string title the same as missing: use "No title" default.

### BUG-126 [medium]: `search_deep` code block regex mismatches `_extract_text` fence format
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_deep()` (~line 1174) vs `_extract_text()` (~line 450)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: `_extract_text` produces fence with space before language name.
  `search_deep` regex requires zero characters between backticks and language name.
  The space breaks the match whenever a language is present.
- **Root cause**: `lang_marker = f" {lang}"` inserts a space; `search_deep` expects no space.
- **Impact**: The "Extracted Code Examples" section is empty for pages where
  `_extract_text` correctly identifies code block languages.
- **Fix direction**: Allow optional whitespace in the fence regex or standardize
  `_extract_text` to omit the space before the language name.

### BUG-127 [medium]: `search_deep` code dedup discards cross-source frequency signal
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_deep()` (~line 1207)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: Three sources contain the identical code pattern. `dict.fromkeys`
  dedup reduces to one example. The synthesis says "1 code example(s) extracted"
  instead of "1 pattern found across 3 sources."
- **Root cause**: Deduplication preserves uniqueness but destroys frequency information.
- **Impact**: Users cannot distinguish between a code snippet found in one source vs.
  a pattern confirmed across all fetched sources - a valuable trust signal is lost.
- **Fix direction**: Track frequency alongside uniqueness: show "Pattern X (found in 3/3 sources)."

### BUG-128 [medium]: `search_deep` coverage metric includes failed fetches in success count
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_deep()` (~line 1184)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: Search finds 3 URLs. Fetch 1 succeeds, 2 fail. Error entries are
  appended to `fetched` list. Header says "fetched 3/3 pages" - wrong.
- **Root cause**: The `fetched` list stores both successful excerpts AND warning
  strings for errors. `len(fetched)` counts both indiscriminately.
- **Impact**: Deep research output overstates source coverage. Users may trust
  incomplete research thinking all sources were successfully analyzed.
- **Fix direction**: Maintain separate counters for success and failure.
  Report "fetched 1/3 pages (2 errors)."

### BUG-129 [medium]: `search_similar_repos` appends "github repository" even though already domain-scoped
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_similar_repos()` (~line 1255)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: `query = f"{repo_description}" + " github repository"` while
  `scoped_domains=_GITHUB_DOMAINS` already restricts to GitHub/GitLab/Bitbucket.
- **Root cause**: Redundant query text. The appended keywords occupy query space
  without improving precision - the domain scope already handles it.
- **Impact**: Queries are longer than needed, which can dilute search precision on
  engines that weigh all terms equally.
- **Fix direction**: Remove the " github repository" suffix when domain scoping is sufficient.

### BUG-130 [medium]: `search_package(auto)` registry order favors Python ecosystem
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_package()` (~line 1294)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: `search_package("react", registry="auto")` tries PyPI first.
  PyPI has a `react` package -> returns Python metadata. User likely wanted npm (React.js).
- **Root cause**: Fixed try-order `["pypi", "npm", "crates", "go"]` with break on
  first HTTP 200. No package-name heuristics to guess the intended ecosystem.
- **Impact**: Ambiguous package names return metadata for the wrong ecosystem.
  Users get incorrect version/license info without realizing it is from the wrong registry.
- **Fix direction**: Return all registry matches when ambiguous, or use package-name
  heuristics (capitalization, `@scope/` prefix, common patterns) to prioritize.

### BUG-131 [medium]: `search_news` duplicates "news" in default-topic queries
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_news()` (~line 1390)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: `search_news()` with no topic -> `query = "latest programming technology news"`
  -> `query = f"{query} news"` -> `"latest programming technology news news"`.
- **Root cause**: When `topic` is empty, the default query already ends with "news".
  The unconditional `f"{query} news"` suffix doubles it.
- **Impact**: Minor query quality degradation - search engines handle redundant words
  but the query looks sloppy and may reduce precision slightly.
- **Fix direction**: Only append " news" if the query does not already contain it.

### BUG-132 [medium]: `_check_rate_limit` can return "Wait 0s" while rejecting the request
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_check_rate_limit()` (~line 50)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: Oldest timestamp in window is at `now - 59.999s`. `window - (now - ts)`
  = `60 - 59.999` = `0.001`. `int(0.001)` = `0`. Message: "Wait 0s or try engine='auto'."
- **Root cause**: `int()` truncates toward zero. At the exact edge of the rate limit
  window, the remaining wait time rounds down to zero.
- **Impact**: User sees contradictory message: "you are rate limited, but wait 0 seconds."
  The guidance to switch engines is correct but the wait time is confusing.
- **Fix direction**: Use `math.ceil()` and clamp to at least 1 second: `max(1, ceil(...))`.

### BUG-133 [medium]: `_ENGINE_PRIORITY` places self-hosted SearXNG last
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_ENGINE_PRIORITY` (~line 665)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: User self-hosts SearXNG as primary engine. `engine="all"` runs all
  configured engines - SearXNG results appear last in parallel execution output.
- **Root cause**: SearXNG is hardcoded last in the priority list. Self-hosted instances
  get lowest priority despite being the user's primary infrastructure investment.
- **Impact**: Self-hosted infrastructure is deprioritized relative to free/default
  engines. Users who invested in SearXNG get lowest priority in multi-engine results.
- **Fix direction**: Move SearXNG after auto but before commercial APIs, or let
  `SEARCH_ENGINES` env var fully control priority (it already does - just change default).

### BUG-134 [medium]: `_extract_text` strips `<aside>` - loses supplementary content
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `STRIP_TAGS` (~line 79) and `_extract_text()` (~line 454)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: Documentation pages with `<aside class="note">` containing
  relevant code examples or caveats. The aside content is decomposed before text extraction.
- **Root cause**: `<aside>` is in `STRIP_TAGS` alongside truly irrelevant elements
  (`nav`, `footer`, `script`). HTML5 `<aside>` is semantically meaningful supplementary
  content - not boilerplate.
- **Impact**: Technical documentation pages lose callouts, warnings, and contextual
  code snippets that are explicitly marked up as supplementary.
- **Fix direction**: Remove `aside` from `STRIP_TAGS` and treat it as content-bearing.

### BUG-135 [medium]: `_search_ddgs` passes unvalidated `safesearch` to DDGS
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_search_ddgs()` (~line 513)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: `_search_ddgs("q", safesearch="invalid")` -> DDGS receives
  `safesearch="invalid"` -> behavior undefined (likely ignored, possibly error).
- **Root cause**: No validation of `safesearch` against `("on", "moderate", "off")`.
  Invalid values are silently passed through to the DDGS library.
- **Impact**: Client typos in `safesearch` parameter are not caught. The search
  silently runs with unknown safe-search behavior instead of the intended setting.
- **Fix direction**: Validate `safesearch` before calling DDGS; reject invalid values.

### BUG-136 [medium]: `_search_baidu` CSS selector depends on build-specific hash suffix
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_search_baidu()` -> `_parse_baidu()` (~line 605)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: Selector `span.content-right_8Zs40` contains `8Zs40` - a
  webpack/build hash that changes with every Baidu frontend deployment.
- **Root cause**: Baidu's CSS class names include content hashes for cache busting.
  These change unpredictably when Baidu redeploys their search UI.
- **Impact**: Baidu search silently returns zero snippets after Baidu updates their
  frontend. Users get results with empty body fields - no indication of the root cause.
- **Fix direction**: Use multiple fallback selectors without hashes, or use
  positional/structural selectors (e.g., `.c-container .c-abstract`).

### BUG-137 [medium]: `search_github_issues` blocks valid filter-only queries
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_github_issues()` (~line 1420)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: `search_github_issues(repo="python/cpython", state="open")` with no
  keyword query -> raises "GitHub issue search requires a query." GitHub API accepts
  filter-only searches (repo: + state: without text query).
- **Root cause**: Validation requires `query.strip()` even when other filters
  (repo, labels, state) are present and sufficient for a valid GitHub API request.
- **Impact**: Legitimate filtered searches are blocked. Users must invent a keyword
  to browse recent issues in a repo.
- **Fix direction**: Require non-empty query only when no other filters are present.

### BUG-138 [medium]: `_format_compact` and `_format_links` skip authority-based sorting
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_do_search()` cache/fresh branches (~line 763, ~line 860)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: Request `output_format="compact"` with `sort_by_authority=True`.
  Results are returned in engine order, not authority order. Full format sorts correctly.
- **Root cause**: `_format_results` accepts `show_authority` and sorts when True.
  `_format_compact` and `_format_links` do not accept or use this parameter.
- **Impact**: Users choosing compact/links format for brevity lose the quality ranking
  that full-format users receive. The same query with different formats returns
  results in different (worse) order for compact/links.
- **Fix direction**: Apply `_sort_by_authority` to results before passing to any
  format function, not only inside `_format_results`.

### BUG-139 [medium]: `_relevance_score` penalizes longer, more specific queries
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_relevance_score()` (~line 300)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: Query "async" (1 term) matching 1/1 terms -> 0.3 score.
  Query "python async decorator pattern" (4 terms) matching 3/4 terms -> 0.225 score.
  The more specific query gets a lower relevance score despite being more descriptive.
- **Root cause**: `overlap / len(query_terms)` divides by total query terms. Longer
  queries are harder to fully match, producing lower scores even when more specific.
- **Impact**: Vague one-word queries get higher relevance boosts than precise multi-word
  queries. The scoring function incentivizes query simplicity contrary to search best practices.
- **Fix direction**: Use a saturating metric (e.g., `min(overlap, 5) / min(len(query_terms), 5)`)
  to cap the denominator and avoid penalizing specificity.

### BUG-140 [medium]: `search_tutorial` hardcodes `timelimit="y"` - users cannot override
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_tutorial()` (~line 1638)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: Users searching for brand-new framework tutorials (released this week)
  get year-old results because timelimit is fixed to "y".
- **Root cause**: `timelimit="y"` is hardcoded in the `_do_search` call with no
  parameter to override it.
- **Impact**: Tutorial freshness cannot be controlled. New technologies get stale
  tutorial results even when recent content exists.
- **Fix direction**: Add `timelimit` parameter with default `"y"` for backward compatibility.

### BUG-141 [medium]: `_fetch` does not catch `httpx.TooManyRedirects`
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_fetch()` (~line 484)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: URL with a redirect loop of 10+ hops -> httpx raises `TooManyRedirects`
  -> caught by generic `except Exception` -> returns `"Fetch failed: TooManyRedirects"`.
- **Root cause**: `TooManyRedirects` is not in the specific exception tuple
  `(httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError)`.
- **Impact**: Users see a generic "Fetch failed" message instead of a clear
  "Too many redirects - the URL may be misconfigured" error.
- **Fix direction**: Add `httpx.TooManyRedirects` to the specific exception handlers
  with a clear error message.

### BUG-142 [medium]: `search_error` auto-generated session IDs prevent session sharing
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_error()` (~line 1044)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: Two related error searches without explicit `session_id` -> each
  gets a unique auto-generated ID -> their histories are isolated.
- **Root cause**: `session_id or f"debug-{hashlib.md5(query.encode()).hexdigest()[:8]}"`
  generates per-query IDs instead of a stable default session for error debugging.
- **Impact**: Users debugging the same issue across multiple error messages see
  fragmented session history instead of a unified debug timeline.
- **Fix direction**: Default to a fixed session ID (`"debug-default"`) when no
  explicit `session_id` is given, allowing cross-query context to accumulate.

### BUG-143 [medium]: `search_crawl` same-domain check is port-sensitive for default ports
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_crawl()` -> `_extract_links()` (~line 1768)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: Base URL `https://example.com:443/` finds link `https://example.com/page`.
  `netloc` comparison: `"example.com:443" != "example.com"` -> link skipped.
- **Root cause**: `netloc` includes the port. Explicit default ports (443 for https,
  80 for http) are treated as different from omitted ports.
- **Impact**: Internal links on sites that explicitly specify default ports are
  skipped during crawling - the crawl misses valid same-domain pages.
- **Fix direction**: Normalize ports before comparison: strip default ports (443 for
  https, 80 for http) from both netloc values.

### BUG-144 [medium]: `search_package` crates.io version display shows "?" for pre-release-only crates
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_package()` (~line 1341)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: Crate with only pre-release versions -> `max_stable_version` is null
  -> fallback to `newest_version` which might also be null for an entirely yanked crate
  -> displays `"?"`.
- **Root cause**: The fallback chain produces `"?"` which is ambiguous - is the version
  unknown, or does the crate not exist?
- **Impact**: Users see `v?` and cannot distinguish between "crate not found" and
  "crate has no stable releases."
- **Fix direction**: Add a note when version is unavailable: "v? (no stable release)"
  or check for `description` presence to confirm crate existence.

### BUG-145 [medium]: `list_engines` hardcodes tool count as static text
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `list_engines()` (~line 1961)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: Tool count in `list_engines` table is manually maintained static
  text. Adding a tool requires updating both the decorator AND the table string.
  `_startup_diagnostics` dynamically counts tools; `list_engines` does not.
- **Root cause**: Static text string "## Coding-Agent Tools (21 total)" with a
  manually curated table below it.
- **Impact**: If tools are added/removed without updating `list_engines`, the
  announced tool count and table become stale. The startup diagnostic is dynamic
  and correct; the runtime help is not.
- **Fix direction**: Generate the tool table dynamically at runtime from the MCP
  server's registered tool list.

### BUG-146 [medium]: `search_rss` topic search uses first URL without verifying it is a feed
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_rss()` (~line 1671)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: Topic search for "python" returns a URL like `https://python.org`
  (not a feed). The tool fetches it, parses as RSS/Atom, finds no entries, raises
  "No RSS/Atom entries found."
- **Root cause**: No Content-Type pre-check or URL pattern heuristic before
  committing to a full fetch and parse.
- **Impact**: Confusing error when the real issue is "that URL is not a feed."
  The user does not know whether the feed is empty or the URL is wrong.
- **Fix direction**: HEAD-request the URL and check for `application/rss+xml`
  or `application/atom+xml` Content-Type before full fetch.

### BUG-147 [medium]: `search_security` auto mode queries all ecosystems even after finding vulns
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `search_security()` (~line 1553)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: Package has vulns in npm (found) and also exists in PyPI (no vulns).
  Auto mode continues past npm to check PyPI, Go, Maven, RubyGems - 4 unnecessary API calls.
- **Root cause**: Auto mode iterates all ecosystems even after finding vulns in one.
  No early exit when vulns are found.
- **Impact**: Wasted API calls to OSV. Each call adds latency; unnecessary calls
  add ~1-3 seconds to the response time.
- **Fix direction**: Stop after the first ecosystem that returns vulnerabilities.

### BUG-148 [medium]: Cached search results lack engine label and elapsed metadata
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_do_search()` cache branch (~line 760)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: First search: "## Web [auto]: query\n_3 results in 245ms_".
  Second search (cache hit): "## Web: query\n_3 results_" - missing [auto] tag
  and elapsed time.
- **Root cause**: Cache branch formats with plain `label` and omits `elapsed_ms`;
  fresh branch constructs `engine_label = f"{label} [{used}]"` and passes elapsed.
- **Impact**: Cached and fresh responses have different header formats for the
  identical query. Users cannot distinguish which engine served cached results.
- **Fix direction**: Include engine label and "(cached)" indicator in cached response.

### BUG-149 [medium]: `_format_results` header markdown breaks when query contains underscores
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_format_results()` (~line 380)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: Query `p_value significance` in header `## Web: p_value significance`.
  The underscore pair renders `value` in italics, breaking the header formatting.
- **Root cause**: Query text is interpolated directly into markdown headings without
  escaping markdown-significant characters like `_` and `*`.
- **Impact**: Markdown rendering of headers is corrupted for queries containing
  underscore or asterisk characters, common in technical queries (variable names).
- **Fix direction**: Escape `_` and `*` in query text before markdown interpolation.

### BUG-150 [medium]: `_is_duplicate` title check is 85% threshold even when URLs differ completely
- **Status**: 🔴 open
- **Severity**: medium
- **File**: `server.py`, `_is_duplicate()` (~line 505)
- **Discovered**: 2026-04-30, medium-only audit round
- **Reproduction**: Two results with DIFFERENT non-empty URLs but very similar titles
  (e.g., "Python async guide" vs "Python async tutorial" -> 89% similar) are deduplicated.
  The URL check passes (different URLs) but the title check catches them even though they
  are different pages on the same topic.
- **Root cause**: Title similarity check runs independently of URL check. Even when
  URLs are clearly different, 85% title similarity triggers dedup.
- **Impact**: Legitimate distinct pages on the same topic are removed from results.
  Users miss alternative perspectives because titles are semantically similar.
- **Fix direction**: When URLs are both non-empty and different, skip the title
  similarity check or raise the threshold to 95%.

### BUG-151 [low]: `search_github_issues` silently treats invalid `state` values as "all"
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_github_issues()` (~line 1435)
- **Discovered**: 2026-04-29, final audit round
- **Reproduction**:
  ```python
  search_github_issues(query="bug", state="merged")  # returns all states
  search_github_issues(query="bug", state="unknown") # returns all states
  ```
- **Root cause**: `state_key = state.lower()` is checked against `("open",
  "closed")`. Any other value — including "merged", "unknown", typos like
  "opne" — skips the filter entirely, behaving identically to `state="all"`.
- **Impact**: Users who mistype the state or expect "merged" to be a valid
  filter (GitHub's own API supports it) get unfiltered results without any
  warning that their filter was ignored.
- **Fix direction**: Reject unknown state values with a clear error message
  listing valid options: "open", "closed", "all".

### BUG-152 [low]: `search_crawl` link extraction can consume excessive memory
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_crawl()` → `_extract_links()` (~line 1702)
- **Discovered**: 2026-04-29, final audit round
- **Reproduction**: Crawl a large documentation site with thousands of
  same-domain internal links. All links are collected into a list before
  being sliced to `max_pages`.
- **Root cause**: `_extract_links()` has no early exit condition. It
  iterates over all `<a>` tags on the page and adds every matching link
  to the list. Only after the function returns does the caller slice
  `target_urls[:max_pages]`.
- **Impact**: On pages with many internal links (e.g., sitemaps, large
  documentation indexes), thousands of URLs are stored in memory
  unnecessarily. In extreme cases (10K+ links), this causes a noticeable
  memory spike.
- **Fix direction**: Add `if len(links) >= max_pages: break` inside the
  link extraction loop.

### BUG-153 [low]: `_check_rate_limit` tracker dictionary grows without bound
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_check_rate_limit()` (~line 39), `_RATE_LIMIT_TRACKER` (~line 39)
- **Discovered**: 2026-04-29, final audit round
- **Reproduction**: Over a long server lifetime, use many different engine
  configurations. `_RATE_LIMIT_TRACKER` accumulates keys for every engine
  name ever used, even if that engine is never called again.
- **Root cause**: The per-engine lists are cleaned (old timestamps removed),
  but the dictionary keys are never removed. If an engine like `"searxng"`
  is used once and then the server runs for weeks, that empty list key
  persists.
- **Impact**: Negligible memory leak under normal usage (a few dozen bytes
  per key, 7 keys max). Only relevant for extremely long-running servers
  with dynamic engine configurations.
- **Fix direction**: After cleaning a list, if it's empty, delete the
  key from the tracker: `if not calls: del _RATE_LIMIT_TRACKER[engine]`.

### BUG-154 [low]: `search_github_issues` misidentifies non-rate-limit 403 responses
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_github_issues()` (~line 1451)
- **Discovered**: 2026-04-29, final audit round
- **Reproduction**: If GitHub returns HTTP 403 for IP banning or abuse
  detection, and the response body contains "rate limit", the tool
  incorrectly suggests setting GITHUB_TOKEN.
- **Root cause**: The check `resp.status_code == 403 and "rate limit" in
  resp.text.lower()` assumes all 403s with "rate limit" in the body are
  rate limit issues. GitHub may return 403 for other reasons with
  different messages that happen to mention rate limiting.
- **Impact**: Users see misleading guidance to set GITHUB_TOKEN when the
  real issue is something else (IP ban, suspicious activity detection).
- **Fix direction**: Check `X-RateLimit-Remaining` header instead of
  parsing the response body text. If the header is present and shows 0,
  it's a rate limit issue. Otherwise, return the actual error message.

### BUG-155 [low]: FastMCP server has no `instructions` set
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `mcp = FastMCP("codingWebSearch")` (~line 21)
- **Discovered**: 2026-04-29, FastMCP documentation review
- **Reproduction**: `server.mcp.instructions` is `None`.
- **Root cause**: `FastMCP()` accepts an `instructions` parameter that
  provides server-level guidance to LLMs. It defaults to `None`, meaning
  the LLM receives no description of what the server does, what tools are
  available, or how to use them effectively.
- **Impact**: LLMs relying on the MCP protocol's server-level instructions
  get no guidance about codingWebSearch. Most clients use tool descriptions
  directly, so the practical impact is minimal.
- **Fix direction**: Set `instructions` to a concise description of the
  server's purpose and available capabilities.

### BUG-156 [low]: `_build_result` authority tags and `_sort_by_authority` ranking use different scores
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_build_result()` (~line 354) vs `_sort_by_authority()` (~line 306)
- **Discovered**: 2026-04-29, final audit round
- **Reproduction**: A result from a low-authority but highly relevant and
  fresh source can be sorted to position #1 by `_sort_by_authority` (which
  uses authority+freshness+relevance), but NOT get a `[official]` or
  `[trusted]` tag (which uses raw `_authority` alone).
- **Root cause**: Two different scoring paths:
  - Tag: `result.get("_authority", 0)` — raw pre-computed authority
  - Sort: `_source_authority() + _source_freshness() + _relevance_score()` — combined
- **Impact**: Users may see a top-ranked result without an authority tag
  and wonder why it was ranked so highly, or see a lower-ranked result
  with an `[official]` tag and wonder why it wasn't ranked higher.
- **Fix direction**: Either use the combined sort score for tag thresholds,
  or add a separate relevance/freshness indicator in the output.

### BUG-157 [low]: `search_crawl` same-domain filtering is host-case sensitive
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_crawl()` → `_extract_links()` (~line 1758)
- **Discovered**: 2026-04-29, crawl/RSS/backend audit round
- **Reproduction**:
  ```html
  <a href="https://EXAMPLE.com/docs">Docs</a>
  ```
  ```python
  await search_crawl(base_url="https://example.com", max_pages=2)
  # The link is skipped even though it points to the same host.
  ```
- **Root cause**: The filter compares raw `urlparse(...).netloc` strings.
  Hostnames are case-insensitive, but `"EXAMPLE.com"` and `"example.com"` do
  not compare equal as raw strings.
- **Impact**: Same-domain links can be skipped when a site emits mixed-case
  hosts in absolute URLs.
- **Fix direction**: Compare `urlparse(url).hostname` values instead of
  `netloc`, or normalize hosts to lowercase before comparison.

### BUG-158 [low]: Invalid `output_format` values silently fall back to full output
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_do_search()` (~line 860)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**:
  ```python
  await web_search("python", output_format="json")
  # Returns normal full markdown instead of rejecting "json".
  ```
- **Root cause**: `_do_search()` only branches for `"compact"` and `"links"`.
  Any other value falls through to `_format_results()`.
- **Impact**: Client typos or unsupported format requests are hidden. Callers
  expecting a specific format may parse the wrong output shape.
- **Fix direction**: Validate `output_format` against `{"full", "compact",
  "links"}` before searching.

### BUG-159 [low]: `_check_rate_limit` can tell users to wait `0s`
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_check_rate_limit()` (~line 42)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**: Put ten timestamps at `now - 59.9` seconds in
  `_RATE_LIMIT_TRACKER["brave"]`, then call `_check_rate_limit("brave")`.
  The function can return `Wait 0s` while still rejecting the request.
- **Root cause**: Remaining wait time is rounded down with `int(...)`.
- **Impact**: The recovery hint is misleading at the exact edge of the rate
  limit window.
- **Fix direction**: Use `math.ceil()` and clamp to at least one second while
  the request is still blocked.

### BUG-160 [low]: `_cache_set` can grow the cache beyond the 200-entry limit
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_cache_set()` (~line 218)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**:
  ```python
  for i in range(250):
      _cache_set(str(i), [{"title": str(i)}])
  len(_search_cache)  # 250 if all entries are still fresh
  ```
- **Root cause**: When the cache exceeds 200 entries, `_cache_set()` deletes
  only expired entries. If all entries are newer than `CACHE_TTL`, nothing is
  evicted.
- **Impact**: A busy server with many distinct fresh queries can exceed the
  intended cache size cap.
- **Fix direction**: After removing stale entries, evict oldest fresh entries
  until `len(_search_cache) <= 200`.

### BUG-161 [low]: Compact output can emit broken Markdown links
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_format_compact()` (~line 393)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**:
  ```python
  _format_compact(
      "q",
      [{"title": "Array] docs", "href": "https://example.com/a)b"}],
      "Web",
  )
  # Emits: [Array] docs](https://example.com/a)b)
  ```
- **Root cause**: Markdown link labels and destinations are interpolated
  without escaping `]`, `)`, backslashes, or other syntax-significant
  characters.
- **Impact**: Search results with unusual titles or URLs render as malformed
  Markdown and can break downstream parsers.
- **Fix direction**: Escape Markdown link text and wrap/escape destinations, or
  use the full output format for unsafe values.

### BUG-162 [low]: `search_api` accepts an empty library name
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_api()` (~line 1049)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**:
  ```python
  await search_api("")
  # Searches for " API reference documentation getting started".
  ```
- **Root cause**: `search_api()` builds a query from `library` without checking
  whether it is blank.
- **Impact**: Blank input triggers a broad, low-value web search instead of a
  validation error.
- **Fix direction**: Require `library.strip()` and raise `SearchError` when it
  is empty.

### BUG-163 [low]: `search_compare` accepts missing technologies
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_compare()` (~line 1080)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**:
  ```python
  await search_compare("", "Django")
  # Searches for " vs Django comparison".
  ```
- **Root cause**: `tech_a` and `tech_b` are interpolated directly into the
  query without validation.
- **Impact**: Users get malformed comparison searches instead of a clear prompt
  to provide both technologies.
- **Fix direction**: Reject blank `tech_a` or `tech_b` with a targeted
  `SearchError`.

### BUG-164 [low]: `search_similar_repos` accepts an empty project description
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_similar_repos()` (~line 1241)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**:
  ```python
  await search_similar_repos("")
  # Searches for " github repository".
  ```
- **Root cause**: `repo_description` is not validated before the generic
  `"github repository"` suffix is appended.
- **Impact**: The tool performs an overly broad repository search with little
  chance of satisfying the user's intent.
- **Fix direction**: Require a non-empty description before searching.

### BUG-165 [low]: `search_tutorial` accepts an empty technology
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_tutorial()` (~line 1608)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**:
  ```python
  await search_tutorial("")
  # Searches for " getting started tutorial for beginners".
  ```
- **Root cause**: `technology` is interpolated into the tutorial query without
  validation.
- **Impact**: Empty input creates generic tutorial searches unrelated to a
  specific language, framework, or tool.
- **Fix direction**: Reject blank `technology` values.

### BUG-166 [low]: Invalid tutorial levels silently become `beginner`
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_tutorial()` (~line 1628)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**:
  ```python
  await search_tutorial("Rust", level="expert")
  # Uses beginner query text.
  ```
- **Root cause**: `level_q.get(level.lower(), level_q["beginner"])` treats all
  unknown levels as beginner.
- **Impact**: A typo or unsupported level changes the search intent without any
  warning.
- **Fix direction**: Validate `level` against `beginner`, `intermediate`, and
  `advanced`.

### BUG-167 [low]: `search_news` forwards invalid `period` values to the engine
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_news()` (~line 1369)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**:
  ```python
  await search_news("Python", period="decade")
  # The invalid timelimit is passed through to DDGS.
  ```
- **Root cause**: The docstring limits `period` to `d`, `w`, `m`, or `y`, but
  the function does not validate it before passing it as `timelimit`.
- **Impact**: Invalid values may be ignored or raise backend-specific errors
  instead of a consistent validation message.
- **Fix direction**: Validate `period` before calling `_do_search()`.

### BUG-168 [low]: `search_error` documentation says version numbers are stripped, but code does not strip them
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_error()` and `_optimize_query()` (~lines 999 and 318)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**:
  ```python
  _optimize_query("library 1.2.3 TypeError", "error")
  # returns "library 1.2.3 TypeError"
  ```
- **Root cause**: The `search_error()` docstring promises version-number
  stripping, but the `error` optimizer only removes timestamps, hex addresses,
  file paths with line numbers, and stack-frame fragments.
- **Impact**: The tool behavior does not match its public help text.
- **Fix direction**: Either add version-number stripping or remove that claim
  from the docstring.

### BUG-169 [low]: `search_github_issues` has unreachable merged-PR labeling
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_github_issues()` (~line 1469)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**: Search for merged pull requests. Items returned through
  GitHub's issues/search shape expose issue state such as `open` or `closed`;
  merged status must be checked through pull-request data.
- **Root cause**: The code labels an item as `[merged]` only when
  `item["state"] == "merged"`, but GitHub issue objects do not use a `merged`
  state.
- **Impact**: Merged PRs are displayed as `[closed]`, and the `[merged]` branch
  is effectively dead.
- **Fix direction**: For PR items, inspect pull request metadata or use the
  pull request endpoint when merged status is required.

### BUG-170 [low]: `search_security` ignores OSV `details` when `summary` is missing
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_security()` (~line 1579)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**:
  ```python
  vuln = {"id": "OSV-1", "details": "Detailed advisory text", "aliases": []}
  # Rendered summary becomes "No description".
  ```
- **Root cause**: OSV records include both `summary` and `details`, but the
  formatter only reads `summary`.
- **Impact**: Advisories with useful `details` but no short `summary` are shown
  as having no description.
- **Fix direction**: Fall back to `details` when `summary` is missing.

### BUG-171 [low]: `search_security(max_results=...)` is applied per ecosystem, not globally
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_security()` (~line 1577)
- **Discovered**: 2026-04-29, extended audit round
- **Reproduction**:
  ```python
  await search_security("shared-name", ecosystem="auto", max_results=20)
  # Can render up to 20 findings for each checked ecosystem.
  ```
- **Root cause**: The loop slices `vulns[:max_results]` separately inside each
  ecosystem result block.
- **Impact**: The public argument says "Max vulnerabilities to show (1-20)",
  but auto mode can display far more than 20 total findings.
- **Fix direction**: Track a global remaining-result budget across ecosystems.

### BUG-172 [low]: `search_security` lowercases package names inconsistently with `search_package`
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_security()` (~line 1515) vs `search_package()` (~line 1287)
- **Discovered**: 2026-04-29, deep audit round 4
- **Reproduction**:
  ```python
  search_package("ReQuEsTs")   # .strip().lower() → "requests"
  search_security("ReQuEsTs")  # .strip() only → "ReQuEsTs"
  ```
- **Root cause**: `search_package` does `package.strip().lower()` but
  `search_security` only does `package.strip()`. The two package-lookup
  tools normalize input inconsistently.
- **Impact**: Different OSV API behavior for the same package name across tools.
- **Fix direction**: Apply consistent normalization across both tools.

### BUG-173 [low]: `_sort_by_authority` redundantly recomputes authority from scratch
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_sort_by_authority()` (~line 306)
- **Discovered**: 2026-04-29, deep audit round 4
- **Reproduction**: Every sort calls `_source_authority(r.get("href", ""))` for
  each result, ignoring the pre-computed `_authority` field already stored by
  engine functions.
- **Root cause**: Engine functions compute and store `"_authority"` in each
  result. `_sort_by_authority` ignores this cache and re-runs `urlparse` +
  35-entry sorted-list scan per result.
- **Impact**: Redundant CPU work — URL parsing per result per sort.
- **Fix direction**: Read `r.get("_authority", 0.4)` first; only recompute if missing.

### BUG-174 [low]: Session `context` dict allocated but never read or written
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_session_add()` (~line 180)
- **Discovered**: 2026-04-29, deep audit round 4
- **Reproduction**: `_search_sessions["s"]["context"]` is always `{}`.
- **Root cause**: Initialized as `{"history": [], "context": {}}` but never populated.
- **Impact**: Dead allocation — wasted empty dict per session.
- **Fix direction**: Remove `context` key or implement actual context storage.

### BUG-175 [low]: `web_fetch_code` fence balance check fooled by content containing ```
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `web_fetch_code()` (~line 1905)
- **Discovered**: 2026-04-29, deep audit round 4
- **Reproduction**: Code blocks demonstrating markdown syntax (tutorials about
  writing markdown) contain literal ``` inside their content. The parity check
  treats these as real fences.
- **Root cause**: `body.count("```") % 2` counts every triple-backtick as a fence,
  including those inside code block content.
- **Impact**: Truncated output may still have unbalanced fences for markdown-tutorial pages.
- **Fix direction**: Track actual fence positions (opening vs closing) instead of
  character-count parity.

### BUG-176 [low]: `search_rss` topic search may pick a non-feed URL
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_rss()` (~line 1671)
- **Discovered**: 2026-04-29, deep audit round 4
- **Reproduction**: Topic search returns links-format output; first URL extracted
  may be a landing page rather than an actual RSS/Atom feed.
- **Root cause**: No feed-detection heuristic (Content-Type check, URL pattern
  matching) before committing to a full fetch.
- **Impact**: "No RSS/Atom entries found" error when the real problem is a non-feed URL.
- **Fix direction**: HEAD-request candidates to check Content-Type before full fetch.

### BUG-177 [low]: `httpx.AsyncClient(timeout=httpx.Timeout(0))` allows infinite hangs
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_fetch()` (~line 479)
- **Discovered**: 2026-04-29, deep audit round 4
- **Reproduction**: `await web_fetch("https://slow.example.com", timeout=0)` —
  `httpx.Timeout(0)` means no timeout, request hangs indefinitely.
- **Root cause**: `timeout` parameter accepts 0 without validation.
- **Impact**: A client passing `timeout=0` can hang the server on a single fetch.
- **Fix direction**: Clamp `timeout` minimum to 1 second or reject 0 explicitly.

### BUG-178 [low]: `_try_one_engine` flattens DDGSException losing structured error info
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_do_search()` → `_try_one_engine()` (~line 811)
- **Discovered**: 2026-04-29, deep audit round 4
- **Reproduction**: `DDGSException` subclasses (`RatelimitException`, `TimeoutException`)
  are caught and flattened to `f"[{eng}] {exc}"`, losing exception type and fields.
- **Root cause**: All exceptions converted to strings for `last_errors` list.
- **Impact**: Diagnostic detail lost in final `SearchError` message.
- **Fix direction**: Preserve exception type in error tuple.

### BUG-179 [low]: Yahoo results from cache show `[ddgs]` tag instead of `[yahoo]`
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_search_yahoo()` (~line 621) and cache logic (~line 759)
- **Discovered**: 2026-04-29, deep audit round 4
- **Reproduction**: Search with `engine="auto"` (caches `"engine": "ddgs"`), then
  same query with `engine="yahoo"` → cache hit returns DDGS-tagged results.
- **Root cause**: `_search_yahoo` overrides engine label only for fresh results.
- **Impact**: Users see `[ddgs]` on Yahoo-requested results.
- **Fix direction**: Apply engine label override to cached results or produce
  distinct cache keys for yahoo vs auto.

### BUG-180 [low]: Domain scoping silently truncates large domain lists to 3–5 entries
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_do_search()` (~line 742) and `_build_site_query()` (~line 348)
- **Discovered**: 2026-04-29, deep audit round 4
- **Reproduction**: `search_docs(...)` passes 25-entry `_DOCS_DOMAINS`; only
  `domains[:5]` (auto) or `scoped_domains[:3]` (other engines) are used.
- **Root cause**: Intentional query-length management, but no user-facing indication.
- **Impact**: Domain-scoped searches less comprehensive than domain lists suggest.
- **Fix direction**: Document the limit or rotate domains across multiple queries.

### BUG-181 [low]: `RatelimitException` retry sleep too short for API rate limits
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_do_search()` → `_try_one_engine()` (~line 803)
- **Discovered**: 2026-04-29, deep audit round 4
- **Reproduction**: `_retry_sleep(attempt) + 1` = 2–5 seconds. API rate limits
  typically require 60+ seconds. `Retry-After` header is not read.
- **Root cause**: Generic exponential backoff used for rate-limit-specific retries.
- **Impact**: Wasted retry attempts that are almost guaranteed to fail.
- **Fix direction**: Read `Retry-After` header or use minimum 30s wait for rate limits.

### BUG-182 [low]: MCP resources do not declare `listChanged` capability or handle `subscribe`
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `mcp = FastMCP("codingWebSearch")` (~line 21)
- **Discovered**: 2026-04-29, MCP specification review
- **Reproduction**: Client sends `resources/subscribe` → protocol error.
- **Root cause**: FastMCP initialized without `resources` capability config.
- **Impact**: Compliant MCP clients that attempt to subscribe get protocol errors.
- **Fix direction**: Declare `listChanged: false` explicitly to signal static resources.

### BUG-183 [low]: Duplicate engines in `SEARCH_ENGINES` trigger duplicate searches
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_resolve_engines()` (~line 700)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**:
  ```python
  os.environ["SEARCH_ENGINES"] = "auto,auto"
  _resolve_engines("auto")  # returns ["auto", "auto"]
  ```
- **Root cause**: The environment override parser preserves all valid tokens
  and never deduplicates the list.
- **Impact**: The same backend can be queried twice in a single request,
  wasting network calls and rate-limit budget.
- **Fix direction**: Deduplicate while preserving order.

### BUG-184 [low]: Invalid `SEARCH_ENGINES` tokens are silently ignored
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_resolve_engines()` (~line 700)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**:
  ```python
  os.environ["SEARCH_ENGINES"] = "brave,gogle,auto"
  _resolve_engines("auto")  # returns ["brave", "auto"]
  ```
- **Root cause**: The parser filters unknown tokens with `if e in
  _ENGINE_INFO` and never reports what was dropped.
- **Impact**: Configuration typos remain hidden and can leave operators with a
  different fallback chain than intended.
- **Fix direction**: Reject unknown environment tokens during startup or include
  a warning in diagnostics.

### BUG-185 [low]: Cached search results lose engine label and elapsed metadata
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_do_search()` cached branch (~line 759)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**: Perform a fresh `web_search("x", engine="auto")`, then the
  same search again. The fresh response header is `## Web [auto]: x`, while the
  cached response header is `## Web: x` and has no elapsed time.
- **Root cause**: The cache branch formats with plain `label` and omits
  `elapsed_ms`; the fresh branch builds `engine_label = f"{label} [{used}]"`.
- **Impact**: Cached and fresh responses have different shapes for the same
  request.
- **Fix direction**: Store or recompute the same display metadata for cached
  responses.

### BUG-186 [low]: `_source_freshness` boosts stale "months ago" snippets
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_source_freshness()` (~line 257)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**:
  ```python
  _source_freshness("updated 18 months ago")  # returns 0.05
  ```
- **Root cause**: The relative-time regex treats any phrase containing
  `"months ago"` as a freshness signal without parsing the number of months.
- **Impact**: Old pages can receive a freshness boost and rank above newer
  sources.
- **Fix direction**: Parse numeric relative ages and only boost within a clear
  recent threshold.

### BUG-187 [low]: `_source_authority` fails for fully-qualified hostnames with trailing dots
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_source_authority()` (~line 244)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**:
  ```python
  _source_authority("https://docs.python.org./3/")  # returns 0.4
  ```
- **Root cause**: The host is lowercased and stripped of `www.`, but a trailing
  DNS root dot is not removed before matching.
- **Impact**: Authoritative URLs using fully-qualified hostnames lose their
  authority score.
- **Fix direction**: Normalize hostnames with `host.rstrip(".")` before
  matching.

### BUG-188 [low]: Site-scoped queries include path-bearing `site:` operands
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_build_site_query()` and domain lists (~lines 87-118)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**:
  ```python
  _build_site_query("q", ["reddit.com/r/programming", "golang.org/pkg"])
  # "(site:reddit.com/r/programming OR site:golang.org/pkg) q"
  ```
- **Root cause**: Several scoped-domain lists contain path fragments. The query
  builder blindly injects those strings into `site:` operators.
- **Impact**: Some search engines handle path-bearing `site:` filters
  inconsistently, reducing recall for scoped searches.
- **Fix direction**: Store host-only domains for `site:` filters and use
  separate path filters only when a backend supports them.

### BUG-189 [low]: `_extract_text` discards image alt text
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_extract_text()` and `STRIP_TAGS` (~lines 79 and 437)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**:
  ```html
  <main><img alt="Architecture diagram showing cache flow"></main>
  ```
  `web_fetch()` returns no readable text from this content.
- **Root cause**: `img` tags are decomposed before their `alt` text is captured.
- **Impact**: Pages that communicate important information through accessible
  image text lose that content.
- **Fix direction**: Replace images with their `alt` text before removing the
  tag.

### BUG-190 [low]: Page titles are returned with raw HTML entities
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, title regex usage in `web_fetch()`, `web_fetch_code()`,
  `search_deep()`, and `search_crawl()`
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**:
  ```html
  <title>FastAPI &amp; Pydantic</title>
  ```
  `web_fetch()` returns `# FastAPI &amp; Pydantic`.
- **Root cause**: Titles are extracted with a regex and `.strip()` instead of
  being decoded through BeautifulSoup or `html.unescape()`.
- **Impact**: User-facing headings display encoded entities instead of readable
  text.
- **Fix direction**: Decode titles after extraction or read title text from the
  parsed document.

### BUG-191 [low]: `web_fetch_code` ignores standalone `<code>` blocks
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `web_fetch_code()` (~line 1870)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**:
  ```html
  <main><code class="language-python">print("hello")</code></main>
  ```
  `web_fetch_code()` raises `No code blocks found on this page.`
- **Root cause**: The parser only iterates `soup.find_all("pre")`; it never
  considers block-like `<code>` elements outside `<pre>`.
- **Impact**: Some documentation pages with standalone code elements are
  reported as having no code.
- **Fix direction**: Include standalone code elements that are not descendants
  of `<pre>`, with safeguards for tiny inline snippets.

### BUG-192 [low]: `search_deep(fetch_top=0)` returns a misleading no-URL warning
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_deep()` (~line 1134)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**:
  ```python
  await search_deep("query", fetch_top=0)
  # Appends: _(No URLs found to fetch for deep research)_
  ```
- **Root cause**: `fetch_top` is clamped to allow 0, but the later no-URL branch
  treats 0 as an unexpected extraction failure.
- **Impact**: A valid "search only" request is reported as if URL extraction
  failed.
- **Fix direction**: If `fetch_top == 0`, return the search result directly.

### BUG-193 [low]: `search_crawl(urls=...)` does not deduplicate duplicate URLs
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_crawl()` (~line 1747)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**:
  ```python
  await search_crawl(urls="https://example.com/a,https://example.com/a")
  # Fetches the same URL twice.
  ```
- **Root cause**: Direct URL mode slices the parsed list but never applies a
  `seen` set like `base_url` mode does.
- **Impact**: Duplicate inputs waste fetch slots and network calls.
- **Fix direction**: Deduplicate direct URLs while preserving order.

### BUG-194 [low]: `search_crawl(max_length_per_page=0)` still returns at least 500 characters
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_crawl()` (~line 1778)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**:
  ```python
  await search_crawl(urls="https://example.com", max_length_per_page=0)
  # max_length_per_page becomes 500.
  ```
- **Root cause**: The parameter is clamped with `max(500, min(...))`, enforcing
  a minimum body length even when the caller requests less.
- **Impact**: Callers cannot request metadata-only or very small crawl output.
- **Fix direction**: Allow 0 or a smaller minimum, or document the hard minimum.

### BUG-195 [low]: `search_package(registry=...)` does not strip whitespace
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_package()` (~line 1290)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**:
  ```python
  await search_package("requests", registry="pypi ")
  # Returns unknown registry.
  ```
- **Root cause**: The code uses `registry.lower()` instead of
  `registry.strip().lower()`.
- **Impact**: Harmless whitespace in client input turns a valid registry into
  an error.
- **Fix direction**: Strip registry names before validation.

### BUG-196 [low]: `search_session` treats unknown actions as `view`
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_session()` (~line 1917)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**:
  ```python
  await search_session("s1", action="delete")
  # Returns session context instead of rejecting the action.
  ```
- **Root cause**: Only `action == "clear"` is special-cased; every other value
  falls through to view behavior.
- **Impact**: Client typos in session management are hidden.
- **Fix direction**: Validate action against `{"view", "clear"}`.

### BUG-197 [low]: `search_package` promises dependency info but never renders dependencies
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_package()` docstring and formatter (~line 1272)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**: `search_package("requests", registry="pypi")` returns
  license, summary, URL, and Python requirement, but no `requires_dist`
  dependency list.
- **Root cause**: The tool description says it provides
  "version/license/dependency info", but formatter code never extracts
  dependency fields from PyPI, npm, crates.io, or Go responses.
- **Impact**: Users relying on the tool to inspect dependencies receive
  incomplete package metadata.
- **Fix direction**: Render concise dependency summaries or remove the claim
  from the public description.

### BUG-198 [low]: `search_github_issues(labels=...)` cannot represent labels containing commas
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_github_issues()` (~line 1435)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**:
  ```python
  await search_github_issues(query="x", labels="area: parser,lexer")
  # Interpreted as two labels: "area: parser" and "lexer".
  ```
- **Root cause**: The API accepts a single comma-separated string and splits on
  every comma, with no escape or quoting syntax.
- **Impact**: Repositories with comma-containing label names cannot be searched
  precisely.
- **Fix direction**: Accept a list-style input or implement CSV parsing with
  quoted fields.

### BUG-199 [low]: `search_github_issues(repo=...)` does not prevalidate owner/repo format
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_github_issues()` (~line 1431)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**:
  ```python
  await search_github_issues(repo="not a repo", query="bug")
  # Sends repo:not a repo to GitHub, then receives a generic 422.
  ```
- **Root cause**: `repo` is appended directly as `repo:{repo.strip()}` without
  validating the `owner/name` shape.
- **Impact**: User input errors become remote API errors instead of clear local
  validation messages.
- **Fix direction**: Validate `repo` with a conservative `owner/repo` pattern
  before making the API request.

### BUG-200 [low]: Explicit `search_security` reports "OK" for nonexistent packages
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_security()` (~line 1573)
- **Discovered**: 2026-04-29, deep audit round 5
- **Reproduction**:
  ```python
  await search_security("definitely-not-a-real-package-xyz", ecosystem="PyPI")
  # Can render: "OK: No known vulnerabilities"
  ```
- **Root cause**: OSV "no vulns" responses are treated as package health
  confirmations. The tool does not verify that the package exists in its
  registry.
- **Impact**: Typos in package names can produce reassuring `OK` output instead
  of a "package not found or not checked" warning.
- **Fix direction**: Optionally verify package existence through registry APIs,
  or soften the wording to "No vulnerabilities returned by OSV".

### BUG-201 [low]: `_source_freshness` year regex only matches 2000-2099
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_source_freshness()`
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: `\b(20\d{2})\b` misses pre-2000 references. Classic CS papers,
  K&R C (1978), early RFCs get zero freshness boost.
- **Root cause**: Year regex hardcoded to 2000-2099 range.
- **Impact**: Pre-2000 authoritative sources ranked below modern equivalents.
- **Fix direction**: Accept 1990-2099 with a low boost for pre-2000.

### BUG-202 [low]: `_ENGINE_PRIORITY` ranks free DDGS before paid API engines
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_ENGINE_PRIORITY`
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: With Brave key configured, `engine="all"` returns DDGS
  results first because `auto` is first in priority list.
- **Root cause**: Priority list is hardcoded; free engines always precede paid.
- **Impact**: Lower-quality free results outrank paid API results.
- **Fix direction**: Let users override priority via `SEARCH_ENGINES` env var.

### BUG-203 [low]: Query containing `#` breaks markdown heading in result headers
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_format_results()`, `_format_compact()`, `_format_links()`
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: Query `"C# generics"` → header `"## Web: C# generics"` →
  `#` parsed as markdown heading, breaking structure.
- **Root cause**: Query text interpolated into markdown heading without escaping.
- **Impact**: Malformed markdown for queries containing `#`, `*`, `_`.
- **Fix direction**: Escape markdown-significant chars or wrap query in backticks.

### BUG-204 [low]: `_fetch` creates new `httpx.AsyncClient` per request — no connection reuse
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_fetch()`
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: Every fetch instantiates fresh `AsyncClient` → TCP+TLS
  handshake per request, no HTTP keep-alive, no DNS cache sharing.
- **Root cause**: `async with httpx.AsyncClient(...)` scoped inside `_fetch()`.
- **Impact**: Increased latency per request.
- **Fix direction**: Use module-level shared `AsyncClient` with connection pooling.

### BUG-205 [low]: `httpx.Timeout(timeout)` applies same value to connect/read/write/pool
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_fetch()`
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: `httpx.Timeout(30)` sets connect=30s — unreachable hosts take
  30 seconds to fail.
- **Root cause**: Single-value `Timeout` applies to all phases equally.
- **Impact**: Slow failure detection for unreachable hosts.
- **Fix direction**: Use `Timeout(connect=10.0, read=timeout, write=timeout, pool=timeout)`.

### BUG-206 [low]: `search_deep` keyword extraction regex only matches Capitalized terms
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_deep()` keyword extraction
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: `re.findall(r'\b[A-Z][a-zA-Z]{3,}...', "async python rust")`
  returns `[]` — all-lowercase technical keywords invisible.
- **Root cause**: Capitalization filter excludes lowercase tech terms.
- **Impact**: Cross-source synthesis biased toward proper nouns only.
- **Fix direction**: Lowercase all terms, filter stopwords instead.

### BUG-207 [low]: `search_crawl` fetches `?query` and `#fragment` links as separate pages
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_crawl()` → `_extract_links()`
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: `<a href="?page=2">` and `<a href="#section">` generate
  crawl targets returning the same base page content.
- **Root cause**: `?` and `#` relative URLs are valid but represent same page.
- **Impact**: Crawl slots wasted on duplicate content.
- **Fix direction**: Strip query/fragment before appending discovered URLs.

### BUG-208 [low]: `search_package` npm output URL uses unencoded lowercased name
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_package()` npm output
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: Package `@types/node` → output URL has literal `@` that
  some markdown renderers fail on.
- **Root cause**: Display URL uses raw `pkg` (already lowercased) without encoding.
- **Impact**: Broken display URLs for scoped/special-char package names.
- **Fix direction**: Use `quote(pkg, safe="@")` for display URLs too.

### BUG-209 [low]: `search_github_issues` label backslash escape incomplete
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `search_github_issues()` label escaping
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: Label `bug\critical` → `label:"bug\critical"` → `\c` is an
  invalid GitHub search escape → API 422.
- **Root cause**: Only `"` is escaped; `\` not handled.
- **Impact**: Repos with backslash in label names cause API errors.
- **Fix direction**: Double-escape backslashes before quote escaping.

### BUG-210 [low]: `_startup_diagnostics` tool count name-based filter may miscount
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_startup_diagnostics()`
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: A non-tool callable named `search_helper` would be counted
  as a tool.
- **Root cause**: `callable(obj) and n.startswith("search_")` is heuristic,
  not actual tool registration check.
- **Impact**: Refactoring could silently inflate tool count.
- **Fix direction**: Count via `@mcp.tool()` decoration metadata.

### BUG-211 [low]: Startup diagnostics always reports ≥3 configured engines
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_startup_diagnostics()`
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: Even when DDGS is network-blocked, startup says "3/7 engines"
  because auto/baidu/yahoo have `key: None` → `_required_keys_present(None)` → True.
- **Root cause**: "No key needed" conflated with "configured and working."
- **Impact**: Operators see "3/7 engines" and assume three backends are functional.
- **Fix direction**: Distinguish "no key needed" from "verified working."

### BUG-212 [low]: `web_fetch_code` extracts all code blocks before truncation
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `web_fetch_code()` → `_parse_code_blocks()`
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: Page with 1000+ code blocks → all extracted into list →
  concatenated → then truncated by `max_length`.
- **Root cause**: No early exit in block extraction loop.
- **Impact**: Memory spike on code-heavy pages.
- **Fix direction**: Track accumulated length, break early when exceeding `max_length`.

### BUG-213 [low]: `_search_ddgs` loses partial results on mid-stream exception
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_search_ddgs()`
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: DDGS returns 8 results then raises `RatelimitException` on 9th.
  `list(generator)` never returns — partial consumption lost.
- **Root cause**: `list()` on generator either fully succeeds or raises.
- **Impact**: Users get zero results when they could have gotten partial results.
- **Fix direction**: Iterate manually with try/except, collect what was gathered.

### BUG-214 [low]: `_format_compact` produces invalid markdown for URLs with spaces
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_format_compact()`
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: URL `https://example.com/my page` → `[T](https://example.com/my page)`
  — space breaks the markdown link.
- **Root cause**: URL interpolated directly into markdown destination without encoding.
- **Impact**: Broken links in compact output format.
- **Fix direction**: URL-encode href before markdown interpolation.

### BUG-215 [low]: Domain lists overlap across categories
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, domain lists
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: `dev.to`, `news.ycombinator.com`, `medium.com` appear in
  both `_CODE_DOMAINS` and tool-local news/tutorial lists.
- **Root cause**: Organic growth without cross-category dedup.
- **Impact**: No functional bug, but domain resource endpoints show confusing
  overlap across categories.
- **Fix direction**: Document overlap as intentional or deduplicate.

### BUG-216 [low]: `_check_rate_limit` with `max_per_minute=0` causes `IndexError`
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_check_rate_limit()`
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: `len(calls) >= 0` always True, but `calls[0]` crashes if
  list is empty.
- **Root cause**: `max_per_minute` not validated; 0 bypasses guard.
- **Impact**: Accidental zero-value call crashes server.
- **Fix direction**: Clamp `max_per_minute >= 1`.

### BUG-217 [low]: `_as_text(False)` returns `"False"` string
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_as_text()`
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: `_as_text(False)` → `"False"`; `_as_text(True)` → `"True"`.
  Boolean API fields produce Python string representation in markdown output.
- **Root cause**: Only `None` is checked; `bool` values pass through `str()`.
- **Impact**: Fields like `"is_deprecated": False` render as `"False"`.
- **Fix direction**: Handle `isinstance(value, bool)` explicitly.

### BUG-218 [low]: `session_id` with whitespace creates phantom sessions
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, tools with `session_id` parameter
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: `session_id=" "` (single space) → truthy → creates session
  named `" "`, untrackable and unclearable by name.
- **Root cause**: `session_id or None` only catches empty string, not whitespace.
- **Impact**: Accidental whitespace creates phantom sessions.
- **Fix direction**: Use `session_id.strip() or None`.

### BUG-219 [low]: `_is_duplicate` O(n²) with expensive SequenceMatcher per comparison
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_is_duplicate()` and `_title_similar()`
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: 50 results → ~1225 SequenceMatcher.ratio() calls, each
  computing full edit-distance matrix.
- **Root cause**: O(n²) title comparisons with CPU-intensive algorithm.
- **Impact**: Noticeable latency for large multi-engine result sets.
- **Fix direction**: Index seen titles by trigrams; use Jaccard pre-filter.

### BUG-220 [low]: `_validate_url` accepts URLs up to memory limit (no length cap)
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py`, `_validate_url()`
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: 100KB URL string passes validation, then crashes httpx or
  remote server.
- **Root cause**: No URL length validation.
- **Impact**: Errors occur deep in HTTP stack rather than clean rejection.
- **Fix direction**: Reject URLs exceeding 8192 chars.

### BUG-221 [low]: `_build_result` title truncation uses char count not display width
- **Status**: ⚪ wontfix
- **Severity**: low
- **File**: `server.py`, `_build_result()`
- **Discovered**: 2026-04-29, deep audit round 6
- **Reproduction**: CJK/emoji titles truncated at 147 Python chars but display
  at ~2x width in monospace terminals.
- **Root cause**: `len(title)` counts characters, not terminal columns.
  `wcwidth` would add a dependency for a cosmetic edge case affecting <1% of queries.
- **Impact**: Negligible — 150-char truncation is a soft limit, not a hard contract.
- **Fix direction**: Accept char-count as good enough for all practical purposes.

### BUG-222 [low]: _source_authority does not normalize IDNA hostnames
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, _source_authority()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Unicode and punycode host forms compare differently.
- **Root cause**: Only lowercase and www stripping are applied.
- **Impact**: Internationalized domains can score inconsistently.
- **Fix direction**: Normalize hostnames through IDNA.

### BUG-223 [low]: _relevance_score gives weight to stopwords
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, _relevance_score()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Query terms like the/and/to count toward overlap.
- **Root cause**: No stopword filter is applied.
- **Impact**: Generic text can receive inflated relevance.
- **Fix direction**: Filter common stopwords before scoring.

### BUG-224 [low]: _relevance_score ignores URL path matches
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, _relevance_score()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: /docs/install-docker gets no relevance credit for docker unless title/body mention it.
- **Root cause**: Only title and body are scored.
- **Impact**: Relevant docs can rank lower.
- **Fix direction**: Include URL path tokens in relevance scoring.

### BUG-225 [low]: _source_freshness boosts future years
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, _source_freshness()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Snippet "roadmap 2099" receives maximum freshness boost.
- **Root cause**: Any year >= current year is considered freshest.
- **Impact**: Future roadmap/version numbers distort ranking.
- **Fix direction**: Cap boosts to plausible publication years.

### BUG-226 [low]: _optimize_query(error) does not strip Windows file paths
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, _optimize_query()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: C:\project\app.py:10 ValueError keeps the Windows path.
- **Root cause**: Path regex is Unix/path-fragment oriented.
- **Impact**: Windows stack traces produce noisier searches.
- **Fix direction**: Add drive-letter path patterns.

### BUG-227 [low]: _optimize_query(error) can strip meaningful mixed error codes
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, _optimize_query()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Windows error 0x80070005 access denied loses the code.
- **Root cause**: All hex-looking tokens are removed as noise.
- **Impact**: Platform-specific error searches become less precise.
- **Fix direction**: Preserve known error-code hex tokens or turn them into hints.

### BUG-228 [low]: _format_compact truncates titles without an ellipsis
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, _format_compact()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: A 200-character title is sliced at 120 chars with no marker.
- **Root cause**: Formatter uses direct slicing.
- **Impact**: Users may not realize title text is incomplete.
- **Fix direction**: Append ellipsis when truncating.

### BUG-229 [low]: _build_result tag formatting is nonstandard when multiple tags exist
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, _build_result()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: A DDGS official result renders [ddgs]/[official].
- **Root cause**: Tags are joined with ]/[ and wrapped once.
- **Impact**: Output looks like broken Markdown tags.
- **Fix direction**: Render tags as separate bracketed labels.

### BUG-230 [low]: _format_links emits blank entries for missing URLs
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, _format_links()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: A result missing href renders as "1. ".
- **Root cause**: Formatter does not skip empty hrefs.
- **Impact**: URL-only output contains unusable entries.
- **Fix direction**: Skip or label missing URLs.

### BUG-231 [low]: _format_results total_found parameter is unused by engines
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, _format_results()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Generic search never passes provider total counts.
- **Root cause**: Total-count metadata is not propagated.
- **Impact**: Output underuses available metadata.
- **Fix direction**: Carry optional total counts from engines.

### BUG-232 [low]: _cache_key uses MD5 for non-security hashing
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, _cache_key()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Cache keys are hashlib.md5 digests.
- **Root cause**: MD5 was chosen for convenience.
- **Impact**: Security scanners may flag the code.
- **Fix direction**: Use SHA-256 or tuple keys.

### BUG-233 [low]: Cache keys do not include cache schema version
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, _cache_key(), _search_cache`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Hot-reloaded code can reuse old cached result shapes.
- **Root cause**: Keys include request parameters only.
- **Impact**: Development sessions can show stale formats.
- **Fix direction**: Include a cache schema/version constant.

### BUG-234 [low]: _session_context omits top URLs even though history stores them
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, _session_context()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: _session_add stores top_urls; session view shows only query/count.
- **Root cause**: Captured URL data is never rendered.
- **Impact**: Session summaries are less useful.
- **Fix direction**: Show top URLs in session context.

### BUG-235 [low]: Session pruning does not run when exactly 50 sessions exist
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, _session_add()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: 50 stale sessions remain until a 51st appears.
- **Root cause**: Pruning runs only when len > 50.
- **Impact**: Stale sessions can linger unnecessarily.
- **Fix direction**: Prune stale sessions opportunistically.

### BUG-236 [low]: _retry_sleep first retry waits one second despite docs saying 2s
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, README.md`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: _retry_sleep(0) returns 1 while README says 2s->4s->8s.
- **Root cause**: Zero-based exponent is undocumented.
- **Impact**: Retry timing docs are inaccurate.
- **Fix direction**: Align docs or code.

### BUG-237 [low]: _search_google_api does not URL-encode API key and CSE ID
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, _search_google_api()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Reserved chars in cx/key are inserted directly into URL.
- **Root cause**: Only q is quote_plus encoded.
- **Impact**: Unusual values can malformed URLs.
- **Fix direction**: Use httpx params for query construction.

### BUG-238 [low]: Direct API errors omit provider response details
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, direct API engine functions`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Brave 401 with JSON body reports only Brave HTTP 401.
- **Root cause**: Non-200 bodies are discarded.
- **Impact**: Users get poor diagnostics for quota/key errors.
- **Fix direction**: Include short sanitized provider error details.

### BUG-239 [low]: _search_baidu has no stable parser fallback
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, _search_baidu()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Markup selector changes produce no parseable results.
- **Root cause**: Parser depends on current Baidu classes.
- **Impact**: Baidu support can break with upstream HTML changes.
- **Fix direction**: Add fallback selectors or clearer best-effort warning.

### BUG-240 [low]: search_api does not strip library and method before query construction
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, search_api()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: search_api(" FastAPI ", " Depends ") keeps extra spaces.
- **Root cause**: Inputs are interpolated directly.
- **Impact**: Equivalent inputs create noisier queries/cache keys.
- **Fix direction**: Strip components before building query.

### BUG-241 [low]: search_compare does not strip technology names or aspect
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, search_compare()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: search_compare(" React ", " Vue ", " performance ") keeps extra spaces.
- **Root cause**: Inputs are interpolated directly.
- **Impact**: Search queries and cache keys are less canonical.
- **Fix direction**: Normalize query components.

### BUG-242 [low]: search_similar_repos does not strip language before prepending it
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, search_similar_repos()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: language=" Python " yields extra spacing.
- **Root cause**: Optional language hint is not stripped.
- **Impact**: Equivalent inputs produce different queries.
- **Fix direction**: Strip language before use.

### BUG-243 [low]: search_news appends news even when topic already ends with news
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, search_news()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: search_news("Python news") searches Python news news.
- **Root cause**: The suffix is unconditional.
- **Impact**: Queries can contain redundant terms.
- **Fix direction**: Detect existing news intent first.

### BUG-244 [low]: search_tutorial does not strip technology before query construction
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, search_tutorial()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: search_tutorial(" Rust ") keeps surrounding spaces.
- **Root cause**: Technology is interpolated directly.
- **Impact**: Equivalent inputs produce different cache keys.
- **Fix direction**: Strip technology names.

### BUG-245 [low]: list_engines hardcodes tool count and can drift
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, list_engines()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Adding/removing tools requires manually updating "21 total".
- **Root cause**: The count is static text.
- **Impact**: Runtime help can become stale.
- **Fix direction**: Compute the count or remove it.

### BUG-246 [low]: Resource JSON output is not stable-sorted
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, resource functions`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: resource_authority dumps insertion order, not sorted keys.
- **Root cause**: json.dumps omits sort_keys=True.
- **Impact**: Diffs can become noisy if dict order changes.
- **Fix direction**: Use sort_keys=True.

### BUG-247 [low]: resource_authority exposes scores without schema metadata
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, resource_authority()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Clients receive a bare domain-to-score object.
- **Root cause**: Resource has no version or score-range metadata.
- **Impact**: Clients cannot detect semantic changes.
- **Fix direction**: Wrap scores with version and description fields.

### BUG-248 [low]: _startup_diagnostics missing-key order is nondeterministic
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, _startup_diagnostics()`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: optional_keys is a set.
- **Root cause**: Set iteration order is not stable.
- **Impact**: Startup logs vary unnecessarily.
- **Fix direction**: Use a list or sorted output.

### BUG-249 [low]: Dockerfile exposes port 8080 even though server uses stdio
- **Status**: 🔴 open
- **Severity**: low
- **File**: `Dockerfile`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: docker inspect shows 8080 but no server listens there.
- **Root cause**: EXPOSE was added without HTTP transport.
- **Impact**: Users may expect a network service.
- **Fix direction**: Remove EXPOSE or implement HTTP transport.

### BUG-250 [low]: Dockerfile does not set PYTHONIOENCODING=utf-8
- **Status**: 🔴 open
- **Severity**: low
- **File**: `Dockerfile`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Image sets PYTHONUNBUFFERED but not PYTHONIOENCODING.
- **Root cause**: Encoding is left to environment defaults.
- **Impact**: Non-UTF-8 environments can still hit encoding issues.
- **Fix direction**: Set PYTHONIOENCODING=utf-8.

### BUG-251 [low]: Repository has no .dockerignore
- **Status**: 🔴 open
- **Severity**: low
- **File**: `Docker build context`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Docker build sends docs/tests/.git unless excluded by Docker defaults.
- **Root cause**: No .dockerignore exists.
- **Impact**: Build context is larger than needed.
- **Fix direction**: Add a minimal .dockerignore.

### BUG-252 [low]: Docker image has no healthcheck
- **Status**: 🔴 open
- **Severity**: low
- **File**: `Dockerfile`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Container orchestrators cannot tell if MCP process is responsive.
- **Root cause**: No HEALTHCHECK is defined.
- **Impact**: Wedged containers may appear healthy.
- **Fix direction**: Add a lightweight healthcheck or document why not.

### BUG-253 [low]: CI does not build the Docker image
- **Status**: 🔴 open
- **Severity**: low
- **File**: `.github/workflows/ci.yml`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Pull requests run Python checks but no docker build.
- **Root cause**: Docker validation exists only in publish workflow.
- **Impact**: Dockerfile breakage can reach master.
- **Fix direction**: Add a non-pushing docker build job.

### BUG-254 [low]: CI does not install the project package
- **Status**: 🔴 open
- **Severity**: low
- **File**: `.github/workflows/ci.yml`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: CI installs requirements but never pip install -e .
- **Root cause**: Tests import local server.py directly.
- **Impact**: Packaging or entrypoint breakage can pass CI.
- **Fix direction**: Install the package and smoke-test console script.

### BUG-255 [low]: CI lints only server.py
- **Status**: 🔴 open
- **Severity**: low
- **File**: `.github/workflows/ci.yml`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: flake8 command excludes tests and future modules.
- **Root cause**: Lint target is a single file.
- **Impact**: Lint errors outside server.py can pass.
- **Fix direction**: Lint server.py tests or the whole package.

### BUG-256 [low]: CI does not run pre-commit despite shipping a config
- **Status**: 🔴 open
- **Severity**: low
- **File**: `.github/workflows/ci.yml, .pre-commit-config.yaml`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: CI never runs pre-commit run --all-files.
- **Root cause**: Local and CI quality gates differ.
- **Impact**: Hook failures can be missed.
- **Fix direction**: Run pre-commit in CI.

### BUG-257 [low]: CI does not run on Windows despite Windows-console fixes
- **Status**: 🔴 open
- **Severity**: low
- **File**: `.github/workflows/ci.yml`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Matrix uses ubuntu-latest only.
- **Root cause**: OS matrix is missing.
- **Impact**: Windows encoding/path regressions can pass.
- **Fix direction**: Add at least one Windows job.

### BUG-258 [low]: CI tool-count check counts decorators instead of registered tools
- **Status**: 🔴 open
- **Severity**: low
- **File**: `.github/workflows/ci.yml`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Regex counts @mcp.tool strings.
- **Root cause**: It does not inspect FastMCP registration.
- **Impact**: Comments or failed registration can fool the check.
- **Fix direction**: Verify registered tools through MCP/FastMCP APIs.

### BUG-259 [low]: Docker publish workflow always adds a latest tag
- **Status**: 🔴 open
- **Severity**: low
- **File**: `.github/workflows/docker-publish.yml`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Any workflow_dispatch or tag build publishes latest.
- **Root cause**: metadata-action includes raw latest unconditionally.
- **Impact**: Older/test builds can overwrite latest.
- **Fix direction**: Publish latest only for release/default branch.

### BUG-260 [low]: README Content & News heading says 5 tools but lists six
- **Status**: 🔴 open
- **Severity**: low
- **File**: `README.md`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: The table lists search_news, search_tutorial, search_rss, web_fetch, search_crawl, web_fetch_code.
- **Root cause**: Heading count was not updated.
- **Impact**: Documentation is internally inconsistent.
- **Fix direction**: Update the heading count.

### BUG-261 [low]: README search-engine description disagrees with list_engines
- **Status**: 🔴 open
- **Severity**: low
- **File**: `README.md, server.py`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: README says auto includes Yandex; list_engines omits Yandex.
- **Root cause**: Engine descriptions are duplicated.
- **Impact**: Users get conflicting backend expectations.
- **Fix direction**: Use one shared description.

### BUG-262 [low]: Configuration guide says Brave plus auto are used with just a Brave key
- **Status**: 🔴 open
- **Severity**: low
- **File**: `CONFIGURING.md`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Setting BRAVE_SEARCH_API_KEY alone does not make default searches use Brave.
- **Root cause**: Text conflates availability with selection.
- **Impact**: Users may set a key and see no Brave traffic.
- **Fix direction**: Clarify engine="all" or SEARCH_ENGINES is needed.

### BUG-263 [low]: Devlog claims RSS uses robust XML parsing
- **Status**: 🔴 open
- **Severity**: low
- **File**: `devlog.md, server.py`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: devlog says robust XML parsing; code uses html.parser.
- **Root cause**: Implementation and notes diverged.
- **Impact**: Maintainers may think XML parsing is already fixed.
- **Fix direction**: Update devlog or use an XML parser.

### BUG-264 [low]: Changelog says package/security selectors are case-insensitive too broadly
- **Status**: 🔴 open
- **Severity**: low
- **File**: `CHANGELOG.md, server.py`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Cases like ecosystem="Crates.io" still fail despite broad changelog claim.
- **Root cause**: Release notes overstate normalization.
- **Impact**: Users may trust variants that still fail.
- **Fix direction**: Narrow the claim or complete normalization.

### BUG-265 [low]: Troubleshooting DuckDuckGo curl test does not match DDGS backend behavior
- **Status**: 🔴 open
- **Severity**: low
- **File**: `TROUBLESHOOTING.md, server.py`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: curling duckduckgo.com/html can pass while ddgs library fails.
- **Root cause**: Diagnostic tests a related endpoint, not the actual code path.
- **Impact**: Users can get misleading connectivity results.
- **Fix direction**: Use a Python DDGS smoke test as primary diagnostic.

### BUG-266 [low]: Troubleshooting diagnostic duplicates engine status logic
- **Status**: 🔴 open
- **Severity**: low
- **File**: `TROUBLESHOOTING.md, server.py`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Docs reimplement env-key checks instead of calling list_engines.
- **Root cause**: Runtime and docs logic are copied separately.
- **Impact**: Diagnostics can drift from actual behavior.
- **Fix direction**: Use list_engines or a shared helper.

### BUG-267 [low]: pre-commit config includes black but CI does not run it
- **Status**: 🔴 open
- **Severity**: low
- **File**: `.pre-commit-config.yaml, .github/workflows/ci.yml`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Local pre-commit may reformat files; CI only runs flake8.
- **Root cause**: Tooling expectations are split.
- **Impact**: Contributors see different local and CI checks.
- **Fix direction**: Run pre-commit in CI or remove unused hooks.

### BUG-268 [low]: Docker Compose omits optional GitHub and SearXNG environment variables
- **Status**: 🔴 open
- **Severity**: low
- **File**: `docker-compose.yml`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Compose comments include Brave/Google/Bing but not GITHUB_TOKEN or SEARXNG_URL.
- **Root cause**: Template was not updated for all features.
- **Impact**: Docker users may miss supported options.
- **Fix direction**: Add commented examples for all optional env vars.

### BUG-269 [low]: pyproject description claims CI/CD but project URLs omit CI and package links
- **Status**: 🔴 open
- **Severity**: low
- **File**: `pyproject.toml`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Description advertises CI/CD but URLs only include homepage/repository/issues.
- **Root cause**: Metadata description is broader than URL metadata.
- **Impact**: Package index users cannot find artifacts from metadata.
- **Fix direction**: Add relevant URLs or simplify description.

### BUG-270 [low]: references.md is not mentioned by runtime help
- **Status**: 🔴 open
- **Severity**: low
- **File**: `server.py, references.md`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: list_engines does not point to references.md.
- **Root cause**: Runtime help and docs index are separate.
- **Impact**: MCP users may not discover comparison research.
- **Fix direction**: Add a docs/resources pointer in runtime help.

### BUG-271 [low]: BUG tracker severity labels are duplicated in heading and body
- **Status**: 🔴 open
- **Severity**: low
- **File**: `BUGS.md`
- **Discovered**: 2026-04-29, deep audit round 7
- **Reproduction**: Each heading has [low]/[medium]/[high] and body repeats Severity.
- **Root cause**: Severity is represented twice.
- **Impact**: Future severity changes must update two places.
- **Fix direction**: Generate heading labels mechanically or keep only one source of truth.

---


---

## Fixed Bugs

### BUG-FIX-001: Dead authority entries with paths never match

- **Status**: 🟢 fixed (commit `bfc1a36`)
- **Severity**: low
- **File**: `server.py`, `_AUTHORITY_SCORES` (~line 125)
- **Root cause**: `"learn.microsoft.com/en-us/azure": 0.95` and
  `"github.com/advisories": 0.9` contain paths. `_source_authority()` only
  checks the URL host (netloc), so these entries could never match.
- **Fix**: Removed the path-containing entries. Bare host versions
  (`"learn.microsoft.com": 1.0`, `"github.com": 0.9`) already existed with
  equal or better scores.

### BUG-FIX-002: `_session_add` crashes on results missing `href` key

- **Status**: 🟢 fixed (commit `bfc1a36`)
- **Severity**: high
- **File**: `server.py`, `_session_add()` (~line 184)
- **Root cause**: `r["href"]` uses direct key access. If a search engine
  returns a result without an `href` field and the caller passes a
  `session_id`, the server crashes with `KeyError: 'href'`.
- **Fix**: Changed to `_as_text(r.get("href"))` with a regression test
  added to `tests/test_regressions.py`.

### BUG-FIX-003: Duplicate keys in `_AUTHORITY_SCORES`

- **Status**: 🟢 fixed (commit `20f68cb`)
- **Severity**: low
- **File**: `server.py`, `_AUTHORITY_SCORES`
- **Root cause**: `"dev.to"`, `"medium.com"`, and `"doc.rust-lang.org"`
  appeared twice in the dict with identical values.
- **Fix**: Removed duplicate entries.

### BUG-FIX-004: `_source_authority` substring matching vulnerability

- **Status**: 🟢 fixed (commit `20f68cb`)
- **Severity**: medium
- **File**: `server.py`, `_source_authority()` (~line 246)
- **Root cause**: Used `if domain in host` (substring match). A domain
  like `"python.org"` would match the host `"python.org.evil.com"`.
- **Fix**: Changed to `host == domain or host.endswith("." + domain)`.

### BUG-FIX-005: Sync BeautifulSoup in `search_rss` and `search_crawl`

- **Status**: 🟢 fixed (commit `20f68cb`)
- **Severity**: medium
- **File**: `server.py`, `search_rss()` and `search_crawl()`
- **Root cause**: BeautifulSoup parsing ran synchronously in async
  context, blocking the event loop.
- **Fix**: Wrapped in `asyncio.to_thread()`.

### BUG-FIX-006: Unused `_ADVISORY_DOMAINS` dead code

- **Status**: 🟢 fixed (commit `20f68cb`)
- **Severity**: low
- **File**: `server.py`
- **Root cause**: `_ADVISORY_DOMAINS` list was defined at module level
  but never referenced by any function.
- **Fix**: Removed the dead code.

---

---

## Summary

| Status | Count |
|--------|-------|
| 🔴 open | 266 |
| 🟡 in progress | 0 |
| ⚪ merged | 1 |
| ⚪ wontfix | 1 |
| 🟢 fixed | 9 |
| **Total** | **277** |
