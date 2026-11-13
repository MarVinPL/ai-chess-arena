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
   c.execute("INSERT INTO model_versions(model_id,version_key,license,license_verified,created_at) VALUES(1,'alpha-v1','test-license',1,'now')")
   c.execute("INSERT INTO model_versions(model_id,version_key,license,license_verified,created_at) VALUES(2,'beta-v1','test-license',1,'now')")
 def tearDown(self):
  league.DB=self.old; self.tmp.cleanup()
 def test_round_robin_has_two_legs_per_pair(self):
  t=league.create_round_robin('Test Cup')
  self.assertEqual(t['games_planned'],2)
  with league.connect() as c:
   self.assertEqual(c.execute('SELECT COUNT(*) FROM tournament_pairings').fetchone()[0],2)
   self.assertEqual(c.execute('SELECT COUNT(*) FROM tournament_entries').fetchone()[0],2)
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
 def test_single_player_tournament_rejected(self):
  with league.connect() as c: c.execute("UPDATE league_models SET active=0 WHERE name='Beta'")
  with self.assertRaises(ValueError): league.create_round_robin('Invalid')
 def test_unverified_model_cannot_enter_tournament(self):
  with league.connect() as c: c.execute("UPDATE model_versions SET license_verified=0 WHERE model_id=2")
  with self.assertRaises(ValueError): league.create_round_robin('Blocked')
if __name__=='__main__': unittest.main(verbosity=2)
