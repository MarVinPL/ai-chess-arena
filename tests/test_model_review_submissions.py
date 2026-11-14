import sqlite3
import unittest

import model_review_submissions as reviews


class ModelReviewSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row

    def tearDown(self):
        self.db.close()

    def test_submission_is_a_proposal_and_does_not_admit_model(self):
        result = reviews.submit(self.db, {'model_id': 1, 'license_review': 'pending'})
        self.assertEqual(result['status'], 'pending_review')
        self.assertIn('Nie zmieniono allowlisty', result['note'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM model_review_submissions').fetchone()[0], 1)
        tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn('model_admission_decisions', tables)

    def test_only_models_one_to_three_are_accepted(self):
        with self.assertRaisesRegex(ValueError, 'wyłącznie modele 1–3'):
            reviews.submit(self.db, {'model_id': 4})

    def test_ready_requires_technical_evidence(self):
        with self.assertRaisesRegex(ValueError, 'dowodów testu'):
            reviews.submit(self.db, {'model_id': 2, 'technical_readiness': 'ready'})

    def test_reviewed_license_requires_identifier(self):
        with self.assertRaisesRegex(ValueError, 'identyfikator licencji'):
            reviews.submit(self.db, {'model_id': 3, 'license_review': 'verified_permissive'})


if __name__ == '__main__':
    unittest.main()
