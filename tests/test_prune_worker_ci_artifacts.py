"""Safety tests for keeping only the latest successful main Worker artifact."""
from scripts.prune_worker_ci_artifacts import select_old_artifact_ids


def artifact(id, name="worker-linux-unsigned", run=10, branch="main", expired=False):
    return {
        "id": id,
        "name": name,
        "expired": expired,
        "workflow_run": {"id": run, "head_branch": branch},
    }


def test_new_main_artifact_replaces_only_old_main_artifact():
    items = [
        artifact(1, run=8),
        artifact(2, run=9),
        artifact(3, run=10),
        artifact(4, run=7, branch="feature"),
        artifact(5, name="worker-windows-unsigned", run=9),
    ]
    assert select_old_artifact_ids(items, "worker-linux-unsigned", 10) == [1, 2]


def test_failed_or_skipped_build_never_deletes_old_versions():
    assert select_old_artifact_ids([artifact(1, run=9)], "worker-linux-unsigned", 10) == []


def test_only_pr_artifact_cannot_trigger_main_cleanup():
    assert select_old_artifact_ids([
        artifact(1, run=9),
        artifact(2, run=10, branch="feature"),
    ], "worker-linux-unsigned", 10) == []


def test_expired_replacement_does_not_delete_last_main_build():
    assert select_old_artifact_ids([
        artifact(1, run=9),
        artifact(2, run=10, expired=True),
    ], "worker-linux-unsigned", 10) == []
