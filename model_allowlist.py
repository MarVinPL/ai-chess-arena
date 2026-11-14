"""Read-only GitHub-sourced model admission register.

This module validates decisions and detects source drift. It never changes the
registry, active tournaments, model files, or admission state in the database.
"""
from __future__ import annotations
import json
import re
import subprocess
from datetime import date
from pathlib import Path
from model_manifest import validate_manifest

ALLOWLIST_PATH = Path(__file__).resolve().parent / 'manifests' / 'model-allowlist.json'
MANIFEST_PATH = Path(__file__).resolve().parent / 'manifests' / 'huggingface-models.json'
STATUSES = frozenset({'eligible', 'restricted', 'blocked', 'review_required'})
ADMISSION_SCOPES = frozenset({'PURE_AI', 'AI_ENGINE'})
_SHA = re.compile(r'^[0-9a-f]{40}$')
_REPO = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')


class AllowlistError(ValueError):
    """The versioned model admission register is invalid or inconsistent."""


def _load_json(path: str | Path) -> dict:
    try:
        with Path(path).open('r', encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise AllowlistError(f'Nie można odczytać JSON: {path}') from exc


def validate_allowlist(payload: dict, manifest: dict | None = None) -> None:
    if not isinstance(payload, dict) or payload.get('schema_version') != 1:
        raise AllowlistError('Nieobsługiwana wersja schematu allowlisty')
    if payload.get('source_manifest') != 'manifests/huggingface-models.json':
        raise AllowlistError('Nieprawidłowe źródło manifestu')
    if payload.get('source_repository') != 'MarVinPL/ai-chess-arena' or payload.get('source_ref') != 'main':
        raise AllowlistError('Allowlista musi pochodzić z main repozytorium AI Chess Arena')
    entries = payload.get('models')
    if not isinstance(entries, list) or not entries:
        raise AllowlistError('Allowlista nie zawiera wpisów')
    seen_ids, seen_repos = set(), set()
    source_by_id = {}
    if manifest is not None:
        source_models = manifest.get('models') if isinstance(manifest, dict) else None
        if not isinstance(source_models, list):
            raise AllowlistError('Nieprawidłowy manifest źródłowy')
        source_by_id = {item.get('model_id'): item for item in source_models if isinstance(item, dict)}
    for item in entries:
        if not isinstance(item, dict):
            raise AllowlistError('Nieprawidłowy wpis allowlisty')
        model_id, repo, revision = item.get('model_id'), item.get('repo_id'), item.get('revision')
        if isinstance(model_id, bool) or not isinstance(model_id, int) or model_id <= 0:
            raise AllowlistError('Nieprawidłowy model_id')
        if model_id in seen_ids or not isinstance(repo, str) or not _REPO.fullmatch(repo) or repo in seen_repos:
            raise AllowlistError('Powtórzony lub nieprawidłowy model_id/repo_id')
        if not isinstance(revision, str) or not _SHA.fullmatch(revision):
            raise AllowlistError('Rewizja musi być pełnym SHA commita')
        status = item.get('status')
        if status not in STATUSES:
            raise AllowlistError('Nieznany status decyzji')
        scope = item.get('admission_scope')
        if not isinstance(scope, list) or not all(isinstance(x, str) and x in ADMISSION_SCOPES for x in scope) or len(scope) != len(set(scope)):
            raise AllowlistError('Nieprawidłowy zakres dopuszczenia')
        license_info = item.get('license_requirements')
        if not isinstance(license_info, dict) or not isinstance(license_info.get('manifest_license'), str) or not license_info['manifest_license']:
            raise AllowlistError('Brak wymagań licencyjnych')
        if license_info.get('license_verified_required') is not True:
            raise AllowlistError('Każdy wpis wymaga niezależnego zatwierdzenia licencji')
        obligations = license_info.get('obligations')
        if not isinstance(obligations, list) or not all(isinstance(x, str) and x.strip() for x in obligations):
            raise AllowlistError('Nieprawidłowe obowiązki licencyjne')
        evidence = item.get('evidence')
        if not isinstance(evidence, list) or not evidence or any(
            not isinstance(ref, dict) or not isinstance(ref.get('path'), str) or not ref['path'] or
            ref['path'].startswith('/') or '..' in Path(ref['path']).parts or
            ref.get('revision') != revision for ref in evidence
        ):
            raise AllowlistError('Dowody muszą wskazywać dokładną rewizję wpisu')
        reviewed_at = item.get('reviewed_at')
        try:
            date.fromisoformat(reviewed_at)
        except (TypeError, ValueError):
            raise AllowlistError('Nieprawidłowa data przeglądu')
        restrictions = item.get('restrictions')
        if not isinstance(restrictions, list) or not all(isinstance(x, str) and x.strip() for x in restrictions):
            raise AllowlistError('Nieprawidłowe ograniczenia')
        technical = item.get('technical_readiness')
        technical_blockers = item.get('technical_blockers')
        if technical not in {'not_evaluated', 'ready', 'blocked'}:
            raise AllowlistError('Nieznany status gotowości technicznej')
        if not isinstance(technical_blockers, list) or not all(isinstance(x, str) and x.strip() for x in technical_blockers):
            raise AllowlistError('Nieprawidłowe blokady techniczne')
        if status in {'blocked', 'review_required'} and technical == 'ready':
            raise AllowlistError('Wpis zablokowany lub wymagający przeglądu nie może być technicznie gotowy')
        if technical == 'ready' and technical_blockers:
            raise AllowlistError('Technicznie gotowy wpis nie może mieć blokad technicznych')
        reason = item.get('block_reason')
        if status in {'blocked', 'review_required'} and (not isinstance(reason, str) or not reason.strip()):
            raise AllowlistError('Status blokujący wymaga uzasadnienia')
        if status == 'eligible' and not scope:
            raise AllowlistError('Status eligible wymaga jawnego zakresu dopuszczenia')
        if status == 'restricted' and (not scope or not restrictions or not obligations):
            raise AllowlistError('Status restricted wymaga zakresu i ograniczeń')
        if status in {'blocked', 'review_required'} and scope:
            raise AllowlistError('Wpis zablokowany lub wymagający przeglądu nie może mieć zakresu dopuszczenia')
        if manifest is not None:
            source = source_by_id.get(model_id)
            if source is None or source.get('repo_id') != repo or source.get('revision') != revision:
                raise AllowlistError(f'Allowlista nie zgadza się z manifestem dla model_id={model_id}')
            if license_info.get('manifest_license') != source.get('license'):
                raise AllowlistError(f'Licencja allowlisty nie zgadza się z manifestem dla model_id={model_id}')
            if license_info.get('manifest_audit_status') != source.get('license_review_status'):
                raise AllowlistError(f'Status audytu allowlisty nie zgadza się z manifestem dla model_id={model_id}')
            admission = source.get('arena_admission') or {}
            if admission.get('status') == 'excluded' and status != 'blocked':
                raise AllowlistError(f'Wykluczony model {model_id} musi mieć status blocked')
            if status == 'eligible' and source.get('license_review_status') != 'verified_permissive':
                raise AllowlistError(f'Model {model_id} nie ma audytu verified_permissive')
            if status == 'restricted' and source.get('license_review_status') != 'verified_restricted':
                raise AllowlistError(f'Model {model_id} nie ma audytu verified_restricted')
        seen_ids.add(model_id)
        seen_repos.add(repo)
    if manifest is not None and seen_ids != set(source_by_id):
        raise AllowlistError('Każdy wpis manifestu musi mieć jawny wpis decyzji w allowliście')


def load_allowlist(path: str | Path = ALLOWLIST_PATH,
                   manifest_path: str | Path = MANIFEST_PATH) -> dict:
    payload = _load_json(path)
    manifest = _load_json(manifest_path)
    validate_manifest(manifest)
    validate_allowlist(payload, manifest)
    return payload


def compare_allowlists(previous: dict, current: dict) -> dict:
    """Describe additions, changes, and withdrawals without mutating either list."""
    old = {item['model_id']: item for item in previous.get('models', [])}
    new = {item['model_id']: item for item in current.get('models', [])}
    return {
        'added': sorted(new.keys() - old.keys()),
        'updated': sorted(key for key in old.keys() & new.keys() if old[key] != new[key]),
        'withdrawn': sorted(old.keys() - new.keys()),
    }



def is_runtime_ready(entry: dict, *, license_verified: bool,
                     local_file_verified: bool, runtime_healthy: bool) -> bool:
    """Admission decision is not runtime readiness; all gates are independent."""
    return bool(
        isinstance(entry, dict) and entry.get('status') in {'eligible', 'restricted'} and
        entry.get('technical_readiness') == 'ready' and not entry.get('technical_blockers') and
        license_verified is True and local_file_verified is True and runtime_healthy is True
    )


def tournament_admission_blockers(entry: dict | None, *, model_id: int,
                                  repo_id: str | None, revision: str | None,
                                  license_name: str | None, license_verified: bool,
                                  tournament_format: str) -> list[str]:
    """Fail-closed admission gate for one exact model version and tournament scope."""
    reasons = []
    if model_id in {9, 10}:
        return ['model jest trwale wyłączony z AI Chess Arena (Issue #71)']
    if not isinstance(entry, dict):
        return ['brak decyzji modelu w allowliście']
    if entry.get('model_id') != model_id:
        reasons.append('niezgodny identyfikator modelu w allowliście')
    if entry.get('status') not in {'eligible', 'restricted'}:
        reasons.append('model nie ma jawnej decyzji dopuszczenia (status allowlisty: %s)' % entry.get('status', 'brak'))
    if tournament_format not in (entry.get('admission_scope') or []):
        reasons.append('allowlista nie dopuszcza modelu do tego formatu turnieju')
    if entry.get('technical_readiness') != 'ready' or entry.get('technical_blockers'):
        reasons.append('gotowość techniczna modelu nie została zatwierdzona')
    if not isinstance(repo_id, str) or repo_id != entry.get('repo_id'):
        reasons.append('repozytorium wersji nie zgadza się z allowlistą')
    if not isinstance(revision, str) or revision != entry.get('revision'):
        reasons.append('rewizja wersji nie zgadza się z allowlistą')
    expected_license = (entry.get('license_requirements') or {}).get('manifest_license')
    if not isinstance(license_name, str) or license_name.casefold() != str(expected_license or '').casefold():
        reasons.append('deklaracja licencji wersji nie zgadza się z allowlistą')
    if license_verified is not True:
        reasons.append('licencja tej wersji nie została niezależnie zatwierdzona')
    return reasons


def check_allowlist_sync(local_commit: str, main_commit: str,
                         allowlist_worktree_clean: bool) -> dict:
    """Report whether the consumer is exactly on origin/main; never fetch or reset."""
    valid = bool(_SHA.fullmatch(local_commit or '') and _SHA.fullmatch(main_commit or ''))
    in_sync = valid and local_commit == main_commit and allowlist_worktree_clean is True
    return {
        'in_sync': bool(in_sync),
        'local_commit': local_commit,
        'main_commit': main_commit,
        'allowlist_worktree_clean': allowlist_worktree_clean is True,
        'reason': 'synchronized' if in_sync else 'stale_or_locally_modified',
        'mutated': False,
    }


def check_repository_sync(repo_root: str | Path) -> dict:
    """Read local Git refs/status only. No fetch, checkout, merge, or file writes."""
    root = Path(repo_root)
    def git(*args: str) -> str:
        return subprocess.check_output(['git', '-C', str(root), *args], text=True, stderr=subprocess.DEVNULL).strip()
    try:
        local_commit = git('rev-parse', 'HEAD')
        main_commit = git('rev-parse', 'origin/main')
        dirty = bool(git('status', '--porcelain', '--', 'manifests/model-allowlist.json'))
    except (OSError, subprocess.CalledProcessError):
        return check_allowlist_sync('', '', False) | {'reason': 'git_ref_unavailable'}
    return check_allowlist_sync(local_commit, main_commit, not dirty)
