import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from huggingface_catalog_sync import API_ROOT, CatalogSyncError, HTTPStatusError, run_sync, sync_catalog
from model_exclusions import EXCLUDED_HF_REPOSITORIES


def source_models():
    statuses = {1:'verified_permissive',2:'review_required',3:'verified_permissive',4:'review_required',5:'review_required',6:'verified_restricted',7:'verified_restricted',8:'review_required'}
    items=[{'model_id':i,'repo_id':f'org/existing-{i}','revision':f'{i:040x}','license_review_status':statuses[i]} for i in range(1,9)]
    for i,repo in zip((9,10),sorted(EXCLUDED_HF_REPOSITORIES)):
        items.append({'model_id':i,'repo_id':repo,'revision':f'{i:040x}','license_review_status':'review_required'})
    return items


def api_model(repo_id, revision=None, modified='2026-11-14T10:00:00Z', tags=None, pipeline_tag='text-generation', **extra):
    value={'id':repo_id,'sha':revision or 'a'*40,'lastModified':modified,
           'tags':tags if tags is not None else ['gguf','text-generation','license:apache-2.0'],
           'pipeline_tag':pipeline_tag,'private':False,'disabled':False,'gated':False}
    value.update(extra)
    return value


class FakeFetcher:
    def __init__(self, source=None, lists=None, details=None):
        self.calls=[]
        self.lists=lists or {'lastModified':[api_model('org/new-a','b'*40),api_model('org/new-b','c'*40)],
                             'downloads':[api_model('org/new-a','b'*40),api_model('org/new-b','c'*40)]}
        self.details={x['repo_id']:api_model(x['repo_id'],x['revision']) for x in (source or []) if x['model_id']<=8}
        self.details.update(details or {})

    def __call__(self,url):
        self.calls.append(url); parsed=urlsplit(url)
        if parsed.path=='/api/models':
            sort=parse_qs(parsed.query).get('sort',[''])[0]; value=self.lists.get(sort,[])
            if isinstance(value,Exception): raise value
            return value,url
        repo=unquote(parsed.path.removeprefix('/api/models/')); value=self.details.get(repo)
        if isinstance(value,Exception): raise value
        if isinstance(value,tuple): return value
        if value is not None: return value,url
        return api_model(repo,'d'*40),url


def initial_files(tmp):
    source=Path(tmp)/'source.json'; catalog=Path(tmp)/'catalog.json'
    source.write_text(json.dumps({'schema_version':1,'models':source_models()}),encoding='utf-8')
    return source,catalog


