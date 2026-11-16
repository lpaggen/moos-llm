import io
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from tools.wait_vllm_ready import probe, wait_ready


class VllmReadinessTests(unittest.TestCase):
    def test_adapter_must_be_registered_even_when_api_is_healthy(self):
        with patch("urllib.request.urlopen", side_effect=[
            io.BytesIO(b""), io.BytesIO(b'{"data": [{"id": "Qwen/Qwen3-8B"}]}'),
        ]):
            with self.assertRaisesRegex(ValueError, "not registered"):
                probe("http://localhost:8001", "local-key", "oow-finetuned")

    def test_healthy_adapter_is_ready_and_requests_are_authenticated(self):
        with patch("urllib.request.urlopen", side_effect=[
            io.BytesIO(b""), io.BytesIO(b'{"data": [{"id": "oow-finetuned"}]}'),
        ]) as urlopen:
            probe("http://localhost:8001", "local-key", "oow-finetuned")
        self.assertEqual([call.args[0].full_url for call in urlopen.call_args_list],
                         ["http://localhost:8001/health", "http://localhost:8001/v1/models"])
        for call in urlopen.call_args_list:
            self.assertEqual(call.args[0].get_header("Authorization"), "Bearer local-key")

    def test_loading_api_is_retried(self):
        with (
            patch("tools.wait_vllm_ready.probe", side_effect=[
                HTTPError("http://localhost/health", 503, "loading", {}, None), None,
            ]) as check,
            patch("time.sleep"),
        ):
            wait_ready("http://localhost", "key", "oow-finetuned", 10)
        self.assertEqual(check.call_count, 2)

    def test_dead_supervisor_fails_without_waiting_for_timeout(self):
        with (
            patch("os.kill", side_effect=ProcessLookupError),
            patch("tools.wait_vllm_ready.probe") as check,
        ):
            with self.assertRaisesRegex(RuntimeError, "supervisor exited"):
                wait_ready("http://localhost", "key", "oow-finetuned", 600, 123)
        check.assert_not_called()

    def test_timeout_reports_last_readiness_failure(self):
        with (
            patch("time.monotonic", side_effect=[0, 0, 1, 2]),
            patch("time.sleep"),
            patch("tools.wait_vllm_ready.probe", side_effect=ValueError("adapter missing")),
        ):
            with self.assertRaisesRegex(RuntimeError, "timed out.*adapter missing"):
                wait_ready("http://localhost", "key", "oow-finetuned", 1)


if __name__ == "__main__":
    unittest.main()
