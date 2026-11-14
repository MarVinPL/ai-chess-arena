"""Safe, opt-in GGUF storage for Local AI Chess Arena.

Downloads require an HTTPS source, a pinned SHA-256, a declared size, and an
explicitly verified free-use license. Only files inside MANAGED_DIR are arena
owned; shared model directories are never cleanup targets.
"""
from __future__ import annotations
import hashlib, os, re, shutil, sqlite3, stat, threading, urllib.request
from pathlib import Path
import league
from model_exclusions import is_excluded_model_identity

ROOT = Path(__file__).resolve().parent
MANAGED_DIR = ROOT / 'managed-models'
MIN_FREE_MARGIN = 512 * 1024 * 1024
CHUNK = 1024 * 1024
# Serializes model downloads and destructive cleanup against tournament model use.
MODEL_LIFECYCLE_LOCK = threading.RLock()


def _safe_filename(name: str) -> str:
    if not name or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,180}\.gguf', name):
        raise ValueError('Nieprawidłowa nazwa pliku GGUF')
    return name


def _download_version_locked(version_id: int, url: str, filename: str, expected_sha256: str,
                              expected_size: int, license_verified: bool = False) -> dict:
    """Download a catalogued model only after explicit provenance/license checks."""
    filename = _safe_filename(filename)
    if not license_verified:
        raise ValueError('Pobieranie zablokowane: licencja nie została jawnie zweryfikowana')
    if not url.startswith('https://'):
        raise ValueError('Źródło musi używać HTTPS')
    if not re.fullmatch(r'[0-9a-fA-F]{64}', expected_sha256 or ''):
        raise ValueError('Wymagany poprawny SHA-256')
    if not isinstance(expected_size, int) or expected_size <= 0:
        raise ValueError('Wymagany dodatni, znany rozmiar pliku')
    with league.connect() as c:
        row = c.execute('''SELECT v.*,m.name,m.active FROM model_versions v
                           JOIN league_models m ON m.id=v.model_id WHERE v.id=?''',
                        (version_id,)).fetchone()
        if row is None or not row['active']:
            raise ValueError('Nieznana lub nieaktywna wersja modelu')
        if is_excluded_model_identity(name=row['name'], source=row['source'], metadata=row['metadata_json']):
            raise ValueError('Pobieranie zablokowane: model jest trwale wyłączony z AI Chess Arena (Issue #71)')
        if row['license_verified'] != 1:
            raise ValueError('Licencja nie jest zatwierdzona w rejestrze')
        if row['source'] and row['source'] != url:
            trusted_hf_file = row['source'].startswith('https://huggingface.co/') and url.startswith(row['source'].rstrip('/') + '/resolve/')
            if not trusted_hf_file:
                raise ValueError('URL nie zgadza się ze źródłem zapisanym w rejestrze')
        if row['license'] == '':
            raise ValueError('Brak nazwy licencji w rejestrze')
    MANAGED_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = MANAGED_DIR / filename
    part = MANAGED_DIR / (filename + '.part')
    if target.exists() or part.exists():
        raise FileExistsError('Plik docelowy lub niedokończony już istnieje; bez nadpisywania')
    usage = shutil.disk_usage(MANAGED_DIR)
    if usage.free < expected_size + MIN_FREE_MARGIN:
        raise OSError('Za mało wolnego miejsca z marginesem bezpieczeństwa 512 MiB')
    digest = hashlib.sha256()
    received = 0
    req = urllib.request.Request(url, headers={'User-Agent': 'LocalAIChessArena/0.1'})
    try:
        with urllib.request.urlopen(req, timeout=30) as response, part.open('xb') as out:
            final_url = response.geturl()
            if not final_url.startswith('https://'):
                raise ValueError('Przekierowanie poza HTTPS zablokowane')
            while True:
                block = response.read(CHUNK)
                if not block:
                    break
                received += len(block)
                if received > expected_size:
                    raise ValueError('Pobrany plik przekracza zadeklarowany rozmiar')
                digest.update(block); out.write(block)
            out.flush(); os.fsync(out.fileno())
        if received != expected_size:
            raise ValueError(f'Rozmiar niezgodny: {received} != {expected_size}')
        actual = digest.hexdigest()
        if actual.lower() != expected_sha256.lower():
            raise ValueError('SHA-256 nie zgadza się; plik nie został aktywowany')
        os.chmod(part, 0o600)
        os.replace(part, target)
        with league.connect() as c:
            c.execute("UPDATE model_versions SET filename=?,sha256=?,size_bytes=?,status='downloaded',managed_copy=1 WHERE id=?",
                      (filename, actual, received, version_id))
        return {'version_id': version_id, 'filename': filename, 'sha256': actual,
                'size_bytes': received, 'managed_copy': True, 'path': str(target)}
    except Exception:
        try: part.unlink()
        except FileNotFoundError: pass
        raise


