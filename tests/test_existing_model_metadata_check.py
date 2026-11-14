import json, tempfile, unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit
from existing_model_metadata_check import MODEL_IDS, API_ROOT, CatalogSyncError, HTTPStatusError, run_sync, sync_catalog

def source_models():
    return [{'model_id':i,'repo_id':f'org/model-{i}','revision':f'{i:040x}','license_audit':{'status':'verified_permissive'}} for i in MODEL_IDS]

def api_model(revision, modified='2026-11-14T10:00:00Z', **extra):
    data={'sha':revision,'lastModified':modified,'private':False,'disabled':False,'gated':False}; data.update(extra); return data

class FakeFetcher:
    def __init__(self,responses=None): self.responses=responses or {}; self.calls=[]
    def __call__(self,url):
        self.calls.append(url); repo=unquote(urlsplit(url).path.removeprefix('/api/models/'))
        result=self.responses.get(repo,api_model(f'{int(repo.rsplit("-",1)[1]):040x}'))
        if isinstance(result,Exception): raise result
        if isinstance(result,tuple): return result
        return result,url

def setup_files(tmp):
    source=Path(tmp)/'source.json'; catalog=Path(tmp)/'catalog.json'
    source.write_text(json.dumps({'models':source_models()}),encoding='utf-8'); return source,catalog

