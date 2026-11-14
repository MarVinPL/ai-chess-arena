import json
import tempfile
import unittest
from pathlib import Path

from scripts.sync_hf_catalog import CatalogSyncError, build_manifest, fetch_catalog, normalize_candidate, write_json_atomic


NOW = "2026-11-14T13:00:00Z"


def model(repo="example/chess-gguf", revision="a" * 40, **overrides):
    record = {
        "id": repo, "sha": revision, "createdAt": "2026-01-01T00:00:00Z",
        "lastModified": "2026-11-14T12:00:00Z", "tags": ["gguf", "text-generation", "license:apache-2.0"],
        "pipeline_tag": "text-generation", "library_name": "gguf", "private": False,
        "disabled": False, "gated": False,
        "siblings": [{"rfilename": "model-q4_k_m.gguf"}, {"rfilename": "README.md"}],
    }
    record.update(overrides)
    return record


class HfCatalogSyncTests(unittest.TestCase):
    def test_normalizes_public_text_generation_gguf_metadata_only(self):
        item = normalize_candidate(model(), NOW)
        self.assertEqual(item["repo_id"], "example/chess-gguf")
        self.assertEqual(item["model_name"], "chess-gguf")
        self.assertEqual(item["author"], "example")
        self.assertEqual(item["revision"], "a" * 40)
        self.assertEqual(item["license_tag"], "apache-2.0")
        self.assertEqual(item["status"], "unverified")
        self.assertTrue(item["review_required"])
        self.assertFalse("download_url" in item)

    def test_gated_auto_status_is_preserved_without_accessing_the_repository(self):
        item = normalize_candidate(model(gated="auto"), NOW)
        self.assertEqual(item["gated"], "auto")
        self.assertEqual(item["status"], "unverified")

    def test_rejects_private_disabled_wrong_task_and_non_gguf_repositories(self):
        cases = [
            model(private=True), model(disabled=True),
            model(pipeline_tag="image-classification", tags=["gguf"]),
            model(siblings=[{"rfilename": "README.md"}]),
        ]
        for record in cases:
            with self.subTest(record=record):
                self.assertIsNone(normalize_candidate(record, NOW))

    def test_filters_sensitive_or_uncensored_catalog_tags(self):
        for tag in ("nsfw", "adult", "uncensored", "porn", "malware", "hacking", "weapons", "gambling", "drugs", "self-harm"):
            with self.subTest(tag=tag):
                self.assertIsNone(normalize_candidate(model(tags=["gguf", "text-generation", tag]), NOW))
        self.assertIsNone(normalize_candidate(model(repo="example/DeepSeek-UNCENSORED"), NOW))
        self.assertIsNone(normalize_candidate(model(tags=["gguf", "text-generation", "base_model:publisher/uncensored-heretic-model"]), NOW))
        self.assertIsNone(normalize_candidate(model(siblings=[{"rfilename": "weapon-guide.gguf"}]), NOW))
        self.assertIsNotNone(normalize_candidate(model(repo="example/TagoreGPT"), NOW))

    def test_auxiliary_only_gguf_files_are_not_candidates(self):
        record = model(siblings=[{"rfilename": "mmproj-model.gguf"}, {"rfilename": "imatrix.gguf"}, {"rfilename": "draft.gguf"}])
        self.assertIsNone(normalize_candidate(record, NOW))

    def test_new_candidates_are_unverified_and_have_discovery_history(self):
        payload = build_manifest(None, [model()], NOW, False)
        item = payload["models"][0]
        self.assertEqual(item["status"], "unverified")
        self.assertEqual(item["detected_at"], NOW)
        self.assertEqual(item["history"][0]["event"], "discovered")
        self.assertFalse(payload["policy"]["license_approved_automatically"])

    def test_unchanged_candidate_keeps_detection_and_verification_timestamps(self):
        old = build_manifest(None, [model()], NOW, False)
        later = "2026-11-21T13:00:00Z"
        refreshed = build_manifest(old, [model()], later, False)
        item = refreshed["models"][0]
        self.assertEqual(item["detected_at"], NOW)
        self.assertEqual(item["last_verified_at"], NOW)
        self.assertEqual(refreshed["updated_at"], old["updated_at"])

    def test_revision_change_is_recorded_and_stays_unverified(self):
        old = build_manifest(None, [model()], NOW, False)
        refreshed = build_manifest(old, [model(revision="b" * 40)], "2026-11-21T13:00:00Z", False)
        item = refreshed["models"][0]
        self.assertEqual(item["status"], "unverified")
        self.assertEqual(item["last_verified_at"], "2026-11-21T13:00:00Z")
        self.assertEqual(item["history"][-1]["event"], "revision_changed")
        self.assertEqual(item["history"][-1]["from_revision"], "a" * 40)

    def test_partial_scan_verifies_absent_repo_directly_instead_of_marking_missing(self):
        old = build_manifest(None, [model()], NOW, False)
        refreshed = build_manifest(old, [], "2026-11-21T13:00:00Z", False, lambda _repo: model())
        self.assertEqual(refreshed["models"][0]["status"], "unverified")
        self.assertEqual(refreshed["models"][0]["revision"], "a" * 40)

    def test_partial_scan_can_record_a_confirmed_404(self):
        old = build_manifest(None, [model()], NOW, False)
        refreshed = build_manifest(old, [], "2026-11-21T13:00:00Z", False, lambda _repo: None)
        self.assertEqual(refreshed["models"][0]["status"], "unavailable")

    def test_complete_scan_marks_404_source_unavailable_and_keeps_history(self):
        old = build_manifest(None, [model()], NOW, False)
        refreshed = build_manifest(old, [], "2026-11-21T13:00:00Z", True, lambda _repo: None)
        item = refreshed["models"][0]
        self.assertEqual(item["status"], "unavailable")
        self.assertEqual(item["history"][-1]["event"], "repository_unavailable")

    def test_complete_scan_tracks_renamed_repository(self):
        old = build_manifest(None, [model()], NOW, False)
        refreshed = build_manifest(old, [], "2026-11-21T13:00:00Z", True,
                                   lambda _repo: model(repo="example/renamed", revision="c" * 40))
        by_repo = {item["repo_id"]: item for item in refreshed["models"]}
        self.assertEqual(by_repo["example/chess-gguf"]["status"], "renamed")
        self.assertEqual(by_repo["example/chess-gguf"]["renamed_to"], "example/renamed")
        self.assertEqual(by_repo["example/renamed"]["renamed_from"], "example/chess-gguf")

    def test_sensitive_existing_candidate_is_redacted_without_locator_data(self):
        old = build_manifest(None, [model()], NOW, False)
        unsafe = model(repo="example/Cyber-Qwen", tags=["gguf", "text-generation", "cybersecurity"])
        refreshed = build_manifest(old, [], "2026-11-21T13:00:00Z", False, lambda _repo: unsafe)
        item = refreshed["models"][0]
        self.assertEqual(item["status"], "filtered_by_discovery_policy")
        self.assertNotIn("repo_id", item)
        self.assertNotIn("model_url", item)
        self.assertNotIn("tags", item)
        self.assertTrue(item["repository_fingerprint"])
        self.assertEqual(item["history"][-1]["event"], "filtered_by_discovery_policy")

    def test_redacted_policy_tombstones_persist_across_future_syncs(self):
        old = build_manifest(None, [model()], NOW, False)
        excluded = build_manifest(old, [], "2026-11-21T13:00:00Z", False,
                                  lambda _repo: model(repo="example/Cyber-Qwen", tags=["gguf", "text-generation", "cybersecurity"]))
        later = build_manifest(excluded, [], "2026-11-28T13:00:00Z", False, lambda _repo: None)
        self.assertEqual(len(later["models"]), 1)
        self.assertEqual(later["models"][0]["status"], "filtered_by_discovery_policy")
        self.assertNotIn("repo_id", later["models"][0])

    def test_repeated_404_does_not_create_weekly_manifest_churn(self):
        old = build_manifest(None, [model()], NOW, False)
        unavailable = build_manifest(old, [], "2026-11-21T13:00:00Z", False, lambda _repo: None)
        repeated = build_manifest(unavailable, [], "2026-11-28T13:00:00Z", False, lambda _repo: None)
        self.assertEqual(repeated["updated_at"], unavailable["updated_at"])
        self.assertEqual(repeated["models"][0]["history"], unavailable["models"][0]["history"])
        self.assertEqual(repeated["models"][0]["status_checked_at"], unavailable["models"][0]["status_checked_at"])

    def test_repeated_rename_does_not_create_weekly_manifest_churn(self):
        old = build_manifest(None, [model()], NOW, False)
        renamed_record = model(repo="example/renamed", revision="c" * 40)
        renamed = build_manifest(old, [], "2026-11-21T13:00:00Z", False, lambda _repo: renamed_record)
        repeated = build_manifest(renamed, [], "2026-11-28T13:00:00Z", False, lambda _repo: renamed_record)
        self.assertEqual(repeated["updated_at"], renamed["updated_at"])
        old_entry = next(item for item in repeated["models"] if item.get("status") == "renamed")
        self.assertEqual(len([event for event in old_entry["history"] if event["event"] == "repository_renamed"]), 1)

    def test_complete_scan_requires_detail_verification_for_absent_entries(self):
        old = build_manifest(None, [model()], NOW, False)
        with self.assertRaises(CatalogSyncError):
            build_manifest(old, [], "2026-11-21T13:00:00Z", True)

    def test_pagination_failure_does_not_return_partial_as_complete(self):
        calls = []
        def fake(url):
            calls.append(url)
            return ([model()], "https://huggingface.co/api/models?cursor=next")
        records, complete, pages = fetch_catalog(max_pages=1, request_json=fake)
        self.assertEqual(len(records), 1)
        self.assertFalse(complete)
        self.assertEqual(pages, 1)

    def test_missing_visibility_metadata_fails_closed(self):
        for field in ("private", "disabled"):
            record = model()
            record.pop(field)
            with self.subTest(field=field), self.assertRaises(CatalogSyncError):
                normalize_candidate(record, NOW)

    def test_invalid_timestamp_metadata_fails_closed(self):
        with self.assertRaises(CatalogSyncError):
            normalize_candidate(model(lastModified="not-a-date"), NOW)

    def test_incomplete_commit_metadata_fails_closed(self):
        with self.assertRaises(CatalogSyncError):
            normalize_candidate(model(sha="main"), NOW)

    def test_duplicate_repo_with_conflicting_revisions_fails_closed(self):
        with self.assertRaises(CatalogSyncError):
            build_manifest(None, [model(), model(revision="b" * 40)], NOW, False)

    def test_existing_repo_that_stops_matching_is_retained_as_history(self):
        old = build_manifest(None, [model()], NOW, False)
        no_longer_candidate = model(pipeline_tag="image-classification", tags=["gguf"])
        updated = build_manifest(old, [], "2026-11-21T13:00:00Z", False,
                                 lambda _repo: no_longer_candidate)
        self.assertEqual(updated["models"][0]["status"], "not_matching")
        self.assertEqual(updated["models"][0]["history"][-1]["event"], "no_longer_matches")

    def test_workflow_is_scheduled_manual_and_pr_only(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/huggingface-catalog-sync.yml").read_text()
        self.assertIn("schedule:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("peter-evans/create-pull-request@v7", workflow)
        self.assertIn("pull-requests: write", workflow)
        self.assertNotIn("auto-merge", workflow)

    def test_empty_first_page_fails_closed(self):
        with self.assertRaises(CatalogSyncError):
            fetch_catalog(max_pages=1, request_json=lambda _url: ([], None))

    def test_api_error_fails_closed(self):
        def fail(_url):
            raise CatalogSyncError("rate limited")
        with self.assertRaisesRegex(CatalogSyncError, "rate limited"):
            fetch_catalog(max_pages=1, request_json=fail)

    def test_manifest_write_is_valid_json(self):
        payload = build_manifest(None, [model()], NOW, False)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidates.json"
            write_json_atomic(path, payload)
            self.assertEqual(json.loads(path.read_text())["models"][0]["repo_id"], "example/chess-gguf")


if __name__ == "__main__":
    unittest.main()