def download_version(version_id: int, url: str, filename: str, expected_sha256: str,
                    expected_size: int, license_verified: bool = False) -> dict:
    with MODEL_LIFECYCLE_LOCK:
        return _download_version_locked(version_id, url, filename, expected_sha256,
                                        expected_size, license_verified)


def download_draft_version(version_id: int, url: str, filename: str, expected_sha256: str,
                           expected_size: int, license_verified: bool = False) -> dict:
    """Download and pin the companion draft GGUF under the same model version."""
    filename = _safe_filename(filename)
    if not license_verified or not url.startswith('https://'):
        raise ValueError('Pobieranie draft wymaga zatwierdzonej licencji i HTTPS')
    if not re.fullmatch(r'[0-9a-fA-F]{64}', expected_sha256 or '') or not isinstance(expected_size, int) or expected_size <= 0:
        raise ValueError('Niekompletny manifest SHA-256/rozmiaru pliku draft')
    with MODEL_LIFECYCLE_LOCK, league.connect() as c:
        row = c.execute('SELECT * FROM model_versions WHERE id=?', (version_id,)).fetchone()
        if row is None or row['license_verified'] != 1:
            raise ValueError('Wersja modelu nie ma jawnie zatwierdzonej licencji')
        if is_excluded_model_identity(source=row['source'], metadata=row['metadata_json']):
            raise ValueError('Pobieranie draft zablokowane: model jest wyłączony z AI Chess Arena (Issue #71)')
        if (row['draft_filename'], row['draft_sha256'], row['draft_size_bytes'], row['draft_download_url']) != (filename, expected_sha256, expected_size, url):
            raise ValueError('Manifest draft nie zgadza się z przypiętą konfiguracją zawodnika')
        if row['source'].startswith('https://huggingface.co/') and not url.startswith(row['source'].rstrip('/') + '/resolve/'):
            raise ValueError('URL draft nie należy do repozytorium modelu')
        MANAGED_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = MANAGED_DIR / filename
        part = MANAGED_DIR / (filename + '.part')
        if target.exists() or part.exists():
            raise FileExistsError('Plik draft już istnieje; odmowa nadpisania')
        if shutil.disk_usage(MANAGED_DIR).free < expected_size + MIN_FREE_MARGIN:
            raise OSError('Za mało wolnego miejsca na plik draft z marginesem bezpieczeństwa')
        digest = hashlib.sha256(); received = 0
        req = urllib.request.Request(url, headers={'User-Agent': 'LocalAIChessArena/0.1'})
        try:
            with urllib.request.urlopen(req, timeout=30) as response, part.open('xb') as out:
                if not response.geturl().startswith('https://'):
                    raise ValueError('Przekierowanie draft poza HTTPS zablokowane')
                while True:
                    block = response.read(CHUNK)
                    if not block: break
                    received += len(block)
                    if received > expected_size: raise ValueError('Draft przekracza zadeklarowany rozmiar')
                    digest.update(block); out.write(block)
                out.flush(); os.fsync(out.fileno())
            actual = digest.hexdigest()
            if received != expected_size or actual.lower() != expected_sha256.lower():
                raise ValueError('Rozmiar lub SHA-256 pliku draft nie zgadza się z manifestem')
            os.chmod(part, 0o600); os.replace(part, target)
            c.execute("UPDATE model_versions SET draft_filename=?,draft_sha256=?,draft_size_bytes=?,draft_status='downloaded',draft_managed_copy=1 WHERE id=?",
                      (filename, actual, received, version_id))
            return {'version_id': version_id, 'filename': filename, 'sha256': actual,
                    'size_bytes': received, 'managed_copy': True, 'path': str(target)}
        except Exception:
            try: part.unlink()
            except FileNotFoundError: pass
            raise


