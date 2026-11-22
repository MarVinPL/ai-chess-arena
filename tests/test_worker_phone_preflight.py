"""Arena phone mode preflight must check matching ready devices, never VPS Llamafile."""
import pytest

import server
import remote_worker_inference_delivery


def preflight_stubs(monkeypatch, ready_sha):
    entries = [
        {
            "model_id": 1, "name": "White Phone Model", "version_id": 11,
            "blockers": [], "manifest_ready": True, "license_verified": True,
            "download_on_demand": True, "draft_download_on_demand": False,
        },
        {
            "model_id": 2, "name": "Black Phone Model", "version_id": 12,
            "blockers": [], "manifest_ready": True, "license_verified": True,
            "download_on_demand": True, "draft_download_on_demand": False,
        },
    ]
    monkeypatch.setattr(server.league, "tournament_entry_status", lambda _: [
        dict(x) for x in entries
    ])
    # Admission is now read exclusively from the SQLite connection opened
    # by tournament_preflight; repository JSON drift checks no longer exist.
    monkeypatch.setattr(
        server.model_allowlist, "load_allowlist",
        lambda connection: {"source": "sqlite", "models": [
            {"model_id": 1}, {"model_id": 2},
        ]},
    )
    monkeypatch.setattr(
        server.model_allowlist, "tournament_admission_blockers", lambda *args, **kwargs: [],
    )

    class FakeDb:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, *_):
            return self

        def fetchone(self):
            return {"format": "round_robin"}

    monkeypatch.setattr(server.league, "connect", FakeDb)
    monkeypatch.setattr(
        server, "_version_record",
        lambda version_id: {"sha256": "a" * 64 if version_id == 11 else "b" * 64},
    )

    class FakeDelivery:
        def __init__(self, path):
            pass

        def find_ready_worker(self, sha):
            return "android_1234567890abcdef" if sha in ready_sha else None

    monkeypatch.setattr(remote_worker_inference_delivery, "WorkerInferenceDelivery", FakeDelivery)


def test_phone_preflight_uses_ready_model_instead_of_vps_runtime(monkeypatch):
    preflight_stubs(monkeypatch, {"a" * 64, "b" * 64})
    monkeypatch.setenv("ARENA_PHONE_INFERENCE_ENABLED", "1")
    monkeypatch.setattr(
        server, "llm_runtime_available",
        lambda: pytest.fail("remote preflight must not probe VPS Llamafile"),
    )
    result = server.tournament_preflight(99)
    assert result["ready"] is True
    assert result["runtime_available"] is True
    assert result["blocked"] == []


def test_phone_preflight_blocks_one_missing_phone_and_no_local_fallback(monkeypatch):
    preflight_stubs(monkeypatch, {"a" * 64})
    monkeypatch.setenv("ARENA_PHONE_INFERENCE_ENABLED", "1")
    result = server.tournament_preflight(99)
    assert result["ready"] is False
    assert result["runtime_available"] is False
    assert len(result["blocked"]) == 1
    assert result["blocked"][0]["model_id"] == 2
    assert "brak gotowego telefonu" in result["blocked"][0]["reasons"][0]


def test_default_local_preflight_still_requires_vps_runtime(monkeypatch):
    preflight_stubs(monkeypatch, set())
    monkeypatch.delenv("ARENA_PHONE_INFERENCE_ENABLED", raising=False)
    monkeypatch.setattr(server, "llm_runtime_available", lambda: False)
    result = server.tournament_preflight(99)
    assert result["ready"] is False
    assert result["runtime_available"] is False
    assert len(result["blocked"]) == 2
    assert all(
        "lokalny runtime Llamafile jest niedostępny" in row["reasons"]
        for row in result["blocked"]
    )
