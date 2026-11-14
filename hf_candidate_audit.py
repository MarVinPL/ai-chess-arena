"""Evidence-only Hugging Face candidate pre-audit. Never downloads model weights."""
from __future__ import annotations

import json
import re
import sqlite3
import time
import threading
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parent
CATALOG_PATH = ROOT / 'manifests' / 'huggingface-candidates.json'
API_BASE = 'https://huggingface.co/api/models/'
RAW_BASE = 'https://huggingface.co/'
TIMEOUT_SECONDS = 8
MAX_TEXT_BYTES = 96 * 1024
MAX_METADATA_BYTES = 512 * 1024
AUDIT_LOCK = threading.Lock()
MAX_FILES = 4
REPO_RE = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')
REV_RE = re.compile(r'^[0-9a-f]{40}$')
TEXT_NAMES = {'readme.md', 'license', 'license.md', 'license.txt', 'copying', 'config.json'}
WEIGHT_SUFFIXES = ('.gguf', '.safetensors', '.bin', '.pt', '.pth', '.onnx', '.ckpt', '.model')


class AuditError(ValueError):
    pass


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def load_catalog(path: Path = CATALOG_PATH):
    payload = json.loads(path.read_text(encoding='utf-8'))
    items = payload.get('models') if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise AuditError('Katalog kandydatów ma nieprawidłowy format.')
    return items


