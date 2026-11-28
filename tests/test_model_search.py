import unittest
from unittest.mock import patch
import server

class ModelSearchTests(unittest.TestCase):
    def test_search_requires_two_characters(self):
        with self.assertRaises(ValueError):
            server.search_hf_models('x')

    def test_search_pairs_matching_qwen_mtp_draft_with_target_and_keeps_target_solo(self):
        import json
        class Response:
            def __init__(self,payload): self.payload=payload
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,n=-1): return json.dumps(self.payload).encode()
        target='Qwen3.8-27B-Uncensored-nomtp-Q8_0.gguf'
        draft='Qwen3.8-27B-Uncensored-draft-Q8_0.gguf'
        files=[{'type':'file','path':target,'lfs':{'size':100,'oid':'a'*64}},
               {'type':'file','path':draft,'lfs':{'size':20,'oid':'b'*64}},
               {'type':'file','path':'mmproj-model.gguf','lfs':{'size':5,'oid':'c'*64}}]
        def open_url(request,timeout=None):
            url=request.full_url if hasattr(request,'full_url') else str(request)
            return Response([{'id':'Qwen/example','cardData':{'license':'apache-2.0'},'downloads':10,'likes':2}] if '/api/models?' in url else files)
        with patch('urllib.request.urlopen',side_effect=open_url):
            result=server.search_hf_models('Qwen MTP')
        self.assertEqual(len(result),1)
        self.assertEqual(len(result[0]['files']),1)
        candidate=result[0]['files'][0]
        self.assertEqual(candidate['filename'],target)
        self.assertEqual(candidate['draft_models'][0]['filename'],draft)
        self.assertEqual(candidate['draft_models'][0]['sha256'],'b'*64)

    def test_random_model_search_removed(self):
        from pathlib import Path
        root = Path(server.__file__).parent
        self.assertFalse(hasattr(server, 'random_hf_model'))
        for path in ('server.py', 'index.html', 'web/js/arena.js'):
            content = (root / path).read_text()
            self.assertNotIn('/api/models/random', content)
            self.assertNotIn('model-random-button', content)
        self.assertIn('/api/models/search?q=', (root / 'web/js/arena.js').read_text())

    def test_automatic_catalog_discovery_retired(self):
        from pathlib import Path
        root = Path(server.__file__).parent
        for path in ('huggingface_catalog_sync.py', 'scripts/sync_huggingface_catalog.py',
                     '.github/workflows/huggingface-candidate-catalog-sync.yml'):
            self.assertFalse((root / path).exists(), path)
        workflow = (root / '.github/workflows/huggingface-model-catalog-sync.yml').read_text()
        self.assertIn('python existing_model_metadata_check.py', workflow)

    def test_license_evidence_fetches_revision_pinned_readme_and_license_text_without_approving(self):
        import json
        class Response:
            def __init__(self, payload, url):
                self.payload = payload
                self.url = url
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, n=-1): return self.payload[:n] if n >= 0 else self.payload
            def geturl(self): return self.url
        card = {
            'sha': 'a' * 40,
            'cardData': {'license': None, 'base_model': ['Qwen/Qwen2']},
            'tags': [],
            'siblings': [{'rfilename': 'README.md'}, {'rfilename': 'LICENSE'}]
        }
        def open_url(request, timeout=None):
            url = request.full_url
            if '/api/models/' in url:
                return Response(json.dumps(card).encode(), url)
            if '/raw/' in url and url.endswith('/README.md'):
                return Response(b'## Model card\\nEducational notes.', url)
            if '/raw/' in url and url.endswith('/LICENSE'):
                return Response(b'License terms: non-commercial.', url)
            raise AssertionError(url)
        with patch('urllib.request.urlopen', side_effect=open_url):
            evidence = server.fetch_hf_license_evidence('https://huggingface.co/example/model')
        self.assertEqual(evidence['revision'], 'a' * 40)
        self.assertEqual(evidence['evidence_status'], 'documents_found_metadata_missing')
        self.assertEqual(len(evidence['documents']), 2)
        self.assertIn('Educational notes', evidence['documents'][0]['excerpt'])
        self.assertIn('non-commercial', evidence['documents'][1]['excerpt'])
        self.assertEqual(evidence['documents'][1]['sha256'], __import__('hashlib').sha256(b'License terms: non-commercial.').hexdigest())
        self.assertNotIn('license_verified', evidence)

    def test_license_evidence_rejects_document_redirect_outside_huggingface(self):
        import json
        class Response:
            def __init__(self, payload, url): self.payload, self.url = payload, url
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, n=-1): return self.payload[:n] if n >= 0 else self.payload
            def geturl(self): return self.url
        card = {'sha': 'b' * 40, 'cardData': {'license': 'apache-2.0'}, 'tags': [], 'siblings': [{'rfilename': 'LICENSE'}]}
        def open_url(request, timeout=None):
            url = request.full_url
            if '/api/models/' in url: return Response(json.dumps(card).encode(), url)
            return Response(b'not trusted', 'https://evil.example/license')
        with patch('urllib.request.urlopen', side_effect=open_url):
            evidence = server.fetch_hf_license_evidence('https://huggingface.co/example/model')
        self.assertIn('fetch_error', evidence['documents'][0])
        self.assertEqual(evidence['evidence_status'], 'metadata_found')

if __name__ == '__main__':
    unittest.main()
