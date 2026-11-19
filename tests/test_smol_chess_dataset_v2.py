import hashlib
import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

import chess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from build_smol_chess_dataset_v2 import build_dataset
from prepare_smol_chess_sft import prepare_rows, write_prepared
from preflight_smol_chess_training import run_preflight


class SmolChessDatasetV2Tests(unittest.TestCase):
    def test_dataset_is_deterministic_pinned_and_split_without_source_leakage(self):
        rows, manifest = build_dataset()
        data_path = ROOT / 'datasets/chess_sft/dataset-v2.jsonl'
        raw = data_path.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), manifest['sha256'])
        self.assertEqual(manifest['rows'], 100)
        self.assertEqual(manifest['split_counts'], {'train': 80, 'validation': 10, 'test': 10})
        self.assertEqual(manifest['license'], 'CC0-1.0')
        self.assertFalse(manifest['network_access'])
        self.assertIsNone(manifest['engine_reference'])
        self.assertEqual(len({row['fen'] for row in rows}), len(rows))
        groups = {}
        for row in rows:
            previous = groups.setdefault(row['source_group'], row['split'])
            self.assertEqual(previous, row['split'])
        self.assertEqual(set(manifest['task_counts']), {
            'endgame_mate_in_one', 'promotion_choice', 'unique_capture',
            'unique_castle', 'unique_check', 'unique_evasion',
        })

    def test_every_target_is_legal_and_satisfies_its_task_rule(self):
        rows = [json.loads(line) for line in (ROOT / 'datasets/chess_sft/dataset-v2.jsonl').read_text().splitlines()]
        for row in rows:
            board = chess.Board(row['fen'])
            legal = list(board.legal_moves)
            target = chess.Move.from_uci(row['best_move'])
            self.assertIn(target, legal)
            self.assertEqual(row['legal_moves'], sorted(move.uci() for move in legal))
            if row['task_type'] == 'endgame_mate_in_one':
                after = board.copy(); after.push(target)
                self.assertTrue(after.is_checkmate())
            elif row['task_type'] == 'unique_check':
                self.assertTrue(board.gives_check(target))
                self.assertEqual(sum(board.gives_check(move) for move in legal), 1)
            elif row['task_type'] == 'unique_capture':
                self.assertTrue(board.is_capture(target))
                self.assertEqual(sum(board.is_capture(move) for move in legal), 1)
            elif row['task_type'] == 'unique_castle':
                self.assertTrue(board.is_castling(target))
                self.assertEqual(sum(board.is_castling(move) for move in legal), 1)
            elif row['task_type'] == 'unique_evasion':
                self.assertTrue(board.is_check())
                self.assertEqual(len(legal), 1)
            elif row['task_type'] == 'promotion_choice':
                self.assertEqual(target.promotion, chess.QUEEN)
                self.assertEqual(sum(move.promotion is not None for move in legal), 4)

    def test_preparation_emits_chat_messages_and_preserves_splits(self):
        prepared = prepare_rows(ROOT / 'datasets/chess_sft/dataset-v2.jsonl')
        self.assertEqual({key: len(value) for key, value in prepared.items()}, {
            'train': 80, 'validation': 10, 'test': 10,
        })
        for split, rows in prepared.items():
            for row in rows:
                self.assertEqual(row['split'], split)
                self.assertEqual([m['role'] for m in row['messages']], ['system', 'user', 'assistant'])
                self.assertEqual(row['messages'][-1]['content'] in {
                    json.loads(line)['best_move'] for line in (ROOT / 'datasets/chess_sft/dataset-v2.jsonl').read_text().splitlines()
                }, True)
        with tempfile.TemporaryDirectory() as tmp:
            manifest = write_prepared(ROOT / 'datasets/chess_sft/dataset-v2.jsonl', Path(tmp))
            self.assertEqual(manifest['outputs']['train']['rows'], 80)
            self.assertFalse(manifest['network_access'])
            self.assertTrue((Path(tmp) / 'manifest.json').is_file())

    def test_training_preflight_blocks_missing_model_and_low_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            meminfo = Path(tmp) / 'meminfo'
            meminfo.write_text('MemAvailable:       1000000 kB\n')
            report = run_preflight(meminfo=meminfo, output_dir=Path(tmp) / 'out')
            self.assertEqual(report['status'], 'blocked')
            self.assertFalse(report['weights_downloaded'])
            self.assertFalse(report['model_loaded'])
            self.assertFalse(report['training_started'])

    def test_training_preflight_only_reports_ready_for_complete_local_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / 'local-model'; model.mkdir()
            (model / 'config.json').write_text('{}')
            (model / 'tokenizer.json').write_text('{}')
            (model / 'model.safetensors').write_bytes(b'local-test-weights')
            meminfo = root / 'meminfo'; meminfo.write_text('MemAvailable: 16000000 kB\n')
            report = run_preflight(model_dir=model, min_ram_gib=1, min_disk_gib=0.01,
                                   output_dir=root / 'out', meminfo=meminfo)
            self.assertEqual(report['status'], 'ready_for_owner_review')
            self.assertEqual(report['local_model']['status'], 'ready_local_files_present')
            self.assertFalse(report['model_loaded'])
            self.assertFalse(report['training_started'])


if __name__ == '__main__':
    unittest.main()