class _SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if parsed.scheme != 'https' or parsed.hostname != 'huggingface.co' or parsed.username or parsed.password:
            raise AuditError('Przekierowanie poza huggingface.co zostało zablokowane.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _request(url: str, *, max_bytes: int):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname != 'huggingface.co' or parsed.username or parsed.password:
        raise AuditError('Odmowa żądania do hosta spoza huggingface.co.')
    req = Request(url, headers={'User-Agent': 'AI-Chess-Arena-Candidate-Audit/1.0', 'Accept': 'application/json,text/plain'})
    try:
        with build_opener(_SafeRedirectHandler()).open(req, timeout=TIMEOUT_SECONDS) as response:
            final = urlparse(response.geturl())
            if final.scheme != 'https' or final.hostname != 'huggingface.co':
                raise AuditError('Przekierowanie poza huggingface.co zostało zablokowane.')
            length = response.headers.get('Content-Length')
            if length and int(length) > max_bytes:
                raise AuditError('Plik przekracza limit rozmiaru audytu.')
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise AuditError('Odpowiedź przekracza limit rozmiaru audytu.')
            return data
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        if isinstance(exc, AuditError):
            raise
        raise AuditError(f'Nie udało się pobrać publicznych metadanych: {exc}') from exc


def _candidate(catalog_id: str, catalog=None):
    if not isinstance(catalog_id, str) or len(catalog_id) > 256:
        raise AuditError('Nieprawidłowy catalog_id.')
    matches = [x for x in (catalog if catalog is not None else load_catalog()) if x.get('catalog_id') == catalog_id]
    if len(matches) != 1:
        raise AuditError('Kandydat nie istnieje w aktualnym katalogu lub wpis jest niejednoznaczny.')
    item = matches[0]
    repo, revision = item.get('repo_id'), (item.get('revision') or item.get('audited_revision'))
    if not isinstance(repo, str) or not REPO_RE.fullmatch(repo):
        raise AuditError('Nieprawidłowy identyfikator repozytorium.')
    if not isinstance(revision, str) or not REV_RE.fullmatch(revision):
        raise AuditError('Kandydat nie ma przypiętej pełnej rewizji; audyt przerwany.')
    if item.get('source_status') not in {'active', 'not_checked'}:
        raise AuditError('Kandydat nie ma aktywnego źródła; audyt przerwany.')
    return item


def audit_candidate(catalog_id: str, *, catalog=None, fetcher=None):
    item = _candidate(catalog_id, catalog)
    repo, revision = item['repo_id'], (item.get('revision') or item.get('audited_revision'))
    fetch = fetcher or _request
    api_url = f'{API_BASE}{quote(repo, safe="/")}/revision/{revision}'
    raw = fetch(api_url, max_bytes=MAX_METADATA_BYTES)
    try:
        info = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditError('Hugging Face zwrócił nieprawidłowe metadane JSON.') from exc
    observed_revision = info.get('sha')
    if observed_revision != revision:
        raise AuditError('Rewizja odpowiedzi nie zgadza się z przypiętym SHA; wynik nie może być użyty.')
    siblings = info.get('siblings')
    if not isinstance(siblings, list):
        raise AuditError('Brak wiarygodnego indeksu plików repozytorium.')
    names = [x.get('rfilename') for x in siblings if isinstance(x, dict) and isinstance(x.get('rfilename'), str)]
    chosen = []
    priority = {'readme.md': 0, 'license': 1, 'license.md': 1, 'license.txt': 1,
                'copying': 1, 'config.json': 2}
    for name in sorted(names, key=lambda value: priority.get(value.rsplit('/', 1)[-1].lower(), 99)):
        parts = name.split('/')
        base = parts[-1].lower()
        if (base in TEXT_NAMES and name not in chosen and
                all(part not in {'', '.', '..'} for part in parts) and
                all(re.fullmatch(r'[A-Za-z0-9_.-]+', part) for part in parts)):
            chosen.append(name)
        if len(chosen) >= MAX_FILES:
            break
    texts = {}
    for name in chosen:
        if name.lower().endswith(WEIGHT_SUFFIXES):
            continue
        url = f'{RAW_BASE}{quote(repo, safe="/")}/raw/{revision}/{quote(name, safe="/")}'
        try:
            data = fetch(url, max_bytes=MAX_TEXT_BYTES)
            texts[name] = data.decode('utf-8', errors='replace')[:MAX_TEXT_BYTES]
        except AuditError as exc:
            texts[name] = f'[Nie udało się odczytać pliku: {exc}]'
    card = info.get('cardData') if isinstance(info.get('cardData'), dict) else {}
    catalog_license = item.get('declared_license_tag')
    card_license = card.get('license')
    declared = card_license or catalog_license
    license_declaration_conflict = bool(catalog_license and card_license and
                                        str(catalog_license).lower() != str(card_license).lower())
    safe_names = [name for name in names if
                  all(part not in {'', '.', '..'} for part in name.split('/')) and
                  all(re.fullmatch(r'[A-Za-z0-9_.-]+', part) for part in name.split('/'))]
    license_files = [name for name in safe_names if name.rsplit('/', 1)[-1].lower() in {'license', 'license.md', 'license.txt', 'copying'}]
    readme_name = next((name for name in texts if name.rsplit('/', 1)[-1].lower() == 'readme.md'), None)
    license_texts = {name: value for name, value in texts.items() if name in license_files}
    notes = [f'Deklaracja katalogu: {declared or "brak"} (nie jest niezależnym potwierdzeniem).',
             f'Rewizja przypięta: {revision}; rewizja API: {observed_revision}.',
             f'Źródła: https://huggingface.co/{repo}/tree/{revision}']
    if readme_name:
        notes.append(f'Karta modelu: https://huggingface.co/{repo}/blob/{revision}/{quote(readme_name, safe="/")}')
    for name in license_files:
        notes.append(f'Plik licencji: https://huggingface.co/{repo}/blob/{revision}/{quote(name, safe="/")}')
    if not license_files:
        notes.append('Nie znaleziono pliku LICENSE/COPYING w indeksie repozytorium.')
    blockers = ['Nie wykonano testu lokalnego artefaktu ani runtime; metadane nie potwierdzają działania modelu.']
    if not license_files:
        blockers.append('Brak jawnego pliku licencji w indeksie repozytorium; wymagany ręczny przegląd warunków.')
    if license_declaration_conflict:
        blockers.append('Tag licencji katalogu różni się od deklaracji w model card; wymagane ręczne wyjaśnienie.')
    if item.get('gated'):
        blockers.append('Repozytorium jest gated; publiczny audyt metadanych nie potwierdza uprawnień dostępu.')
    report = {
        'catalog_id': catalog_id, 'arena_model_id': item.get('arena_model_id'), 'repo_id': repo, 'display_name': item.get('display_name', repo.rsplit('/', 1)[-1]),
        'pinned_revision': revision, 'observed_revision': observed_revision, 'revision_matches': True,
        'audited_at': _now(), 'sources': notes, 'declared_license': declared,
        'license_declarations': {'catalog_tag': catalog_license, 'model_card': card_license,
                                 'conflict': license_declaration_conflict},
        'license_files': license_files, 'license_file_texts': license_texts,
        'readme_excerpt': (texts.get(readme_name, '')[:6000] if readme_name else None),
        'metadata': {'pipeline_tag': info.get('pipeline_tag'), 'tags': info.get('tags', [])[:80],
                     'gated': info.get('gated') is True, 'siblings_count': len(names),
                     'config_present': 'config.json' in [n.rsplit('/', 1)[-1].lower() for n in names]},
        'evidence_level': 'public_metadata_only',
        'limitations': ['To wstępna analiza publicznych danych, nie opinia prawna.',
                        'Deklaracja licencji, tagi i model card mogą być niekompletne lub nieprawdziwe.',
                        'Nie pobrano wag, plików GGUF ani shardów; nie uruchomiono kodu ani inferencji.'],
        'suggested_form': {
            'license_id': str(declared or ''), 'license_review': 'pending',
            'license_notes': '\n'.join(notes), 'technical_readiness': 'not_evaluated',
            'technical_evidence': 'Wstępny odczyt publicznych metadanych; nie jest testem runtime.\n' + '\n'.join(notes[:3]),
            'technical_blockers': '\n'.join(blockers), 'revision_confirmed': False,
            'revision_notes': f'Przypięty SHA: {revision}; API zwróciło: {observed_revision}. Potwierdzenie wymaga kontroli źródeł.',
            'proposed_status': 'review_required', 'restrictions': '', 'obligations': '',
            'block_reason': 'Wstępny audyt nie rozstrzyga licencji ani gotowości technicznej; wymagany przegląd człowieka.',
            'scope_formats': []
        }
    }
    return report


def save_report(connection: sqlite3.Connection, report: dict):
    connection.execute('''CREATE TABLE IF NOT EXISTS hf_candidate_audits (
        catalog_id TEXT NOT NULL, revision TEXT NOT NULL, audited_at TEXT NOT NULL,
        report_json TEXT NOT NULL, PRIMARY KEY(catalog_id, revision)
    )''')
    connection.execute('''INSERT INTO hf_candidate_audits(catalog_id,revision,audited_at,report_json)
        VALUES(?,?,?,?) ON CONFLICT(catalog_id,revision) DO UPDATE SET
        audited_at=excluded.audited_at, report_json=excluded.report_json''',
        (report['catalog_id'], report['pinned_revision'], report['audited_at'], json.dumps(report, ensure_ascii=False, sort_keys=True)))


def get_report(connection: sqlite3.Connection, catalog_id: str, revision: str):
    try:
        row = connection.execute(
            'SELECT report_json FROM hf_candidate_audits WHERE catalog_id=? AND revision=?',
            (catalog_id, revision)).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    return json.loads(row[0])


def list_reports(connection: sqlite3.Connection):
    try:
        rows = connection.execute('SELECT catalog_id,revision,audited_at FROM hf_candidate_audits ORDER BY audited_at DESC').fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(zip(('catalog_id', 'revision', 'audited_at'), row)) for row in rows]
