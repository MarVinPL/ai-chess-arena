"""Small, dependency-free password login and signed-cookie sessions for the Arena web UI."""
from __future__ import annotations
import base64, getpass, hashlib, hmac, ipaddress, os, re, secrets, sys, threading, time
from http.cookies import SimpleCookie

COOKIE_NAME = "arena_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
PBKDF2_ITERATIONS = 600_000
LOGIN_WINDOW_SECONDS = 10 * 60
LOGIN_MAX_FAILURES = 5
_failures = {}
_failures_lock = threading.Lock()

def _b64e(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

def _b64d(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

def hash_password(password, salt=None):
    if not isinstance(password, str) or len(password) < 16:
        raise ValueError("Hasło musi mieć co najmniej 16 znaków")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256$" + str(PBKDF2_ITERATIONS) + "$" + _b64e(salt) + "$" + _b64e(digest)

def _password_hash_parts():
    try:
        algorithm, rounds, salt, digest = os.environ.get("ARENA_AUTH_PASSWORD_HASH", "").split("$", 3)
        iterations = int(rounds)
        salt_bytes, digest_bytes = _b64d(salt), _b64d(digest)
        if algorithm != "pbkdf2_sha256" or iterations < 300_000 or len(salt_bytes) < 16 or len(digest_bytes) != 32:
            return None
        return iterations, salt_bytes, digest_bytes
    except Exception:
        return None

def _session_secret():
    try:
        value = _b64d(os.environ.get("ARENA_AUTH_SESSION_SECRET", ""))
        return value if len(value) >= 32 else None
    except Exception:
        return None

def is_configured():
    return bool(os.environ.get("ARENA_AUTH_USERNAME") and _password_hash_parts()
                and _session_secret() and os.environ.get("ARENA_AUTH_ORIGIN", "").startswith("https://"))

def verify_password(password):
    parts = _password_hash_parts()
    if not parts or not isinstance(password, str):
        return False
    iterations, salt, expected = parts
    try:
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    except UnicodeError:
        return False
    return hmac.compare_digest(actual, expected)

def login_allowed(remote_key, now=None):
    # Atomically reserve an attempt before password derivation to prevent
    # concurrent requests from bypassing the per-client limit.
    now = time.monotonic() if now is None else now
    with _failures_lock:
        started, count = _failures.get(remote_key, (now, 0))
        if now - started >= LOGIN_WINDOW_SECONDS:
            started, count = now, 0
        if count >= LOGIN_MAX_FAILURES:
            return False
        if remote_key not in _failures and len(_failures) >= 4096:
            for key, (stamp, _) in list(_failures.items()):
                if now - stamp >= LOGIN_WINDOW_SECONDS:
                    _failures.pop(key, None)
            while len(_failures) >= 4096:
                _failures.pop(next(iter(_failures)))
        _failures[remote_key] = (started, count + 1)
        return True

def record_failure(remote_key, now=None):
    now = time.monotonic() if now is None else now
    with _failures_lock:
        started, count = _failures.get(remote_key, (now, 0))
        if now - started >= LOGIN_WINDOW_SECONDS:
            started, count = now, 0
        _failures[remote_key] = (started, count + 1)

def clear_failures(remote_key):
    with _failures_lock:
        _failures.pop(remote_key, None)

def client_key(forwarded_for, peer_address):
    # The backend binds to loopback and is reachable publicly only through Caddy.
    # Caddy appends the actual client address to X-Forwarded-For; trust its last value.
    candidate = str(forwarded_for or "").split(",")[-1].strip()
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        try:
            return str(ipaddress.ip_address(str(peer_address)))
        except ValueError:
            return "unknown"

def check_login(username, password, remote_key):
    if not is_configured():
        return False, "not_configured"
    if not login_allowed(remote_key):
        return False, "rate_limited"
    user_ok = hmac.compare_digest(
        str(username).encode("utf-8"), os.environ["ARENA_AUTH_USERNAME"].encode("utf-8"))
    password_ok = verify_password(password)
    if user_ok and password_ok:
        clear_failures(remote_key)
        return True, "ok"
    return False, "invalid_credentials"

def create_session_token(username, now=None):
    secret = _session_secret()
    if not secret:
        raise RuntimeError("Brak prawidłowego sekretu sesji")
    issued = int(time.time() if now is None else now)
    nonce = secrets.token_urlsafe(18)
    message = f"{username}:{issued}:{nonce}".encode("utf-8")
    signature = _b64e(hmac.new(secret, message, hashlib.sha256).digest())
    return f"{issued}.{nonce}.{signature}"

def session_user(cookie_header, now=None):
    if not is_configured() or not cookie_header:
        return None
    try:
        cookie = SimpleCookie()
        cookie.load(cookie_header)
        token = cookie[COOKIE_NAME].value
        issued_text, nonce, signature = token.split(".", 2)
        issued = int(issued_text)
        current = int(time.time() if now is None else now)
        if issued > current + 60 or current - issued > SESSION_TTL_SECONDS:
            return None
        username = os.environ["ARENA_AUTH_USERNAME"]
        message = f"{username}:{issued}:{nonce}".encode("utf-8")
        expected = _b64e(hmac.new(_session_secret(), message, hashlib.sha256).digest())
        return username if hmac.compare_digest(signature, expected) else None
    except Exception:
        return None

def session_cookie(token):
    return f"{COOKIE_NAME}={token}; Path=/; Max-Age={SESSION_TTL_SECONDS}; HttpOnly; Secure; SameSite=Strict"

def expired_cookie():
    return f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict"

def origin_allowed(origin):
    expected = os.environ.get("ARENA_AUTH_ORIGIN", "")
    if not expected or not origin:
        return False
    try:
        return hmac.compare_digest(origin.rstrip("/").encode("utf-8"), expected.rstrip("/").encode("utf-8"))
    except UnicodeError:
        return False

def generate_env_file(path):
    username = input("Login [arena]: ").strip() or "arena"
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", username):
        raise SystemExit("Login może zawierać tylko litery ASCII, cyfry, kropkę, podkreślenie i myślnik")
    password = getpass.getpass("Nowe hasło (minimum 16 znaków): ")
    confirmation = getpass.getpass("Powtórz hasło: ")
    if len(password) > 1024:
        raise SystemExit("Hasło jest zbyt długie (maksymalnie 1024 znaki)")
    if not hmac.compare_digest(password, confirmation):
        raise SystemExit("Hasła nie są identyczne")
    password_hash = hash_password(password)
    session_secret = _b64e(secrets.token_bytes(48))
    content = (f"ARENA_AUTH_USERNAME={username}\n"
               f"ARENA_AUTH_PASSWORD_HASH={password_hash}\n"
               f"ARENA_AUTH_SESSION_SECRET={session_secret}\n"
               "ARENA_AUTH_ORIGIN=https://arena.148-113-182-137.sslip.io\n")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(content)
    print(f"Zapisano konfigurację logowania do {path} (tryb 0600).")

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--generate-env":
        generate_env_file(sys.argv[2])
    else:
        raise SystemExit("Użycie: python arena_auth.py --generate-env /etc/ai-chess-arena/auth.env")
