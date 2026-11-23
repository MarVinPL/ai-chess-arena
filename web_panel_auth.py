"""Browser-session authentication endpoints for the web panel (Issue #314).

Machine-authenticated Worker endpoints are NOT accepted by this module.
This preserves the pre-existing origin, rate-limit, session-cookie and
payload checks; HTTP composition still delegates Worker routes first.
"""
import json

import arena_auth

PANEL_AUTH_POST_ROUTES = frozenset({"/api/auth/login", "/api/auth/logout"})


def dispatch_panel_auth_post(handler, path):
    """Return True for handled panel login/logout requests, denied or allowed."""
    if path == "/api/auth/login":
        if not arena_auth.is_configured():
            handler.send_json({"error": "Logowanie nie zostało skonfigurowane na serwerze."}, 503)
            return True
        if not arena_auth.origin_allowed(handler.headers.get("Origin")):
            handler.send_json({"error": "Odrzucono żądanie spoza dozwolonej domeny."}, 403)
            return True
        try:
            length = int(handler.headers.get("Content-Length", "0"))
            if length < 1 or length > 4096:
                raise ValueError("Nieprawidłowy rozmiar żądania logowania")
            payload = json.loads(handler.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Nieprawidłowe dane logowania")
            username = payload.get("username", "")
            password = payload.get("password", "")
            if not isinstance(username, str) or not isinstance(password, str):
                raise ValueError("Nieprawidłowe dane logowania")
            if len(username) > 128 or len(password) > 1024:
                raise ValueError("Dane logowania przekraczają dozwoloną długość")
            remote_key = arena_auth.client_key(
                handler.headers.get("X-Forwarded-For"), handler.client_address[0]
            )
            ok, reason = arena_auth.check_login(username, password, remote_key)
            if not ok:
                if reason == "rate_limited":
                    handler.send_json({"error": "Zbyt wiele prób logowania. Spróbuj ponownie za 10 minut."}, 429)
                else:
                    handler.send_json({"error": "Nieprawidłowy login lub hasło."}, 401)
                return True
            token = arena_auth.create_session_token(username)
            handler.send_json({"ok": True}, 200, [
                ("Set-Cookie", arena_auth.session_cookie(token))
            ])
        except (ValueError, json.JSONDecodeError) as exc:
            handler.send_json({"error": str(exc)}, 400)
        return True
    if path == "/api/auth/logout":
        if not arena_auth.origin_allowed(handler.headers.get("Origin")):
            handler.send_json({"error": "Odrzucono żądanie spoza dozwolonej domeny."}, 403)
            return True
        handler.send_json({"ok": True}, 200, [
            ("Set-Cookie", arena_auth.expired_cookie())
        ])
        return True
    return False
