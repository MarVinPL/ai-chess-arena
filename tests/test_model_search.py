import unittest
import server

class ModelSearchTests(unittest.TestCase):
    def test_search_requires_two_characters(self):
        with self.assertRaises(ValueError):
            server.search_hf_models('x')

if __name__ == '__main__':
    unittest.main()
