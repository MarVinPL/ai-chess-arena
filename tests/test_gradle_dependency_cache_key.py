"""Regression tests for the Gradle dependency cache fingerprint."""
import os
import json
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
            "remote_worker_gui/src-tauri/gen/android/app/build.gradle.kts",
            "remote_worker_gui/src-tauri/gen/android/settings.gradle.kts",
            "remote_worker_gui/src-tauri/gen/android/gradle.properties",
            "remote_worker_gui/src-tauri/gen/android/gradle/gradle-wrapper.properties",
            "remote_worker_gui/src-tauri/gen/android/gradle/libs.versions.toml",
            "remote_worker_gui/src-tauri/gen/android/app/gradle.lockfile",
        )
        for path in self.inputs:
            self.write(path, "fixture\n")
        self.npm_path = "remote_worker_gui/package-lock.json"
        self.cargo_path = "remote_worker_gui/src-tauri/Cargo.lock"
        self.npm = {"packages": {"": {"version": "0.1.0"}, "node_modules/@tauri-apps/cli": {"version": "2.12.6", "integrity": "sha512-fixture"}}}
        self.cargo = """[[package]]
name = "tauri"
version = "2.12.7"
[[package]]
name = "unrelated"
version = "1.0.0"
"""
        self.write(self.npm_path, json.dumps(self.npm))
        self.write(self.cargo_path, self.cargo)

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

    def test_unrelated_lock_changes_do_not_invalidate(self):
        key = self.key()
        self.npm["packages"][""]["version"] = "0.2.0"
        self.npm["packages"]["node_modules/unrelated"] = {"version": "9.0.0"}
        self.write(self.npm_path, json.dumps(self.npm))
        self.write(self.cargo_path, self.cargo.replace("1.0.0", "1.1.0"))
        self.assertEqual(key, self.key())

    def test_generator_versions_invalidate(self):
        key = self.key()
        self.npm["packages"]["node_modules/@tauri-apps/cli"]["version"] = "2.12.8"
        self.write(self.npm_path, json.dumps(self.npm))
        self.assertNotEqual(key, self.key())
        self.npm["packages"]["node_modules/@tauri-apps/cli"]["version"] = "2.12.6"
        self.write(self.npm_path, json.dumps(self.npm))
        self.assertEqual(key, self.key())
        self.write(self.cargo_path, self.cargo.replace("2.12.7", "2.12.8"))
        self.assertNotEqual(key, self.key())

    def test_buildsrc_and_verification_metadata_invalidate(self):
        key = self.key()
        self.write("remote_worker_gui/src-tauri/gen/android/buildSrc/src/main/kotlin/Plugin.kt", "plugin")
        self.assertNotEqual(key, self.key())
        key = self.key()
        self.write("remote_worker_gui/src-tauri/gen/android/gradle/verification-metadata.xml", "<verification/>")
        self.assertNotEqual(key, self.key())

    def test_exact_native_packaging_override_preserves_dependency_key(self):
        path = self.inputs[0]
        original = 'android {\n    namespace = "example"\n}\ndependencies { implementation("example:lib:1") }\n'
        self.write(path, original)
        key = self.key()
        patched = original.replace('android {\n', 'android {\n    packaging {\n        jniLibs.useLegacyPackaging = true\n    }\n', 1)
        self.write(path, patched)
        self.assertEqual(key, self.key())
        self.write(path, patched.replace('example:lib:1', 'example:lib:2'))
        self.assertNotEqual(key, self.key())
        self.write(path, patched.replace('useLegacyPackaging = true', 'useLegacyPackaging = false'))
        self.assertNotEqual(key, self.key())
        self.write(path, patched.replace('jniLibs.useLegacyPackaging = true', 'jniLibs.useLegacyPackaging = true\n        resources.excludes.add("example")'))
        self.assertNotEqual(key, self.key())

    def test_platform_separation(self):
        key = self.key()
        self.assertNotEqual(key, self.key("Windows"))
        self.assertNotEqual(key, self.key(arch="ARM64"))

    def test_missing_generated_project_fails(self):
        (self.root / self.inputs[0]).unlink()
        with self.assertRaises(subprocess.CalledProcessError):
            self.key()


if __name__ == "__main__":
    unittest.main()
