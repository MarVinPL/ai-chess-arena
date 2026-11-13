import unittest
from unittest.mock import patch
import server

class ModelSearchTests(unittest.TestCase):
    def test_search_requires_two_characters(self):
        with self.assertRaises(ValueError):
            server.search_hf_models('x')

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
