import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import hf_candidate_downloads as downloads

REV='a'*40
SHA=hashlib.sha256(b'gguf').hexdigest()

class DummyThread:
    def __init__(self,*args,**kwargs): self.args=args; self.kwargs=kwargs
    def start(self): pass

class CandidateDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tempdir=tempfile.TemporaryDirectory()
        downloads.init_db(Path(self.tempdir.name)/'arena.sqlite3')
        with downloads._lock:
            downloads._jobs.clear(); downloads._cancel_events.clear(); downloads._active=False
        self.inventory={'catalog_id':'hf:org/model','repo_id':'org/model','revision':REV,
                        'free_bytes':30*1024**3,'files':[{'name':'model.Q4_K_M.gguf','size_bytes':4,'sha256':SHA,'revision':REV}]}

    def tearDown(self):
        self.tempdir.cleanup()

    def group(self,catalog='hf:org/model'):
        return {'catalog_id':catalog,'files':[{'name':'model.Q4_K_M.gguf','sha256':SHA,'revision':REV}]}

    def allow_test_files(self, *catalogs):
        return patch.object(downloads, 'APPROVED_ARTIFACTS',
            {(catalog, REV, name) for catalog, name in catalogs})

    def test_start_requires_exact_revision_and_sha_from_inventory(self):
        with patch.object(downloads,'list_files',return_value=self.inventory):
            with self.assertRaisesRegex(downloads.DownloadError,'manifestem'):
                downloads.start([{'catalog_id':'hf:org/model','files':[{'name':'model.Q4_K_M.gguf','sha256':'0'*64,'revision':REV}]}])

    def test_start_rejects_too_many_candidates(self):
        with self.assertRaisesRegex(downloads.DownloadError,'od 1 do 3'):
            downloads.start([{}, {}, {}, {}])

    def test_start_rejects_insufficient_disk_space(self):
        inventory=dict(self.inventory,free_bytes=100)
        with self.allow_test_files(('hf:org/model','model.Q4_K_M.gguf')), patch.object(downloads,'list_files',return_value=inventory):
            with self.assertRaisesRegex(downloads.DownloadError,'Za mało wolnego miejsca'):
                downloads.start([self.group()])

    def test_start_accepts_multiple_candidates_in_one_batch(self):
        second=dict(self.inventory,catalog_id='hf:org/other',repo_id='org/other')
        with self.allow_test_files(('hf:org/model','model.Q4_K_M.gguf'),('hf:org/other','model.Q4_K_M.gguf')), patch.object(downloads,'list_files',side_effect=[self.inventory,second]), patch.object(downloads.threading,'Thread',DummyThread):
            job=downloads.start([self.group(),self.group('hf:org/other')])
        self.assertEqual(job['total_files'],2)
        self.assertEqual(len(job['catalogs']),2)
        self.assertEqual(job['total_bytes'],8)

    def test_start_rejects_more_than_three_files_across_candidates(self):
        inventory=dict(self.inventory,files=[
            {'name':f'model{i}.gguf','size_bytes':4,'sha256':SHA,'revision':REV} for i in range(4)])
        groups=[{'catalog_id':'hf:org/model','files':[{'name':f'model{i}.gguf','sha256':SHA,'revision':REV} for i in range(4)]}]
        with self.allow_test_files(*[('hf:org/model',f'model{i}.gguf') for i in range(4)]), patch.object(downloads,'list_files',return_value=inventory):
            with self.assertRaisesRegex(downloads.DownloadError,'maksymalnie 3'):
                downloads.start(groups)

    def test_list_files_uses_only_gguf_with_size_and_sha256_lfs(self):
        info={'sha':REV,'siblings':[
            {'rfilename':'model.gguf','size':4,'lfs':{'sha256':SHA}},
            {'rfilename':'README.md','size':20},
            {'rfilename':'../escape.gguf','size':4,'lfs':{'sha256':SHA}},
            {'rfilename':'unknown.gguf','size':4},
        ]}
        with patch.object(downloads.audit,'_candidate',return_value={'repo_id':'org/model','revision':REV}), \
             patch.object(downloads.audit,'_request',return_value=json.dumps(info).encode()), \
             patch.object(downloads,'_free_bytes',return_value=10*1024**3):
            result=downloads.list_files('hf:org/model')
        self.assertEqual([x['name'] for x in result['files']],['model.gguf'])
        self.assertEqual(result['files'][0]['sha256'],SHA)

    def test_current_reports_aggregate_progress_for_latest_job(self):
        with downloads._lock:
            downloads._jobs['old']={'id':'old','status':'completed','total_bytes':4,'results':[{'size_bytes':4}]}
            downloads._jobs['new']={'id':'new','status':'downloading','total_bytes':20,'current_file_bytes':5,
                                    'current_file_total_bytes':10,'results':[{'size_bytes':3}]}
        snapshot=downloads.current()
        self.assertEqual(snapshot['id'],'new')
        self.assertEqual(snapshot['downloaded_bytes'],8)
        self.assertEqual(snapshot['progress_percent'],40.0)
        self.assertTrue(snapshot['active'])
        self.assertEqual(downloads.get('new')['current_file_total_bytes'],10)

    def test_manual_start_rejects_artifacts_outside_explicit_allowlist(self):
        explicit={
            ('hf:abenzerps/Apodex-1.1-mini-GGUF','e44e0cbd3151d08a9f5a4cbf19cd44560685de04','Apodex-1.1-mini-IQ1_M.gguf'),
            ('hf:abenzerps/Spark-X2.5-4B-GGUF','5eb255d86a3d0a3db3c589d171f833ef4ff4b00a','Spark-X2.5-4B-Q4_K_M.gguf'),
        }
        with patch.object(downloads,'APPROVED_ARTIFACTS',explicit), patch.object(downloads,'list_files',return_value=self.inventory):
            with self.assertRaisesRegex(downloads.DownloadError,'jawnej allowliście'):
                downloads.start([self.group()])

    def test_job_history_survives_database_reinitialization(self):
        with self.allow_test_files(('hf:org/model','model.Q4_K_M.gguf')), patch.object(downloads,'list_files',return_value=self.inventory), patch.object(downloads.threading,'Thread',DummyThread):
            job=downloads.start([self.group()])
        downloads.init_db(Path(self.tempdir.name)/'arena.sqlite3')
        restored=downloads.current()
        self.assertEqual(restored['id'],job['id'])
        self.assertEqual(restored['status'],'interrupted')
        self.assertEqual(restored['tasks'][0]['file']['name'],'model.Q4_K_M.gguf')
        self.assertEqual(downloads.events(job['id'])[0]['event'],'interrupted')

    def test_interrupted_job_can_resume_with_same_id_and_allowlisted_revision(self):
        with self.allow_test_files(('hf:org/model','model.Q4_K_M.gguf')), patch.object(downloads,'list_files',return_value=self.inventory), patch.object(downloads.threading,'Thread',DummyThread):
            job=downloads.start([self.group()])
            downloads.init_db(Path(self.tempdir.name)/'arena.sqlite3')
            resumed=downloads.resume(job['id'])
        self.assertEqual(resumed['id'],job['id'])
        self.assertEqual(resumed['status'],'queued')
        self.assertEqual(downloads._jobs[job['id']]['tasks'],job['tasks'])

    def test_automatic_planner_only_includes_exact_preapproved_artifacts(self):
        allowed=sorted(downloads.APPROVED_ARTIFACTS)
        catalog=[{'catalog_id':x[0],'repo_id':x[0].split(':',1)[1],'revision':x[1],
                  'source_status':'active','license_verified':False} for x in allowed]
        inventories=[]
        for cid,rev,name in allowed:
            inventories.append({'catalog_id':cid,'repo_id':cid.split(':',1)[1],'revision':rev,'free_bytes':30*1024**3,
                'files':[{'name':name,'size_bytes':4,'sha256':SHA,'revision':rev}]})
        with patch.object(downloads.audit,'load_catalog',return_value=catalog), patch.object(downloads,'list_files',side_effect=inventories), patch.object(downloads,'_find_existing_verified',return_value=None):
            plan=downloads.refresh_plan()
        self.assertEqual(plan['summary']['approved_artifacts'],2)
        self.assertEqual(plan['summary']['planned'],2)
        self.assertFalse(plan['settings']['auto_download_enabled'])
        self.assertTrue(plan['settings']['manual_confirmation_required'])
        self.assertEqual(downloads._jobs, {})

    def test_redirect_rejects_non_huggingface_hosts(self):
        handler=downloads._Redirects()
        with self.assertRaisesRegex(downloads.DownloadError,'przekierowanie'):
            handler.redirect_request(None,None,302,'Found',{},'https://evil.example/file')

if __name__=='__main__': unittest.main()
