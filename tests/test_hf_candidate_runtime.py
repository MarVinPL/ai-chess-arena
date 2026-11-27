import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import hf_candidate_downloads as downloads
import hf_candidate_runtime as runtime
import hf_candidate_reviews as reviews
import league


class RuntimePreflightTests(unittest.TestCase):

    def test_removed_light_r1_is_not_in_runtime_candidate_allowlist(self):
        self.assertFalse(any('Light-R1' in item[0] or 'Light-R1' in item[2] for item in runtime.RUNTIME_CANDIDATES))

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.db=self.root/'arena.sqlite3'
        self.quarantine=self.root/'quarantine'
        self.quarantine.mkdir()
        self.artifact=self.quarantine/'model.gguf.quarantine'
        self.artifact.write_bytes(b'fake-gguf-metadata-only-test')
        self.digest=hashlib.sha256(self.artifact.read_bytes()).hexdigest()
        self.stat=self.artifact.stat()
        self.catalog=[{'catalog_id':'hf:org/model','repo_id':'org/model','revision':'a'*40,'display_name':'Test Model'}]
        self.patches=[
            patch.object(league,'DB',self.db),
            patch.object(downloads,'DB_PATH',self.db),
            patch.object(downloads,'STAGING_DIR',self.quarantine),
            patch.object(downloads,'APPROVED_ARTIFACTS',{('hf:org/model','a'*40,'model.gguf')}),
            patch.object(downloads.audit,'load_catalog',return_value=self.catalog),
            patch.object(runtime,'_memory_available',return_value=32*1024**3),
            patch.object(runtime,'RUNTIME_CANDIDATES',set()),
        ]
        for item in self.patches:item.start()
        downloads.init_db(self.db)
        with downloads._db() as c:
            c.execute('INSERT INTO hf_download_verified_artifacts VALUES(?,?,?,?,?,?)',(
                'hf:org/model|'+'a'*40+'|model.gguf',str(self.artifact),self.stat.st_size,self.digest,'2026-11-16T00:00:00Z',self.stat.st_mtime_ns))

    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        self.tmp.cleanup()

    def test_sha256_is_rechecked_against_exact_quarantine_file(self):
        artifact = {'path': str(self.artifact), 'size_bytes': self.artifact.stat().st_size, 'sha256': self.digest}
        result = runtime.verify_artifact_sha256_for_runtime(artifact)
        self.assertTrue(result['ok'])
        self.assertEqual(result['sha256'], self.digest)
        self.artifact.write_bytes(b'changed')
        result = runtime.verify_artifact_sha256_for_runtime(artifact)
        self.assertFalse(result['ok'])
        self.assertIn('nie powiodła', result['reason'])

    def test_sha256_runtime_gate_rejects_path_outside_quarantine(self):
        outside = self.root / 'outside.gguf'
        outside.write_bytes(b'fake')
        result = runtime.verify_artifact_sha256_for_runtime({
            'path': str(outside), 'size_bytes': outside.stat().st_size,
            'sha256': hashlib.sha256(outside.read_bytes()).hexdigest()})
        self.assertFalse(result['ok'])
        self.assertIn('poza kwarantanną', result['reason'])

    def test_runtime_handoff_gate_requires_explicit_approval(self):
        key='hf:org/model|'+'a'*40+'|model.gguf'
        result=runtime.authorize_runtime_handoff(key)
        self.assertFalse(result['ok'])
        self.assertIn('jawnego zatwierdzenia',result['reason'])

    def test_runtime_handoff_gate_rehashes_registry_artifact_and_does_not_start_runtime(self):
        key='hf:org/model|'+'a'*40+'|model.gguf'
        result=runtime.authorize_runtime_handoff(key,explicit_approval=True)
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['sha256'],self.digest)
        self.assertEqual(result['path'],str(self.artifact.resolve()))
        self.assertFalse(result['runtime_started'])
        self.assertTrue(result['requires_separate_runtime_executor'])
        original_stat=self.artifact.stat()
        self.artifact.write_bytes(b'X'*original_stat.st_size)
        import os
        os.utime(self.artifact,ns=(original_stat.st_atime_ns,original_stat.st_mtime_ns))
        rejected=runtime.authorize_runtime_handoff(key,explicit_approval=True)
        self.assertFalse(rejected['ok'])
        self.assertIn('nie powiodła',rejected['reason'])

    def test_runtime_handoff_gate_rejects_unregistered_artifact(self):
        key='hf:org/model|'+'a'*40+'|model.gguf'
        with downloads._db() as c:
            c.execute('DELETE FROM hf_download_verified_artifacts WHERE artifact_key=?',(key,))
        result=runtime.authorize_runtime_handoff(key,explicit_approval=True)
        self.assertFalse(result['ok'])
        self.assertIn('nie figuruje już w rejestrze',result['reason'])

    def test_runtime_handoff_gate_rejects_unapproved_artifact_key(self):
        result=runtime.authorize_runtime_handoff('hf:org/other|'+'b'*40+'|other.gguf',explicit_approval=True)
        self.assertFalse(result['ok'])
        self.assertIn('jawnego uprawnienia',result['reason'])

    def test_verified_allowlisted_artifact_is_ready_but_never_started(self):
        result=runtime.refresh_preflight()
        item=next(x for x in result['items'] if x['catalog_id']=='hf:org/model')
        self.assertEqual(item['status'],'ready_pending_approval')
        self.assertFalse(result['summary']['runtime_enabled'])
        self.assertEqual(result['summary']['runtime_active'],0)
        self.assertFalse(result['policy']['auto_start'])

    def test_model_that_does_not_fit_is_skipped_without_runtime(self):
        with patch.object(runtime,'_memory_available',return_value=1*1024**3):
            result=runtime.refresh_preflight()
        item=next(x for x in result['items'] if x['catalog_id']=='hf:org/model')
        self.assertEqual(item['status'],'skipped_blocked')
        self.assertIn('Za mało RAM',item['reason'])
        self.assertEqual(result['summary']['runtime_active'],0)

    def test_non_allowlisted_quarantine_artifact_is_blocked(self):
        with patch.object(downloads,'APPROVED_ARTIFACTS',set()):
            result=runtime.refresh_preflight()
        item=next(x for x in result['items'] if x['catalog_id']=='hf:org/model')
        self.assertEqual(item['status'],'skipped_blocked')
        self.assertIn('allowliście',item['reason'])

    def test_artifact_outside_quarantine_is_blocked(self):
        outside=self.root/'outside.gguf'
        outside.write_bytes(b'fake')
        with downloads._db() as c:
            c.execute('UPDATE hf_download_verified_artifacts SET path=?',(str(outside),))
        result=runtime.refresh_preflight()
        item=next(x for x in result['items'] if x['catalog_id']=='hf:org/model')
        self.assertEqual(item['status'],'skipped_blocked')
        self.assertIn('poza katalogiem kwarantanny',item['reason'])


    def test_runtime_candidate_requires_manual_review(self):
        candidate=('hf:org/model','a'*40,'model.gguf')
        with patch.object(runtime,'RUNTIME_CANDIDATES',{candidate}):
            result=runtime.refresh_preflight()
        item=next(x for x in result['items'] if x['artifact_key']=='|'.join(candidate))
        self.assertEqual(item['status'],'skipped_blocked')
        self.assertIn('Brak ręcznego przeglądu',item['reason'])

    def test_runtime_candidate_requires_all_three_review_gates(self):
        candidate=('hf:org/model','a'*40,'model.gguf')
        with league.connect() as c:
            reviews.save(c,{'catalog_id':candidate[0],'revision':candidate[1],
                'license_id':'apache-2.0','license_status':'verified_permissive',
                'license_evidence':'Ręcznie zweryfikowane źródło licencji i zakres praw.',
                'revision_confirmed':True,'revision_notes':'Porównano pełny SHA rewizji z API.',
                'technical_readiness':'not_evaluated'})
        with patch.object(runtime,'RUNTIME_CANDIDATES',{candidate}):
            result=runtime.refresh_preflight()
        item=next(x for x in result['items'] if x['artifact_key']=='|'.join(candidate))
        self.assertEqual(item['status'],'skipped_blocked')
        self.assertIn('Gotowość techniczna',item['reason'])

    def test_declared_apache_license_is_accepted_without_claiming_independent_verification(self):
        candidate=('hf:org/model','a'*40,'model.gguf')
        with league.connect() as c:
            reviews.save(c,{'catalog_id':candidate[0],'revision':candidate[1],
                'license_id':'apache-2.0','license_status':'declared_permissive',
                'license_evidence':'Repozytorium i README deklarują Apache-2.0 dla przypiętej rewizji.',
                'revision_confirmed':True,'revision_notes':'SHA rewizji porównany z API.',
                'technical_readiness':'ready','technical_evidence':'Test techniczny i kontrola artefaktu zakończone.',
                'technical_blockers':''})
        with patch.object(runtime,'RUNTIME_CANDIDATES',{candidate}):
            result=runtime.refresh_preflight()
        item=next(x for x in result['items'] if x['artifact_key']=='|'.join(candidate))
        self.assertEqual(item['status'],'ready_pending_approval')
        self.assertFalse(result['policy']['auto_start'])
        self.assertFalse(result['policy']['runtime_enabled'])

    def test_declared_permissive_requires_explicit_apache_evidence(self):
        candidate=('hf:org/model','a'*40,'model.gguf')
        with league.connect() as c:
            reviews.save(c,{'catalog_id':candidate[0],'revision':candidate[1],
                'license_id':'apache-2.0','license_status':'declared_permissive',
                'license_evidence':'Deklaracja repozytorium bez identyfikacji Apache.',
                'revision_confirmed':True,'revision_notes':'SHA rewizji porównany z API.',
                'technical_readiness':'ready','technical_evidence':'Test techniczny zakończony.',
                'technical_blockers':''})
        with patch.object(runtime,'RUNTIME_CANDIDATES',{candidate}):
            result=runtime.refresh_preflight()
        item=next(x for x in result['items'] if x['artifact_key']=='|'.join(candidate))
        self.assertEqual(item['status'],'skipped_blocked')
        self.assertIn('jawnego wskazania deklaracji Apache-2.0',item['reason'])

    def test_runtime_candidate_can_only_pass_after_manual_review_and_verified_artifact(self):
        candidate=('hf:org/model','a'*40,'model.gguf')
        with league.connect() as c:
            reviews.save(c,{'catalog_id':candidate[0],'revision':candidate[1],
                'license_id':'apache-2.0','license_status':'verified_permissive',
                'license_evidence':'Ręcznie zweryfikowane źródło licencji i zakres praw.',
                'revision_confirmed':True,'revision_notes':'Porównano pełny SHA rewizji z API.',
                'technical_readiness':'ready','technical_evidence':'Zapisano dowody kontroli technicznej i zgodności pliku.',
                'technical_blockers':''})
        with patch.object(runtime,'RUNTIME_CANDIDATES',{candidate}):
            result=runtime.refresh_preflight()
        item=next(x for x in result['items'] if x['artifact_key']=='|'.join(candidate))
        self.assertEqual(item['status'],'ready_pending_approval')
        self.assertFalse(result['policy']['auto_start'])
        self.assertFalse(result['policy']['runtime_enabled'])

    def test_runtime_candidate_without_verified_quarantine_record_is_visible_and_blocked(self):
        candidate=('hf:org/model','a'*40,'missing.gguf')
        with patch.object(runtime,'RUNTIME_CANDIDATES',{candidate}):
            result=runtime.refresh_preflight()
        item=next(x for x in result['items'] if x['artifact_key']=='|'.join(candidate))
        self.assertEqual(item['status'],'skipped_blocked')
        self.assertIn('Brak rekordu artefaktu',item['reason'])
        self.assertEqual(item['path'],'')


if __name__=='__main__':unittest.main()
