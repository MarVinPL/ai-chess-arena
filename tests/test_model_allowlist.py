import copy
import unittest

from model_allowlist import AllowlistError, is_eligible, validate_allowlist

MAIN_SHA = "9d0be54d044f35c97cc37d1b7989bdb21a2a4b49"
REVISION = "a" * 40


def base_payload():
    return {
        "schema_version": 1,
        "source_main_sha": MAIN_SHA,
        "policy": {
            "default_decision": "blocked",
            "empty_list_means_no_models_eligible": True,
        },
        "models": [],
    }


def eligible_entry():
    return {
        "model_id": "engine/example",
        "repo_id": "owner/model",
        "artifact_revision": REVISION,
        "state": "eligible",
        "scope": "tournament",
        "license_audit_status": "verified_permissive",
        "technical_status": "ready",
        "audit_evidence_ref": "docs/model-audit/evidence.md#example",
        "reviewed_at": "2026-11-14",
        "restrictions": [],
        "blockers": [],
    }


class ModelAllowlistTests(unittest.TestCase):
    def test_empty_allowlist_is_valid_and_admits_nobody(self):
        payload = base_payload()
        validate_allowlist(payload, expected_main_sha=MAIN_SHA)
        self.assertFalse(is_eligible(payload, "engine/example", expected_main_sha=MAIN_SHA))

    def test_explicit_fully_verified_entry_is_eligible(self):
        payload = base_payload()
        payload["models"].append(eligible_entry())
        self.assertTrue(is_eligible(payload, "engine/example", expected_main_sha=MAIN_SHA))

    def test_main_drift_is_detected(self):
        with self.assertRaises(AllowlistError):
            validate_allowlist(base_payload(), expected_main_sha="b" * 40)

    def test_missing_evidence_or_revision_is_rejected(self):
        for field in ("audit_evidence_ref", "artifact_revision", "reviewed_at"):
            payload = base_payload()
            entry = eligible_entry()
            entry.pop(field)
            payload["models"].append(entry)
            with self.subTest(field=field), self.assertRaises(AllowlistError):
                validate_allowlist(payload, expected_main_sha=MAIN_SHA)

    def test_unverified_license_cannot_be_eligible(self):
        payload = base_payload()
        entry = eligible_entry()
        entry["license_audit_status"] = "review_required"
        payload["models"].append(entry)
        with self.assertRaises(AllowlistError):
            validate_allowlist(payload, expected_main_sha=MAIN_SHA)

    def test_not_ready_or_blocked_model_cannot_be_eligible(self):
        for field, value in (("technical_status", "pending"), ("blockers", ["runtime check"])):
            payload = base_payload()
            entry = eligible_entry()
            entry[field] = value
            payload["models"].append(entry)
            with self.subTest(field=field), self.assertRaises(AllowlistError):
                validate_allowlist(payload, expected_main_sha=MAIN_SHA)

    def test_duplicate_model_ids_are_rejected(self):
        payload = base_payload()
        payload["models"] = [eligible_entry(), copy.deepcopy(eligible_entry())]
        with self.assertRaises(AllowlistError):
            validate_allowlist(payload, expected_main_sha=MAIN_SHA)

    def test_unknown_model_and_restricted_entry_are_not_eligible(self):
        payload = base_payload()
        entry = eligible_entry()
        entry["state"] = "restricted"
        entry["restrictions"] = ["non-commercial"]
        payload["models"].append(entry)
        self.assertFalse(is_eligible(payload, "engine/example", expected_main_sha=MAIN_SHA))
        self.assertFalse(is_eligible(payload, "engine/unknown", expected_main_sha=MAIN_SHA))


if __name__ == "__main__":
    unittest.main(verbosity=2)
