import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from model_allowlist import (
    AllowlistError, check_allowlist_sync, compare_allowlists, is_runtime_ready,
    load_allowlist, validate_allowlist, check_repository_sync,
)
from model_manifest import load_manifest


class ModelAllowlistTests(unittest.TestCase):
    def setUp(self):
        self.allowlist = load_allowlist()
        self.manifest = load_manifest()

    def test_checked_in_allowlist_is_complete_and_non_activating(self):
        validate_allowlist(self.allowlist, self.manifest)
        self.assertEqual(len(self.allowlist['models']), len(self.manifest['models']))
        for item in self.allowlist['models']:
            self.assertEqual(item['admission_scope'], [])
            self.assertTrue(item['license_requirements']['license_verified_required'])
            self.assertEqual(item['technical_readiness'], 'blocked' if item['status'] == 'blocked' else 'not_evaluated')
            self.assertIn(item['status'], {'review_required', 'blocked'})
        self.assertEqual({x['model_id'] for x in self.allowlist['models'] if x['status'] == 'blocked'}, {9, 10})

    def test_revision_mismatch_is_rejected(self):
        broken = copy.deepcopy(self.allowlist)
        broken['models'][0]['revision'] = '0' * 40
        with self.assertRaises(AllowlistError):
            validate_allowlist(broken, self.manifest)

    def test_duplicate_model_or_repository_is_rejected(self):
        for field in ('model_id', 'repo_id'):
            broken = copy.deepcopy(self.allowlist)
            broken['models'][1][field] = broken['models'][0][field]
            with self.subTest(field=field), self.assertRaises(AllowlistError):
                validate_allowlist(broken, self.manifest)

    def test_missing_entry_or_evidence_is_rejected(self):
        missing = copy.deepcopy(self.allowlist)
        missing['models'].pop()
        with self.assertRaises(AllowlistError):
            validate_allowlist(missing, self.manifest)
        no_evidence = copy.deepcopy(self.allowlist)
        no_evidence['models'][0]['evidence'] = []
        with self.assertRaises(AllowlistError):
            validate_allowlist(no_evidence, self.manifest)

    def test_eligible_and_restricted_decisions_require_matching_audit_status(self):
        approved = copy.deepcopy(self.allowlist)
        eligible = approved['models'][0]
        eligible['status'] = 'eligible'
        eligible['admission_scope'] = ['PURE_AI']
        eligible['block_reason'] = None
        restricted = approved['models'][5]
        restricted['status'] = 'restricted'
        restricted['admission_scope'] = ['PURE_AI']
        restricted['block_reason'] = None
        restricted['restrictions'] = ['Licencja ma dodatkowe warunki.']
        restricted['license_requirements']['obligations'] = ['Wymagane spełnienie warunków licencji.']
        validate_allowlist(approved, self.manifest)

    def test_unreviewed_model_cannot_be_marked_eligible_or_restricted(self):
        for status in ('eligible', 'restricted'):
            broken = copy.deepcopy(self.allowlist)
            item = broken['models'][1]  # SmolLM2 remains review_required.
            item['status'] = status
            item['admission_scope'] = ['PURE_AI']
            item['block_reason'] = None
            item['restrictions'] = ['review'] if status == 'restricted' else []
            if status == 'restricted':
                item['license_requirements']['obligations'] = ['restriction']
            with self.subTest(status=status), self.assertRaises(AllowlistError):
                validate_allowlist(broken, self.manifest)

    def test_excluded_models_cannot_be_promoted(self):
        for status in ('eligible', 'restricted', 'review_required'):
            broken = copy.deepcopy(self.allowlist)
            item = next(x for x in broken['models'] if x['model_id'] == 9)
            item['status'] = status
            if status in ('eligible', 'restricted'):
                item['admission_scope'] = ['PURE_AI']
                item['restrictions'] = ['condition']
                item['license_requirements']['obligations'] = ['condition']
                item['block_reason'] = None
            with self.subTest(status=status), self.assertRaises(AllowlistError):
                validate_allowlist(broken, self.manifest)

    def test_add_update_and_withdrawal_are_explicit_diffs(self):
        previous = {'models': [copy.deepcopy(self.allowlist['models'][0]),
                               copy.deepcopy(self.allowlist['models'][1])]}
        current = {'models': [copy.deepcopy(previous['models'][0]),
                              copy.deepcopy(previous['models'][1]),
                              {'model_id': 11, 'status': 'review_required'}]}
        current['models'][1]['block_reason'] = 'Updated review reason'
        delta = compare_allowlists(previous, current)
        self.assertEqual(delta, {'added': [11], 'updated': [2], 'withdrawn': []})
        withdrawn = {'models': [copy.deepcopy(current['models'][0])]}
        self.assertEqual(compare_allowlists(current, withdrawn),
                         {'added': [], 'updated': [], 'withdrawn': [2, 11]})

    def test_withdrawal_of_admission_is_a_blocked_status(self):
        withdrawn = copy.deepcopy(self.allowlist)
        item = withdrawn['models'][0]
        item['status'] = 'blocked'
        item['admission_scope'] = []
        item['block_reason'] = 'Wycofano decyzję; wymaga ponownego przeglądu.'
        validate_allowlist(withdrawn, self.manifest)

    def test_allowlist_decision_does_not_automatically_mean_runtime_ready(self):
        candidate = copy.deepcopy(self.allowlist['models'][0])
        candidate['status'] = 'eligible'
        candidate['admission_scope'] = ['PURE_AI']
        candidate['block_reason'] = None
        validate_allowlist({**self.allowlist, 'models': [candidate] + self.allowlist['models'][1:]}, self.manifest)
        self.assertFalse(is_runtime_ready(candidate, license_verified=True,
                                          local_file_verified=True, runtime_healthy=True))
        candidate['technical_readiness'] = 'ready'
        candidate['technical_blockers'] = []
        self.assertTrue(is_runtime_ready(candidate, license_verified=True,
                                         local_file_verified=True, runtime_healthy=True))
        self.assertFalse(is_runtime_ready(candidate, license_verified=False,
                                          local_file_verified=True, runtime_healthy=True))

    def test_sync_check_detects_stale_and_dirty_consumer_without_mutating(self):
        commit = 'a' * 40
        self.assertTrue(check_allowlist_sync(commit, commit, True)['in_sync'])
        stale = check_allowlist_sync('b' * 40, commit, True)
        dirty = check_allowlist_sync(commit, commit, False)
        self.assertFalse(stale['in_sync'])
        self.assertFalse(dirty['in_sync'])
        self.assertFalse(stale['mutated'])
        self.assertFalse(dirty['mutated'])

    def test_repository_sync_check_is_read_only_and_detects_dirty_or_stale_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '-b', 'main', str(root)], check=True, capture_output=True)
            subprocess.run(['git', '-C', str(root), 'config', 'user.email', 'test@example.invalid'], check=True)
            subprocess.run(['git', '-C', str(root), 'config', 'user.name', 'Test'], check=True)
            target = root / 'manifests' / 'model-allowlist.json'
            target.parent.mkdir()
            target.write_text('{}\n', encoding='utf-8')
            subprocess.run(['git', '-C', str(root), 'add', 'manifests/model-allowlist.json'], check=True)
            subprocess.run(['git', '-C', str(root), 'commit', '-m', 'initial'], check=True, capture_output=True)
            head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
            subprocess.run(['git', '-C', str(root), 'update-ref', 'refs/remotes/origin/main', head], check=True)
            self.assertTrue(check_repository_sync(root)['in_sync'])
            target.write_text('{"local":true}\n', encoding='utf-8')
            before = target.read_text(encoding='utf-8')
            dirty = check_repository_sync(root)
            self.assertFalse(dirty['in_sync'])
            self.assertEqual(target.read_text(encoding='utf-8'), before)
            target.write_text('{}\n', encoding='utf-8')
            (root / 'other.txt').write_text('new commit\n', encoding='utf-8')
            subprocess.run(['git', '-C', str(root), 'add', 'other.txt'], check=True)
            subprocess.run(['git', '-C', str(root), 'commit', '-m', 'advance local'], check=True, capture_output=True)
            stale = check_repository_sync(root)
            self.assertFalse(stale['in_sync'])
            self.assertFalse(stale['mutated'])

    def test_load_rejects_invalid_json_and_missing_source_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bad.json'
            path.write_text('{broken', encoding='utf-8')
            with self.assertRaises(AllowlistError):
                load_allowlist(path, Path(tmp) / 'manifest.json')
        broken = copy.deepcopy(self.allowlist)
        broken['source_manifest'] = 'somewhere-else.json'
        with self.assertRaises(AllowlistError):
            validate_allowlist(broken, self.manifest)


if __name__ == '__main__':
    unittest.main(verbosity=2)
