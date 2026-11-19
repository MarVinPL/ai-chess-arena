import sqlite3
import unittest

import hf_candidate_reviews as reviews

REV = 'a' * 40
ITEM = {'catalog_id': 'hf:org/model', 'repo_id': 'org/model', 'display_name': 'model',
        'revision': REV, 'source_status': 'active'}


class CandidateReviewTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.payload = {'catalog_id': ITEM['catalog_id'], 'revision': REV,
                        'license_status': 'pending', 'technical_readiness': 'not_evaluated'}

    def tearDown(self):
        self.db.close()

    def test_saves_candidate_review_without_arena_model_id_or_admission(self):
        result = reviews.save(self.db, self.payload, catalog=[ITEM])
        self.assertEqual(result['workflow_status'], 'review_in_progress')
        self.assertEqual(reviews.get(self.db, ITEM['catalog_id'], REV)['review']['technical_readiness'], 'not_evaluated')
        tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn('hf_candidate_reviews', tables)
        self.assertNotIn('model_admission_decisions', tables)

    def test_verified_license_requires_source_evidence(self):
        self.payload.update(license_status='verified_permissive', license_id='Apache-2.0')
        with self.assertRaisesRegex(ValueError, 'źródeł i uzasadnienia'):
            reviews.save(self.db, self.payload, catalog=[ITEM])

    def test_ready_requires_revision_and_technical_evidence(self):
        self.payload.update(technical_readiness='ready', technical_evidence='Runtime test passed')
        with self.assertRaisesRegex(ValueError, 'potwierdzenia przypiętej rewizji'):
            reviews.save(self.db, self.payload, catalog=[ITEM])
        self.payload.update(revision_confirmed=True, revision_notes='SHA compared')
        reviews.save(self.db, self.payload, catalog=[ITEM])

    def test_revision_must_match_current_catalog(self):
        with self.assertRaisesRegex(ValueError, 'aktualną, przypiętą rewizją'):
            reviews.save(self.db, {**self.payload, 'revision': 'b' * 40}, catalog=[ITEM])

    def test_updates_append_review_history(self):
        reviews.save(self.db, self.payload, catalog=[ITEM])
        reviews.save(self.db, {**self.payload, 'reviewer_notes': 'Second review pass'}, catalog=[ITEM])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM hf_candidate_review_events').fetchone()[0], 2)
        self.assertEqual(reviews.list_reviews(self.db)[0]['revision'], REV)

    def test_blocked_technical_review_requires_blocker(self):
        self.payload.update(technical_readiness='blocked')
        with self.assertRaisesRegex(ValueError, 'opisu blokad'):
            reviews.save(self.db, self.payload, catalog=[ITEM])

    def test_empty_review_queue_is_readable_on_legacy_database(self):
        self.db.execute('CREATE TABLE hf_candidate_reviews (catalog_id TEXT, revision TEXT, reviewed_at TEXT, reviewer TEXT, review_json TEXT, PRIMARY KEY(catalog_id,revision))')
        self.assertEqual(reviews.list_proposals(self.db), [])

    def test_candidate_proposal_enters_separate_review_queue_without_arena_id(self):
        created = reviews.save(self.db, self.payload, catalog=[ITEM])
        rows = reviews.list_proposals(self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['catalog_id'], ITEM['catalog_id'])
        self.assertEqual(rows[0]['revision'], REV)
        self.assertEqual(rows[0]['status'], 'pending_review')
        self.assertNotIn('model_id', rows[0])
        self.assertEqual(reviews.get(self.db, ITEM['catalog_id'], REV)['proposal_status'], 'pending_review')

    def test_acceptance_only_moves_proposal_to_pr_stage_and_is_audited(self):
        reviews.save(self.db, self.payload, catalog=[ITEM])
        proposal = reviews.list_proposals(self.db)[0]
        result = reviews.apply_proposal_action(self.db, proposal_id=proposal['id'],
            action='accept', catalog=[ITEM], actor='reviewer')
        self.assertEqual(result['status'], 'accepted_into_pr')
        self.assertIn('bez admission', result['note'])
        history = reviews.list_proposals(self.db)[0]['history']
        self.assertEqual(history[-1]['event'], 'accepted_into_pr')
        tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn('model_admission_decisions', tables)

    def test_decision_rejects_candidate_if_pinned_revision_changed(self):
        reviews.save(self.db, self.payload, catalog=[ITEM])
        proposal = reviews.list_proposals(self.db)[0]
        changed = {**ITEM, 'revision': 'b' * 40}
        with self.assertRaisesRegex(ValueError, 'aktualną, przypiętą rewizją'):
            reviews.apply_proposal_action(self.db, proposal_id=proposal['id'],
                action='accept', catalog=[changed])

    def test_rejection_requires_reason_and_is_audited(self):
        reviews.save(self.db, self.payload, catalog=[ITEM])
        proposal = reviews.list_proposals(self.db)[0]
        with self.assertRaisesRegex(ValueError, 'uzasadnienia'):
            reviews.apply_proposal_action(self.db, proposal_id=proposal['id'],
                action='reject', catalog=[ITEM], reason='')
        result = reviews.apply_proposal_action(self.db, proposal_id=proposal['id'],
            action='reject', catalog=[ITEM], reason='Brak dowodów.')
        self.assertEqual(result['status'], 'rejected')


if __name__ == '__main__':
    unittest.main()
