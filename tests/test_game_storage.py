import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import league
import model_store
import server


class PerGameStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = league.DB
        self.old_managed = model_store.MANAGED_DIR
        league.DB = Path(self.tmp.name) / 'arena.sqlite3'
        model_store.MANAGED_DIR = Path(self.tmp.name) / 'managed-models'
        model_store.MANAGED_DIR.mkdir(mode=0o700)
        league.init()
        self.players = []
        for index, name in enumerate(('Alpha', 'Beta', 'Unused'), 1):
            payload = f'GGUF model {name}'.encode()
            filename = f'{name.lower()}.gguf'
            digest = hashlib.sha256(payload).hexdigest()
            with league.connect() as c:
                c.execute('INSERT INTO league_models(name,format,created_at) VALUES(?,?,?)',
                          (name, 'PURE_AI', 'now'))
                c.execute('''INSERT INTO model_versions(model_id,version_key,filename,sha256,size_bytes,source,license,status,managed_copy,license_verified,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                    (index, digest, filename, digest, len(payload), f'https://example.test/{filename}',
                     'Apache-2.0', 'catalogued', 0, 1, 'now'))
            self.players.append({'name': name, 'version_id': index})
        self.old_download = model_store.download_version

    def tearDown(self):
        league.DB = self.old_db
        model_store.MANAGED_DIR = self.old_managed
        self.tmp.cleanup()

    def _fake_download(self, version_id, url, filename, sha, size, license_verified):
        payload = f'GGUF model {self.players[version_id - 1]["name"]}'.encode()
        self.assertEqual(hashlib.sha256(payload).hexdigest(), sha)
        self.assertEqual(len(payload), size)
        path = model_store.MANAGED_DIR / filename
        path.write_bytes(payload)
        with league.connect() as c:
            c.execute("UPDATE model_versions SET status='downloaded',managed_copy=1 WHERE id=?", (version_id,))
        return {'path': str(path), 'filename': filename, 'managed_copy': True}

    def test_pair_downloads_only_needed_models_and_cleanup_is_per_game(self):
        calls = []
        def download(*args):
            calls.append(args[0])
            return self._fake_download(*args)
        with league.connect() as c:
            before = c.execute("SELECT COUNT(*) FROM model_versions WHERE status='catalogued' AND managed_copy=0").fetchone()[0]
        self.assertEqual(before, 3)
        with patch.object(model_store, 'download_version', side_effect=download), \
             patch('shutil.disk_usage') as disk:
            disk.return_value.free = 10**10
            white_assets, black_assets = server._prepare_game_models(self.players[0], self.players[1])
            white_path, black_path = white_assets['path'], black_assets['path']
        self.assertEqual(calls, [1, 2])
        self.assertTrue(Path(white_path).is_file())
        self.assertTrue(Path(black_path).is_file())
        self.assertFalse((model_store.MANAGED_DIR / 'unused.gguf').exists())
        server.cleanup_unneeded_models()
        self.assertFalse(Path(white_path).exists())
        self.assertFalse(Path(black_path).exists())
        with league.connect() as c:
            rows = c.execute('SELECT id,filename,status,managed_copy FROM model_versions ORDER BY id').fetchall()
        self.assertEqual([r['filename'] for r in rows], ['alpha.gguf', 'beta.gguf', 'unused.gguf'])
        self.assertTrue(all(r['status'] == 'catalogued' and r['managed_copy'] == 0 for r in rows))
        # Removing the local cache never removes tournament eligibility.
        tournament = league.create_round_robin('After cache cleanup')
        with league.connect() as c:
            entries = c.execute('SELECT COUNT(*) FROM tournament_entries WHERE tournament_id=?', (tournament['id'],)).fetchone()[0]
        self.assertEqual(entries, 3)

    def test_existing_pair_is_kept_without_redownload(self):
        for player in self.players[:2]:
            self._fake_download(player['version_id'], '', f"{player['name'].lower()}.gguf",
                                hashlib.sha256(f"GGUF model {player['name']}".encode()).hexdigest(),
                                len(f"GGUF model {player['name']}".encode()), True)
        with patch.object(model_store, 'download_version', side_effect=AssertionError('unexpected download')):
            with patch('shutil.disk_usage') as disk:
                disk.return_value.free = 10**10
                assets = server._prepare_game_models(self.players[0], self.players[1])
        self.assertTrue(all(Path(item['path']).is_file() for item in assets))

    def test_target_and_draft_are_downloaded_and_cleaned_as_one_configuration(self):
        target_payload=b'GGUF target bytes'; draft_payload=b'GGUF draft bytes'
        target_sha=hashlib.sha256(target_payload).hexdigest(); draft_sha=hashlib.sha256(draft_payload).hexdigest()
        paired=league.register_model('Alpha + MTP',source='https://huggingface.co/example/alpha',license='Apache-2.0',license_verified=True,
            metadata={'filename':'alpha-pair.gguf','sha256':target_sha,'size_bytes':len(target_payload),
                'revision':'a'*40,'source_path':'alpha-pair.gguf','download_url':'https://huggingface.co/example/alpha/resolve/'+'a'*40+'/alpha-pair.gguf',
                'draft_model':{'filename':'alpha-draft.gguf','sha256':draft_sha,'size_bytes':len(draft_payload),
                    'download_url':'https://huggingface.co/example/alpha/resolve/main/alpha-draft.gguf','source_path':'alpha-draft.gguf'}})
        player={'name':'Alpha + MTP','version_id':paired['versions'][0]['id']}
        def fake_target(version_id,url,filename,sha,size,approved):
            self.assertEqual((sha,size),(target_sha,len(target_payload)))
            path=model_store.MANAGED_DIR/filename; path.write_bytes(target_payload)
            with league.connect() as c: c.execute("UPDATE model_versions SET status='downloaded',managed_copy=1 WHERE id=?",(version_id,))
            return {'path':str(path)}
        def fake_draft(version_id,url,filename,sha,size,approved):
            self.assertEqual((sha,size),(draft_sha,len(draft_payload)))
            path=model_store.MANAGED_DIR/filename; path.write_bytes(draft_payload)
            with league.connect() as c: c.execute("UPDATE model_versions SET draft_status='downloaded',draft_managed_copy=1 WHERE id=?",(version_id,))
            return {'path':str(path)}
        with patch.object(model_store,'download_version',side_effect=fake_target), \
             patch.object(model_store,'download_draft_version',side_effect=fake_draft), \
             patch('shutil.disk_usage') as disk:
            disk.return_value.free=10**10
            assets=server._prepare_single_model(player)
        self.assertTrue(Path(assets['path']).is_file())
        self.assertTrue(Path(assets['draft_path']).is_file())
        with patch.object(model_store,'_open_by_process',return_value=([],False)):
            server.cleanup_unneeded_models()
        self.assertFalse(Path(assets['path']).exists())
        self.assertFalse(Path(assets['draft_path']).exists())
        with league.connect() as c:
            row=c.execute('SELECT filename,draft_filename,license_verified,managed_copy,draft_managed_copy FROM model_versions WHERE id=?',(player['version_id'],)).fetchone()
        self.assertEqual(row['filename'],'alpha-pair.gguf')
        self.assertEqual(row['draft_filename'],'alpha-draft.gguf')
        self.assertEqual(row['license_verified'],1)
        self.assertEqual((row['managed_copy'],row['draft_managed_copy']),(0,0))

    def test_existing_same_size_model_is_rejected_when_sha256_changed(self):
        player = self.players[0]
        original = f'GGUF model {player["name"]}'.encode()
        self._fake_download(player['version_id'], '', f"{player['name'].lower()}.gguf",
                            hashlib.sha256(original).hexdigest(), len(original), True)
        path = model_store.MANAGED_DIR / f"{player['name'].lower()}.gguf"
        path.write_bytes(b'X' * len(original))
        with patch.object(model_store, 'download_version', side_effect=AssertionError('must not redownload')):
            with self.assertRaisesRegex(RuntimeError, 'SHA-256/rozmiaru'):
                server._ensure_version_file(player['version_id'])

    def test_space_check_is_for_missing_models_in_this_game(self):
        with patch('shutil.disk_usage') as disk, \
             patch.object(model_store, 'download_version') as download:
            disk.return_value.free = 1
            with self.assertRaises(OSError):
                server._prepare_game_models(self.players[0], self.players[1])
        download.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
