import asyncio
import contextlib
import io
import os
import re
import unittest
from urllib.parse import parse_qs, unquote, urlparse

import server


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
            result = await server.web_search("google key regression", engine="google", max_results=1)
        finally:
            if original_google_key is None:
                os.environ.pop(server.ENV_GOOGLE_KEY, None)
            else:
                os.environ[server.ENV_GOOGLE_KEY] = original_google_key
            if original_google_cx is None:
                os.environ.pop(server.ENV_GOOGLE_CX, None)
            else:
                os.environ[server.ENV_GOOGLE_CX] = original_google_cx

        self.assertIn("GOOGLE_API_KEY + GOOGLE_CSE_ID", result)

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
            result = await server.web_fetch_code("https://example.com/code", max_length=80)
        finally:
            server._fetch = original

        self.assertIn("# Example - Code Blocks", result)
        self.assertIn("``` python", result)
        self.assertEqual(result.count("```") % 2, 0)
        self.assertIn("truncated to 80 chars", result)

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

        self.assertIn("truncated to 0 chars", result)
        self.assertNotIn("truncated to -1 chars", result)


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

    async def test_search_package_rejects_empty_package_name(self):
        with self.assertRaises(server.SearchError):
            await server.search_package("   ", registry="pypi")

    async def test_search_security_rejects_empty_package_name(self):
        with self.assertRaises(server.SearchError):
            await server.search_security("   ", ecosystem="npm")

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

        self.assertIn("(no description)", result)

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
        self.assertIn("[No title]()", server._format_compact("q", [result], "Label"))
        self.assertIn("1. ", server._format_links("q", [result], "Label"))

    def test_duplicate_detection_handles_nullable_fields(self):
        self.assertTrue(server._is_duplicate({"title": None, "href": None}, [{"title": None, "href": None}]))


if __name__ == "__main__":
    unittest.main()
