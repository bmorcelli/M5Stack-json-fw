import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from v2 import translate as tr


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache_path = Path(self.temp.name) / "cache.json"
        self.now = 0.0
        self.sleeps = []
        clock = patch.object(tr.time, "monotonic", side_effect=lambda: self.now)
        sleep = patch.object(tr.time, "sleep", side_effect=self.sleep)
        jitter = patch.object(tr.random, "uniform", return_value=0)
        for mock in (clock, sleep, jitter):
            mock.start()
            self.addCleanup(mock.stop)

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def guarded(self, backend=None, cache=None, seconds=3000):
        return tr.GuardedTranslator(backend or Mock(), cache if cache is not None else {},
                                    self.cache_path, seconds)

    def test_requests_are_spaced_after_completion(self):
        backend = Mock()
        def translate(text):
            self.now += 2  # network latency
            return "Hello"
        backend.translate.side_effect = translate
        guarded = self.guarded(backend)
        guarded.translate("你好")
        guarded.translate("世界")
        self.assertEqual(self.sleeps, [5])
        self.assertEqual(guarded.requests, 2)

    def test_429_recovery_respects_retry_after_and_slows_following_requests(self):
        backend = Mock()
        backend.translate.side_effect = [tr.TooManyRequests(180), "Hello", "World"]
        guarded = self.guarded(backend)
        self.assertEqual(guarded.translate("你好"), "Hello")
        guarded.translate("世界")
        self.assertEqual(self.sleeps, [180, 10, 10])
        self.assertEqual(guarded.requests, 3)

    def test_no_sleep_or_retry_beyond_budget(self):
        backend = Mock()
        backend.translate.side_effect = tr.TooManyRequests(500)
        guarded = self.guarded(backend, seconds=100)
        with self.assertRaises(tr.BudgetExceeded):
            guarded.translate("你好")
        self.assertEqual(self.sleeps, [])
        self.assertEqual(backend.translate.call_count, 1)
        self.assertTrue(guarded.stopped)

    def test_persistent_429_opens_circuit(self):
        backend = Mock()
        backend.translate.side_effect = tr.TooManyRequests()
        guarded = self.guarded(backend, seconds=10000)
        with self.assertRaises(tr.BudgetExceeded):
            guarded.translate("你好")
        self.assertEqual(backend.translate.call_count, tr.MAX_CONSECUTIVE_RATE_LIMITS)
        self.assertTrue(guarded.stopped)

    def test_cache_still_applied_after_first_network_miss_exhausts_budget(self):
        backend = Mock()
        guarded = self.guarded(backend, {"世界": "World"}, seconds=0)
        data = [{"name": "你好"}, {"name": "世界"}]
        stats = tr.Stats()
        tr.walk(data, "", guarded, guarded.cache, stats, [])
        self.assertTrue(guarded.stopped)
        self.assertEqual(data[1]["name_en"], "World")
        self.assertEqual(stats.updated_fields, 1)
        backend.translate.assert_not_called()

    def test_mixed_text_uses_one_request_instead_of_fragment_requests(self):
        backend = Mock()
        backend.translate.return_value = "M5 Hello, World!"
        guarded = self.guarded(backend)
        text = "M5 你好，世界！"
        result = tr.translate_to_english(text, guarded, guarded.cache, tr.Stats(), [], {})
        self.assertEqual(result, "M5 Hello, World!")
        backend.translate.assert_called_once_with(text)

    def test_legacy_segment_cache_is_reused_without_network(self):
        backend = Mock()
        guarded = self.guarded(backend, {"你好": "Hello", "世界": "World"}, seconds=0)
        result = tr.translate_to_english("M5Stack 你好，世界！", guarded, guarded.cache,
                                         tr.Stats(), [], {})
        self.assertEqual(result, "M5Stack Hello，World！")
        backend.translate.assert_not_called()

    def test_checkpoint_contains_latest_translation(self):
        backend = Mock()
        backend.translate.return_value = "Hello"
        guarded = self.guarded(backend)
        with patch.object(tr, "CHECKPOINT_EVERY", 1):
            tr.translate_to_english("你好", guarded, guarded.cache, tr.Stats(), [], {})
        self.assertEqual(json.loads(self.cache_path.read_text(encoding="utf-8")),
                         {"你好": "Hello"})

    def test_long_translation_resumes_cached_chunks_after_interruption(self):
        first = "你好" * 450
        second = "世界" * 450
        text = first + "\n" + second
        backend = Mock()
        backend.translate.side_effect = ["Hello", tr.TooManyRequests(500)]
        guarded = self.guarded(backend, seconds=100)
        with self.assertRaises(tr.BudgetExceeded):
            tr.translate_to_english(text, guarded, guarded.cache, tr.Stats(), [], {})
        self.assertEqual(guarded.cache[first], "Hello")
        resumed_backend = Mock()
        resumed_backend.translate.return_value = "World"
        resumed = self.guarded(resumed_backend, guarded.cache)
        result = tr.translate_to_english(text, resumed, resumed.cache, tr.Stats(), [], {})
        self.assertEqual(result, "Hello\nWorld")
        resumed_backend.translate.assert_called_once_with(second)

    def test_json_client_preserves_lines_and_handles_429(self):
        session = Mock()
        response = Mock(status_code=200)
        response.json.return_value = [["Hello.", "zh-CN"], ["World!", "zh-CN"]]
        session.get.return_value.__enter__ = Mock(return_value=response)
        session.get.return_value.__exit__ = Mock(return_value=False)
        with patch.object(tr, "requests", Mock(Session=Mock(return_value=session))):
            client = tr.GoogleTranslator()
        self.assertEqual(client.translate("你好。\n\n# English\n世界！"),
                         "Hello.\n\n# English\nWorld!")
        params = session.get.call_args.kwargs["params"]
        self.assertEqual([value for key, value in params if key == "q"], ["你好。", "世界！"])
        self.assertEqual(session.get.call_args.args[0],
                         "https://translate.googleapis.com/translate_a/t")
        response.status_code = 429
        response.headers = {"Retry-After": "120"}
        with self.assertRaises(tr.TooManyRequests) as raised:
            client.translate("你好")
        self.assertEqual(raised.exception.retry_after, 120)

        # A truncated batch must not silently replace only part of the input.
        response.status_code = 200
        response.json.return_value = [["Hello", "zh-CN"]]
        with self.assertRaises(ValueError):
            client.translate("你好\n世界")
        client.source = "zh-CN"
        response.json.return_value = ["Hello", "World"]
        self.assertEqual(client.translate("你好\n世界"), "Hello\nWorld")

    def test_json_escaped_unicode_reaches_client_as_real_characters(self):
        source = json.loads(r'{"name": "\u4f60\u597d"}')
        backend = Mock()
        backend.translate.return_value = "Hello"
        guarded = self.guarded(backend)
        tr.walk([source], "", guarded, guarded.cache, tr.Stats(), [])
        backend.translate.assert_called_once_with("你好")
        self.assertEqual(source["name_en"], "Hello")


if __name__ == "__main__":
    unittest.main()
