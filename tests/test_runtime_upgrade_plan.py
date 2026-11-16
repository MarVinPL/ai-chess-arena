import unittest
from pathlib import Path

from runtime_upgrade_plan import UpstreamLlamaServerProfile


class UpstreamRuntimePlanTests(unittest.TestCase):
    def test_candidate_profile_is_separate_loopback_endpoint(self):
        profile = UpstreamLlamaServerProfile()
        self.assertEqual(profile.api_base, "http://127.0.0.1:8089")
        self.assertNotEqual(profile.port, 8088)
        self.assertEqual(profile.parallel, 1)

    def test_command_is_deterministic_and_uses_one_slot(self):
        profile = UpstreamLlamaServerProfile(binary=Path("/opt/candidate/llama-server"))
        argv = profile.build_argv("/var/lib/arena/quarantine/model.gguf")
        self.assertEqual(argv[0], "/opt/candidate/llama-server")
        self.assertIn("--host", argv)
        self.assertIn("127.0.0.1", argv)
        self.assertIn("--port", argv)
        self.assertIn("8089", argv)
        self.assertIn("-np", argv)
        self.assertEqual(argv[argv.index("-np") + 1], "1")
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


if __name__ == "__main__":
    unittest.main()
