import asyncio
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


if __name__ == "__main__":
    unittest.main()
