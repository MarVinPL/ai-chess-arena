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

    def test_random_model_search_uses_multiple_model_families_and_returns_one_candidate(self):
        candidates = [{'name': 'Qwen/model', 'files': []}, {'name': 'SmolLM/model', 'files': []}]
        with patch.object(server, 'search_hf_models', side_effect=[[], candidates]) as search:
            result = server.random_hf_model()
        self.assertEqual(len(result), 1)
        self.assertIn(result[0], candidates)
        self.assertEqual(search.call_count, 2)
        self.assertNotEqual(search.call_args_list[0].args[0], search.call_args_list[1].args[0])

    def test_random_model_search_returns_empty_when_no_gguf_candidates_exist(self):
        with patch.object(server, 'search_hf_models', return_value=[]) as search:
            result = server.random_hf_model()
        self.assertEqual(result, [])
        self.assertGreater(search.call_count, 1)

if __name__ == '__main__':
    unittest.main()
