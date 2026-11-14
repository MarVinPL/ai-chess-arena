import json
import unittest
from pathlib import Path


class Issue132SmolLM17BCandidateTests(unittest.TestCase):
    def test_candidate_is_pinned_and_review_only(self):
        path = Path(__file__).resolve().parents[1] / "manifests" / "huggingface-candidates.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        matches = [item for item in payload["models"]
                   if item.get("repo_id") == "QuantFactory/SmolLM2-1.7B-Instruct-GGUF"]
        self.assertEqual(len(matches), 1)
        item = matches[0]
        artifact = item["selected_artifact"]
        self.assertEqual(item["revision"], "6702e34d1eebb7d21caaa1658c98e9d481e69732")
        self.assertEqual(artifact["revision"], "d6802b513a12fa4da4e5ab8f3d8c2c65dba1091d")
        self.assertEqual(artifact["size_bytes"], 1055609536)
        self.assertEqual(artifact["sha256"], "bc8986d129483f44768b5fcc2bd2f148d6af000282cdb9ec7a92122226fa7921")
        self.assertEqual(item["license_review_status"], "review_required")
        self.assertFalse(item["license_verified"])
        self.assertEqual(item["technical_readiness"], "not_evaluated")
        self.assertEqual(item["admission_status"], "not_admitted")
        self.assertIsNone(item["arena_model_id"])
        self.assertEqual(item["admission_scope"], [])
        self.assertFalse(item["inference_run"])
