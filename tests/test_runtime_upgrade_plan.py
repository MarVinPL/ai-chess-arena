import unittest
from pathlib import Path

from runtime_upgrade_plan import (
    RuntimeContractError,
    UpstreamLlamaServerProfile,
    parse_chat_completion_response,
    validate_health_response,
)


class UpstreamRuntimePlanTests(unittest.TestCase):
    def test_candidate_profile_is_separate_loopback_endpoint(self):
        profile = UpstreamLlamaServerProfile()
        self.assertEqual(profile.api_base, "http://127.0.0.1:8089")
        self.assertNotEqual(profile.port, 8088)
        self.assertEqual(profile.parallel, 1)

    def test_command_is_deterministic_and_cpu_only_offline_one_slot(self):
        profile = UpstreamLlamaServerProfile(binary=Path("/opt/candidate/llama-server"))
        argv = profile.build_argv("/var/lib/arena/quarantine/model.gguf")
        self.assertEqual(argv[0], "/opt/candidate/llama-server")
        self.assertEqual(argv[argv.index("--host") + 1], "127.0.0.1")
        self.assertEqual(argv[argv.index("--port") + 1], "8089")
        self.assertEqual(argv[argv.index("-np") + 1], "1")
        self.assertEqual(argv[argv.index("-ngl") + 1], "0")
        self.assertIn("--offline", argv)
        self.assertIn("--no-webui", argv)
        self.assertIn("--no-warmup", argv)
        self.assertTrue(argv[argv.index("-m") + 1].endswith("model.gguf"))

    def test_candidate_profile_rejects_non_loopback_binding(self):
        with self.assertRaises(ValueError):
            UpstreamLlamaServerProfile(host="0.0.0.0").build_argv("model.gguf")

    def test_candidate_profile_rejects_parallel_models_and_bad_resources(self):
        with self.assertRaises(ValueError):
            UpstreamLlamaServerProfile(parallel=2).build_argv("model.gguf")
        with self.assertRaises(ValueError):
            UpstreamLlamaServerProfile(threads=0).build_argv("model.gguf")

    def test_candidate_profile_rejects_non_gguf(self):
        with self.assertRaises(ValueError):
            UpstreamLlamaServerProfile().build_argv("model.bin")

    def test_health_contract_accepts_ready_and_rejects_loading_or_malformed(self):
        validate_health_response(200, {"status": "ok"})
        for status, payload in (
            (503, {"error": "loading"}),
            (200, {"status": "loading"}),
            (200, []),
        ):
            with self.subTest(status=status, payload=payload):
                with self.assertRaises(RuntimeContractError):
                    validate_health_response(status, payload)

    def test_chat_completion_contract_extracts_runner_fields(self):
        result = parse_chat_completion_response(200, {
            "choices": [{"message": {"content": "e2e4"}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 4},
        })
        self.assertEqual(result, ("e2e4", "stop", 4))

    def test_chat_completion_contract_allows_optional_usage(self):
        self.assertEqual(
            parse_chat_completion_response(200, {"choices": [{"message": {"content": "e2e4"}}]}),
            ("e2e4", None, None),
        )
        self.assertEqual(
            parse_chat_completion_response(200, {
                "choices": [{"message": {"content": "e2e4"}}],
                "usage": {"completion_tokens": True},
            }),
            ("e2e4", None, None),
        )

    def test_chat_completion_contract_rejects_bad_status_and_shapes(self):
        invalid = (
            (503, {"choices": [{"message": {"content": "e2e4"}}]}),
            (200, []),
            (200, {"choices": []}),
            (200, {"choices": [{"message": {}}]}),
            (200, {"choices": [{"message": {"content": 42}}]}),
            (200, {"choices": [{"message": {"content": "e2e4"}, "finish_reason": 7}]}),
        )
        for status, payload in invalid:
            with self.subTest(status=status, payload=payload):
                with self.assertRaises(RuntimeContractError):
                    parse_chat_completion_response(status, payload)


if __name__ == "__main__":
    unittest.main()
