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


if __name__ == '__main__':
    unittest.main()
