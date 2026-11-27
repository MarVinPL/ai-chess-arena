import hashlib
import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime_supervisor_service import (
    SUPERVISOR_BUNDLE_PATH,
    SupervisorServiceConfigError,
    _verify_supervisor_bundle,
)
from tools.build_runtime_supervisor_bundle import BUNDLE_MODULES, build_bundle

ROOT = Path(__file__).resolve().parents[1]


def test_bundle_is_deterministic_and_contains_only_the_supervisor_dependency_closure(tmp_path):
    first = tmp_path / "first.pyz"
    second = tmp_path / "second.pyz"
    first_hash = build_bundle(ROOT, first)
    second_hash = build_bundle(ROOT, second)
    assert first_hash == second_hash
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert set(archive.namelist()) == {"__main__.py", *BUNDLE_MODULES}
        assert archive.testzip() is None
        bootstrap = archive.read("__main__.py")
        compile(bootstrap, "__main__.py", "exec")
        assert b"ARENA_RUNTIME_SUPERVISOR_BUNDLE_SHA256" in bootstrap
        assert bootstrap.index(b"if not (sys.flags.isolated and sys.flags.no_site)") < bootstrap.index(b"import hashlib")
        assert bootstrap.index(b"digest.hexdigest()") < bootstrap.index(b"from runtime_supervisor_service import main")
        assert b"sys.flags.isolated and sys.flags.no_site" in bootstrap
        assert all("tests/" not in name and "__pycache__" not in name for name in archive.namelist())


def test_bundle_imports_from_zipapp_without_repository_checkout(tmp_path):
    bundle = tmp_path / "runtime_supervisor.pyz"
    build_bundle(ROOT, bundle)
    import_code = (
        f"import sys; sys.path.insert(0, {str(bundle)!r}); "
        "import runtime_supervisor_service, runtime_lease_broker; print('bundle-import-ok')"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", import_code],
        cwd=tmp_path, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "bundle-import-ok"


def test_bundle_verifier_requires_root_owned_non_writable_pinned_archive(tmp_path, monkeypatch):
    bundle = tmp_path / "runtime_supervisor.pyz"
    bundle.write_bytes(b"immutable test bundle")
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    real_lstat = Path.lstat

    def root_owned_lstat(path):
        if path == bundle:
            return SimpleNamespace(
                st_mode=stat.S_IFREG | 0o644, st_uid=0, st_gid=0,
            )
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", root_owned_lstat)
    _verify_supervisor_bundle(
        bundle, digest, running_path=bundle, expected_path=bundle,
    )
    with pytest.raises(SupervisorServiceConfigError, match="SHA-256 mismatch"):
        _verify_supervisor_bundle(
            bundle, "0" * 64, running_path=bundle, expected_path=bundle,
        )
    with pytest.raises(SupervisorServiceConfigError, match="launched from"):
        _verify_supervisor_bundle(
            bundle, digest, running_path=ROOT / "server.py", expected_path=bundle,
        )


def test_bundle_verifier_rejects_untrusted_owner_and_write_permissions(tmp_path, monkeypatch):
    bundle = tmp_path / "runtime_supervisor.pyz"
    bundle.write_bytes(b"immutable test bundle")
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    real_lstat = Path.lstat

    def untrusted_owner(path):
        if path == bundle:
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=1000, st_gid=1000)
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", untrusted_owner)
    with pytest.raises(SupervisorServiceConfigError, match="root-owned"):
        _verify_supervisor_bundle(
            bundle, digest, running_path=bundle, expected_path=bundle,
        )

    def writable_bundle(path):
        if path == bundle:
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o664, st_uid=0, st_gid=0)
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", writable_bundle)
    with pytest.raises(SupervisorServiceConfigError, match="root-owned"):
        _verify_supervisor_bundle(
            bundle, digest, running_path=bundle, expected_path=bundle,
        )


def test_production_bundle_path_is_a_fixed_root_owned_location():
    assert SUPERVISOR_BUNDLE_PATH == Path(
        "/usr/local/lib/ai-chess-arena-runtime/runtime_supervisor.pyz"
    )


def test_bundle_builder_refuses_to_overwrite_a_source_module(tmp_path):
    with pytest.raises(ValueError, match="must not overwrite"):
        build_bundle(ROOT, ROOT / "runtime_supervisor.py")


def test_zipapp_bootstrap_refuses_non_isolated_python_before_service_import(tmp_path):
    bundle = tmp_path / "runtime_supervisor.pyz"
    build_bundle(ROOT, bundle)
    env = dict(os.environ)
    env["ARENA_RUNTIME_SUPERVISOR_BUNDLE_SHA256"] = hashlib.sha256(bundle.read_bytes()).hexdigest()
    result = subprocess.run(
        [sys.executable, str(bundle)], cwd=tmp_path, env=env,
        text=True, capture_output=True, check=False,
    )
    assert result.returncode != 0
    assert "isolated/no-site mode (-I -S)" in result.stderr
