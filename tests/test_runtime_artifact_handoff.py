import hashlib
import os
import stat

import pytest

from runtime_artifact_handoff import (
    ArtifactHandoffError,
    prepare_artifact_handoff,
)


def setup_roots(tmp_path):
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir(mode=0o700)
    source_dir = quarantine / "download-17"
    source_dir.mkdir(mode=0o700)
    handoff = tmp_path / "handoff"
    handoff.mkdir(mode=0o710)
    os.chmod(handoff, 0o710)
    os.chown(handoff, os.getuid(), os.getgid())
    return quarantine, source_dir, handoff


def prepare(quarantine, source, handoff, *, job_id=17, content=b"verified GGUF bytes",
            sha=None, size=None):
    digest = hashlib.sha256(content).hexdigest() if sha is None else sha
    expected_size = len(content) if size is None else size
    return prepare_artifact_handoff(
        job_id=job_id, source_root=quarantine, source_path=str(source),
        expected_sha256=digest, expected_size=expected_size,
        handoff_root=handoff, runtime_gid=os.getgid(), owner_uid=os.getuid(),
    )


def test_copies_exact_pinned_bytes_to_runtime_group_readable_snapshot(tmp_path):
    quarantine, source_dir, handoff = setup_roots(tmp_path)
    content = b"verified GGUF bytes"
    source = source_dir / "model.quarantine"
    source.write_bytes(content)
    result = prepare(quarantine, source, handoff)

    target = handoff / "job-17" / "model.gguf"
    assert result.path == str(target)
    assert target.read_bytes() == content
    assert result.sha256 == hashlib.sha256(content).hexdigest()
    assert result.size_bytes == len(content)
    assert target.stat().st_uid == os.getuid()
    assert target.stat().st_gid == os.getgid()
    assert stat.S_IMODE(target.stat().st_mode) == 0o440
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o750
    assert stat.S_IMODE(handoff.stat().st_mode) == 0o710


def test_hash_mismatch_fails_and_removes_partial_job_directory(tmp_path):
    quarantine, source_dir, handoff = setup_roots(tmp_path)
    source = source_dir / "model.quarantine"
    source.write_bytes(b"not the pinned artifact")
    with pytest.raises(ArtifactHandoffError, match="SHA-256"):
        prepare(quarantine, source, handoff, sha="a" * 64,
                size=len(b"not the pinned artifact"))
    assert not (handoff / "job-17").exists()


def test_size_mismatch_fails_before_copy(tmp_path):
    quarantine, source_dir, handoff = setup_roots(tmp_path)
    source = source_dir / "model.quarantine"
    source.write_bytes(b"abc")
    with pytest.raises(ArtifactHandoffError, match="size"):
        prepare(quarantine, source, handoff, size=4)
    assert not (handoff / "job-17").exists()


@pytest.mark.parametrize("bad_path", [
    "/outside/model.gguf",
    "/quarantine/../outside/model.gguf",
])
def test_rejects_source_path_outside_quarantine(tmp_path, bad_path):
    quarantine, _, handoff = setup_roots(tmp_path)
    with pytest.raises(ArtifactHandoffError):
        prepare_artifact_handoff(
            job_id=17, source_root=quarantine, source_path=bad_path,
            expected_sha256="a" * 64, expected_size=1,
            handoff_root=handoff, runtime_gid=os.getgid(), owner_uid=os.getuid(),
        )


def test_rejects_symlinked_source_file(tmp_path):
    quarantine, source_dir, handoff = setup_roots(tmp_path)
    outside = tmp_path / "outside.gguf"
    outside.write_bytes(b"verified GGUF bytes")
    source = source_dir / "model.quarantine"
    source.symlink_to(outside)
    with pytest.raises(ArtifactHandoffError):
        prepare(quarantine, source, handoff)
    assert not (handoff / "job-17").exists()


def test_rejects_symlinked_source_directory(tmp_path):
    quarantine, _, handoff = setup_roots(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "model.quarantine").write_bytes(b"verified GGUF bytes")
    link = quarantine / "linked"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ArtifactHandoffError):
        prepare(quarantine, link / "model.quarantine", handoff)


def test_existing_job_directory_is_never_overwritten(tmp_path):
    quarantine, source_dir, handoff = setup_roots(tmp_path)
    source = source_dir / "model.quarantine"
    source.write_bytes(b"verified GGUF bytes")
    existing = handoff / "job-17"
    existing.mkdir()
    sentinel = existing / "keep"
    sentinel.write_text("preserve")
    with pytest.raises(ArtifactHandoffError):
        prepare(quarantine, source, handoff)
    assert sentinel.read_text() == "preserve"


def test_handoff_root_must_have_exact_owner_group_and_mode(tmp_path):
    quarantine, source_dir, handoff = setup_roots(tmp_path)
    source = source_dir / "model.quarantine"
    source.write_bytes(b"verified GGUF bytes")
    os.chmod(handoff, 0o700)
    with pytest.raises(ArtifactHandoffError, match="ownership/mode"):
        prepare(quarantine, source, handoff)


def test_rejects_invalid_size_and_sha_before_creating_job_directory(tmp_path):
    quarantine, source_dir, handoff = setup_roots(tmp_path)
    source = source_dir / "model.quarantine"
    source.write_bytes(b"abc")
    with pytest.raises(ArtifactHandoffError):
        prepare(quarantine, source, handoff, size=0)
    with pytest.raises(ArtifactHandoffError):
        prepare(quarantine, source, handoff, sha="not-a-sha")
    assert list(handoff.iterdir()) == []
