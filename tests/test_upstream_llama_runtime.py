import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from runtime_factory import create_runtime
from runtime_upgrade_plan import RuntimeContractError, UpstreamLlamaServerProfile
from upstream_llama_runtime import UpstreamLlamaServerRuntime


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class FakeProcess:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


class UpstreamRuntimeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.runtime_root = root / "runtimes"
        self.runtime_root.mkdir()
        self.binary = self.runtime_root / "llama-server"
        self.binary.write_text("# test fixture; never executed", encoding="utf-8")
        self.binary.chmod(0o755)
        self.model_root = root / "managed-models"
        self.model_root.mkdir()
        self.model = self.model_root / "fixture.gguf"
        self.model.write_bytes(b"synthetic test fixture, not a model")

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def healthy_open(_request, timeout=None):
        return FakeResponse({"status": "ok"})

    def make_runtime(self, **kwargs):
        return UpstreamLlamaServerRuntime(
            profile=UpstreamLlamaServerProfile(binary=self.binary),
            monitor_interval=3600,
            urlopen=kwargs.pop("urlopen", self.healthy_open),
            runtime_root=self.runtime_root,
            model_root=self.model_root,
            **kwargs,
        )

    def test_starts_only_owned_process_with_isolated_safe_argv(self):
        processes = []
        def fake_popen(*args, **kwargs):
            process = FakeProcess(*args, **kwargs)
            processes.append(process)
            return process
        popen = Mock(side_effect=fake_popen)
        runtime = self.make_runtime(popen=popen)
        try:
            runtime.ensure_loaded(self.model)
            args, options = popen.call_args
            argv = args[0]
            self.assertEqual(argv[0], str(self.binary))
            self.assertEqual(argv[argv.index("--host") + 1], "127.0.0.1")
            self.assertEqual(argv[argv.index("--port") + 1], "8089")
            self.assertEqual(argv[argv.index("-np") + 1], "1")
            self.assertEqual(argv[argv.index("-ngl") + 1], "0")
            self.assertIn("--offline", argv)
            self.assertIn("--no-webui", argv)
            self.assertIn("--no-warmup", argv)
            self.assertTrue(options["start_new_session"])
            self.assertEqual(runtime.loaded_model, str(self.model))
            runtime.stop()
            self.assertTrue(processes[0].terminated)
        finally:
            runtime.shutdown()

    def test_rejects_missing_or_non_gguf_model_before_process_start(self):
        popen = Mock()
        runtime = self.make_runtime(popen=popen)
        try:
            with self.assertRaises(Exception):
                runtime.ensure_loaded(self.model.with_name("missing.gguf"))
            with self.assertRaises(Exception):
                runtime.ensure_loaded(self.model.with_suffix(".bin"))
            popen.assert_not_called()
        finally:
            runtime.shutdown()

    def test_health_response_fails_closed(self):
        runtime = self.make_runtime(
            urlopen=lambda *_a, **_k: FakeResponse({"status": "loading"})
        )
        try:
            self.assertFalse(runtime._healthy())
        finally:
            runtime.shutdown()

    def test_chat_completion_payload_is_validated_before_replay(self):
        runtime = self.make_runtime(
            urlopen=lambda *_a, **_k: FakeResponse({"choices": []})
        )
        try:
            from urllib.request import Request
            request = Request(
                "http://127.0.0.1:8089/v1/chat/completions",
                data=b"{}",
                method="POST",
            )
            with self.assertRaises(RuntimeContractError):
                runtime._validated_urlopen(request, timeout=None)
        finally:
            runtime.shutdown()

    def test_chat_completion_valid_payload_is_replayed_after_validation(self):
        payload = {
            "choices": [{"message": {"content": "e2e4"}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 1},
        }
        runtime = self.make_runtime(urlopen=lambda *_a, **_k: FakeResponse(payload))
        try:
            from urllib.request import Request
            response = runtime._validated_urlopen(
                Request("http://127.0.0.1:8089/v1/chat/completions", data=b"{}", method="POST")
            )
            self.assertEqual(json.loads(response.read())["choices"][0]["message"]["content"], "e2e4")
        finally:
            runtime.shutdown()

    def test_model_path_must_be_inside_managed_model_store(self):
        outside = Path(self.temp.name) / "outside.gguf"
        outside.write_bytes(b"synthetic fixture")
        popen = Mock()
        runtime = self.make_runtime(popen=popen)
        try:
            from engine_runner import RunnerError
            with self.assertRaisesRegex(RunnerError, "arena-managed model store"):
                runtime.ensure_loaded(outside)
            popen.assert_not_called()
        finally:
            runtime.shutdown()

    def test_binary_path_must_be_inside_dedicated_runtime_root(self):
        outside = Path(self.temp.name) / "outside" / "llama-server"
        with self.assertRaisesRegex(ValueError, "dedicated runtime directory"):
            UpstreamLlamaServerRuntime(
                profile=UpstreamLlamaServerProfile(binary=outside),
                runtime_root=self.runtime_root,
                model_root=self.model_root,
                monitor_interval=3600,
            )

    def test_stop_kills_only_owned_child_after_terminate_timeout(self):
        from subprocess import TimeoutExpired
        process = FakeProcess()
        wait_calls = {"count": 0}
        def wait(timeout=None):
            wait_calls["count"] += 1
            if wait_calls["count"] == 1:
                raise TimeoutExpired("llama-server", timeout)
            return process.returncode
        process.wait = wait
        runtime = self.make_runtime(popen=Mock(return_value=process))
        try:
            runtime._process = process
            runtime._desired_model_path = self.model
            runtime.stop()
            self.assertTrue(process.terminated)
            self.assertTrue(process.killed)
            self.assertIsNone(runtime._process)
            self.assertIsNone(runtime._desired_model_path)
        finally:
            runtime.shutdown()

    def test_inherited_generate_uses_validated_transport_and_runtime_metadata(self):
        import chess
        from engine_runner import RunnerError
        runtime = self.make_runtime()
        runtime.ensure_loaded = lambda *_args, **_kwargs: None
        payloads = [
            {"choices": [{"message": {"content": "e2e4"}, "finish_reason": "stop"}]},
            {"choices": []},
        ]
        runtime._http_open = lambda *_args, **_kwargs: FakeResponse(payloads.pop(0))
        try:
            move, metadata = runtime.generate(self.model, chess.Board())
            self.assertEqual(move.uci(), "e2e4")
            self.assertEqual(metadata["runtime"], UpstreamLlamaServerRuntime.RUNTIME_NAME)
            with self.assertRaises(RuntimeContractError):
                runtime.generate(self.model, chess.Board())
        finally:
            runtime.shutdown()

    def test_intentional_stop_does_not_leave_watchdog_restart_intent(self):
        popen = Mock(side_effect=lambda *a, **k: FakeProcess(*a, **k))
        runtime = self.make_runtime(popen=popen)
        try:
            runtime.ensure_loaded(self.model)
            runtime.stop()
            runtime._monitor_once()
            self.assertEqual(popen.call_count, 1)
            self.assertIsNone(runtime._desired_model_path)
        finally:
            runtime.shutdown()

    def test_factory_defaults_to_existing_llamafile_runtime(self):
        runtime = create_runtime(environ={}, popen=lambda *_a, **_k: FakeProcess())
        try:
            from engine_runner import LlamafileRuntime
            self.assertIs(type(runtime), LlamafileRuntime)
            self.assertEqual(runtime.api_base, "http://127.0.0.1:8088")
        finally:
            runtime.shutdown()

    def test_factory_requires_explicit_upstream_enablement(self):
        with self.assertRaises(ValueError):
            create_runtime(environ={"ARENA_RUNTIME": "llama-server"})

    def test_factory_rejects_unknown_runtime_without_fallback(self):
        with self.assertRaises(ValueError):
            create_runtime(environ={"ARENA_RUNTIME": "automatic"})

    def test_factory_selects_upstream_only_after_both_opt_ins(self):
        runtime = create_runtime(
            environ={
                "ARENA_RUNTIME": "llama-server",
                "ARENA_UPSTREAM_RUNTIME_ENABLED": "1",
                "ARENA_UPSTREAM_LLAMA_SERVER_BIN": str(self.binary),
                "ARENA_UPSTREAM_RUNTIME_ROOT": str(self.runtime_root),
            },
            popen=lambda *_a, **_k: FakeProcess(),
            urlopen=self.healthy_open,
            model_root=self.model_root,
        )
        try:
            self.assertIsInstance(runtime, UpstreamLlamaServerRuntime)
            self.assertEqual(runtime.api_base, "http://127.0.0.1:8089")
        finally:
            runtime.shutdown()


if __name__ == "__main__":
    unittest.main()
