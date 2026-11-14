"""Regression tests for the GitHub Actions catalog-sync entrypoint."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CatalogSyncEntrypointTests(unittest.TestCase):
    def test_entrypoint_imports_project_module_from_unrelated_working_directory(self):
        repository_root = Path(__file__).resolve().parents[1]
        entrypoint = repository_root / "scripts" / "sync_huggingface_catalog.py"
        code = """
import importlib
import importlib.util
import sys

entrypoint_path = sys.argv[1]
spec = importlib.util.spec_from_file_location("catalog_sync_entrypoint", entrypoint_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
catalog = importlib.import_module("huggingface_catalog_sync")
assert hasattr(catalog, "run_sync")
print("entrypoint import OK")
"""
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        with tempfile.TemporaryDirectory() as unrelated_cwd:
            result = subprocess.run(
                [sys.executable, "-c", code, str(entrypoint)],
                cwd=unrelated_cwd,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("entrypoint import OK", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
