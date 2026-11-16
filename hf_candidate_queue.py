"""Persistent, sequential metadata-only Hugging Face candidate audit queue."""
from __future__ import annotations
import json, shutil, threading, time
from pathlib import Path
import league
import hf_candidate_audit
import hf_candidate_downloads

LOCK = threading.RLock()
WORKER_LOCK = threading.Lock()
_STARTED = False
REFRESH_SECONDS = 15 * 60
POLL_SECONDS = 2


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _init(c):
    c.execute('''CREATE TABLE IF NOT EXISTS hf_audit_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT, catalog_id TEXT NOT NULL, revision TEXT NOT NULL,
        repo_id TEXT NOT NULL, display_name TEXT NOT NULL, status TEXT NOT NULL,
        queued_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, error TEXT,
        report_json TEXT, UNIQUE(catalog_id, revision))''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_hf_audit_queue_status ON hf_audit_queue(status,id)')


def refresh_queue():
    """Queue only candidates without an audit report for their pinned revision."""
    catalog = hf_candidate_audit.load_catalog()
    added = 0
    with league.connect() as c:
        _init(c)
        for item in catalog:
            catalog_id = item.get('catalog_id')
            revision = item.get('revision') or item.get('audited_revision')
            if not catalog_id or not revision or item.get('source_status') not in {'active','not_checked'}:
                continue
            if hf_candidate_audit.get_report(c, catalog_id, revision) is not None:
                continue
            existing = c.execute('SELECT status FROM hf_audit_queue WHERE catalog_id=? AND revision=?', (catalog_id,revision)).fetchone()
            if existing:
                continue
            c.execute('''INSERT INTO hf_audit_queue(catalog_id,revision,repo_id,display_name,status,queued_at)
                VALUES(?,?,?,?,?,?)''',(catalog_id,revision,item.get('repo_id',''),item.get('display_name') or item.get('repo_id',''), 'queued',_now()))
            added += 1
    return {'added':added,'queue':snapshot()}


def snapshot():
    with league.connect() as c:
        _init(c)
        rows = c.execute('''SELECT id,catalog_id,revision,repo_id,display_name,status,queued_at,started_at,finished_at,error
            FROM hf_audit_queue ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END, id''').fetchall()
        counts = {row['status']: row['n'] for row in c.execute('SELECT status,COUNT(*) n FROM hf_audit_queue GROUP BY status')}
    return {'items':[dict(row) for row in rows], 'counts':counts,
            'active':sum(counts.get(k,0) for k in ('queued','running')),
            'running':counts.get('running',0)}


def hardware_snapshot():
    """Return host resource readings; unavailable metrics are explicitly null."""
    memory = {}
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            key, value = line.split(':',1)
            parts=value.strip().split()
            if parts and parts[0].isdigit(): memory[key]=int(parts[0])*1024
    except (OSError, ValueError):
        pass
    def gib(value): return round(value/(1024**3),2) if value is not None else None
    try:
        probe=hf_candidate_downloads.STAGING_DIR
        while not probe.exists() and probe != probe.parent: probe=probe.parent
        disk=shutil.disk_usage(probe)
        disk_data={'total_gib':gib(disk.total),'used_gib':gib(disk.used),'free_gib':gib(disk.free),'path':str(hf_candidate_downloads.STAGING_DIR),'measured_at':str(probe)}
    except OSError as exc:
        disk_data={'total_gib':None,'used_gib':None,'free_gib':None,'path':str(hf_candidate_downloads.STAGING_DIR),'error':str(exc)}
    # No CUDA/NVIDIA command is executed. VRAM is reported only from procfs when present.
    vram=None
    nvidia_path=Path('/proc/driver/nvidia/gpus')
    if nvidia_path.exists():
        vram={'status':'detected_device_details_unavailable','total_gib':None,'free_gib':None}
    else:
        vram={'status':'not_available','total_gib':None,'free_gib':None}
    return {'captured_at':_now(), 'ram':{'total_gib':gib(memory.get('MemTotal')),'available_gib':gib(memory.get('MemAvailable')),'free_gib':gib(memory.get('MemFree'))},
            'swap':{'total_gib':gib(memory.get('SwapTotal')),'free_gib':gib(memory.get('SwapFree'))},'disk':disk_data,'vram':vram,
            'runtime_tests_enabled':False,'runtime_active':0}


def _run_one():
    if not WORKER_LOCK.acquire(blocking=False):
        return False
    try:
        with league.connect() as c:
            _init(c)
            row=c.execute("SELECT * FROM hf_audit_queue WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if not row: return False
            task=dict(row)
            existing_report=hf_candidate_audit.get_report(c,task['catalog_id'],task['revision'])
            if existing_report is not None:
                c.execute("UPDATE hf_audit_queue SET status='completed',finished_at=?,error=NULL,report_json=? WHERE id=?",(_now(),json.dumps(existing_report,ensure_ascii=False),task['id']))
                return True
            c.execute("UPDATE hf_audit_queue SET status='running',started_at=?,finished_at=NULL,error=NULL WHERE id=?",(_now(),task['id']))
        try:
            if not hf_candidate_audit.AUDIT_LOCK.acquire(blocking=True, timeout=1):
                with league.connect() as c: c.execute("UPDATE hf_audit_queue SET status='queued',started_at=NULL WHERE id=?",(task['id'],))
                return False
            try:
                report=hf_candidate_audit.audit_candidate(task['catalog_id'])
            finally:
                hf_candidate_audit.AUDIT_LOCK.release()
            with league.connect() as c:
                hf_candidate_audit.save_report(c,report)
                c.execute("UPDATE hf_audit_queue SET status='completed',finished_at=?,report_json=?,error=NULL WHERE id=?",(_now(),json.dumps(report,ensure_ascii=False),task['id']))
        except Exception as exc:
            with league.connect() as c:
                c.execute("UPDATE hf_audit_queue SET status='failed',finished_at=?,error=? WHERE id=?",(_now(),str(exc)[:2000],task['id']))
        return True
    finally:
        WORKER_LOCK.release()


def _worker_loop():
    while True:
        try:
            if not _run_one(): time.sleep(POLL_SECONDS)
        except Exception:
            time.sleep(POLL_SECONDS)


def _scheduler_loop():
    while True:
        try: refresh_queue()
        except Exception: pass
        time.sleep(REFRESH_SECONDS)


def start_background_workers():
    global _STARTED
    with LOCK:
        if _STARTED: return
        _STARTED=True
        with league.connect() as c:
            _init(c)
            # A process crash cannot leave a task falsely marked as running.
            c.execute("UPDATE hf_audit_queue SET status='queued',started_at=NULL,error='Wznowiono po restarcie procesu.' WHERE status='running'")
        refresh_queue()
        threading.Thread(target=_worker_loop,daemon=True,name='hf-audit-queue-worker').start()
        threading.Thread(target=_scheduler_loop,daemon=True,name='hf-audit-queue-scheduler').start()
