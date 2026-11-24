"""DB-only allowlist: fail closed on a missing/revoked/stale decision."""
import json
import sqlite3
import unittest

from model_allowlist import load_allowlist, tournament_admission_blockers, is_runtime_ready


class DatabaseAllowlistTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.executescript('''
            CREATE TABLE league_models(id INTEGER PRIMARY KEY,name TEXT,active INTEGER,format TEXT);
            CREATE TABLE model_versions(id INTEGER PRIMARY KEY,model_id INTEGER,license TEXT,
                license_verified INTEGER,filename TEXT,sha256 TEXT,size_bytes INTEGER,
                source TEXT,metadata_json TEXT);
            CREATE TABLE model_admission_decisions(model_id INTEGER PRIMARY KEY,status TEXT,
                repo_id TEXT,revision TEXT,snapshot_json TEXT);
        ''')
        self.revision = 'a' * 40
        self.repo = 'org/testmodel'
        self.src = 'https://huggingface.co/' + self.repo
        self.metadata = {'repo_id': self.repo, 'revision': self.revision,
                         'download_url': self.src + '/resolve/' + self.revision + '/test.gguf'}
        self.snapshot = {'model_id': 1, 'repo_id': self.repo, 'revision': self.revision,
                         'status': 'eligible', 'admission_scope': ['PURE_AI'],
                         'license': 'apache-2.0', 'technical_readiness': 'not_evaluated',
                         'technical_blockers': ['history: not evaluated']}
        self.db.execute("INSERT INTO league_models VALUES(1,'Example model',1,'PURE_AI')")
        self.db.execute("INSERT INTO model_versions VALUES(1,1,'apache-2.0',1,'test.gguf',?,1024,?,?)",
                        ('b'*64,self.src,json.dumps(self.metadata)))
        self.db.execute("INSERT INTO model_admission_decisions VALUES(1,'admitted',?,?,?)",
                        (self.repo,self.revision,json.dumps(self.snapshot)))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def entries(self):
        return load_allowlist(self.db)['models']

    def test_only_sqlite_is_read_even_without_git_checkout_or_json(self):
        entry = self.entries()[0]
        self.assertEqual(entry['status'], 'eligible')
        self.assertEqual(entry['decision_status'], 'admitted')
        self.assertEqual(entry['admission_scope'], ['PURE_AI'])
        self.assertEqual(load_allowlist(self.db)['source'], 'sqlite')
        self.assertIsNone(entry['block_reason'])

    def test_missing_decision_cannot_appear_in_allowlist(self):
        self.db.execute("DELETE FROM model_admission_decisions")
        self.assertEqual(self.entries(), [])
        self.assertTrue(tournament_admission_blockers(
            None, model_id=1, repo_id=self.repo, revision=self.revision,
            license_name='apache-2.0',license_verified=True,tournament_format='PURE_AI'))

    def test_revoke_blocks_without_deleting_audit(self):
        self.db.execute("UPDATE model_admission_decisions SET status='revoked'")
        entry=self.entries()[0]
        self.assertEqual(entry['status'], 'blocked')
        self.assertTrue(entry['block_reason'])
        self.assertTrue(entry['can_restore'])

    def test_version_pin_change_blocks_even_if_db_decision_still_admitted(self):
        self.metadata['revision'] = 'c' * 40
        self.db.execute("UPDATE model_versions SET metadata_json=?", (json.dumps(self.metadata),))
        entry = self.entries()[0]
        self.assertEqual(entry['status'], 'blocked')
        self.assertIn('rewizja', entry['block_reason'])

    def test_unverified_license_blocks_without_changing_admission_decision(self):
        self.db.execute("UPDATE model_versions SET license_verified=0")
        self.assertEqual(self.entries()[0]['status'], 'blocked')

    def test_mismatched_snapshot_identity_blocks(self):
        self.snapshot['revision'] = 'c' * 40
        self.db.execute("UPDATE model_admission_decisions SET snapshot_json=?", (json.dumps(self.snapshot),))
        self.assertEqual(self.entries()[0]['status'], 'blocked')

    def test_corrupt_json_fails_closed(self):
        self.db.execute("UPDATE model_admission_decisions SET snapshot_json='broken'")
        from model_allowlist import AllowlistError
        with self.assertRaises(AllowlistError):
            self.entries()

    def test_tournament_accepts_database_admission_without_runtime_pass(self):
        entry=self.entries()[0]
        kwargs={'model_id':1,'repo_id':self.repo,'revision':self.revision,
                'license_name':'apache-2.0','license_verified':True,'tournament_format':'PURE_AI'}
        self.assertEqual(tournament_admission_blockers(entry, **kwargs), [])
        self.assertTrue(tournament_admission_blockers(entry, **{**kwargs, 'revision': 'f'*40}))
        self.assertTrue(tournament_admission_blockers(entry, **{**kwargs, 'license_verified':False}))
        self.assertFalse(is_runtime_ready(entry,
                                           local_file_verified=True,runtime_healthy=True))

    def test_runtime_readiness_does_not_override_database_admission(self):
        self.snapshot['technical_readiness'] = 'ready'
        self.snapshot['technical_blockers'] = []
        self.db.execute("UPDATE model_admission_decisions SET snapshot_json=?,status='revoked'",
                        (json.dumps(self.snapshot),))
        entry = self.entries()[0]
        self.assertTrue(is_runtime_ready(entry, local_file_verified=True, runtime_healthy=True))
        self.assertFalse(is_runtime_ready(entry, local_file_verified=False, runtime_healthy=True))
        self.assertFalse(is_runtime_ready(entry, local_file_verified=True, runtime_healthy=False))
        self.assertTrue(tournament_admission_blockers(
            entry, model_id=1, repo_id=self.repo, revision=self.revision,
            license_name='apache-2.0', license_verified=True, tournament_format='PURE_AI'))

    def test_restricted_terms_stay_in_snapshot(self):
        self.snapshot['status']='restricted'
        self.snapshot['restrictions']=['Tylko użycie zgodne z warunkami licencji']
        self.db.execute("UPDATE model_admission_decisions SET snapshot_json=?", (json.dumps(self.snapshot),))
        entry=self.entries()[0]
        self.assertEqual(entry['status'],'restricted')
        self.assertEqual(entry['restrictions'],self.snapshot['restrictions'])


if __name__ == '__main__':
    unittest.main()
