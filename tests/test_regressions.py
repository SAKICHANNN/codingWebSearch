import asyncio
import contextlib
import io
import os
import re
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, unquote, urlparse

import server


def _mock_httpx_client(html: str):
    """Create a mock httpx.AsyncClient that returns *html* for any GET."""
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = html
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


class SearchCoreRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_do_search_wraps_engine_coroutines_as_tasks(self):
        original = server._search_with_engine
        query = "regression task wrapping"

        async def fake_search(*_args, **_kwargs):
            return [
                {
                    "title": "Result",
                    "href": "https://example.com/task-wrapping",
                    "body": "Body",
                    "engine": "fake",
                }
            ]

        server._search_with_engine = fake_search
        try:
            result = await server._do_search(
                query=query,
                label="Web",
                max_results=1,
                engine="auto",
                region="wt-wt",
                safesearch="off",
                timelimit=None,
            )
        finally:
            server._search_with_engine = original

        self.assertIn("https://example.com/task-wrapping", result)

    def test_all_engine_name_is_case_insensitive(self):
        self.assertEqual(server._resolve_engines("ALL"), server._ENGINE_PRIORITY)

    def test_unknown_engine_is_rejected(self):
        with self.assertRaises(server.SearchError):
            server._resolve_engines("not-a-real-engine")

    def test_search_engines_env_does_not_override_explicit_engine(self):
        original = os.environ.get("SEARCH_ENGINES")
        os.environ["SEARCH_ENGINES"] = "auto"
        try:
            self.assertEqual(server._resolve_engines("bing"), ["bing"])
            self.assertEqual(server._resolve_engines("auto"), ["auto"])
        finally:
            if original is None:
                os.environ.pop("SEARCH_ENGINES", None)
            else:
                os.environ["SEARCH_ENGINES"] = original

    def test_runtime_status_output_is_gbk_encodable(self):
        str(server.SearchError("message", "recovery")).encode("gbk")
        asyncio.run(server.list_engines()).encode("gbk")

    async def test_cache_key_separates_safesearch_and_timelimit(self):
        original = server._search_with_engine
        calls = []

        async def fake_search(_query, _eng, _max_results, _region, safesearch, timelimit):
            calls.append((safesearch, timelimit))
            return [
                {
                    "title": f"{safesearch}:{timelimit}",
                    "href": f"https://example.com/{safesearch}/{timelimit}",
                    "body": "",
                    "engine": "fake",
                }
            ]

        server._search_cache.clear()
        server._search_with_engine = fake_search
        try:
            first = await server.web_search("cache-regression", safesearch="off", timelimit="d", max_results=1)
            second = await server.web_search("cache-regression", safesearch="on", timelimit="y", max_results=1)
        finally:
            server._search_with_engine = original
            server._search_cache.clear()

        self.assertIn("https://example.com/off/d", first)
        self.assertIn("https://example.com/on/y", second)
        self.assertEqual(calls, [("off", "d"), ("on", "y")])

    async def test_cached_results_are_added_to_session_history(self):
        original = server._search_with_engine

        async def fake_search(*_args, **_kwargs):
            return [
                {
                    "title": "Cached",
                    "href": "https://example.com/cached",
                    "body": "",
                    "engine": "fake",
                }
            ]

        server._search_cache.clear()
        server._search_sessions.clear()
        server._search_with_engine = fake_search
        try:
            await server.web_search("session-cache-regression", max_results=1)
            await server.web_search("session-cache-regression", max_results=1, session_id="s1")
        finally:
            server._search_with_engine = original
            server._search_cache.clear()

        self.assertIn("s1", server._search_sessions)
        self.assertEqual(server._search_sessions["s1"]["history"][0]["query"], "session-cache-regression")
        server._search_sessions.clear()

    async def test_google_requires_both_api_key_and_cse_id(self):
        original_google_key = os.environ.get(server.ENV_GOOGLE_KEY)
        original_google_cx = os.environ.get(server.ENV_GOOGLE_CX)
        os.environ[server.ENV_GOOGLE_KEY] = "present"
        os.environ.pop(server.ENV_GOOGLE_CX, None)
        try:
            with self.assertRaises(server.SearchError) as cm:
                await server.web_search("google key regression", engine="google", max_results=1)
        finally:
            if original_google_key is None:
                os.environ.pop(server.ENV_GOOGLE_KEY, None)
            else:
                os.environ[server.ENV_GOOGLE_KEY] = original_google_key
            if original_google_cx is None:
                os.environ.pop(server.ENV_GOOGLE_CX, None)
            else:
                os.environ[server.ENV_GOOGLE_CX] = original_google_cx

        self.assertIn("GOOGLE_API_KEY + GOOGLE_CSE_ID", str(cm.exception))

    def test_startup_diagnostics_counts_tools_and_complete_key_sets(self):
        optional_keys = [
            server.ENV_BRAVE_KEY,
            server.ENV_GOOGLE_KEY,
            server.ENV_GOOGLE_CX,
            server.ENV_BING_KEY,
            server.ENV_SEARXNG_URL,
        ]
        originals = {key: os.environ.get(key) for key in optional_keys}
        for key in optional_keys:
            os.environ.pop(key, None)
        os.environ[server.ENV_GOOGLE_KEY] = "present"

        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                server._startup_diagnostics()
        finally:
            for key, value in originals.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        output = stderr.getvalue()
        self.assertIn("3/7 engines", output)
        self.assertIn("21 tools", output)

    def test_error_query_optimizer_preserves_all_noise_queries(self):
        self.assertEqual(server._optimize_query("0x1234abcd", "error"), "0x1234abcd")
        self.assertEqual(
            server._optimize_query("2024-01-01T12:00:00", "error"),
            "2024-01-01T12:00:00",
        )

    def test_url_validation_blocks_private_ip_literals(self):
        for url in (
            "http://127.0.0.1:8080/admin",
            "http://10.0.0.1/",
            "http://192.168.1.10/",
            "http://169.254.169.254/metadata/",
            "http://[::1]/",
        ):
            self.assertIn("Blocked private or local address", server._validate_url(url))

    def test_url_validation_blocks_private_dns_resolution(self):
        original = server.socket.getaddrinfo

        def fake_getaddrinfo(*_args, **_kwargs):
            return [(server.socket.AF_INET, server.socket.SOCK_STREAM, 0, "", ("127.0.0.1", 80))]

        server.socket.getaddrinfo = fake_getaddrinfo
        try:
            err = server._validate_url("http://rebind.example/")
        finally:
            server.socket.getaddrinfo = original

        self.assertIn("Blocked private or local address", err)

    async def test_fetch_blocks_redirects_to_private_addresses(self):
        original = server.httpx.AsyncClient
        calls = []

        class Resp:
            def __init__(self, status_code, headers=None, text=""):
                self.status_code = status_code
                self.headers = headers or {}
                self.text = text

        class Client:
            def __init__(self, *_args, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                pass

            async def get(self, url):
                calls.append(url)
                return Resp(302, {"location": "http://127.0.0.1/admin"})

        server.httpx.AsyncClient = Client
        try:
            html, err = await server._fetch("https://93.184.216.34/start", timeout=1)
        finally:
            server.httpx.AsyncClient = original

        self.assertIsNone(html)
        self.assertIn("Redirect blocked", err)
        self.assertEqual(calls, ["https://93.184.216.34/start"])

    async def test_search_yahoo_does_not_mutate_source_results(self):
        original = server.DDGS
        shared = [{"title": "T", "href": "https://example.com", "body": "", "engine": "ddgs"}]

        class FakeDDGS:
            def __init__(self, *_args, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def text(self, *_args, **_kwargs):
                return shared

        server.DDGS = FakeDDGS
        try:
            result = await server._search_yahoo("query", max_results=1)
        finally:
            server.DDGS = original

        self.assertEqual(shared[0]["engine"], "ddgs")
        self.assertEqual(result[0]["engine"], "yahoo")

    def test_cache_get_returns_defensive_copies(self):
        server._search_cache.clear()
        server._cache_set("copy-test", [{"title": "T", "href": "https://example.com", "engine": "ddgs"}])

        cached = server._cache_get("copy-test")
        cached[0]["engine"] = "mutated"
        cached_again = server._cache_get("copy-test")

        self.assertEqual(cached_again[0]["engine"], "ddgs")
        server._search_cache.clear()

    # ── Baidu parser + engine fallback tests ──────────────────────────

    async def test_baidu_parse_with_modern_html_structure(self):
        """_search_baidu should parse current Baidu SERP HTML patterns."""
        html = """<html><body>
        <div class="result c-container" id="1">
          <h3 class="t"><a href="https://example.com/page1" target="_blank">Title One</a></h3>
          <div class="c-abstract">Snippet one content</div>
        </div>
        <div data-log='{"result":"2"}' class="c-container">
          <h3><a href="https://example.org/page2" data-url="https://real.url/page2">Title Two</a></h3>
          <span class="content-right_abc123">Snippet two</span>
        </div>
        <div class="result-op c-container">
          <h3><a href="https://example.net/page3" mu="https://real.mu/page3">Title Three</a></h3>
          <div class="c-abstract">Snippet three</div>
        </div>
        </body></html>"""

        with patch("server.httpx.AsyncClient", return_value=_mock_httpx_client(html)):
            results = await server._search_baidu("test query", max_results=5)

        self.assertEqual(len(results), 3)
        titles = [r["title"] for r in results]
        self.assertIn("Title One", titles)
        self.assertIn("Title Two", titles)
        self.assertIn("Title Three", titles)
        self.assertTrue(any("real.url" in r["href"] for r in results))

    async def test_baidu_parse_with_structural_fallback(self):
        """When CSS selectors miss, structural fallback finds h3 > a[href^=http]."""
        html = """<html><body>
        <div class="new-result-wrapper_xyz">
          <h3><a href="https://example.com/1">Structural Title 1</a></h3>
          <div class="new-snippet_abc">Snippet 1</div>
        </div>
        <div class="new-result-wrapper_xyz">
          <h3><a href="https://example.com/2">Structural Title 2</a></h3>
          <div class="new-snippet_abc">Snippet 2</div>
        </div>
        </body></html>"""

        with patch("server.httpx.AsyncClient", return_value=_mock_httpx_client(html)):
            results = await server._search_baidu("test", max_results=5)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "Structural Title 1")
        self.assertEqual(results[1]["title"], "Structural Title 2")

    async def test_baidu_captcha_detection(self):
        """_search_baidu should detect CAPTCHA pages and raise early."""
        captcha_html = """<html><body>
        <div>百度安全验证</div>
        <div id="captcha">请完成验证</div>
        </body></html>"""

        with patch("server.httpx.AsyncClient", return_value=_mock_httpx_client(captcha_html)):
            with self.assertRaises(server.DDGSException) as cm:
                await server._search_baidu("test", max_results=5)
            self.assertIn("CAPTCHA", str(cm.exception))

    async def test_baidu_no_parseable_results_diagnostics(self):
        """Zero results writes HTML preview to stderr, then raises."""
        html = "<html><body><p>No results here</p></body></html>"

        with patch("server.httpx.AsyncClient", return_value=_mock_httpx_client(html)):
            with self.assertRaises(server.DDGSException) as cm:
                await server._search_baidu("obscure-query-xyz", max_results=5)
            self.assertIn("no parseable results", str(cm.exception))

    async def test_auto_fallback_to_baidu_when_ddgs_fails(self):
        """When engine='auto' and DDGS fails, Baidu should be tried."""
        original_ddgs = server._search_ddgs
        original_baidu = server._search_baidu

        async def failing_ddgs(*_args, **_kwargs):
            raise server.DDGSException("DDGS timeout from China")

        async def succeeding_baidu(*_args, **_kwargs):
            return [{
                "title": "Baidu Result", "href": "https://example.com/baidu",
                "body": "Baidu body", "engine": "baidu", "_authority": 0.4,
            }]

        server._search_ddgs = failing_ddgs
        server._search_baidu = succeeding_baidu
        try:
            result = await server._search_with_engine("test query", "auto", max_results=5)
        finally:
            server._search_ddgs = original_ddgs
            server._search_baidu = original_baidu

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["engine"], "baidu")

    async def test_auto_fallback_raises_when_both_fail(self):
        """When both DDGS and Baidu fail, a combined error is raised."""
        original_ddgs = server._search_ddgs
        original_baidu = server._search_baidu

        async def failing_ddgs(*_args, **_kwargs):
            raise server.DDGSException("DDGS timeout")

        async def failing_baidu(*_args, **_kwargs):
            raise server.DDGSException("Baidu CAPTCHA")

        server._search_ddgs = failing_ddgs
        server._search_baidu = failing_baidu
        try:
            with self.assertRaises(server.DDGSException) as cm:
                await server._search_with_engine("test", "auto", max_results=5)
            error_text = str(cm.exception)
            self.assertIn("DuckDuckGo", error_text)
            self.assertIn("Baidu", error_text)
        finally:
            server._search_ddgs = original_ddgs
            server._search_baidu = original_baidu

    async def test_auto_does_not_fallback_on_successful_ddgs(self):
        """When DDGS succeeds, Baidu fallback should not be invoked."""
        original_ddgs = server._search_ddgs
        original_baidu = server._search_baidu
        baidu_called = False

        async def succeeding_ddgs(*_args, **_kwargs):
            return [{
                "title": "DDGS Result", "href": "https://example.com/ddgs",
                "body": "body", "engine": "ddgs",
            }]

        async def should_not_be_called(*_args, **_kwargs):
            nonlocal baidu_called
            baidu_called = True
            return []

        server._search_ddgs = succeeding_ddgs
        server._search_baidu = should_not_be_called
        try:
            result = await server._search_with_engine("test", "auto", max_results=5)
        finally:
            server._search_ddgs = original_ddgs
            server._search_baidu = original_baidu

        self.assertFalse(baidu_called)
        self.assertEqual(result[0]["engine"], "ddgs")

    async def test_baidu_rate_limit_enforced(self):
        """Baidu should respect rate limiting like other free engines."""
        server._RATE_LIMIT_TRACKER.clear()
        for _ in range(10):
            server._check_rate_limit("baidu")
        limit_err = server._check_rate_limit("baidu")
        self.assertIsNotNone(limit_err)
        self.assertIn("Rate limit", limit_err)
        self.assertIn("baidu", limit_err.lower())
        server._RATE_LIMIT_TRACKER.clear()


class FetchCodeRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_web_fetch_code_truncation_keeps_code_block_structure(self):
        original = server._fetch
        html = (
            "<html><title>Example</title><body><pre><code class='language-python'>"
            + "print('hello')\n" * 40
            + "</code></pre></body></html>"
        )

        async def fake_fetch(*_args, **_kwargs):
            return html, None

        server._fetch = fake_fetch
        try:
            result = await server.web_fetch_code("https://example.com/code", max_length=220)
        finally:
            server._fetch = original

        self.assertIn("# Example - Code Blocks", result)
        self.assertIn("``` python", result)
        self.assertEqual(result.count("```") % 2, 0)
        self.assertIn("truncated to 220 chars", result)
        self.assertLessEqual(len(result), 220)

    async def test_search_crawl_elapsed_time_includes_fetch_work(self):
        original = server._fetch
        html = "<html><title>Example</title><body><p>content</p></body></html>"

        async def fake_fetch(*_args, **_kwargs):
            await asyncio.sleep(0.02)
            return html, None

        server._fetch = fake_fetch
        try:
            result = await server.search_crawl(urls="https://example.com/page", max_pages=1)
        finally:
            server._fetch = original

        elapsed = int(re.search(r"fetched in (\d+)ms", result).group(1))
        self.assertGreaterEqual(elapsed, 15)

    async def test_web_fetch_negative_max_length_truncates_to_zero(self):
        original = server._fetch
        html = "<html><title>Example</title><body><p>content</p></body></html>"

        async def fake_fetch(*_args, **_kwargs):
            return html, None

        server._fetch = fake_fetch
        try:
            result = await server.web_fetch("https://example.com/page", max_length=-1)
        finally:
            server._fetch = original

        self.assertEqual("", result)

    async def test_web_fetch_parses_json_responses(self):
        original = server._fetch

        async def fake_fetch(*_args, **_kwargs):
            return '{"ok": true, "items": [1, 2]}', None

        server._fetch = fake_fetch
        try:
            result = await server.web_fetch("https://example.com/data.json", max_length=200)
        finally:
            server._fetch = original

        self.assertIn('"ok": true', result)
        self.assertIn('"items"', result)

    async def test_search_crawl_keeps_commas_inside_urls(self):
        original = server._fetch
        fetched = []
        html = "<html><title>Example</title><body><p>content</p></body></html>"

        async def fake_fetch(url, *_args, **_kwargs):
            fetched.append(url)
            return html, None

        server._fetch = fake_fetch
        try:
            result = await server.search_crawl(
                urls="https://example.com/a,b?x=1, https://example.com/c",
                max_pages=2,
            )
        finally:
            server._fetch = original

        self.assertIn("https://example.com/a,b?x=1", fetched)
        self.assertIn("2/2 pages fetched", result)


class ExternalApiRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_package_registry_case_and_scoped_npm_url(self):
        original = server.httpx.AsyncClient
        urls = []

        class Resp:
            status_code = 200

            def json(self):
                return {"name": "@types/node", "version": "1.0.0", "description": None, "license": None}

        class Client:
            def __init__(self, *_args, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                pass

            async def get(self, url, headers=None):
                urls.append(url)
                return Resp()

        server.httpx.AsyncClient = Client
        try:
            result = await server.search_package("@types/node", registry="NPM")
        finally:
            server.httpx.AsyncClient = original

        self.assertIn("### npm: @types/node v1.0.0", result)
        self.assertIn("@types%2Fnode", urls[0])

    async def test_search_package_explicit_registry_failure_raises(self):
        original = server.httpx.AsyncClient

        class Resp:
            status_code = 404

            def json(self):
                return {}

        class Client:
            def __init__(self, *_args, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                pass

            async def get(self, *_args, **_kwargs):
                return Resp()

        server.httpx.AsyncClient = Client
        try:
            with self.assertRaises(server.SearchError):
                await server.search_package("missing-package", registry="pypi")
        finally:
            server.httpx.AsyncClient = original

    async def test_search_package_rejects_empty_package_name(self):
        with self.assertRaises(server.SearchError):
            await server.search_package("   ", registry="pypi")

    async def test_search_security_rejects_empty_package_name(self):
        with self.assertRaises(server.SearchError):
            await server.search_security("   ", ecosystem="npm")

    async def test_search_security_rejects_unknown_ecosystem(self):
        with self.assertRaises(server.SearchError):
            await server.search_security("pkg", ecosystem="unknown-eco")

    async def test_search_security_auto_is_case_insensitive(self):
        original = server.httpx.AsyncClient
        seen = []

        class Resp:
            status_code = 200

            def json(self):
                return {}

        class Client:
            def __init__(self, *_args, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                pass

            async def post(self, _url, json):
                seen.append(json["package"]["ecosystem"])
                return Resp()

        server.httpx.AsyncClient = Client
        try:
            result = await server.search_security("pkg", ecosystem="AUTO")
        finally:
            server.httpx.AsyncClient = original

        self.assertIn("checked ecosystems", result)
        self.assertIn("npm", seen)

    async def test_search_security_auto_checks_past_first_no_vulns_ecosystem(self):
        original = server.httpx.AsyncClient
        seen = []

        class Resp:
            status_code = 200

            def json(self):
                if seen[-1] == "npm":
                    return {"vulns": [{"id": "NPM-1", "summary": "bad"}]}
                return {}

        class Client:
            def __init__(self, *_args, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                pass

            async def post(self, _url, json):
                seen.append(json["package"]["ecosystem"])
                return Resp()

        server.httpx.AsyncClient = Client
        try:
            result = await server.search_security("lodash")
        finally:
            server.httpx.AsyncClient = original

        self.assertIn("NPM-1", result)
        self.assertNotIn("PyPI: lodash - OK", result)
        self.assertIn("npm", seen)

    async def test_search_security_extracts_fixed_versions_from_osv_ranges(self):
        original = server.httpx.AsyncClient

        class Resp:
            status_code = 200

            def json(self):
                return {
                    "vulns": [
                        {
                            "id": "OSV-1",
                            "summary": None,
                            "aliases": None,
                            "affected": [
                                {
                                    "ranges": [
                                        {"events": [{"introduced": "0"}, {"fixed": "1.2.3"}]}
                                    ]
                                }
                            ],
                        }
                    ]
                }

        class Client:
            def __init__(self, *_args, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                pass

            async def post(self, *_args, **_kwargs):
                return Resp()

        server.httpx.AsyncClient = Client
        try:
            result = await server.search_security("pkg", ecosystem="npm")
        finally:
            server.httpx.AsyncClient = original

        self.assertIn("No description", result)
        self.assertIn("Aliases: none", result)
        self.assertIn("Fixed in: 1.2.3", result)

    async def test_github_issue_labels_are_split_and_quoted(self):
        original = server.httpx.AsyncClient
        urls = []

        class Resp:
            status_code = 200
            text = "{}"

            def json(self):
                return {"items": []}

        class Client:
            def __init__(self, *_args, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                pass

            async def get(self, url, headers=None):
                urls.append(url)
                return Resp()

        server.httpx.AsyncClient = Client
        try:
            await server.search_github_issues(query="x", labels="bug,good first issue")
        finally:
            server.httpx.AsyncClient = original

        search_q = unquote(parse_qs(urlparse(urls[0]).query)["q"][0])
        self.assertIn("label:bug", search_q)
        self.assertIn('label:"good first issue"', search_q)

    async def test_github_issue_null_body_does_not_crash(self):
        original = server.httpx.AsyncClient

        class Resp:
            status_code = 200
            text = "{}"

            def json(self):
                return {
                    "total_count": 1,
                    "items": [
                        {
                            "number": 1,
                            "html_url": "https://github.com/o/r/issues/1",
                            "state": "open",
                            "title": "T",
                            "repository_url": "https://api.github.com/repos/o/r",
                            "user": {"login": "u", "html_url": "https://github.com/u"},
                            "comments": 0,
                            "updated_at": "2026-04-29T00:00:00Z",
                            "body": None,
                            "labels": [],
                        }
                    ],
                }

        class Client:
            def __init__(self, *_args, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                pass

            async def get(self, *_args, **_kwargs):
                return Resp()

        server.httpx.AsyncClient = Client
        try:
            result = await server.search_github_issues(query="x")
        finally:
            server.httpx.AsyncClient = original

        self.assertIn("no description", result)

    async def test_github_issue_nullable_metadata_does_not_crash(self):
        original = server.httpx.AsyncClient

        class Resp:
            status_code = 200
            text = "{}"

            def json(self):
                return {
                    "total_count": 1,
                    "items": [
                        {
                            "number": None,
                            "html_url": None,
                            "state": None,
                            "title": None,
                            "repository_url": None,
                            "user": None,
                            "comments": None,
                            "updated_at": None,
                            "body": None,
                            "labels": [{"name": None}],
                        }
                    ],
                }

        class Client:
            def __init__(self, *_args, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                pass

            async def get(self, *_args, **_kwargs):
                return Resp()

        server.httpx.AsyncClient = Client
        try:
            result = await server.search_github_issues(query="x")
        finally:
            server.httpx.AsyncClient = original

        self.assertIn("Issue #?", result)
        self.assertIn("no title", result)
        self.assertIn("by unknown", result)
        self.assertIn("no description", result)

    async def test_github_issue_state_is_case_insensitive(self):
        original = server.httpx.AsyncClient
        urls = []

        class Resp:
            status_code = 200
            text = "{}"

            def json(self):
                return {"items": []}

        class Client:
            def __init__(self, *_args, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                pass

            async def get(self, url, headers=None):
                urls.append(url)
                return Resp()

        server.httpx.AsyncClient = Client
        try:
            await server.search_github_issues(query="x", state="Open")
        finally:
            server.httpx.AsyncClient = original

        search_q = unquote(parse_qs(urlparse(urls[0]).query)["q"][0])
        self.assertIn("state:open", search_q)

    async def test_github_issue_filter_only_repo_query_is_allowed(self):
        original = server.httpx.AsyncClient
        urls = []

        class Resp:
            status_code = 200
            text = "{}"

            def json(self):
                return {"items": []}

        class Client:
            def __init__(self, *_args, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                pass

            async def get(self, url, headers=None):
                urls.append(url)
                return Resp()

        server.httpx.AsyncClient = Client
        try:
            await server.search_github_issues(repo="python/cpython", query="", state="open")
        finally:
            server.httpx.AsyncClient = original

        search_q = unquote(parse_qs(urlparse(urls[0]).query)["q"][0])
        self.assertIn("repo:python/cpython", search_q)
        self.assertIn("state:open", search_q)

    async def test_search_rss_resolves_relative_links_and_guid_fallback(self):
        original = server._fetch
        feed = """
        <rss><channel><title>Feed</title>
          <item><title>One</title><link>/posts/one</link><description><![CDATA[<b>Hello</b> world]]></description></item>
          <item><title>Two</title><guid>/posts/two</guid></item>
        </channel></rss>
        """

        async def fake_fetch(*_args, **_kwargs):
            return feed, None

        server._fetch = fake_fetch
        try:
            result = await server.search_rss(feed_url="https://example.com/feed.xml", max_results=2)
        finally:
            server._fetch = original

        self.assertIn("https://example.com/posts/one", result)
        self.assertIn("https://example.com/posts/two", result)
        self.assertIn("Hello world", result)

    async def test_search_tutorial_level_is_case_insensitive(self):
        original = server._do_search
        captured = {}

        async def fake_search(**kwargs):
            captured.update(kwargs)
            return kwargs["query"]

        server._do_search = fake_search
        try:
            result = await server.search_tutorial("Python", level="Advanced")
        finally:
            server._do_search = original

        self.assertIn("advanced deep dive guide", result)
        self.assertIn("advanced deep dive guide", captured["query"])


class FormattingRegressionTests(unittest.TestCase):
    def test_result_formatters_handle_nullable_fields(self):
        result = {"title": None, "href": None, "body": None, "engine": None}
        self.assertIn("No title", server._build_result(1, result))
        self.assertIn("(no snippet)", server._build_result(1, result))
        self.assertIn("No title (no URL)", server._format_compact("q", [result], "Label"))
        self.assertIn("1. (no URL)", server._format_links("q", [result], "Label"))

    def test_duplicate_detection_handles_nullable_fields(self):
        self.assertFalse(server._is_duplicate({"title": None, "href": None}, [{"title": None, "href": None}]))

    def test_session_add_survives_missing_href(self):
        server._search_sessions.clear()
        results = [{"title": "No href here"}, {"href": "https://a.com", "title": "Has href"}]
        server._session_add("test", "query", results)
        self.assertIn("test", server._search_sessions)
        self.assertEqual(server._search_sessions["test"]["history"][0]["count"], 2)
        server._search_sessions.clear()


if __name__ == "__main__":
    unittest.main()
