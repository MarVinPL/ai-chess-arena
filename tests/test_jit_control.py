import sqlite3
import threading
import pytest
import server
import league
import passkey_test
import chess

@pytest.fixture
def arena(tmp_path, monkeypatch):
    db=tmp_path/'arena.sqlite3'
    for module in (server, league):
        monkeypatch.setattr(module,'DB',db)
    for key,value in {
        'LOG_DIR':tmp_path/'logs','MODEL_RESPONSES_LOG':tmp_path/'responses.jsonl',
        'LIVE_EVENTS_LOG':tmp_path/'events.jsonl','tournament_runtime':None,
        'tournament_thread':None,'tournament_stop_event':threading.Event(),
        'tournament_error':'','cleanup_unneeded_models':lambda *a,**k:None,
        'tournament_preflight':lambda *a:{'ready':True,'entrants':[1,2,3],'runtime_available':True},
        '_prepare_single_model':lambda player,**k:{'path':player['name']},
        '_prepare_game_models':lambda white,black,**k:({'path':white['name']},{'path':black['name']})
    }.items():
        monkeypatch.setattr(server,key,value)
    events=[]
    monkeypatch.setattr(server,'set_live',lambda stage,message,**fields:events.append((stage,fields)))
    server.db_init()
    for name, digest in [('Alpha','a'*64),('Beta','b'*64),('Gamma','c'*64)]:
        league.register_model(name,source=f'https://example.test/{name}.gguf',license='Apache-2.0',
            license_verified=True,metadata={'filename':name+'.gguf','sha256':digest,'size_bytes':100})
    tid=league.create_round_robin('JIT fixture')['id']
    class Runtime:
        def request_cancel(self): pass
        def stop(self): pass
        def shutdown(self): pass
    monkeypatch.setattr(server.runtime_factory,'create_runtime',lambda **k:Runtime())
    yield db,tid,events

def test_first_game_precedes_third_model_test_and_resume_preserves_answers(arena,monkeypatch):
    db,tid,events=arena
    calls=[]
    def answer(runtime,path,question,event,**kw):
        calls.append(('test',path))
        return {'status':'answered','answer':'fixture','seconds':0.1}
    monkeypatch.setattr(passkey_test,'ask_passkey_question',answer)
    class Runner:
        def __init__(self,runtime): pass
        def play_game(self,white,black,event,**kw):
            calls.append(('game',white['name'],black['name']))
            event.set()
            return {'status':'stopped'}
    monkeypatch.setattr(server.engine_runner,'SequentialChessRunner',Runner)
    for _ in range(2):
        server.start_tournament(tid)
        server.tournament_thread.join(5)
        assert not server.tournament_thread.is_alive()
        with sqlite3.connect(db) as c:
            assert c.execute('select status from tournaments where id=?',(tid,)).fetchone()[0]=='stopped',server.tournament_error
            assert c.execute('select count(*) from passkey_test_answers where tournament_id=?',(tid,)).fetchone()[0]==6
    first_game=next(x for x in calls if x[0]=='game')
    tested=[x[1] for x in calls if x[0]=='test']
    assert set(tested)==set(first_game[1:])
    assert len(tested)==6
    assert calls[-2:]==[first_game,first_game]
    assert all(f.get('pairing_id') and f.get('white') and f.get('black') for stage,f in events if stage=='passkey_question')
    assert server.tournament_checkpoint.load(db,tid) is not None

@pytest.mark.parametrize('result_status',['stopped','failed',None])
def test_incomplete_control_answer_never_starts_game(arena,monkeypatch,result_status):
    db,tid,events=arena
    calls=[]
    def answer(*a,**kw):
        calls.append('test')
        return {'status':result_status,'answer':'','seconds':None}
    monkeypatch.setattr(passkey_test,'ask_passkey_question',answer)
    class Runner:
        def __init__(self,runtime): pass
        def play_game(self,*a,**kw):
            calls.append('game')
            raise AssertionError('Incomplete control test must not start game')
    monkeypatch.setattr(server.engine_runner,'SequentialChessRunner',Runner)
    server.start_tournament(tid)
    server.tournament_thread.join(5)
    assert not server.tournament_thread.is_alive()
    assert calls==['test']
    assert 'Test kontrolny modelu nie został ukończony' in server.tournament_error
    with sqlite3.connect(db) as c:
        assert c.execute('select status from tournaments where id=?',(tid,)).fetchone()[0]=='failed'
        assert c.execute('select count(*) from passkey_test_answers where tournament_id=?',(tid,)).fetchone()[0]==0
        assert c.execute("select count(*) from tournament_pairings where tournament_id=? and status!='pending'",(tid,)).fetchone()[0]==0
    assert server.tournament_checkpoint.load(db,tid) is None
