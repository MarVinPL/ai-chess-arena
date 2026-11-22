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

    def test_candidate_proposal_enters_shared_review_table_without_arena_id(self):
        created = reviews.save(self.db, self.payload, catalog=[ITEM])
        rows = reviews.list_proposals(self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['catalog_id'], ITEM['catalog_id'])
        self.assertEqual(rows[0]['revision'], REV)
        self.assertEqual(rows[0]['status'], 'pending_review')
        self.assertNotIn('model_id', rows[0])
        self.assertIsNone(self.db.execute('SELECT model_id FROM model_review_submissions WHERE id=?', (rows[0]['id'],)).fetchone()[0])
        tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn('hf_candidate_review_proposals', tables)
        self.assertNotIn('hf_candidate_review_proposal_events', tables)
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

    def test_arena_action_endpoint_cannot_bypass_hf_catalog_revalidation(self):
        import model_review_submissions
        reviews.save(self.db, self.payload, catalog=[ITEM])
        candidate = reviews.list_proposals(self.db)[0]
        self.assertEqual(model_review_submissions.list_submissions(self.db), [])
        with self.assertRaisesRegex(ValueError, 'wyłącznie akcja z ponowną walidacją katalogu'):
            model_review_submissions.apply_action(self.db, submission_id=candidate['id'], action='accept')

    def test_decision_rejects_candidate_if_repository_identity_changed(self):
        reviews.save(self.db, self.payload, catalog=[ITEM])
        proposal = reviews.list_proposals(self.db)[0]
        changed = {**ITEM, 'repo_id': 'other/model'}
        with self.assertRaisesRegex(ValueError, 'Repozytorium kandydata zmieniło się'):
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


    def test_migration_preserves_arena_submission_id_and_migrates_hf_history(self):
        self.db.execute("CREATE TABLE model_review_submissions (id INTEGER PRIMARY KEY AUTOINCREMENT, model_id INTEGER NOT NULL CHECK(model_id>0), submitted_at TEXT NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending_review')")
        self.db.execute("INSERT INTO model_review_submissions(id,model_id,submitted_at,payload_json,status) VALUES(41,2,'2026-01-01T00:00:00Z','{\"legacy\":true}','pending_review')")
        self.db.execute("CREATE TABLE model_review_events (id INTEGER PRIMARY KEY AUTOINCREMENT,submission_id INTEGER NOT NULL,event TEXT NOT NULL,actor TEXT NOT NULL,occurred_at TEXT NOT NULL,reason TEXT NOT NULL DEFAULT '',previous_status TEXT,new_status TEXT,previous_payload_json TEXT,new_payload_json TEXT)")
        self.db.execute("CREATE TABLE hf_candidate_reviews (catalog_id TEXT,revision TEXT,reviewed_at TEXT,reviewer TEXT,review_json TEXT,PRIMARY KEY(catalog_id,revision))")
        self.db.execute("INSERT INTO hf_candidate_reviews VALUES(?,?,?,?,?)", (ITEM['catalog_id'],REV,'2026-01-02T00:00:00Z','reviewer','{}'))
        self.db.execute("CREATE TABLE hf_candidate_review_proposals (id INTEGER PRIMARY KEY,catalog_id TEXT,revision TEXT,repo_id TEXT,submitted_at TEXT,status TEXT)")
        self.db.execute("INSERT INTO hf_candidate_review_proposals VALUES(7,?,?,?,?,?)", (ITEM['catalog_id'],REV,ITEM['repo_id'],'2026-01-02T00:00:00Z','accepted_into_pr'))
        self.db.execute("CREATE TABLE hf_candidate_review_proposal_events (id INTEGER PRIMARY KEY,proposal_id INTEGER,event TEXT,actor TEXT,occurred_at TEXT,reason TEXT,previous_status TEXT,new_status TEXT)")
        self.db.execute("INSERT INTO hf_candidate_review_proposal_events VALUES(9,7,'accepted_into_pr','reviewer','2026-01-02T00:01:00Z','ok','pending_review','accepted_into_pr')")
        import model_review_submissions
        model_review_submissions.ensure_schema(self.db)
        arena = self.db.execute('SELECT id,model_id,payload_json FROM model_review_submissions WHERE model_id=2').fetchone()
        self.assertEqual(arena, (41,2,'{\"legacy\":true}'))
        hf = self.db.execute('SELECT id,model_id,catalog_id,revision,repo_id,status FROM model_review_submissions WHERE catalog_id=?',(ITEM['catalog_id'],)).fetchone()
        self.assertEqual(hf[0], 7)
        self.assertIsNone(hf[1]); self.assertEqual(hf[2:], (ITEM['catalog_id'],REV,ITEM['repo_id'],'accepted_into_pr'))
        self.assertEqual(self.db.execute('SELECT id,event,actor FROM model_review_events WHERE submission_id=?',(hf[0],)).fetchone(), (9,'accepted_into_pr','reviewer'))
        self.assertFalse(self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='hf_candidate_review_proposals'").fetchone())

    def test_shared_queue_rejects_mixed_or_invalid_identity(self):
        reviews.save(self.db, self.payload, catalog=[ITEM])
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO model_review_submissions(model_id,catalog_id,revision,repo_id,submitted_at,payload_json) VALUES(1,?,?,?,?,?)", (ITEM['catalog_id'],REV,ITEM['repo_id'],'now','{}'))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO model_review_submissions(model_id,catalog_id,revision,repo_id,submitted_at,payload_json) VALUES(NULL,?,?,?,?,?)", ('bad','short','repo','now','{}'))


if __name__ == '__main__':
    unittest.main()
