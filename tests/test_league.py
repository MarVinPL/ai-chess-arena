import tempfile,unittest,sqlite3
from pathlib import Path
import league

class LeagueTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.old=league.DB; league.DB=Path(self.tmp.name)/'league.sqlite3'; league.init()
  with league.connect() as c:
   c.execute('CREATE TABLE games(id INTEGER PRIMARY KEY,white_model TEXT,black_model TEXT,result TEXT)')
   c.execute("INSERT INTO league_models(name,format,created_at) VALUES('Alpha','PURE_AI','now')")
   c.execute("INSERT INTO league_models(name,format,created_at) VALUES('Beta','PURE_AI','now')")
   c.execute("INSERT INTO model_versions(model_id,version_key,filename,sha256,size_bytes,source,license,license_verified,created_at) VALUES(1,'alpha-v1','alpha.gguf',? ,100,'https://example.test/alpha','Apache-2.0',1,'now')", ('a'*64,))
   c.execute("INSERT INTO model_versions(model_id,version_key,filename,sha256,size_bytes,source,license,license_verified,created_at) VALUES(2,'beta-v1','beta.gguf',?,100,'https://example.test/beta','Apache-2.0',1,'now')", ('b'*64,))
 def tearDown(self):
  league.DB=self.old; self.tmp.cleanup()
 def test_round_robin_has_two_legs_per_pair(self):
  t=league.create_round_robin('Test Cup')
  self.assertEqual(t['games_planned'],2)
  with league.connect() as c:
   self.assertEqual(c.execute('SELECT COUNT(*) FROM tournament_pairings').fetchone()[0],2)
   self.assertEqual(c.execute('SELECT COUNT(*) FROM tournament_entries').fetchone()[0],2)
   self.assertEqual(c.execute('SELECT COUNT(*) FROM tournament_pairings WHERE white_model_id=black_model_id').fetchone()[0],0)
   pairings=c.execute('SELECT white_model_id,black_model_id FROM tournament_pairings').fetchall()
   self.assertTrue(all(row['white_model_id'] != row['black_model_id'] for row in pairings))

 def test_ten_entrants_all_reach_draft_and_bracket_advances_to_champion(self):
  for i in range(8):
   league.register_model(f'Extra {i}',source=f'https://example.test/extra-{i}',license='Apache-2.0',
    license_verified=True,metadata={'filename':f'extra-{i}.gguf','sha256':format(i+10,'064x'),'size_bytes':100})
  tournament=league.create_round_robin('Ten player championship')
  self.assertEqual(tournament['players'],10)
  self.assertEqual(tournament['games_planned'],90)
  self.assertEqual(tournament['draft_games_planned'],7)
  self.assertEqual(tournament['total_games_planned'],97)
  with league.connect() as c:
   pairings=c.execute("SELECT id,white_model_id,black_model_id FROM tournament_pairings WHERE tournament_id=? AND stage='league'",(tournament['id'],)).fetchall()
   for p in pairings:
    cur=c.execute("INSERT INTO games(white_model,black_model,result) VALUES(?,?,?)",
     (f'Model {p["white_model_id"]}',f'Model {p["black_model_id"]}','1-0'))
    c.execute("UPDATE tournament_pairings SET status='completed',game_id=? WHERE id=?",(cur.lastrowid,p['id']))
  first=league.next_draft_match(tournament['id'])
  self.assertIsNotNone(first)
  with league.connect() as c:
   self.assertEqual(c.execute("SELECT COUNT(*) FROM tournament_seeds WHERE tournament_id=?",(tournament['id'],)).fetchone()[0],10)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM tournament_pairings WHERE tournament_id=? AND stage='draft' AND round_no=0",(tournament['id'],)).fetchone()[0],2)
  # Both play-in winners advance; the top six get byes into the eight-player bracket.
  current=first
  while current:
   league.record_draft_result(current['id'],'1-0')
   current=league.next_draft_match(tournament['id'])
  champion=league.draft_champion(tournament['id'])
  self.assertIsNotNone(champion)
  with league.connect() as c:
   self.assertEqual(c.execute("SELECT COUNT(*) FROM tournament_pairings WHERE tournament_id=? AND stage='draft'",(tournament['id'],)).fetchone()[0],9)
   self.assertEqual(c.execute("SELECT COUNT(*) FROM tournament_pairings WHERE tournament_id=? AND stage='draft' AND white_model_id=black_model_id",(tournament['id'],)).fetchone()[0],0)

 def test_draft_qualification_keeps_all_winners_then_best_remaining(self):
  for i in range(8):
   league.register_model(f'Extra {i}',source=f'https://example.test/extra-{i}',license='Apache-2.0',
    license_verified=True,metadata={'filename':f'extra-{i}.gguf','sha256':format(i+10,'064x'),'size_bytes':100})
  tournament=league.create_round_robin('Selective draft',qualification_percent=80)
  self.assertEqual(tournament['expected_qualifiers'],8)
  with league.connect() as c:
   pairings=c.execute("SELECT id,white_model_id,black_model_id FROM tournament_pairings WHERE tournament_id=? AND stage='league'",(tournament['id'],)).fetchall()
   for p in pairings:
    w,b=p['white_model_id'],p['black_model_id']
    if w<=4 and b<=4: result='1-0'
    elif w<=4: result='1-0'
    elif b<=4: result='0-1'
    else: result='1/2-1/2'
    cur=c.execute("INSERT INTO games(white_model,black_model,result) VALUES(?,?,?)",(f'Model {w}',f'Model {b}',result))
    c.execute("UPDATE tournament_pairings SET status='completed',game_id=? WHERE id=?",(cur.lastrowid,p['id']))
  first=league.next_draft_match(tournament['id'])
  self.assertIsNotNone(first)
  with league.connect() as c:
   qualified={r['model_id'] for r in c.execute("SELECT model_id FROM tournament_seeds WHERE tournament_id=?",(tournament['id'],))}
  self.assertEqual(qualified,{1,2,3,4,5,6,7,8})
  self.assertEqual(len(qualified),8)

 def test_draft_qualification_percent_must_be_between_75_and_90(self):
  for invalid in (74,91,True,80.0):
   with self.assertRaises(ValueError):
    league.create_round_robin('Invalid qualification',qualification_percent=invalid)

 def test_catalogued_models_are_tournament_eligible_without_local_gguf(self):
  tournament=league.create_round_robin('Remote cache test')
  with league.connect() as c:
   rows=c.execute('''SELECT v.status,v.managed_copy,v.filename,v.sha256,v.size_bytes
     FROM tournament_entries e JOIN model_versions v ON v.id=e.version_id
     WHERE e.tournament_id=? ORDER BY e.model_id''',(tournament['id'],)).fetchall()
  self.assertEqual(len(rows),2)
  self.assertTrue(all(r['status']=='catalogued' and r['managed_copy']==0 for r in rows))
  self.assertTrue(all(r['filename'] and r['sha256'] and r['size_bytes'] for r in rows))

 def test_registration_stores_remote_manifest_without_downloading_file(self):
  result=league.register_model('Remote GGUF',source='https://huggingface.co/example/model',
   license='Apache-2.0',license_verified=True,
   metadata={'filename':'remote.gguf','sha256':'c'*64,'size_bytes':12345})
  version=result['versions'][0]
  self.assertEqual(version['status'],'catalogued')
  self.assertEqual(version['managed_copy'],0)
  self.assertEqual((version['filename'],version['sha256'],version['size_bytes']),('remote.gguf','c'*64,12345))

 def test_registration_rejects_incomplete_remote_manifest(self):
  with self.assertRaises(ValueError):
   league.register_model('Incomplete GGUF',source='https://huggingface.co/example/model',
    license='Apache-2.0',metadata={'filename':'only-name.gguf'})
 def test_duplicate_upstream_filename_gets_content_specific_cache_name(self):
  first=league.register_model('Remote One',source='https://huggingface.co/example/one',license='Apache-2.0',
   metadata={'filename':'model.gguf','sha256':'1'*64,'size_bytes':100},license_verified=True)
  second=league.register_model('Remote Two',source='https://huggingface.co/example/two',license='Apache-2.0',
   metadata={'filename':'model.gguf','sha256':'2'*64,'size_bytes':200},license_verified=True)
  a=first['versions'][0]; b=second['versions'][0]
  self.assertEqual(a['filename'],'model.gguf')
  self.assertEqual(b['filename'],'model-'+'2'*16+'.gguf')
  import json
  self.assertEqual(json.loads(b['metadata_json'])['source_path'],'model.gguf')
 def test_elo_starts_at_1200_without_games(self):
  rows=league.elo_ratings()
  self.assertEqual({x['name']:x['elo'] for x in rows},{'Alpha':1200,'Beta':1200})
 def test_formats_are_separate(self):
  with league.connect() as c:
   c.execute("INSERT INTO league_models(name,format,created_at) VALUES('Engine','AI_ENGINE','now')")
  self.assertEqual(len(league.elo_ratings('AI_ENGINE')),1)
 def test_elo_changes_after_a_decisive_game(self):
  with league.connect() as c:
   c.execute("INSERT INTO games(white_model,black_model,result) VALUES('Alpha','Beta','1-0')")
  rows={x['name']:x for x in league.elo_ratings()}
  self.assertEqual(rows['Alpha']['elo'],1216)
  self.assertEqual(rows['Beta']['elo'],1184)
  self.assertEqual((rows['Alpha']['wins'],rows['Beta']['losses']),(1,1))
 def test_model_file_is_hashed_but_not_auto_approved(self):
  old=league.MODEL_DIR
  try:
   league.MODEL_DIR=Path(self.tmp.name)/'managed-models'; league.MODEL_DIR.mkdir()
   (league.MODEL_DIR/'gamma.gguf').write_bytes(b'GGUF test bytes')
   result=league.register_model('Gamma','gamma.gguf',source='test source',license='test license')
   version=result['versions'][0]
   self.assertEqual(version['size_bytes'],15)
   self.assertEqual(len(version['sha256']),64)
   self.assertEqual(version['license_verified'],0)
  finally: league.MODEL_DIR=old
 def test_unverified_license_is_not_approved(self):
  with league.connect() as c:
   cols={r[1] for r in c.execute('PRAGMA table_info(model_versions)')}
  self.assertIn('license_verified',cols)
  with league.connect() as c:
   c.execute("INSERT INTO model_versions(model_id,version_key,license,created_at) VALUES(1,'test-sha','UNVERIFIED','now')")
   v=c.execute("SELECT license_verified FROM model_versions WHERE version_key='test-sha'").fetchone()[0]
  self.assertEqual(v,0)
 def test_registration_cannot_approve_unknown_license(self):
  for license_name in ('unknown','nieustalona','n/a','unverified',''):
   with self.subTest(license=license_name):
    with self.assertRaises(ValueError):
     league.register_model('Blocked '+(license_name or 'empty'),source='https://huggingface.co/example/model',license=license_name,license_verified=True)
 def test_registration_cannot_approve_without_https_source(self):
  with self.assertRaises(ValueError):
   league.register_model('Blocked Source',source='http://example.test/model',license='Apache-2.0',license_verified=True)
 def test_target_solo_and_target_with_draft_are_distinct_competitors(self):
  paired=league.register_model('Alpha target + MTP',source='https://huggingface.co/example/alpha',license='Apache-2.0',license_verified=True,
   metadata={'filename':'alpha-pair.gguf','sha256':'a'*64,'size_bytes':100,
    'draft_model':{'filename':'alpha-draft.gguf','sha256':'c'*64,'size_bytes':25,
     'download_url':'https://huggingface.co/example/alpha/resolve/main/alpha-draft.gguf','source_path':'alpha-draft.gguf'}})
  tournament=league.create_round_robin('Target variants')
  with league.connect() as c:
   entries=c.execute('''SELECT m.name,v.sha256,v.draft_sha256 FROM tournament_entries e
    JOIN league_models m ON m.id=e.model_id JOIN model_versions v ON v.id=e.version_id
    WHERE e.tournament_id=? ORDER BY m.name''',(tournament['id'],)).fetchall()
  self.assertEqual(len(entries),3)
  configs={(r['sha256'],r['draft_sha256'] or '') for r in entries}
  self.assertIn(('a'*64,''),configs)
  self.assertIn(('a'*64,'c'*64),configs)
  self.assertTrue(any(r['name']=='Alpha target + MTP' for r in entries))
  self.assertEqual(paired['versions'][0]['draft_filename'],'alpha-draft.gguf')

 def test_identical_weights_under_different_names_are_not_two_competitors(self):
  with league.connect() as c:
   c.execute("UPDATE model_versions SET sha256=? WHERE model_id=2", ('a'*64,))
  with self.assertRaisesRegex(ValueError, 'dwóch różnych modeli'):
   league.create_round_robin('Duplicate weights')

 def test_single_player_tournament_rejected(self):
  with league.connect() as c: c.execute("UPDATE league_models SET active=0 WHERE name='Beta'")
  with self.assertRaises(ValueError): league.create_round_robin('Invalid')
 def test_unverified_model_cannot_enter_tournament(self):
  with league.connect() as c: c.execute("UPDATE model_versions SET license_verified=0 WHERE model_id=2")
  with self.assertRaises(ValueError): league.create_round_robin('Blocked')
if __name__=='__main__': unittest.main(verbosity=2)
