import hashlib
import json
import sqlite3

import pytest
import chess
from scripts.phone_sandbox_preflight import check_processes

from scripts.phone_sandbox_preflight import check_database


@pytest.fixture
def database(tmp_path):
    p = tmp_path / "arena.sqlite3"
    with sqlite3.connect(p) as c:
        c.executescript("""
        CREATE TABLE tournaments(id INTEGER,status TEXT,resume_blocked INTEGER);
        CREATE TABLE tournament_pairings(id INTEGER,tournament_id INTEGER,status TEXT,white_model_id INTEGER,black_model_id INTEGER,stage TEXT);
        CREATE TABLE tournament_checkpoints(tournament_id INTEGER,schema_version INTEGER,payload_json TEXT,sha256 TEXT);
        CREATE TABLE worker_job_safety(state TEXT,job_id TEXT);
        INSERT INTO tournaments VALUES(7,'stopped',0);
        INSERT INTO tournament_pairings VALUES(31,7,'running',1,5,'league');
        INSERT INTO worker_job_safety VALUES('idle_confirmed',NULL);
        """)
        for table in ("worker_inference_assignments", "worker_inference_results",
                      "worker_update_pause_requests", "worker_update_resume", "worker_update_holds"):
            c.execute("CREATE TABLE " + table + "(id INTEGER)")
        payload = json.dumps(dict(schema_version=1,tournament_id=7,phase="game",moves=[],move_times=[],fen=chess.STARTING_FEN,side_to_move="white",pairing={"id":31,"white_model_id":1,"black_model_id":5,"stage":"league"}))
        c.execute("INSERT INTO tournament_checkpoints VALUES(7,1,?,?)",(payload,hashlib.sha256(payload.encode()).hexdigest()))
    return p


def test_stopped_read_only(database):
    before = database.read_bytes()
    check_database(database)
    assert database.read_bytes() == before


@pytest.mark.parametrize("sql", [
    "UPDATE tournaments SET status='running'",
    "UPDATE tournaments SET status='unknown'",
    "UPDATE tournaments SET resume_blocked=1",
    "DELETE FROM tournament_checkpoints",
    "UPDATE tournament_checkpoints SET sha256='bad'",
    "UPDATE tournament_pairings SET id=32",
    "UPDATE worker_job_safety SET state='running'",
    "UPDATE worker_job_safety SET job_id='job'",
    *["INSERT INTO " + table + " VALUES(1)" for table in (
        "worker_inference_assignments","worker_inference_results",
        "worker_update_pause_requests","worker_update_resume","worker_update_holds")],
])
def test_refuses_unsafe(database,sql):
    with sqlite3.connect(database) as c:
        c.execute(sql)
    with pytest.raises(ValueError):
        check_database(database)

def set_payload(database, change):
    with sqlite3.connect(database) as c:
        p = json.loads(c.execute("SELECT payload_json FROM tournament_checkpoints").fetchone()[0])
        change(p)
        encoded = json.dumps(p)
        c.execute("UPDATE tournament_checkpoints SET payload_json=?,sha256=?",(encoded,hashlib.sha256(encoded.encode()).hexdigest()))


@pytest.mark.parametrize("change", [
    lambda p: p.update(fen="invalid"),
    lambda p: p.update(side_to_move="black"),
    lambda p: p.update(variant="unknown"),
    lambda p: p.update(move_times=[1]),
    lambda p: p["pairing"].update(white_model_id=999),
    lambda p: p.update(moves=[dict(ply=1,color="white",uci="e2e5",fen="bad")],move_times=[1]),
])
def test_valid_hash_is_not_enough(database, change):
    set_payload(database, change)
    with pytest.raises(ValueError):
        check_database(database)


def test_replay_one_move(database):
    board=chess.Board()
    board.push_uci("e2e4")
    set_payload(database, lambda p:p.update(moves=[dict(ply=1,color="white",uci="e2e4",fen=board.fen())],move_times=[1],fen=board.fen(),side_to_move="black"))
    check_database(database)


@pytest.mark.parametrize("state,pids,engine,ok", [
    ("active","123",None,True),
    ("inactive","123",None,False),
    ("active","123 456",None,False),
    ("active","123","llama-server",False),
    ("active","",None,False),
])
def test_process_guard(tmp_path,monkeypatch,state,pids,engine,ok):
    import subprocess
    group=tmp_path/"cg"/"system.slice"/"ai-chess-arena.service"
    group.mkdir(parents=True)
    (group/"cgroup.procs").write_text(pids)
    proc=tmp_path/"proc"
    (proc/"123").mkdir(parents=True)
    (proc/"123"/"comm").write_text("python")
    if engine:
        (proc/"999").mkdir()
        (proc/"999"/"comm").write_text(engine)
    monkeypatch.setattr(subprocess,"check_output",lambda *a,**k:f"MainPID=123\nControlGroup=/system.slice/ai-chess-arena.service\nActiveState={state}\n")
    if ok:
        check_processes(tmp_path/"cg",proc)
    else:
        with pytest.raises(ValueError):
            check_processes(tmp_path/"cg",proc)
