"""Regression tests for the Gradle dependency cache fingerprint."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/gradle_dependency_cache_key.py"


class GradleDependencyCacheKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inputs = (
            "remote_worker_gui/package-lock.json",
            "remote_worker_gui/src-tauri/Cargo.lock",
            "remote_worker_gui/src-tauri/gen/android/app/build.gradle.kts",
            "remote_worker_gui/src-tauri/gen/android/settings.gradle.kts",
            "remote_worker_gui/src-tauri/gen/android/gradle.properties",
            "remote_worker_gui/src-tauri/gen/android/gradle/gradle-wrapper.properties",
            "remote_worker_gui/src-tauri/gen/android/gradle/libs.versions.toml",
            "remote_worker_gui/src-tauri/gen/android/app/gradle.lockfile",
        )
        for path in self.inputs:
            self.write(path, "fixture\n")

    def write(self, path, content):
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def key(self, runner_os="Linux", arch="X64"):
        output = self.root / "output"
        output.write_text("", encoding="utf-8")
        env = dict(os.environ, RUNNER_OS=runner_os, RUNNER_ARCH=arch,
                   GITHUB_OUTPUT=str(output))
        proc = subprocess.run([sys.executable, str(SCRIPT)], cwd=self.root,
                              env=env, check=True, capture_output=True, text=True)
        key = proc.stdout.strip()
        self.assertEqual(output.read_text().strip(), "key=" + key)
        return key

    def test_stability_and_source_exclusion(self):
        key = self.key()
        self.assertEqual(key, self.key())
        for path in ("remote_worker_gui/src-tauri/src/lib.rs",
                     "remote_worker_gui/public/index.html",
                     "remote_worker_gui/src-tauri/gen/android/.gradle/ignored.gradle",
                     "remote_worker_gui/src-tauri/gen/android/app/build/ignored.gradle"):
            self.write(path, "changed\n")
        self.assertEqual(key, self.key())

    def test_each_dependency_input_changes_key(self):
        key = self.key()
        for path in self.inputs:
            with self.subTest(path=path):
                self.write(path, "changed\n")
                self.assertNotEqual(key, self.key())
                self.write(path, "fixture\n")
                self.assertEqual(key, self.key())

    def test_platform_separation(self):
        key = self.key()
        self.assertNotEqual(key, self.key("Windows"))
        self.assertNotEqual(key, self.key(arch="ARM64"))

    def test_missing_generated_project_fails(self):
        (self.root / self.inputs[2]).unlink()
        with self.assertRaises(subprocess.CalledProcessError):
            self.key()


if __name__ == "__main__":
    unittest.main()
