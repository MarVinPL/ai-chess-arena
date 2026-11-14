import sqlite3
import unittest

from model_admission import apply_decision, evaluate_candidates, list_decisions


def fixtures():
    revision = 'a' * 40
    entry = {
        'model_id': 1, 'repo_id': 'org/model', 'revision': revision,
        'status': 'eligible', 'admission_scope': ['PURE_AI'],
        'license_requirements': {
            'manifest_license': 'apache-2.0',
            'manifest_audit_status': 'verified_permissive',
            'license_verified_required': True, 'obligations': [],
        },
        'technical_readiness': 'ready', 'technical_blockers': [],
        'block_reason': None,
    }
    allowlist = {'models': [entry]}
    manifest = {'models': [{
        'model_id': 1, 'repo_id': 'org/model', 'revision': revision,
        'filename': 'model.gguf', 'sha256': 'b' * 64, 'size_bytes': 1024,
        'license_review_status': 'verified_permissive',
    }]}
    return allowlist, manifest


class ModelAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE model_admission_decisions(
                model_id INTEGER PRIMARY KEY,status TEXT NOT NULL,actor TEXT NOT NULL,
                decided_at TEXT NOT NULL,reason TEXT NOT NULL,repo_id TEXT NOT NULL,
                revision TEXT NOT NULL,snapshot_json TEXT NOT NULL);
            CREATE TABLE model_admission_history(
                id INTEGER PRIMARY KEY,model_id INTEGER NOT NULL,event TEXT NOT NULL,
                actor TEXT NOT NULL,occurred_at TEXT NOT NULL,reason TEXT NOT NULL,
                repo_id TEXT NOT NULL,revision TEXT NOT NULL,snapshot_json TEXT NOT NULL);
        ''')
        self.allowlist, self.manifest = fixtures()

    def tearDown(self):
        self.db.close()

    def test_positive_candidate_requires_all_independent_gates(self):
        result = evaluate_candidates(allowlist=self.allowlist, manifest=self.manifest)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]['eligible'])
        self.assertEqual(result[0]['blockers'], [])

    def test_review_required_or_blocked_model_cannot_be_admitted(self):
        self.allowlist['models'][0]['status'] = 'review_required'
        self.allowlist['models'][0]['admission_scope'] = []
        self.allowlist['models'][0]['technical_readiness'] = 'not_evaluated'
        self.allowlist['models'][0]['block_reason'] = 'review pending'
        with self.assertRaisesRegex(ValueError, 'Nie można dopuścić'):
            apply_decision(self.db, model_id=1, action='admit', confirm=True,
                           allowlist=self.allowlist, manifest=self.manifest)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM model_admission_history').fetchone()[0], 0)

    def test_unknown_model_and_missing_confirmation_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'jawne potwierdzenie'):
            apply_decision(self.db, model_id=1, action='admit', confirm=False,
                           allowlist=self.allowlist, manifest=self.manifest)
        with self.assertRaisesRegex(ValueError, 'nie znajduje się'):
            apply_decision(self.db, model_id=2, action='admit', confirm=True,
                           allowlist=self.allowlist, manifest=self.manifest)

    def test_admit_records_actor_snapshot_and_history(self):
        result = apply_decision(self.db, model_id=1, action='admit', confirm=True,
                                allowlist=self.allowlist, manifest=self.manifest)
        self.assertEqual(result['status'], 'admitted')
        self.assertEqual(result['actor'], 'arena')
        rows = list_decisions(self.db, allowlist=self.allowlist, manifest=self.manifest)
        self.assertEqual(rows[0]['decision']['status'], 'admitted')
        self.assertEqual(rows[0]['decision']['actor'], 'arena')
        self.assertTrue(rows[0]['can_revoke'])
        self.assertEqual(rows[0]['history'][0]['event'], 'admitted')

    def test_revoke_requires_reason_and_preserves_audit_history(self):
        apply_decision(self.db, model_id=1, action='admit', confirm=True,
                       allowlist=self.allowlist, manifest=self.manifest)
        with self.assertRaisesRegex(ValueError, 'uzasadnienie'):
            apply_decision(self.db, model_id=1, action='revoke', confirm=True, reason='  ',
                           allowlist=self.allowlist, manifest=self.manifest)
        result = apply_decision(self.db, model_id=1, action='revoke', confirm=True, reason='Zmiana audytu',
                                allowlist=self.allowlist, manifest=self.manifest)
        rows = list_decisions(self.db, allowlist=self.allowlist, manifest=self.manifest)
        self.assertEqual(result['status'], 'revoked')
        self.assertEqual([event['event'] for event in rows[0]['history']], ['admitted', 'revoked'])
        self.assertEqual(rows[0]['decision']['reason'], 'Zmiana audytu')
        self.assertFalse(rows[0]['can_revoke'])

    def test_previous_revision_decision_does_not_admit_new_revision(self):
        apply_decision(self.db, model_id=1, action='admit', confirm=True,
                       allowlist=self.allowlist, manifest=self.manifest)
        self.allowlist['models'][0]['revision'] = 'c' * 40
        self.manifest['models'][0]['revision'] = 'c' * 40
        rows = list_decisions(self.db, allowlist=self.allowlist, manifest=self.manifest)
        self.assertEqual(rows[0]['decision']['status'], 'stale')
        self.assertTrue(rows[0]['can_admit'])
        self.assertFalse(rows[0]['can_revoke'])
        with self.assertRaisesRegex(ValueError, 'dokładnej rewizji'):
            apply_decision(self.db, model_id=1, action='revoke', confirm=True, reason='wycofanie',
                           allowlist=self.allowlist, manifest=self.manifest)
        updated = apply_decision(self.db, model_id=1, action='admit', confirm=True,
                                 allowlist=self.allowlist, manifest=self.manifest)
        self.assertEqual(updated['revision'], 'c' * 40)

    def test_manifest_license_or_revision_mismatch_blocks_admission(self):
        self.manifest['models'][0]['revision'] = 'c' * 40
        result = evaluate_candidates(allowlist=self.allowlist, manifest=self.manifest)
        self.assertFalse(result[0]['eligible'])
        self.assertTrue(any('rewizja' in item.lower() for item in result[0]['blockers']))


if __name__ == '__main__':
    unittest.main()
