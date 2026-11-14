import unittest

import model_policy


class ModelPolicyTests(unittest.TestCase):
    def test_ids_nine_and_ten_are_excluded_without_changing_other_ids(self):
        self.assertEqual(model_policy.EXCLUDED_MODEL_IDS, frozenset({9, 10}))
        for model_id in (9, 10):
            with self.subTest(model_id=model_id):
                repo=sorted(model_policy.EXCLUDED_MODEL_REPOS)[model_id-9]
                source=f'https://huggingface.co/{repo}'
                self.assertTrue(model_policy.is_model_excluded(model_id, source))
                self.assertFalse(model_policy.is_model_admission_allowed(model_id, source))
                with self.assertRaisesRegex(ValueError, f"Model ID {model_id}"):
                    model_policy.assert_model_not_excluded(model_id, "test", source)
        for model_id in (1, 2, 8, 11, 100):
            with self.subTest(model_id=model_id):
                self.assertFalse(model_policy.is_model_excluded(model_id))
                self.assertTrue(model_policy.is_model_admission_allowed(model_id))

    def test_non_integer_values_do_not_match_reserved_ids(self):
        self.assertFalse(model_policy.is_model_excluded("9"))
        self.assertFalse(model_policy.is_model_excluded(True))
        self.assertTrue(model_policy.is_model_excluded(9))  # Missing provenance for a reserved ID fails closed.
        self.assertFalse(model_policy.is_model_excluded(9, 'https://example.test/other.gguf'))
        self.assertTrue(model_policy.is_model_excluded(9, metadata={'license_evidence': {'repo_id': 'JonathanColetti/Qwen3.8-27B-Uncensored-GGUF'}}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
