"""Worker CI artifact dedup never skips unverified or changed source builds."""
from unittest.mock import patch

from scripts import reuse_worker_artifacts as module


def artifact(name, sha="a" * 40, branch="feature", expired=False):
    return {
        "name": name,
        "expired": expired,
        "workflow_run": {"head_sha": sha, "head_branch": branch},
    }


def test_matching_unexpired_same_branch_artifact_reused():
    with patch.object(module, "git_sources_match", return_value=True):
        assert module.can_reuse_platform(
            "windows", [artifact("worker-windows-unsigned")], "feature"
        )


def test_unrelated_branch_artifact_is_never_reused():
    with patch.object(module, "git_sources_match", return_value=True) as match:
        assert not module.can_reuse_platform(
            "linux", [artifact("worker-linux-unsigned", branch="main")], "feature"
        )
        match.assert_not_called()


def test_expired_artifact_does_not_suppress_build():
    with patch.object(module, "git_sources_match", return_value=True) as match:
        assert not module.can_reuse_platform(
            "linux", [artifact("worker-linux-unsigned", expired=True)], "feature"
        )
        match.assert_not_called()


def test_unverified_or_changed_sources_force_rebuild():
    with patch.object(module, "git_sources_match", return_value=False):
        assert not module.can_reuse_platform(
            "linux", [artifact("worker-linux-unsigned")], "feature"
        )


def test_only_android_retries_when_desktops_have_artifacts():
    env = {
        "SHARED": "true",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_HEAD_REF": "feature",
        "GITHUB_TOKEN": "test-token",
        "GITHUB_REPOSITORY": "owner/repo",
    }
    with (
        patch.object(module, "list_artifacts") as artifacts,
        patch.object(module, "git_sources_match", return_value=True),
    ):
        artifacts.side_effect = lambda _, name, __: (
            [] if "arm64-debug" in name else [artifact(name)]
        )
        assert module.select_platforms(env) == {
            "linux": False, "windows": False, "android": True
        }


def test_api_error_forces_build_instead_of_skipping():
    env = {
        "SHARED": "true",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_HEAD_REF": "feature",
        "GITHUB_TOKEN": "token",
        "GITHUB_REPOSITORY": "owner/repo",
    }
    with patch.object(module, "list_artifacts", side_effect=OSError("Unavailable")):
        assert all(module.select_platforms(env).values())


def test_non_pr_main_push_never_reuses_pr_artifacts():
    env = {
        "SHARED": "true",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_TOKEN": "token",
        "GITHUB_REPOSITORY": "owner/repo",
    }
    with patch.object(module, "list_artifacts") as artifacts:
        assert all(module.select_platforms(env).values())
        artifacts.assert_not_called()


def test_git_scope_protects_all_shared_and_platform_inputs():
    assert "remote_worker_gui/src-tauri/src" in module.SHARED_SOURCES
    assert "remote_worker_gui/src-tauri/Cargo.lock" in module.SHARED_SOURCES
    assert "remote_worker_gui/src-tauri/icons/icon.png" in module.SHARED_SOURCES
    assert "remote_worker_gui/src-tauri/icons/icon.ico" in module.PLATFORM_SOURCES["windows"]
    assert "remote_worker_gui/src-tauri/icons/128x128.png" in module.PLATFORM_SOURCES["linux"]
    assert "remote_worker_gui/src-tauri/gen/android" in module.PLATFORM_SOURCES["android"]


def test_invalid_sha_never_reused():
    assert not module.git_sources_match("badsha", ("remote_worker_gui/public",))


def test_different_input_changes_result_in_build():
    assert module.select_platforms({
        "SHARED": "false", "WINDOWS": "false", "LINUX": "false", "ANDROID": "true"
    }) == {"linux": False, "windows": False, "android": True}