def _open_by_process(path: Path) -> tuple[list[int], bool]:
    """Scan processes that can access the private 0700 Arena model directory.

    The VPS exposes host/root processes through /proc but hides their FD tables
    from the unprivileged Arena account. The directory is deliberately private;
    same-UID processes are the only non-privileged processes able to open its
    files. If the ownership/permissions invariant or a same-UID scan is unclear,
    cleanup fails closed.
    """
    users = []
    uncertain = False
    try:
        directory_stat = MANAGED_DIR.stat()
        owner_uid = directory_stat.st_uid
        if stat.S_IMODE(directory_stat.st_mode) & 0o077:
            return users, True
        if path.resolve().parent != MANAGED_DIR.resolve():
            return users, True
    except OSError:
        return users, True

    proc = Path('/proc')
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status = (entry / 'status').read_text()
            uid_line = next(line for line in status.splitlines() if line.startswith('Uid:'))
            effective_uid = int(uid_line.split()[2])
        except FileNotFoundError:
            continue
        except (PermissionError, OSError, StopIteration, ValueError):
            uncertain = True
            continue
        # Other unprivileged UIDs cannot traverse a mode-0700 directory.
        # Privileged host processes are outside this account's /proc visibility;
        # the private directory is the isolation boundary for them.
        if effective_uid != owner_uid:
            continue
        try:
            command_line = (entry / 'cmdline').read_bytes()
        except FileNotFoundError:
            continue
        except (PermissionError, OSError):
            command_line = None
        target_bytes = str(path.resolve()).encode()
        if command_line is not None and (target_bytes in command_line or path.name.encode() in command_line):
            users.append(int(entry.name))
            continue
        fd_dir = entry / 'fd'
        try:
            fds = list(fd_dir.iterdir())
        except FileNotFoundError:
            continue
        except (PermissionError, OSError):
            # Hidden system/session processes are not allowed to traverse the
            # 0700 directory; if their visible command line names this model,
            # the check above already fails closed. Unknown command lines remain
            # uncertain rather than being treated as safe.
            if command_line is None:
                uncertain = True
            elif b'llamafile' in command_line and b'.gguf' in command_line:
                uncertain = True
            continue
        for fd in fds:
            try:
                if fd.resolve() == path.resolve():
                    users.append(int(entry.name))
                    break
            except FileNotFoundError:
                continue
            except (PermissionError, OSError):
                uncertain = True
    return users, uncertain


def cleanup_version(version_id: int) -> dict:
    """Remove target and optional draft caches only after proving neither is open."""
    with MODEL_LIFECYCLE_LOCK, league.connect() as c:
        row = c.execute('SELECT * FROM model_versions WHERE id=?', (version_id,)).fetchone()
        if row is None:
            raise ValueError('Nieznana wersja modelu')
        assets = []
        if row['managed_copy'] == 1 and row['filename']:
            assets.append(('target', row['filename']))
        if row['draft_managed_copy'] == 1 and row['draft_filename']:
            assets.append(('draft', row['draft_filename']))
        if not assets:
            raise PermissionError('Brak plików oznaczonych jako kopie zarządzane przez arenę')
        removed = []
        for role, filename in assets:
            path = (MANAGED_DIR / filename).resolve()
            if path.parent != MANAGED_DIR.resolve() or not path.is_file() and path.exists():
                raise PermissionError(f'Nieprawidłowa ścieżka kopii {role}')
            if not path.exists():
                continue
            users, uncertain = _open_by_process(path)
            if users or uncertain:
                raise RuntimeError(f'Nie można bezpiecznie usunąć {role}; procesy={users}, niepewność={uncertain}')
        for role, filename in assets:
            path = MANAGED_DIR / filename
            if path.exists():
                path.unlink(); removed.append(role)
        c.execute("UPDATE model_versions SET status='catalogued',managed_copy=0,draft_status='catalogued',draft_managed_copy=0 WHERE id=?", (version_id,))
    return {'version_id': version_id, 'removed': bool(removed), 'removed_assets': removed,
            'already_absent': not removed, 'metadata_retained': True}
