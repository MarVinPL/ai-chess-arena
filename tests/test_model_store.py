import hashlib, tempfile, unittest, io
from pathlib import Path
from unittest.mock import patch
import league, model_store

class ModelStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.old_db=league.DB; self.old_dir=model_store.MANAGED_DIR
        league.DB=Path(self.tmp.name)/'db.sqlite3'; league.init()
        model_store.MANAGED_DIR=Path(self.tmp.name)/'managed'
        with league.connect() as c:
            c.execute("INSERT INTO league_models(name,format,created_at) VALUES('Test','PURE_AI','now')")
            c.execute("INSERT INTO model_versions(model_id,version_key,source,license,license_verified,created_at) VALUES(1,'v1','https://example.test/model.gguf','MIT',1,'now')")
    def tearDown(self):
        league.DB=self.old_db; model_store.MANAGED_DIR=self.old_dir; self.tmp.cleanup()
    def test_requires_explicit_license(self):
        with self.assertRaises(ValueError): model_store.download_version(1,'https://example.test/a.gguf','a.gguf','0'*64,10,False)
    def test_rejects_http_and_bad_filename(self):
        with self.assertRaises(ValueError): model_store.download_version(1,'http://example.test/a.gguf','a.gguf','0'*64,10,True)
        with self.assertRaises(ValueError): model_store.download_version(1,'https://example.test/a.gguf','../a.gguf','0'*64,10,True)
    def test_rejects_unpinned_hash(self):
        with self.assertRaises(ValueError): model_store.download_version(1,'https://example.test/a.gguf','a.gguf','bad',10,True)
    def test_excluded_model_cannot_be_downloaded_even_if_license_flag_is_set(self):
        repo='JonathanColetti/Qwen3.8-27B-Uncensored-GGUF'
        with league.connect() as c:
            c.execute("INSERT INTO league_models(name,format,created_at) VALUES(?,?,?)", (repo,'PURE_AI','now'))
            c.execute("INSERT INTO model_versions(model_id,version_key,source,license,license_verified,metadata_json,created_at) VALUES(?,?,?,?,?,?,?)",
                      (2,'excluded','https://huggingface.co/'+repo,'Apache-2.0',1,'{}','now'))
        with self.assertRaisesRegex(ValueError,'Issue #71'):
            model_store.download_version(2,'https://huggingface.co/'+repo+'/resolve/'+'a'*40+'/model.gguf',
                                         'model.gguf','a'*64,100,True)

    def test_download_success_verifies_hash_and_marks_managed(self):
        payload=b'GGUF-mock-payload'; digest=hashlib.sha256(payload).hexdigest()
        class Response:
            def __init__(self): self.stream=io.BytesIO(payload)
            def __enter__(self): return self
            def __exit__(self,*args): self.stream.close()
            def geturl(self): return 'https://example.test/model.gguf'
            def read(self,n=-1): return self.stream.read(n)
        with patch.object(model_store.urllib.request,'urlopen',return_value=Response()):
            result=model_store.download_version(1,'https://example.test/model.gguf','test.gguf',digest,len(payload),True)
        self.assertEqual(result['sha256'],digest)
        self.assertTrue(result['managed_copy'])
        self.assertEqual((model_store.MANAGED_DIR/'test.gguf').read_bytes(),payload)
        with league.connect() as c:
            row=c.execute('SELECT filename,status,managed_copy,sha256 FROM model_versions WHERE id=1').fetchone()
        self.assertEqual((row['filename'],row['status'],row['managed_copy'],row['sha256']),('test.gguf','downloaded',1,digest))

    def test_download_bad_hash_removes_partial_file(self):
        payload=b'bad-hash-payload'
        class Response:
            def __init__(self): self.stream=io.BytesIO(payload)
            def __enter__(self): return self
            def __exit__(self,*args): self.stream.close()
            def geturl(self): return 'https://example.test/model.gguf'
            def read(self,n=-1): return self.stream.read(n)
        with patch.object(model_store.urllib.request,'urlopen',return_value=Response()):
            with self.assertRaises(ValueError): model_store.download_version(1,'https://example.test/model.gguf','bad.gguf','0'*64,len(payload),True)
        self.assertFalse((model_store.MANAGED_DIR/'bad.gguf.part').exists())
        self.assertFalse((model_store.MANAGED_DIR/'bad.gguf').exists())
    def test_model_lifecycle_lock_is_reentrant(self):
        with model_store.MODEL_LIFECYCLE_LOCK:
            self.assertTrue(model_store.MODEL_LIFECYCLE_LOCK.acquire(blocking=False))
            model_store.MODEL_LIFECYCLE_LOCK.release()

    def test_cleanup_lifecycle_lock_is_reentrant_for_tournament_worker(self):
        lock = model_store.MODEL_LIFECYCLE_LOCK
        with lock:
            self.assertTrue(lock.acquire(blocking=False))
            lock.release()

    def test_cleanup_refuses_unmanaged_version(self):
        with self.assertRaises(PermissionError): model_store.cleanup_version(1)
    def test_cleanup_preserves_pinned_manifest_for_next_game(self):
        model_store.MANAGED_DIR.mkdir()
        q=model_store.MANAGED_DIR/'repeat.gguf'; q.write_bytes(b'GGUF test')
        with league.connect() as c:
            c.execute("UPDATE model_versions SET filename='repeat.gguf',sha256=?,size_bytes=?,status='downloaded',managed_copy=1 WHERE id=1",
                      (hashlib.sha256(b'GGUF test').hexdigest(),len(b'GGUF test')))
        with patch.object(model_store, '_open_by_process', return_value=([], False)):
            result=model_store.cleanup_version(1)
        self.assertTrue(result['removed'])
        self.assertFalse(q.exists())
        with league.connect() as c:
            row=c.execute('SELECT filename,sha256,size_bytes,status,managed_copy,source,license_verified FROM model_versions WHERE id=1').fetchone()
        self.assertEqual(row['filename'],'repeat.gguf')
        self.assertEqual(row['status'],'catalogued')
        self.assertEqual(row['managed_copy'],0)
        self.assertEqual(row['source'],'https://example.test/model.gguf')
        self.assertEqual(row['license_verified'],1)
    def test_open_fd_detection(self):
        p=Path(self.tmp.name)/'open.gguf'; p.write_bytes(b'x')
        with p.open('rb') as f:
            with patch.object(model_store, '_open_by_process', return_value=([123], False)):
                with self.assertRaises(RuntimeError):
                    # Mark an actual file under managed dir as arena-owned.
                    model_store.MANAGED_DIR.mkdir()
                    q=model_store.MANAGED_DIR/'open.gguf'; q.write_bytes(b'x')
                    with league.connect() as c: c.execute("UPDATE model_versions SET filename='open.gguf',managed_copy=1 WHERE id=1")
                    model_store.cleanup_version(1)
            self.assertTrue(q.exists())
    def test_cleanup_fails_closed_when_proc_scan_is_incomplete(self):
        model_store.MANAGED_DIR.mkdir()
        q=model_store.MANAGED_DIR/'unknown.gguf'; q.write_bytes(b'x')
        with league.connect() as c: c.execute("UPDATE model_versions SET filename='unknown.gguf',managed_copy=1 WHERE id=1")
        with patch.object(model_store, '_open_by_process', return_value=([], True)):
            with self.assertRaises(RuntimeError): model_store.cleanup_version(1)
        self.assertTrue(q.exists())

if __name__=='__main__': unittest.main(verbosity=2)
