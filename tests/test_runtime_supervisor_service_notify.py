import signal
import socket
import threading

import runtime_supervisor_service as service


class _Watchdog:
    def __init__(self):
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class _Server:
    def __init__(self):
        self.ready = threading.Event()
        self.revocation_watchdog = _Watchdog()

    def __init__(self):
        self.ready = threading.Event()
        self.revocation_watchdog = _Watchdog()
        self.shutdown_called = False

    def serve_forever(self):
        self.ready.set()
        if self.shutdown_called:
            return
        self._shutdown_event.wait(2)

    @property
    def _shutdown_event(self):
        if not hasattr(self, "_event"):
            self._event = threading.Event()
        return self._event

    def shutdown(self):
        self.shutdown_called = True
        self._shutdown_event.set()


def test_systemd_notify_supports_filesystem_socket(tmp_path, monkeypatch):
    path = str(tmp_path / "notify.sock")
    receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    receiver.bind(path)
    monkeypatch.setenv("NOTIFY_SOCKET", path)
    try:
        service._notify_systemd("READY=1")
        assert receiver.recv(128) == b"READY=1"
    finally:
        receiver.close()


def test_main_notifies_only_after_listener_ready_and_stops_watchdog(monkeypatch):
    server = _Server()
    messages = []
    handlers = {}

    def capture_signal(signum, handler):
        handlers[signum] = handler
        return signal.SIG_DFL

    def notify(message):
        messages.append(message)
        handlers[signal.SIGTERM](signal.SIGTERM, None)

    monkeypatch.setattr(service, "build_service", lambda: server)
    monkeypatch.setattr(service.signal, "signal", capture_signal)
    monkeypatch.setattr(service, "_notify_systemd", notify)

    assert service.main() == 0
    assert server.revocation_watchdog.started
    assert server.revocation_watchdog.stopped
    assert messages == ["READY=1\nSTATUS=Supervisor IPC socket is accepting API peers"]


def test_systemd_notify_fails_closed_without_notify_socket(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    try:
        service._notify_systemd("READY=1")
    except service.SupervisorServiceConfigError:
        pass
    else:
        raise AssertionError("missing NOTIFY_SOCKET must fail closed")


def test_main_signal_shutdown_closes_listener_and_stops_watchdog(monkeypatch):
    server = _Server()
    handlers = {}

    def capture_signal(signum, handler):
        handlers[signum] = handler
        return signal.SIG_DFL

    def notify(_message):
        handlers[signal.SIGTERM](signal.SIGTERM, None)

    monkeypatch.setattr(service, "build_service", lambda: server)
    monkeypatch.setattr(service.signal, "signal", capture_signal)
    monkeypatch.setattr(service, "_notify_systemd", notify)

    assert service.main() == 0
    assert server.shutdown_called
    assert server.revocation_watchdog.stopped
