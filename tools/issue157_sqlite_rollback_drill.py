#!/usr/bin/env python3
"""Disposable SQLite WAL/sidecar backup and restore drill for Issue #157 B2."""
from __future__ import annotations
import argparse, hashlib, json, os, signal, sqlite3, subprocess, sys
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

def connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{quote(str(path), safe='/')}?mode=ro", uri=True, timeout=10)

def online_backup(source: Path, destination: Path) -> None:
    with closing(connect_readonly(source)) as src, closing(sqlite3.connect(destination, timeout=10)) as dst:
        src.backup(dst, pages=128, sleep=0.01)

def integrity(path: Path) -> dict[str, object]:
    with closing(connect_readonly(path)) as db:
        return {"integrity_check": db.execute("PRAGMA integrity_check").fetchone()[0],
                "foreign_key_violations": len(db.execute("PRAGMA foreign_key_check").fetchall()),
                "journal_mode": db.execute("PRAGMA journal_mode").fetchone()[0]}

def logical_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with closing(connect_readonly(path)) as db:
        for line in db.iterdump(): digest.update(line.encode("utf-8") + b"\n")
    return digest.hexdigest()

def assert_clean(path: Path, label: str) -> dict[str, object]:
    result = integrity(path)
    if result["integrity_check"] != "ok" or result["foreign_key_violations"] != 0:
        raise RuntimeError(f"{label} failed integrity/FK checks: {result}")
    return result

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    source, root = args.source.absolute(), args.root.absolute()
    if not source.is_file() or source.is_symlink(): raise SystemExit("source must be a regular non-symlink file")
    if root.parent != Path('/var/tmp') or root.exists() or root.is_symlink(): raise SystemExit("root must be a new direct child of /var/tmp")
    if root in source.parents: raise SystemExit("source cannot be inside disposable root")
    root.mkdir(mode=0o700); os.chmod(root, 0o700)
    baseline, wal_db, restore_point = (root / n for n in ("baseline.sqlite3", "wal-live.sqlite3", "restore-point.sqlite3"))
    report = {"source":str(source),"root":str(root),"python":sys.version.split()[0],"sqlite":sqlite3.sqlite_version}
    try:
        online_backup(source, baseline); os.chmod(baseline,0o600)
        report["baseline"] = assert_clean(baseline,"baseline online backup")
        report["baseline_logical_sha256"] = logical_digest(baseline)
        online_backup(baseline,wal_db); os.chmod(wal_db,0o600)
        writer=sqlite3.connect(wal_db,timeout=10)
        if writer.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower()!='wal': raise RuntimeError("could not enable WAL")
        writer.execute("CREATE TABLE b2_drill_probe (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        writer.execute("INSERT INTO b2_drill_probe VALUES (1,'baseline')"); writer.commit()
        reader=sqlite3.connect(wal_db,timeout=10); reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM b2_drill_probe").fetchone()
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE b2_drill_probe SET value='committed-before-backup' WHERE id=1"); writer.commit()
        wal_path,shm_path=Path(str(wal_db)+'-wal'),Path(str(wal_db)+'-shm')
        report["sidecars_while_reader_pinned"]={"wal_exists":wal_path.exists(),"shm_exists":shm_path.exists(),"wal_bytes":wal_path.stat().st_size if wal_path.exists() else 0}
        if not wal_path.exists() or not shm_path.exists(): raise RuntimeError("expected both WAL sidecars with pinned reader")
        online_backup(wal_db,restore_point); os.chmod(restore_point,0o600)
        with closing(connect_readonly(restore_point)) as check: captured=check.execute("SELECT value FROM b2_drill_probe WHERE id=1").fetchone()[0]
        if captured!='committed-before-backup': raise RuntimeError(f"online backup omitted committed WAL data: {captured!r}")
        report["backup_with_live_sidecars"]={**assert_clean(restore_point,"WAL backup"),"captured_value":captured,"logical_sha256":logical_digest(restore_point)}
        reader.rollback(); reader.close(); writer.close()
        with closing(sqlite3.connect(wal_db,timeout=10)) as mutate:
            mutate.execute("UPDATE b2_drill_probe SET value='mutated-after-backup' WHERE id=1")
            mutate.execute("INSERT INTO b2_drill_probe VALUES (2,'must-disappear-on-rollback')")
            mutate.commit()
        mutated_digest=logical_digest(wal_db)
        report["mutated_clone"]={"logical_sha256":mutated_digest,"sidecars_after_writer_close":{"wal_exists":wal_path.exists(),"shm_exists":shm_path.exists()}}
        before_restore_sidecars={"wal_exists":wal_path.exists(),"shm_exists":shm_path.exists()}
        if mutated_digest==logical_digest(restore_point): raise RuntimeError("mutation did not change the disposable DB")
        if not all(before_restore_sidecars.values()): raise RuntimeError("expected WAL/SHM sidecars to remain before restore")
        with closing(connect_readonly(restore_point)) as src, closing(sqlite3.connect(wal_db,timeout=10)) as dst: src.backup(dst,pages=128,sleep=0.01)
        restored_digest=logical_digest(wal_db)
        if restored_digest!=logical_digest(restore_point): raise RuntimeError("restored logical DB differs from restore point")
        with closing(connect_readonly(wal_db)) as restored: rows=restored.execute("SELECT id,value FROM b2_drill_probe ORDER BY id").fetchall()
        if rows!=[(1,'committed-before-backup')]: raise RuntimeError(f"rollback rows mismatch: {rows!r}")
        report["rollback_restore"]={**assert_clean(wal_db,"restored WAL database"),"sidecars_before_restore":before_restore_sidecars,"sidecars_after_restore":{"wal_exists":wal_path.exists(),"shm_exists":shm_path.exists()},"logical_sha256":restored_digest,"rows":rows,"matches_restore_point":True}
        child_code = ("import sqlite3,sys,time; c=sqlite3.connect(sys.argv[1],timeout=10); "
                      "c.execute('BEGIN IMMEDIATE'); c.execute(\"INSERT INTO b2_drill_probe VALUES (3,'uncommitted-must-vanish')\"); "
                      "print('READY',flush=True); time.sleep(60)")
        child = subprocess.Popen([sys.executable, "-c", child_code, str(wal_db)], stdout=subprocess.PIPE, text=True)
        try:
            ready = child.stdout.readline().strip() if child.stdout else ""
            if ready != "READY": raise RuntimeError(f"crash-test child did not reach transaction: {ready!r}")
            os.kill(child.pid, signal.SIGKILL)
            child.wait(timeout=10)
        finally:
            if child.poll() is None:
                child.kill(); child.wait(timeout=10)
        with closing(connect_readonly(wal_db)) as reopened: leaked=reopened.execute("SELECT COUNT(*) FROM b2_drill_probe WHERE id=3").fetchone()[0]
        if leaked: raise RuntimeError("uncommitted transaction survived SIGKILL/reopen")
        report["uncommitted_transaction_recovery"]={"child_exit_code":child.returncode,"termination":"SIGKILL","uncommitted_row_visible_after_reopen":leaked,**assert_clean(wal_db,"reopened database")}
        report["result"]="PASS"
        out=root/"evidence.json"; out.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8"); os.chmod(out,0o600)
        print(json.dumps(report,indent=2,sort_keys=True)); return 0
    except Exception as exc:
        report.update(result="FAIL",error=f"{type(exc).__name__}: {exc}")
        out=root/"evidence.json"; out.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8"); os.chmod(out,0o600)
        print(json.dumps(report,indent=2,sort_keys=True),file=sys.stderr); return 1

if __name__=='__main__': raise SystemExit(main())
