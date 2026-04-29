import asyncio
import contextlib
import io
import os
import re
import unittest

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


if __name__ == "__main__":
    unittest.main()
