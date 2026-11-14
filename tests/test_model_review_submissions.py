import sqlite3
import unittest

import model_review_submissions as reviews


class ModelReviewSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.allowed = {1, 2, 3, 4, 5, 7, 8}

    def tearDown(self):
        self.db.close()

    def test_submission_is_a_proposal_and_does_not_admit_model(self):
        result = reviews.submit(self.db, {'model_id': 1, 'license_review': 'pending', 'block_reason': 'Oczekuje na niezależny przegląd.'}, allowed_model_ids=self.allowed)
        self.assertEqual(result['status'], 'pending_review')
        self.assertIn('Nie zmieniono allowlisty', result['note'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM model_review_submissions').fetchone()[0], 1)
        tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn('model_admission_decisions', tables)

    def test_other_allowlisted_models_are_supported_but_excluded_ids_are_rejected(self):
        result = reviews.submit(self.db, {'model_id': 8, 'block_reason': 'Oczekuje na niezależny przegląd.'}, allowed_model_ids=self.allowed)
        self.assertEqual(result['model_id'], 8)
        with self.assertRaisesRegex(ValueError, 'wykluczone modele'):
            reviews.submit(self.db, {'model_id': 9}, allowed_model_ids=self.allowed)
        with self.assertRaisesRegex(ValueError, 'wykluczone modele'):
            reviews.submit(self.db, {'model_id': 6}, allowed_model_ids=self.allowed)

    def test_ready_requires_technical_evidence(self):
        with self.assertRaisesRegex(ValueError, 'dowodów testu'):
            reviews.submit(self.db, {'model_id': 2, 'technical_readiness': 'ready', 'block_reason': 'Oczekuje na niezależny przegląd.'}, allowed_model_ids=self.allowed)
        with self.assertRaisesRegex(ValueError, 'dokładnej rewizji'):
            reviews.submit(self.db, {'model_id': 2, 'technical_readiness': 'ready', 'technical_evidence': 'test OK', 'block_reason': 'Oczekuje na niezależny przegląd.'}, allowed_model_ids=self.allowed)

    def test_reviewed_license_requires_identifier(self):
        with self.assertRaisesRegex(ValueError, 'identyfikator licencji'):
            reviews.submit(self.db, {'model_id': 3, 'license_review': 'verified_permissive', 'block_reason': 'Oczekuje na niezależny przegląd.'}, allowed_model_ids=self.allowed)

    def test_positive_status_requires_scope(self):
        with self.assertRaisesRegex(ValueError, 'wymaga zakresu'):
            reviews.submit(self.db, {'model_id': 1, 'proposed_status': 'eligible'}, allowed_model_ids=self.allowed)

    def test_restricted_status_requires_restrictions_and_obligations(self):
        payload = {'model_id': 7, 'proposed_status': 'restricted', 'scope_formats': ['PURE_AI'], 'revision_confirmed': True, 'license_review': 'verified_restricted', 'license_id': 'CC-BY-NC-4.0', 'license_notes': 'Pinned LICENSE checked.'}
        with self.assertRaisesRegex(ValueError, 'ograniczeń i obowiązków'):
            reviews.submit(self.db, payload, allowed_model_ids=self.allowed)


    def test_eligible_requires_permissive_audit_and_revision_confirmation(self):
        payload = {'model_id': 1, 'proposed_status': 'eligible', 'scope_formats': ['PURE_AI'],
                   'license_review': 'verified_permissive', 'license_id': 'Apache-2.0', 'license_notes': 'Pinned LICENSE checked.',
                   'block_reason': '', 'revision_confirmed': False}
        with self.assertRaisesRegex(ValueError, 'potwierdzenia dokładnej rewizji'):
            reviews.submit(self.db, payload, allowed_model_ids=self.allowed)
        payload['revision_confirmed'] = True
        self.assertEqual(reviews.submit(self.db, payload, allowed_model_ids=self.allowed)['status'], 'pending_review')

    def test_restricted_requires_restricted_audit(self):
        payload = {'model_id': 7, 'proposed_status': 'restricted', 'scope_formats': ['PURE_AI'],
                   'restrictions': 'Wyłącznie użytek niekomercyjny.', 'obligations': 'Atrybucja.',
                   'license_review': 'verified_restricted', 'license_id': 'CC-BY-NC-4.0', 'license_notes': 'Pinned LICENSE checked.',
                   'revision_confirmed': True}
        self.assertEqual(reviews.submit(self.db, payload, allowed_model_ids=self.allowed)['status'], 'pending_review')

    def test_accept_moves_only_submission_to_next_review_stage_and_audits(self):
        created = reviews.submit(self.db, {'model_id': 1, 'block_reason': 'Wymaga testu runtime.'}, allowed_model_ids=self.allowed)
        result = reviews.apply_action(self.db, submission_id=created['submission_id'], action='accept')
        self.assertEqual(result['status'], 'accepted_into_pr')
        self.assertEqual(self.db.execute('SELECT status FROM model_review_submissions').fetchone()[0], 'accepted_into_pr')
        event = self.db.execute('SELECT event,previous_status,new_status FROM model_review_events').fetchone()
        self.assertEqual(event, ('accepted_into_pr', 'pending_review', 'accepted_into_pr'))
        tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn('model_admission_decisions', tables)

    def test_queue_returns_proposal_and_audit_history(self):
        created = reviews.submit(self.db, {'model_id': 1, 'block_reason': 'Wymaga testu runtime.'}, allowed_model_ids=self.allowed)
        reviews.apply_action(self.db, submission_id=created['submission_id'], action='accept')
        rows = reviews.list_submissions(self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], created['submission_id'])
        self.assertEqual(rows[0]['proposal']['block_reason'], 'Wymaga testu runtime.')
        self.assertEqual(rows[0]['history'][0]['event'], 'accepted_into_pr')

    def test_rejection_requires_reason_and_is_audited(self):
        created = reviews.submit(self.db, {'model_id': 1, 'block_reason': 'Wymaga testu runtime.'}, allowed_model_ids=self.allowed)
        with self.assertRaisesRegex(ValueError, 'wymaga uzasadnienia'):
            reviews.apply_action(self.db, submission_id=created['submission_id'], action='reject', reason='')
        result = reviews.apply_action(self.db, submission_id=created['submission_id'], action='reject', reason='Brak dowodów.')
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(self.db.execute('SELECT reason FROM model_review_events').fetchone()[0], 'Brak dowodów.')

    def test_revision_revalidates_payload_and_returns_to_pending(self):
        created = reviews.submit(self.db, {'model_id': 1, 'block_reason': 'Wymaga testu runtime.'}, allowed_model_ids=self.allowed)
        reviews.apply_action(self.db, submission_id=created['submission_id'], action='accept')
        revised = {'model_id': 1, 'block_reason': 'Nadal wymaga niezależnego testu.', 'technical_readiness': 'not_evaluated'}
        result = reviews.apply_action(self.db, submission_id=created['submission_id'], action='revise', payload=revised, allowed_model_ids=self.allowed)
        self.assertEqual(result['status'], 'pending_review')
        stored = self.db.execute('SELECT payload_json FROM model_review_submissions').fetchone()[0]
        self.assertIn('Nadal wymaga niezależnego testu.', stored)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM model_review_events').fetchone()[0], 2)

    def test_blocked_status_requires_reason(self):
        with self.assertRaisesRegex(ValueError, 'wymaga uzasadnienia'):
            reviews.submit(self.db, {'model_id': 2, 'proposed_status': 'blocked'}, allowed_model_ids=self.allowed)


if __name__ == '__main__':
    unittest.main()
