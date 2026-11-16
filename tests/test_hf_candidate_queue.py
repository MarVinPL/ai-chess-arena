import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import hf_candidate_queue as queue


class AuditQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'arena.sqlite3'
        self.catalog = [{
            'catalog_id': 'hf:org/model', 'repo_id': 'org/model',
            'display_name': 'Model', 'revision': 'a' * 40,
            'source_status': 'active',
        }]
        self.patches = [
            patch.object(queue.league, 'DB', self.db),
            patch.object(queue.hf_candidate_audit, 'load_catalog', return_value=self.catalog),
        ]
        for item in self.patches: item.start()

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        self.tmp.cleanup()

    def test_refresh_queue_is_persistent_and_idempotent(self):
        first = queue.refresh_queue()
        second = queue.refresh_queue()
        self.assertEqual(first['added'], 1)
        self.assertEqual(second['added'], 0)
        snapshot = queue.snapshot()
        self.assertEqual(snapshot['counts']['queued'], 1)
        self.assertEqual(snapshot['items'][0]['catalog_id'], 'hf:org/model')

    def test_existing_revision_report_is_not_requeued(self):
        with queue.league.connect() as c:
            report = {'catalog_id': 'hf:org/model', 'pinned_revision': 'a' * 40, 'audited_at': '2026-11-16T00:00:00Z'}
            queue.hf_candidate_audit.save_report(c, report)
        result = queue.refresh_queue()
        self.assertEqual(result['added'], 0)
        self.assertEqual(queue.snapshot()['items'], [])

    def test_hardware_snapshot_is_explicit_about_unavailable_vram(self):
        result = queue.hardware_snapshot()
        self.assertIn('ram', result)
        self.assertIn('swap', result)
        self.assertIn('disk', result)
        self.assertIn(result['vram']['status'], {'not_available', 'detected_device_details_unavailable'})
        self.assertFalse(result['runtime_tests_enabled'])
        self.assertEqual(result['runtime_active'], 0)

    def test_one_worker_processes_only_one_queue_item_per_call(self):
        self.catalog.append({
            'catalog_id': 'hf:org/model2', 'repo_id': 'org/model2',
            'display_name': 'Model 2', 'revision': 'b' * 40,
            'source_status': 'active',
        })
        queue.refresh_queue()
        def fake_audit(catalog_id):
            return {'catalog_id': catalog_id, 'pinned_revision': 'a' * 40 if catalog_id == 'hf:org/model' else 'b' * 40, 'audited_at': '2026-11-16T00:00:00Z'}
        with patch.object(queue.hf_candidate_audit, 'audit_candidate', side_effect=fake_audit):
            self.assertTrue(queue._run_one())
        statuses = [x['status'] for x in queue.snapshot()['items']]
        self.assertEqual(statuses.count('completed'), 1)
        self.assertEqual(statuses.count('queued'), 1)


if __name__ == '__main__':
    unittest.main()
