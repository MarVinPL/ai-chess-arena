"""End-to-end API dispatcher -> client runner -> real AF_UNIX supervisor IPC."""
import json
import threading

from runtime_api_worker import RuntimeApiWorker
from runtime_supervisor_client import RuntimeSupervisorClientRunner
from runtime_supervisor_ipc import RuntimeSupervisorIPCServer, supervisor_request


class Production:
    production_requested = True
    execution_enabled = True


def _server(tmp_path):
    state = {"lease": "absent", "unit": None, "actions": []}
    job_id = 73
    unit = f"ai-chess-arena-runtime-{job_id}.service"

    def start(requested):
        assert requested == job_id
        state["actions"].append(("start", requested))
        state.update(lease="active", unit=unit)
        return {"job_id": job_id, "unit_name": unit,
                "lease_state": "active", "unit_state": "start_requested"}

    def stop(requested):
        assert requested == job_id
        state["actions"].append(("stop", requested))
        state["lease"] = "stopped"
        return {"job_id": job_id, "unit_name": unit, "lease_state": "stopped"}

    def status(requested):
        assert requested == job_id
        state["actions"].append(("status", requested))
        return {"job_id": job_id, "lease_exists": state["lease"] != "absent",
                "unit_name": state["unit"], "lease_state": state["lease"]}

    server = RuntimeSupervisorIPCServer(
        tmp_path / "supervisor.sock", allowed_uid=__import__("os").getuid(),
        start=start, stop=stop, status=status,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.ready.wait(2), "AF_UNIX supervisor did not become ready"
    return server, thread, state, job_id


def _runner(socket_path, bridge_call):
    return RuntimeSupervisorClientRunner(
        supervisor_socket=socket_path,
        bridge_socket_template="/tmp/runtime-job-{job_id}.sock",
        supervisor_call=supervisor_request,
        bridge_call=bridge_call,
        sleep=lambda seconds: __import__("time").sleep(min(seconds, 0.005)),
    )


def _bridge(_socket, method, path, _body=b"", *, timeout_seconds):
    if method == "GET":
        return 200, b"healthy"
    return 200, json.dumps({"choices": [{"message": {"content": "READY"}}]}).encode()


def test_api_dispatcher_uses_real_supervisor_ipc_and_stops_exact_job(tmp_path, monkeypatch):
    server, thread, state, job_id = _server(tmp_path)
    try:
        runner = _runner(server.socket_path, _bridge)
        worker = RuntimeApiWorker(
            deployment=Production(), runner=runner, find_next=lambda: job_id,
            execute=lambda selected, *, runner: runner(
                {"id": selected, "sha256": "a" * 64}, 5, lambda: False
            ),
        )
        monkeypatch.setenv("ARENA_RUNTIME_EXECUTION_ENABLED", "1")
        result = worker.run_once()
        assert result == {"status": "dispatched", "job_id": job_id,
                          "result_status": None}
        assert state["actions"] == [("start", job_id), ("stop", job_id)]
        assert state["lease"] == "stopped"
    finally:
        server.shutdown()
        thread.join(2)


def test_cancellation_during_inference_uses_real_ipc_stop(tmp_path):
    server, thread, state, job_id = _server(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def blocking_bridge(_socket, method, path, _body=b"", *, timeout_seconds):
        if method == "GET":
            return 200, b"healthy"
        entered.set()
        release.wait(1)
        return 200, json.dumps({"choices": [{"message": {"content": "READY"}}]}).encode()

    calls = 0
    def cancelled():
        nonlocal calls
        calls += 1
        return calls >= 2

    try:
        runner = _runner(server.socket_path, blocking_bridge)
        errors = []
        def invoke():
            try:
                runner({"id": job_id, "sha256": "a" * 64}, 5, cancelled)
            except BaseException as exc:
                errors.append(exc)
        task = threading.Thread(target=invoke)
        task.start()
        assert entered.wait(2), "inference bridge was not reached"
        task.join(2)
        release.set()
        assert not task.is_alive()
        assert len(errors) == 1 and isinstance(errors[0], InterruptedError)
        assert state["actions"] == [("start", job_id), ("stop", job_id)]
        assert state["lease"] == "stopped"
    finally:
        release.set()
        server.shutdown()
        thread.join(2)


def test_ipc_start_side_effect_survives_lost_response_and_status_reconciles(tmp_path):
    import socketserver
    import pytest
    from runtime_supervisor_ipc import SupervisorIPCError, supervisor_request

    state = {"lease": "absent", "unit": None, "start_calls": 0}
    job_id = 74
    unit = f"ai-chess-arena-runtime-{job_id}.service"

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            request = json.loads(self.rfile.readline().decode())
            if request["action"] == "start":
                state.update(lease="active", unit=unit)
                state["start_calls"] += 1
                # Simulate process/socket loss after the privileged side effect
                # but before the client receives its acknowledgement.
                return
            if request["action"] == "status":
                response = {"ok": True, "result": {
                    "job_id": job_id, "lease_exists": state["lease"] != "absent",
                    "unit_name": state["unit"], "lease_state": state["lease"],
                }}
                self.wfile.write(json.dumps(response).encode() + b"\n")

    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True

    socket_path = str(tmp_path / "drop-response.sock")
    server = Server(socket_path, Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(SupervisorIPCError, match="nieprawidłową odpowiedź"):
            supervisor_request(socket_path, "start", job_id)
        status = supervisor_request(socket_path, "status", job_id)
        assert status["lease_state"] == "active"
        assert status["unit_name"] == unit
        assert state["start_calls"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
