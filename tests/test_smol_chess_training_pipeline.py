import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from train_smol_chess_lora import train, dry_run_report as train_dry_run
from evaluate_smol_chess_lora import evaluate, dry_run_report as evaluate_dry_run

PREPARED = ROOT / 'datasets/chess_sft/prepared-v2'


class SmolChessTrainingPipelineTests(unittest.TestCase):
    def test_training_dry_run_never_loads_model_or_starts_training(self):
        report = train_dry_run(None, PREPARED, Path('/tmp/unused-smol-output'))
        self.assertEqual(report['mode'], 'archived_dry_run')
        self.assertTrue(report['prepared_files_present'])
        self.assertFalse(report['model_loaded'])
        self.assertFalse(report['training_started'])
        self.assertFalse(report['network_access'])

    def test_training_is_cancelled_even_when_execution_is_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'został anulowany'):
                train(base_model_dir=Path(tmp) / 'missing-model', prepared_dir=PREPARED,
                      output_dir=Path(tmp) / 'adapter', execute=True,
                      confirm_local_offline_training=True)

    def test_evaluation_dry_run_does_not_load_model_or_run_inference(self):
        report = evaluate_dry_run(None, None, PREPARED / 'test.jsonl')
        self.assertEqual(report['mode'], 'archived_dry_run')
        self.assertEqual(report['test_rows'], 10)
        self.assertFalse(report['model_loaded'])
        self.assertFalse(report['inference_run'])
        self.assertFalse(report['network_access'])

    def test_adapter_evaluation_is_not_applicable_even_when_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'NOT_APPLICABLE'):
                evaluate(base_model_dir=Path(tmp) / 'missing-model', adapter_dir=Path(tmp) / 'adapter',
                         test_path=PREPARED / 'test.jsonl', execute=True,
                         confirm_local_inference=True)


if __name__ == '__main__':
    unittest.main()
