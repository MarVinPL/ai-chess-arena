import json
import sqlite3
import unittest

import hf_candidate_audit as audit


REV = 'a' * 40


class CandidateAuditTests(unittest.TestCase):
    def setUp(self):
        self.item = {
            'catalog_id': 'hf:org/model', 'arena_model_id': None,
            'repo_id': 'org/model', 'display_name': 'model',
            'revision': REV, 'source_status': 'active',
            'declared_license_tag': 'apache-2.0', 'gated': False,
        }
        self.info = {
            'sha': REV, 'pipeline_tag': 'text-generation',
            'tags': ['gguf', 'license:apache-2.0'],
            'cardData': {'license': 'apache-2.0'},
            'siblings': [
                {'rfilename': 'README.md'}, {'rfilename': 'LICENSE'},
                {'rfilename': 'config.json'}, {'rfilename': 'model.gguf'},
                {'rfilename': 'model-00001-of-00002.safetensors'},
            ],
        }

    def fetcher(self, url, *, max_bytes):
        self.assertLessEqual(max_bytes, audit.MAX_METADATA_BYTES if '/api/models/' in url else audit.MAX_TEXT_BYTES)
        if '/api/models/' in url:
            return json.dumps(self.info).encode()
        if url.endswith('/README.md'):
            return b'---\\nlicense: apache-2.0\\n---\\nModel card'
        if url.endswith('/LICENSE'):
            return b'Apache License Version 2.0'
        if url.endswith('/config.json'):
            return b'{"model_type":"llama"}'
        self.fail('Audyt próbował pobrać nieoczekiwany plik: ' + url)

    def test_audit_uses_pinned_revision_and_only_small_text_files(self):
        report = audit.audit_candidate('hf:org/model', catalog=[self.item], fetcher=self.fetcher)
        self.assertEqual(report['pinned_revision'], REV)
        self.assertTrue(report['revision_matches'])
        self.assertEqual(report['evidence_level'], 'public_metadata_only')
        self.assertEqual(report['license_files'], ['LICENSE'])
        self.assertEqual(report['suggested_form']['license_review'], 'pending')
        self.assertEqual(report['suggested_form']['technical_readiness'], 'not_evaluated')
        self.assertEqual(report['suggested_form']['proposed_status'], 'review_required')
        self.assertEqual(report['suggested_form']['scope_formats'], [])
        self.assertFalse(report['suggested_form']['revision_confirmed'])
        self.assertNotIn('verified_permissive', json.dumps(report))
        self.assertIn('Nie pobrano wag', ' '.join(report['limitations']))

    def test_conflicting_license_declarations_are_flagged(self):
        self.info['cardData']['license'] = 'mit'
        report = audit.audit_candidate('hf:org/model', catalog=[self.item], fetcher=self.fetcher)
        self.assertTrue(report['license_declarations']['conflict'])
        self.assertIn('różni się', report['suggested_form']['technical_blockers'])
        self.assertEqual(report['suggested_form']['license_review'], 'pending')

    def test_revision_mismatch_fails_closed(self):
        self.info['sha'] = 'b' * 40
        with self.assertRaisesRegex(audit.AuditError, 'nie zgadza'):
            audit.audit_candidate('hf:org/model', catalog=[self.item], fetcher=self.fetcher)

    def test_invalid_repo_and_revision_are_rejected(self):
        item = dict(self.item, repo_id='https://evil.example/org/model')
        with self.assertRaisesRegex(audit.AuditError, 'identyfikator repozytorium'):
            audit.audit_candidate('hf:org/model', catalog=[item], fetcher=self.fetcher)
        item = dict(self.item, revision='main')
        with self.assertRaisesRegex(audit.AuditError, 'pełnej rewizji'):
            audit.audit_candidate('hf:org/model', catalog=[item], fetcher=self.fetcher)

    def test_unsafe_remote_file_paths_are_never_requested(self):
        self.info['siblings'] = [{'rfilename': '../LICENSE'}, {'rfilename': 'README.md'}]
        report = audit.audit_candidate('hf:org/model', catalog=[self.item], fetcher=self.fetcher)
        self.assertEqual(report['license_files'], [])
        self.assertEqual(report['suggested_form']['license_review'], 'pending')
        self.assertIn('Brak jawnego pliku licencji', report['suggested_form']['technical_blockers'])

    def test_inactive_candidate_is_rejected(self):
        item = dict(self.item, source_status='safety_filtered')
        with self.assertRaisesRegex(audit.AuditError, 'aktywnego źródła'):
            audit.audit_candidate('hf:org/model', catalog=[item], fetcher=self.fetcher)

    def test_missing_license_file_remains_pending(self):
        self.info['siblings'] = [{'rfilename': 'README.md'}, {'rfilename': 'model.gguf'}]
        report = audit.audit_candidate('hf:org/model', catalog=[self.item], fetcher=self.fetcher)
        self.assertEqual(report['suggested_form']['license_review'], 'pending')
        self.assertIn('Brak jawnego pliku licencji', report['suggested_form']['technical_blockers'])

    def test_arena_candidate_can_be_prefilled_without_auto_approval(self):
        item = dict(self.item, catalog_id='arena:1', arena_model_id=1,
                    repo_id='org/model', revision=None, audited_revision=REV,
                    source_status='not_checked')
        report = audit.audit_candidate('arena:1', catalog=[item], fetcher=self.fetcher)
        self.assertEqual(report['arena_model_id'], 1)
        self.assertEqual(report['suggested_form']['proposed_status'], 'review_required')

    def test_report_is_saved_separately_and_idempotently(self):
        report = audit.audit_candidate('hf:org/model', catalog=[self.item], fetcher=self.fetcher)
        db = sqlite3.connect(':memory:')
        audit.save_report(db, report)
        audit.save_report(db, report)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM hf_candidate_audits').fetchone()[0], 1)
        self.assertEqual(len(audit.list_reports(db)), 1)
        self.assertEqual(audit.get_report(db, report['catalog_id'], report['pinned_revision'])['repo_id'], 'org/model')
        db.close()


if __name__ == '__main__':
    unittest.main()