class CatalogSyncTests(unittest.TestCase):
    def test_exactly_eight_existing_repositories_are_checked_no_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog=setup_files(tmp); fetcher=FakeFetcher()
            result,report=sync_catalog(source,catalog,fetcher,'2026-11-14T12:00:00Z')
            self.assertEqual(report['scope_model_ids'],list(range(1,9)))
            self.assertEqual(len(result['models']),8); self.assertEqual(len(fetcher.calls),8)
            self.assertTrue(all('/api/models/org/model-' in u for u in fetcher.calls))
            self.assertFalse(any('api/models?' in u for u in fetcher.calls))

    def test_first_sync_keeps_audited_revision_statuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog=setup_files(tmp); result,report=sync_catalog(source,catalog,FakeFetcher(),'2026-11-14T12:00:00Z')
            self.assertEqual(report['unchanged'],8)
            self.assertTrue(all(x['metadata_status']=='unchanged' for x in result['models']))
            self.assertTrue(all(x['license_review_status']=='verified_permissive' for x in result['models']))

    def test_repeat_unchanged_check_does_not_rewrite_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog=setup_files(tmp); first,_=sync_catalog(source,catalog,FakeFetcher(),'2026-11-14T12:00:00Z')
            catalog.write_text(json.dumps(first),encoding='utf-8')
            again,report=sync_catalog(source,catalog,FakeFetcher(),'2026-11-21T12:00:00Z')
            self.assertFalse(report['catalog_changed']); self.assertEqual(again,first)

    def test_changed_revision_invalidates_license_review_and_stays_review_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog=setup_files(tmp); first,_=sync_catalog(source,catalog,FakeFetcher(),'2026-11-14T12:00:00Z')
            catalog.write_text(json.dumps(first),encoding='utf-8')
            changed,_=sync_catalog(source,catalog,FakeFetcher({'org/model-1':api_model('f'*40)}),'2026-11-21T12:00:00Z')
            item=changed['models'][0]
            self.assertEqual(item['metadata_status'],'review_required'); self.assertEqual(item['license_review_status'],'review_required')
            catalog.write_text(json.dumps(changed),encoding='utf-8')
            repeated,report=sync_catalog(source,catalog,FakeFetcher({'org/model-1':api_model('f'*40)}),'2026-11-28T12:00:00Z')
            self.assertFalse(report['catalog_changed']); self.assertEqual(repeated['models'][0]['metadata_status'],'review_required')

    def test_missing_repository_retained_without_weekly_manifest_churn(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog=setup_files(tmp); first,_=sync_catalog(source,catalog,FakeFetcher(),'2026-11-14T12:00:00Z')
            catalog.write_text(json.dumps(first),encoding='utf-8'); fetcher=FakeFetcher({'org/model-1':HTTPStatusError(404,API_ROOT+'org/model-1')})
            missing,_=sync_catalog(source,catalog,fetcher,'2026-11-21T12:00:00Z')
            self.assertEqual(missing['models'][0]['source_status'],'missing')
            catalog.write_text(json.dumps(missing),encoding='utf-8')
            again,report=sync_catalog(source,catalog,fetcher,'2026-11-28T12:00:00Z')
            self.assertFalse(report['catalog_changed']); self.assertEqual(again['models'][0]['source_status'],'missing')

    def test_moved_repository_records_destination_without_changing_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog=setup_files(tmp); first,_=sync_catalog(source,catalog,FakeFetcher(),'2026-11-14T12:00:00Z')
            catalog.write_text(json.dumps(first),encoding='utf-8'); destination=API_ROOT+'org/new-model-1'
            fetcher=FakeFetcher({'org/model-1':(api_model('a'*40),destination)})
            moved,report=sync_catalog(source,catalog,fetcher,'2026-11-21T12:00:00Z')
            self.assertEqual(report['moved'],1); self.assertEqual(moved['models'][0]['repo_id'],'org/model-1')
            self.assertEqual(moved['models'][0]['moved_to_repo_id'],'org/new-model-1')

    def test_gated_private_disabled_sources_are_review_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog=setup_files(tmp); first,_=sync_catalog(source,catalog,FakeFetcher(),'2026-11-14T12:00:00Z')
            catalog.write_text(json.dumps(first),encoding='utf-8')
            responses={'org/model-1':api_model('1'*40,gated=True),'org/model-2':api_model('2'*40,private=True),'org/model-3':api_model('3'*40,disabled=True)}
            result,report=sync_catalog(source,catalog,FakeFetcher(responses),'2026-11-21T12:00:00Z')
            self.assertEqual(report['restricted'],3)
            self.assertEqual([x['source_status'] for x in result['models'][:3]],['gated','private','disabled'])
            self.assertTrue(all(x['license_review_status']=='review_required' for x in result['models'][:3]))

    def test_api_error_rate_limit_and_incomplete_metadata_abort(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog=setup_files(tmp); first,_=sync_catalog(source,catalog,FakeFetcher(),'2026-11-14T12:00:00Z')
            catalog.write_text(json.dumps(first),encoding='utf-8'); before=catalog.read_text()
            for failure in (HTTPStatusError(429,API_ROOT),HTTPStatusError(503,API_ROOT),{},api_model('bad-sha')):
                with self.subTest(failure=failure):
                    with self.assertRaises(CatalogSyncError): run_sync(source,catalog,Path(tmp)/'report.json',FakeFetcher({'org/model-1':failure}),'2026-11-21T12:00:00Z')
                    self.assertEqual(catalog.read_text(),before)

    def test_invalid_source_or_extra_catalog_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog=setup_files(tmp); source.write_text(json.dumps({'models':source_models()[:-1]}))
            with self.assertRaises(CatalogSyncError): sync_catalog(source,catalog,FakeFetcher(),'2026-11-14T12:00:00Z')
            source.write_text(json.dumps({'models':source_models()}))
            first,_=sync_catalog(source,catalog,FakeFetcher(),'2026-11-14T12:00:00Z'); first['models'].append(dict(first['models'][-1],model_id=9))
            catalog.write_text(json.dumps(first))
            with self.assertRaises(CatalogSyncError): sync_catalog(source,catalog,FakeFetcher(),'2026-11-21T12:00:00Z')

    def test_run_sync_writes_report_and_never_claims_weights_or_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog=setup_files(tmp); report_path=Path(tmp)/'report.json'
            report=run_sync(source,catalog,report_path,FakeFetcher(),'2026-11-14T12:00:00Z')
            self.assertTrue(report_path.exists()); self.assertFalse(report['weights_downloaded']); self.assertFalse(report['inference_run'])
            data=json.loads(catalog.read_text()); self.assertFalse(data['weights_downloaded']); self.assertFalse(data['inference_run'])

if __name__=='__main__': unittest.main(verbosity=2)
