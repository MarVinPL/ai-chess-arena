import tempfile
import unittest
from pathlib import Path

from model_sandbox import (
    SandboxLimits, artifact_fingerprint, build_systemd_run_argv,
    validate_artifact, validate_limits,
)


class ModelSandboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "managed-models"
        self.root.mkdir()
        self.artifact = self.root / "candidate.gguf"
        self.artifact.write_bytes(b"fixture-not-a-model")

    def tearDown(self):
        self.temp.cleanup()

    def test_profile_is_shell_free_and_pins_read_only_artifact(self):
        argv = build_systemd_run_argv(
            job_id="a" * 16,
            artifact_path=self.artifact,
            worker_argv=["/opt/arena/bin/trusted-worker", "--model", str(self.artifact)],
            model_root=self.root,
        )
        self.assertEqual(argv[0], "/usr/bin/systemd-run")
        self.assertIn("--system", argv)
        self.assertIn("--wait", argv)
        self.assertIn("--collect", argv)
        self.assertIn("--", argv)
        binds = next(x for x in argv if x.startswith("--property=BindReadOnlyPaths="))
        self.assertIn(str(self.artifact), binds)
        self.assertIn("/opt/arena/bin/trusted-worker", binds)
        self.assertIn("--property=DynamicUser=yes", argv)
        self.assertIn("--property=PrivateNetwork=yes", argv)
        self.assertIn("--property=ProtectHome=tmpfs", argv)
        self.assertIn("--property=KillMode=control-group", argv)
        self.assertTrue(any(x.startswith("--property=CPUQuota=") for x in argv))
        self.assertEqual(argv[-3:], ["/opt/arena/bin/trusted-worker", "--model", str(self.artifact)])
        self.assertNotIn("shell", " ".join(argv).lower())

    def test_artifact_fingerprint_records_sha256_and_size(self):
        record = artifact_fingerprint(self.artifact, self.root)
        self.assertEqual(record["size_bytes"], len(b"fixture-not-a-model"))
        self.assertEqual(len(record["sha256"]), 64)
        self.assertEqual(record["artifact_name"], "candidate.gguf")

    def test_rejects_artifact_outside_private_model_root(self):
        outside = Path(self.temp.name) / "outside.gguf"
        outside.write_bytes(b"x")
        with self.assertRaisesRegex(ValueError, "managed-models"):
            validate_artifact(outside, self.root)

    def test_rejects_non_gguf_and_missing_artifacts(self):
        other = self.root / "weights.bin"
        other.write_bytes(b"x")
        with self.assertRaisesRegex(ValueError, "GGUF"):
            validate_artifact(other, self.root)
        with self.assertRaises(FileNotFoundError):
            validate_artifact(self.root / "missing.gguf", self.root)

    def test_rejects_symlink_escape(self):
        outside = Path(self.temp.name) / "outside.gguf"
        outside.write_bytes(b"x")
        link = self.root / "escape.gguf"
        link.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "managed-models"):
            validate_artifact(link, self.root)

    def test_rejects_invalid_job_id_and_non_absolute_worker(self):
        for job_id in ("../../bad", "short", "g" * 16):
            with self.assertRaisesRegex(ValueError, "job_id"):
                build_systemd_run_argv(
                    job_id=job_id, artifact_path=self.artifact,
                    worker_argv=["/opt/worker"], model_root=self.root)
        with self.assertRaisesRegex(ValueError, "bezwzględna"):
            build_systemd_run_argv(
                job_id="b" * 16, artifact_path=self.artifact,
                worker_argv=["worker"], model_root=self.root)

    def test_rejects_invalid_limits(self):
        invalid = [
            SandboxLimits(cpu_quota_percent=0),
            SandboxLimits(memory_max_bytes=1),
            SandboxLimits(runtime_max_seconds=0),
            SandboxLimits(tasks_max=0),
        ]
        for limits in invalid:
            with self.assertRaises(ValueError):
                validate_limits(limits)

    def test_shell_metacharacters_remain_literal_arguments(self):
        arg = "$(touch /tmp/not-run); ;"
        argv = build_systemd_run_argv(
            job_id="c" * 16, artifact_path=self.artifact,
            worker_argv=["/opt/arena/bin/worker", arg], model_root=self.root)
        self.assertEqual(argv[-1], arg)
        self.assertNotIn("sh", argv)


    def test_rejects_whitespace_in_systemd_bind_paths(self):
        spaced = self.root / "candidate with spaces.gguf"
        spaced.write_bytes(b"fixture")
        with self.assertRaisesRegex(ValueError, "białych znaków"):
            build_systemd_run_argv(
                job_id="d" * 16, artifact_path=spaced,
                worker_argv=["/opt/arena/bin/trusted-worker"],
                model_root=self.root)


if __name__ == "__main__":
    unittest.main()