class HuggingFaceCatalogSyncTests(unittest.TestCase):
    def test_discovers_new_candidates_and_seeds_existing_models_without_approving(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog_path=initial_files(tmp); fetcher=FakeFetcher(source_models())
            catalog,report=sync_catalog(source,catalog_path,fetcher,'2026-11-14T12:00:00Z')
            self.assertEqual(report['source_models_checked'],8); self.assertEqual(report['new'],2)
            self.assertEqual(len(catalog['models']),10)
            new=[x for x in catalog['models'] if x['arena_model_id'] is None]
            self.assertEqual({x['repo_id'] for x in new},{'org/new-a','org/new-b'})
            for item in new:
                self.assertEqual(item['license_review_status'],'review_required'); self.assertFalse(item['license_verified'])
                self.assertEqual(item['technical_readiness'],'not_evaluated'); self.assertEqual(item['admission_status'],'not_admitted')
            existing=next(x for x in catalog['models'] if x['arena_model_id']==1)
            self.assertEqual(existing['license_review_status'],'verified_permissive'); self.assertFalse(existing['license_verified'])
            self.assertFalse(report['weights_downloaded']); self.assertFalse(report['inference_run'])

    def test_conversational_gguf_without_pipeline_tag_is_still_a_chat_candidate(self):
        chat = api_model('org/conversational-only', '8' * 40, tags=['gguf', 'conversational', 'license:apache-2.0'], pipeline_tag=None)
        lists = {'lastModified':[chat], 'downloads':[chat]}
        with tempfile.TemporaryDirectory() as tmp:
            source, catalog_path = initial_files(tmp)
            catalog, report = sync_catalog(source, catalog_path, FakeFetcher(source_models(), lists), '2026-11-14T12:00:00Z')
            item = next(x for x in catalog['models'] if x['repo_id']=='org/conversational-only')
            self.assertEqual(item['pipeline_tag'], 'conversational')
            self.assertEqual(item['license_review_status'], 'review_required')
            self.assertEqual(report['new'], 1)

    def test_exact_excluded_models_9_and_10_never_enter_candidate_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog_path=initial_files(tmp); excluded=[api_model(repo,'e'*40) for repo in EXCLUDED_HF_REPOSITORIES]
            safe=api_model('org/new-safe','f'*40); lists={'lastModified':excluded+[safe],'downloads':excluded+[safe]}
            catalog,report=sync_catalog(source,catalog_path,FakeFetcher(source_models(),lists),'2026-11-14T12:00:00Z')
            repos={x['repo_id'] for x in catalog['models']}
            self.assertTrue(EXCLUDED_HF_REPOSITORIES.isdisjoint(repos)); self.assertIn('safety_filtered',report['filtered_by_reason'])

    def test_known_porn_model_is_filtered_even_without_nsfw_tags(self):
        porn_model = api_model('rectangleworm/PornMaster_Klein-9b', '4' * 40, tags=['gguf', 'text-generation', 'license:other'])
        lists = {'lastModified':[porn_model], 'downloads':[porn_model]}
        with tempfile.TemporaryDirectory() as tmp:
            source, catalog_path = initial_files(tmp)
            catalog, report = sync_catalog(source, catalog_path, FakeFetcher(source_models(), lists), '2026-11-14T12:00:00Z')
            self.assertNotIn('rectangleworm/PornMaster_Klein-9b', {x['repo_id'] for x in catalog['models']})
            self.assertGreater(report['filtered_by_reason'].get('safety_filtered', 0), 0)

    def test_additional_adult_model_terms_are_filtered(self):
        unsafe = api_model('org/model-smut', '5' * 40, tags=['gguf', 'text-generation', 'license:apache-2.0'])
        lists = {'lastModified':[unsafe], 'downloads':[unsafe]}
        with tempfile.TemporaryDirectory() as tmp:
            source, catalog_path = initial_files(tmp)
            catalog, report = sync_catalog(source, catalog_path, FakeFetcher(source_models(), lists), '2026-11-14T12:00:00Z')
            self.assertNotIn('org/model-smut', {x['repo_id'] for x in catalog['models']})
            self.assertGreater(report['filtered_by_reason'].get('safety_filtered', 0), 0)

    def test_non_text_and_safety_filtered_new_models_are_not_added(self):
        unsafe=api_model('org/unsafe','1'*40,tags=['gguf','nsfw'])
        image=api_model('org/image','2'*40,tags=['gguf','text-to-image'],pipeline_tag='text-to-image')
        gated=api_model('org/gated','3'*40,gated=True)
        lists={'lastModified':[unsafe,image,gated],'downloads':[unsafe,image,gated]}
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog_path=initial_files(tmp); catalog,report=sync_catalog(source,catalog_path,FakeFetcher(source_models(),lists),'2026-11-14T12:00:00Z')
            repos={x['repo_id'] for x in catalog['models']}
            self.assertTrue({'org/unsafe','org/image','org/gated'}.isdisjoint(repos))
            self.assertGreater(report['filtered_by_reason'].get('safety_filtered',0),0)
            self.assertGreater(report['filtered_by_reason'].get('not_text_generation',0),0)
            self.assertGreater(report['filtered_by_reason'].get('gated',0),0)

    def test_changed_existing_revision_requires_new_license_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog_path=initial_files(tmp); first,_=sync_catalog(source,catalog_path,FakeFetcher(source_models()),'2026-11-14T12:00:00Z')
            catalog_path.write_text(json.dumps(first),encoding='utf-8')
            details={'org/existing-1':api_model('org/existing-1','f'*40)}
            updated,report=sync_catalog(source,catalog_path,FakeFetcher(source_models(),details=details),'2026-11-21T12:00:00Z')
            item=next(x for x in updated['models'] if x['arena_model_id']==1)
            self.assertEqual(item['revision'],'f'*40); self.assertEqual(item['license_review_status'],'review_required')
            self.assertEqual(item['metadata_status'],'review_required'); self.assertEqual(item['history'][-1]['event'],'revision_changed')
            self.assertGreater(report['changed'],0)

    def test_unchanged_existing_audit_status_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog_path=initial_files(tmp); first,_=sync_catalog(source,catalog_path,FakeFetcher(source_models()),'2026-11-14T12:00:00Z')
            catalog_path.write_text(json.dumps(first),encoding='utf-8')
            updated,report=sync_catalog(source,catalog_path,FakeFetcher(source_models()),'2026-11-21T12:00:00Z')
            item=next(x for x in updated['models'] if x['arena_model_id']==6)
            self.assertEqual(item['license_review_status'],'verified_restricted'); self.assertFalse(report['catalog_changed'])

    def test_new_candidate_revision_change_never_grants_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog_path=initial_files(tmp); first,_=sync_catalog(source,catalog_path,FakeFetcher(source_models()),'2026-11-14T12:00:00Z')
            catalog_path.write_text(json.dumps(first),encoding='utf-8')
            lists={'lastModified':[api_model('org/new-a','9'*40)],'downloads':[api_model('org/new-a','9'*40)]}
            updated,_=sync_catalog(source,catalog_path,FakeFetcher(source_models(),lists),'2026-11-21T12:00:00Z')
            item=next(x for x in updated['models'] if x['repo_id']=='org/new-a')
            self.assertEqual(item['license_review_status'],'review_required'); self.assertFalse(item['license_verified'])
            self.assertEqual(item['technical_readiness'],'not_evaluated'); self.assertEqual(item['admission_status'],'not_admitted')

    def test_removed_candidate_is_retained_as_missing_tombstone(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog_path=initial_files(tmp); first,_=sync_catalog(source,catalog_path,FakeFetcher(source_models()),'2026-11-14T12:00:00Z')
            catalog_path.write_text(json.dumps(first),encoding='utf-8')
            lists={'lastModified':[api_model('org/other','8'*40)],'downloads':[api_model('org/other','8'*40)]}
            details={'org/new-a':HTTPStatusError(404,API_ROOT+'/org/new-a')}
            updated,report=sync_catalog(source,catalog_path,FakeFetcher(source_models(),lists,details),'2026-11-21T12:00:00Z')
            item=next(x for x in updated['models'] if x['repo_id']=='org/new-a')
            self.assertEqual(item['source_status'],'missing'); self.assertEqual(item['history'][-1]['event'],'missing')
            self.assertEqual(report['missing'],1)

    def test_moved_candidate_records_destination_without_replacing_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog_path=initial_files(tmp); first,_=sync_catalog(source,catalog_path,FakeFetcher(source_models()),'2026-11-14T12:00:00Z')
            catalog_path.write_text(json.dumps(first),encoding='utf-8')
            lists={'lastModified':[api_model('org/other','8'*40)],'downloads':[api_model('org/other','8'*40)]}
            details={'org/new-a':(api_model('org/new-a-moved','7'*40),API_ROOT+'/org/new-a-moved')}
            updated,report=sync_catalog(source,catalog_path,FakeFetcher(source_models(),lists,details),'2026-11-21T12:00:00Z')
            item=next(x for x in updated['models'] if x['repo_id']=='org/new-a')
            self.assertEqual(item['source_status'],'moved'); self.assertEqual(item['moved_to_repo_id'],'org/new-a-moved')
            self.assertEqual(item['repo_id'],'org/new-a'); self.assertEqual(report['moved'],1)

    def test_missing_and_moved_existing_source_models_are_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog_path=initial_files(tmp)
            details={'org/existing-1':HTTPStatusError(404,API_ROOT+'/org/existing-1'),
                     'org/existing-2':(api_model('org/redirected','6'*40),API_ROOT+'/org/redirected')}
            catalog,report=sync_catalog(source,catalog_path,FakeFetcher(source_models(),details=details),'2026-11-14T12:00:00Z')
            one=next(x for x in catalog['models'] if x['arena_model_id']==1); two=next(x for x in catalog['models'] if x['arena_model_id']==2)
            self.assertEqual(one['source_status'],'missing'); self.assertEqual(two['source_status'],'moved')
            self.assertEqual(two['moved_to_repo_id'],'org/redirected'); self.assertEqual(report['missing'],1); self.assertEqual(report['moved'],1)

    def test_private_disabled_and_gated_existing_sources_remain_review_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog_path=initial_files(tmp)
            details={'org/existing-1':api_model('org/existing-1','1'*40,private=True),
                     'org/existing-2':api_model('org/existing-2','2'*40,disabled=True),
                     'org/existing-3':api_model('org/existing-3','3'*40,gated=True)}
            catalog,report=sync_catalog(source,catalog_path,FakeFetcher(source_models(),details=details),'2026-11-14T12:00:00Z')
            statuses={x['arena_model_id']:x['source_status'] for x in catalog['models'] if x['arena_model_id'] in (1,2,3)}
            self.assertEqual(statuses,{1:'private',2:'disabled',3:'gated'})
            self.assertGreater(report['filtered_by_reason'].get('private',0),0)
            self.assertTrue(all(x['license_review_status']=='review_required' for x in catalog['models'] if x['arena_model_id'] in (1,2,3)))

    def test_safety_filtered_existing_candidate_is_retained_as_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog_path=initial_files(tmp); first,_=sync_catalog(source,catalog_path,FakeFetcher(source_models()),'2026-11-14T12:00:00Z')
            catalog_path.write_text(json.dumps(first),encoding='utf-8')
            unsafe=api_model('org/new-a','b'*40,tags=['gguf','text-generation','nsfw'])
            lists={'lastModified':[unsafe],'downloads':[unsafe]}
            updated,_=sync_catalog(source,catalog_path,FakeFetcher(source_models(),lists,{'org/new-a':unsafe}),'2026-11-21T12:00:00Z')
            item=next(x for x in updated['models'] if x['repo_id']=='org/new-a')
            self.assertEqual(item['source_status'],'safety_filtered'); self.assertEqual(item['admission_status'],'blocked')
            self.assertEqual(item['history'][-1]['event'],'safety_filtered')

    def test_duplicate_discovery_results_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog_path=initial_files(tmp); repeated=api_model('org/duplicate','a'*40)
            lists={'lastModified':[repeated],'downloads':[repeated]}
            catalog,report=sync_catalog(source,catalog_path,FakeFetcher(source_models(),lists),'2026-11-14T12:00:00Z')
            self.assertEqual(sum(x['repo_id']=='org/duplicate' for x in catalog['models']),1); self.assertEqual(report['new'],1)

    def test_inconsistent_duplicate_revisions_abort(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog_path=initial_files(tmp)
            lists={'lastModified':[api_model('org/duplicate','a'*40)],'downloads':[api_model('org/duplicate','b'*40)]}
            with self.assertRaises(CatalogSyncError): sync_catalog(source,catalog_path,FakeFetcher(source_models(),lists),'2026-11-14T12:00:00Z')
            self.assertFalse(catalog_path.exists())

    def test_empty_response_rate_limit_and_incomplete_metadata_preserve_previous_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog_path=initial_files(tmp); initial,_=sync_catalog(source,catalog_path,FakeFetcher(source_models()),'2026-11-14T12:00:00Z')
            catalog_path.write_text(json.dumps(initial),encoding='utf-8'); before=catalog_path.read_text(encoding='utf-8')
            failures=[{'lastModified':[],'downloads':[api_model('org/new')]},
                      {'lastModified':HTTPStatusError(429,API_ROOT),'downloads':[api_model('org/new')]},
                      {'lastModified':[{'id':'org/bad','tags':['gguf','text-generation'],'pipeline_tag':'text-generation'}],'downloads':[api_model('org/new')]}]
            for lists in failures:
                with self.subTest(failure=str(lists)[:40]):
                    with self.assertRaises(CatalogSyncError): run_sync(source,catalog_path,Path(tmp)/'report.json',FakeFetcher(source_models(),lists),'2026-11-21T12:00:00Z')
                    self.assertEqual(catalog_path.read_text(encoding='utf-8'),before)
                    self.assertEqual(json.loads((Path(tmp)/'report.json').read_text())['status'],'error')

    def test_duplicate_or_invalid_existing_catalog_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog_path=initial_files(tmp)
            invalid={'schema_version':1,'models':[{'repo_id':'org/a','catalog_id':'same'},{'repo_id':'org/a','catalog_id':'same'}]}
            catalog_path.write_text(json.dumps(invalid),encoding='utf-8'); before=catalog_path.read_text(encoding='utf-8')
            with self.assertRaises(CatalogSyncError): sync_catalog(source,catalog_path,FakeFetcher(source_models()),'2026-11-14T12:00:00Z')
            self.assertEqual(catalog_path.read_text(encoding='utf-8'),before)

    def test_no_change_does_not_rewrite_catalog_but_report_tracks_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog_path=initial_files(tmp); first=run_sync(source,catalog_path,Path(tmp)/'report.json',FakeFetcher(source_models()),'2026-11-14T12:00:00Z')
            before=catalog_path.read_text(encoding='utf-8')
            second=run_sync(source,catalog_path,Path(tmp)/'report.json',FakeFetcher(source_models()),'2026-11-21T12:00:00Z')
            self.assertTrue(first['catalog_changed']); self.assertFalse(second['catalog_changed'])
            self.assertEqual(catalog_path.read_text(encoding='utf-8'),before)
            self.assertEqual(json.loads((Path(tmp)/'report.json').read_text())['checked_at'],'2026-11-21T12:00:00Z')

    def test_catalog_history_is_not_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog_path=initial_files(tmp); first,_=sync_catalog(source,catalog_path,FakeFetcher(source_models()),'2026-11-14T12:00:00Z')
            candidate=next(x for x in first['models'] if x['arena_model_id'] is None)
            candidate['history']=[{'at':str(i),'event':'prior'} for i in range(35)]
            catalog_path.write_text(json.dumps(first),encoding='utf-8')
            lists={'lastModified':[api_model('org/other','8'*40)],'downloads':[api_model('org/other','8'*40)]}
            updated,_=sync_catalog(source,catalog_path,FakeFetcher(source_models(),lists,{'org/new-a':HTTPStatusError(404,API_ROOT+'/org/new-a')}),'2026-11-21T12:00:00Z')
            item=next(x for x in updated['models'] if x['repo_id']=='org/new-a')
            self.assertEqual(len(item['history']),36)

    def test_only_metadata_api_is_called_and_weights_are_never_downloaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,catalog_path=initial_files(tmp); fetcher=FakeFetcher(source_models())
            catalog,report=sync_catalog(source,catalog_path,fetcher,'2026-11-14T12:00:00Z')
            self.assertTrue(all(url.startswith(API_ROOT) for url in fetcher.calls))
            self.assertFalse(any('/resolve/' in url for url in fetcher.calls))
            self.assertFalse(catalog['weights_downloaded']); self.assertFalse(catalog['inference_run'])
            self.assertFalse(report['weights_downloaded']); self.assertFalse(report['inference_run'])


    def test_unauthorized_candidate_is_retained_as_access_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, catalog_path = initial_files(tmp)
            first, _ = sync_catalog(
                source, catalog_path, FakeFetcher(source_models()), '2026-11-14T12:00:00Z'
            )
            catalog_path.write_text(json.dumps(first), encoding='utf-8')
            lists = {
                'lastModified': [api_model('org/other', '8' * 40)],
                'downloads': [api_model('org/other', '8' * 40)],
            }
            details = {'org/new-a': HTTPStatusError(401, API_ROOT + '/org/new-a')}
            updated, report = sync_catalog(
                source, catalog_path, FakeFetcher(source_models(), lists, details),
                '2026-11-21T12:00:00Z'
            )
            item = next(x for x in updated['models'] if x['repo_id'] == 'org/new-a')
            self.assertEqual(item['source_status'], 'access_denied')
            self.assertEqual(item['license_review_status'], 'review_required')
            self.assertFalse(item['license_verified'])
            self.assertEqual(item['admission_status'], 'not_admitted')
            self.assertGreater(report['filtered_by_reason'].get('access_denied', 0), 0)



if __name__ == '__main__':
    unittest.main(verbosity=2)
