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


    def test_candidate_verification_error_reports_http_status(self):
        import json

        from huggingface_catalog_sync import CatalogSyncError, HTTPStatusError, sync_catalog

        manifest = {
            "models": [
                {
                    "model_id": model_id,
                    "repo_id": f"test-org/model-{model_id}",
                    "revision": f"{model_id:040x}",
                    "license_review_status": "review_required",
                }
                for model_id in range(1, 9)
            ]
        }

        def failing_fetcher(url):
            raise HTTPStatusError(429, url)

        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "models.json"
            catalog_path = Path(temp_dir) / "catalog.json"
            source_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(CatalogSyncError, r"HTTP 429"):
                sync_catalog(
                    source_path=source_path,
                    catalog_path=catalog_path,
                    fetcher=failing_fetcher,
                )

            self.assertFalse(catalog_path.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
