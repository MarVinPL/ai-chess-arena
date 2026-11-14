"""Fail-closed WebAuthn operator identity and session primitives.

No HTTP handler or production configuration is enabled by importing this module.
The caller must supply an explicit database path and validated RP configuration.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from contextlib import contextmanager
from urllib.parse import urlsplit

from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

TOKEN_TTL_SECONDS = 600
CHALLENGE_TTL_SECONDS = 300
SESSION_IDLE_SECONDS = 1800
SESSION_ABSOLUTE_SECONDS = 28800
RATE_LIMIT_WINDOW_SECONDS = 900
_OPERATOR_ID = re.compile(r"^[a-f0-9-]{36}$")
_DISPLAY_NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,100}$")
_RP_ID = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class OperatorAuthError(ValueError):
    """Expected authentication, configuration, or ceremony failure."""


@dataclass(frozen=True)
class WebAuthnConfig:
    rp_id: str
    origin: str
    rp_name: str = "AI Chess Arena"


def load_config(environ=None) -> WebAuthnConfig:
    """Load strict deployment config. Never guess a hostname or permit HTTP."""
    env = os.environ if environ is None else environ
    rp_id = str(env.get("ARENA_WEBAUTHN_RP_ID", "")).strip().lower()
    origin = str(env.get("ARENA_WEBAUTHN_ORIGIN", "")).strip()
    try:
        ipaddress.ip_address(rp_id)
        is_ip_literal = True
    except ValueError:
        is_ip_literal = False
    if not rp_id or not _RP_ID.fullmatch(rp_id) or rp_id == "localhost" or is_ip_literal:
        raise OperatorAuthError("Brak prawidłowego DNS ARENA_WEBAUTHN_RP_ID.")
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError as exc:
        raise OperatorAuthError("Nieprawidłowy port w ARENA_WEBAUTHN_ORIGIN.") from exc
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
            parsed.password or parsed.path not in ("", "/") or parsed.query or
            parsed.fragment):
        raise OperatorAuthError("ARENA_WEBAUTHN_ORIGIN musi być dokładnym originem HTTPS.")
    host = parsed.hostname.lower().rstrip(".")
    if host != rp_id and not host.endswith("." + rp_id):
        raise OperatorAuthError("Origin HTTPS nie należy do skonfigurowanego RP ID.")
    if port not in (None, 443):
        origin = f"https://{host}:{port}"
    else:
        origin = f"https://{host}"
    return WebAuthnConfig(rp_id=rp_id, origin=origin)


def _connect(db_path):
    conn = sqlite3.connect(str(db_path), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def _db(db_path):
    conn = _connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def ensure_schema(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS arena_operators (
        operator_id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('owner','operator')),
        user_handle BLOB NOT NULL UNIQUE,
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
        created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS arena_webauthn_credentials (
        credential_id TEXT PRIMARY KEY,
        operator_id TEXT NOT NULL REFERENCES arena_operators(operator_id),
        public_key BLOB NOT NULL,
        sign_count INTEGER NOT NULL DEFAULT 0,
        transports_json TEXT NOT NULL DEFAULT '[]',
        device_type TEXT,
        backed_up INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        last_used_at REAL,
        revoked_at REAL
    );
    CREATE TABLE IF NOT EXISTS arena_auth_rate_limits (
        bucket_hash TEXT PRIMARY KEY,
        window_started_at REAL NOT NULL,
        attempts INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_arena_credentials_operator
        ON arena_webauthn_credentials(operator_id);
    CREATE TABLE IF NOT EXISTS arena_operator_enrollments (
        token_hash TEXT PRIMARY KEY,
        role TEXT NOT NULL CHECK(role IN ('owner','operator')),
        display_name TEXT NOT NULL,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        consumed_at REAL
    );
    CREATE TABLE IF NOT EXISTS arena_auth_challenges (
        ceremony_id TEXT PRIMARY KEY,
        purpose TEXT NOT NULL CHECK(purpose IN ('registration','authentication')),
        challenge BLOB NOT NULL,
        token_hash TEXT,
        operator_id TEXT,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        consumed_at REAL,
        user_handle BLOB
    );
    CREATE TABLE IF NOT EXISTS arena_operator_sessions (
        session_hash TEXT PRIMARY KEY,
        csrf_hash TEXT NOT NULL,
        operator_id TEXT NOT NULL REFERENCES arena_operators(operator_id),
        created_at REAL NOT NULL,
        last_seen_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        revoked_at REAL
    );
    CREATE INDEX IF NOT EXISTS idx_arena_sessions_operator
        ON arena_operator_sessions(operator_id);
    """)
    credential_cols = {row[1] for row in conn.execute("PRAGMA table_info(arena_webauthn_credentials)")}
    if "revoked_at" not in credential_cols:
        conn.execute("ALTER TABLE arena_webauthn_credentials ADD COLUMN revoked_at REAL")
    challenge_cols = {row[1] for row in conn.execute("PRAGMA table_info(arena_auth_challenges)")}
    if "user_handle" not in challenge_cols:
        conn.execute("ALTER TABLE arena_auth_challenges ADD COLUMN user_handle BLOB")


def _token_hash(token):
    if not isinstance(token, str) or len(token) < 32 or len(token) > 256:
        raise OperatorAuthError("Nieprawidłowy token rejestracyjny.")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_owner_enrollment(db_path, display_name, ttl=TOKEN_TTL_SECONDS):
    """Local-admin bootstrap primitive. Returns the raw one-use token once."""
    if not isinstance(display_name, str) or not _DISPLAY_NAME.fullmatch(display_name.strip()):
        raise OperatorAuthError("Nieprawidłowa nazwa właściciela.")
    if not isinstance(ttl, int) or not 60 <= ttl <= 1800:
        raise OperatorAuthError("Nieprawidłowy czas ważności tokenu.")
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _db(db_path) as conn:
        ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        owners = conn.execute(
            "SELECT COUNT(*) FROM arena_operators WHERE role='owner' AND active=1"
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM arena_operator_enrollments WHERE role='owner' AND consumed_at IS NULL AND expires_at>?",
            (now,),
        ).fetchone()[0]
        if owners or pending:
            raise OperatorAuthError("Właściciel już istnieje albo aktywny token rejestracji jest wydany.")
        conn.execute(
            "INSERT INTO arena_operator_enrollments(token_hash,role,display_name,created_at,expires_at) VALUES(?,?,?,?,?)",
            (_token_hash(token), "owner", display_name.strip(), now, now + ttl),
        )
        conn.commit()
    return token


def create_operator_invitation(db_path, owner_operator_id, display_name, ttl=TOKEN_TTL_SECONDS):
    """Create an operator invitation; caller must already have verified owner role."""
    if not isinstance(display_name, str) or not _DISPLAY_NAME.fullmatch(display_name.strip()):
        raise OperatorAuthError("Nieprawidłowa nazwa operatora.")
    if not isinstance(ttl, int) or not 60 <= ttl <= 1800:
        raise OperatorAuthError("Nieprawidłowy czas ważności zaproszenia.")
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _db(db_path) as conn:
        ensure_schema(conn)
        if not conn.execute(
            "SELECT 1 FROM arena_operators WHERE operator_id=? AND role='owner' AND active=1",
            (owner_operator_id,),
        ).fetchone():
            raise OperatorAuthError("Wymagane jest uwierzytelnione konto właściciela.")
        conn.execute(
            "INSERT INTO arena_operator_enrollments(token_hash,role,display_name,created_at,expires_at) VALUES(?,?,?,?,?)",
            (_token_hash(token), "operator", display_name.strip(), now, now + ttl),
        )
    return token


def begin_registration(db_path, config, token):
    token_hash = _token_hash(token)
    now = time.time()
    with _db(db_path) as conn:
        ensure_schema(conn)
        enrollment = conn.execute(
            "SELECT * FROM arena_operator_enrollments WHERE token_hash=? AND consumed_at IS NULL AND expires_at>?",
            (token_hash, now),
        ).fetchone()
        if enrollment is None:
            raise OperatorAuthError("Token rejestracyjny jest nieprawidłowy, zużyty lub wygasł.")
        if enrollment["role"] == "owner" and conn.execute(
            "SELECT 1 FROM arena_operators WHERE role='owner' AND active=1 LIMIT 1"
        ).fetchone():
            raise OperatorAuthError("Właściciel jest już zarejestrowany.")
        user_handle = secrets.token_bytes(32)
        options = generate_registration_options(
            rp_id=config.rp_id,
            rp_name=config.rp_name,
            user_name=enrollment["display_name"],
            user_display_name=enrollment["display_name"],
            user_id=user_handle,
            timeout=60000,
            attestation=AttestationConveyancePreference.NONE,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
        )
        ceremony_id = secrets.token_urlsafe(24)
        conn.execute(
            "INSERT INTO arena_auth_challenges(ceremony_id,purpose,challenge,token_hash,created_at,expires_at,user_handle) VALUES(?,?,?,?,?,?,?)",
            (ceremony_id, "registration", options.challenge, token_hash, now, now + CHALLENGE_TTL_SECONDS, user_handle),
        )
        result = json.loads(options_to_json(options))
        result["ceremony_id"] = ceremony_id
        return result


def finish_registration(db_path, config, ceremony_id, credential):
    if not isinstance(ceremony_id, str) or len(ceremony_id) > 128 or not isinstance(credential, dict):
        raise OperatorAuthError("Nieprawidłowa odpowiedź rejestracji.")
    now = time.time()
    with _db(db_path) as conn:
        ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        challenge = conn.execute(
            "SELECT * FROM arena_auth_challenges WHERE ceremony_id=? AND purpose='registration' AND consumed_at IS NULL AND expires_at>?",
            (ceremony_id, now),
        ).fetchone()
        if challenge is None:
            raise OperatorAuthError("Wyzwanie rejestracyjne wygasło lub zostało już użyte.")
        enrollment = conn.execute(
            "SELECT * FROM arena_operator_enrollments WHERE token_hash=? AND consumed_at IS NULL AND expires_at>?",
            (challenge["token_hash"], now),
        ).fetchone()
        if enrollment is None:
            raise OperatorAuthError("Token rejestracyjny jest nieprawidłowy, zużyty lub wygasł.")
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=bytes(challenge["challenge"]),
            expected_rp_id=config.rp_id,
            expected_origin=config.origin,
            require_user_verification=True,
        )
        from webauthn.helpers import bytes_to_base64url
        credential_id = bytes_to_base64url(verified.credential_id)
        if conn.execute(
            "SELECT 1 FROM arena_webauthn_credentials WHERE credential_id=?",
            (credential_id,),
        ).fetchone():
            raise OperatorAuthError("To poświadczenie Passkey jest już zarejestrowane.")
        if enrollment["role"] == "owner" and conn.execute(
            "SELECT 1 FROM arena_operators WHERE role='owner' AND active=1 LIMIT 1"
        ).fetchone():
            raise OperatorAuthError("Właściciel jest już zarejestrowany.")
        operator_id = str(uuid.uuid4())
        user_handle = bytes(challenge["user_handle"] or b"")
        if len(user_handle) < 16 or len(user_handle) > 64:
            raise OperatorAuthError("Brak prawidłowego uchwytu użytkownika dla rejestracji.")
        conn.execute(
            "INSERT INTO arena_operators(operator_id,display_name,role,user_handle,created_at) VALUES(?,?,?,?,?)",
            (operator_id, enrollment["display_name"], enrollment["role"], user_handle, now),
        )
        conn.execute(
            """INSERT INTO arena_webauthn_credentials
            (credential_id,operator_id,public_key,sign_count,device_type,backed_up,created_at)
            VALUES(?,?,?,?,?,?,?)""",
            (credential_id, operator_id, verified.credential_public_key,
             verified.sign_count, str(verified.credential_device_type),
             int(verified.credential_backed_up), now),
        )
        conn.execute(
            "UPDATE arena_operator_enrollments SET consumed_at=? WHERE token_hash=? AND consumed_at IS NULL",
            (now, challenge["token_hash"]),
        )
        conn.execute(
            "UPDATE arena_auth_challenges SET consumed_at=? WHERE ceremony_id=? AND consumed_at IS NULL",
            (now, ceremony_id),
        )
        conn.commit()
        return {"operator_id": operator_id, "display_name": enrollment["display_name"],
                "role": enrollment["role"]}


def begin_authentication(db_path, config):
    now = time.time()
    options = generate_authentication_options(
        rp_id=config.rp_id,
        timeout=60000,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    ceremony_id = secrets.token_urlsafe(24)
    with _db(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT INTO arena_auth_challenges(ceremony_id,purpose,challenge,created_at,expires_at) VALUES(?,?,?,?,?)",
            (ceremony_id, "authentication", options.challenge, now, now + CHALLENGE_TTL_SECONDS),
        )
    result = json.loads(options_to_json(options))
    result["ceremony_id"] = ceremony_id
    return result


def _new_session(conn, operator_id, now):
    raw_session = secrets.token_urlsafe(32)
    raw_csrf = secrets.token_urlsafe(32)
    conn.execute(
        """INSERT INTO arena_operator_sessions
        (session_hash,csrf_hash,operator_id,created_at,last_seen_at,expires_at)
        VALUES(?,?,?,?,?,?)""",
        (hashlib.sha256(raw_session.encode()).hexdigest(),
         hashlib.sha256(raw_csrf.encode()).hexdigest(), operator_id, now, now,
         now + SESSION_ABSOLUTE_SECONDS),
    )
    return raw_session, raw_csrf


def finish_authentication(db_path, config, ceremony_id, credential):
    if not isinstance(ceremony_id, str) or len(ceremony_id) > 128 or not isinstance(credential, dict):
        raise OperatorAuthError("Nieprawidłowa odpowiedź logowania.")
    raw_id = credential.get("rawId") or credential.get("id")
    try:
        credential_id = __import__("webauthn.helpers", fromlist=["bytes_to_base64url"]).bytes_to_base64url(
            base64url_to_bytes(raw_id)
        )
    except Exception as exc:
        raise OperatorAuthError("Nieprawidłowy identyfikator Passkey.") from exc
    now = time.time()
    with _db(db_path) as conn:
        ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        challenge = conn.execute(
            "SELECT * FROM arena_auth_challenges WHERE ceremony_id=? AND purpose='authentication' AND consumed_at IS NULL AND expires_at>?",
            (ceremony_id, now),
        ).fetchone()
        if challenge is None:
            raise OperatorAuthError("Wyzwanie logowania wygasło lub zostało już użyte.")
        row = conn.execute(
            """SELECT c.*,o.operator_id,o.role,o.display_name,o.active,o.user_handle
            FROM arena_webauthn_credentials c JOIN arena_operators o
            ON o.operator_id=c.operator_id
            WHERE c.credential_id=? AND c.revoked_at IS NULL""",
            (credential_id,),
        ).fetchone()
        if row is None or not row["active"]:
            raise OperatorAuthError("Nie udało się uwierzytelnić operatora.")
        raw_user_handle = (credential.get("response") or {}).get("userHandle")
        try:
            supplied_user_handle = base64url_to_bytes(raw_user_handle)
        except Exception as exc:
            raise OperatorAuthError("Nie udało się uwierzytelnić operatora.") from exc
        if not secrets.compare_digest(supplied_user_handle, bytes(row["user_handle"])):
            raise OperatorAuthError("Nie udało się uwierzytelnić operatora.")
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=bytes(challenge["challenge"]),
            expected_rp_id=config.rp_id,
            expected_origin=config.origin,
            credential_public_key=bytes(row["public_key"]),
            credential_current_sign_count=int(row["sign_count"]),
            require_user_verification=True,
        )
        if verified.credential_id != base64url_to_bytes(raw_id):
            raise OperatorAuthError("Identyfikator uwierzytelnionego poświadczenia jest niezgodny.")
        conn.execute(
            "UPDATE arena_webauthn_credentials SET sign_count=?,last_used_at=?,device_type=?,backed_up=? WHERE credential_id=?",
            (verified.new_sign_count, now, str(verified.credential_device_type),
             int(verified.credential_backed_up), credential_id),
        )
        conn.execute(
            "UPDATE arena_auth_challenges SET consumed_at=? WHERE ceremony_id=? AND consumed_at IS NULL",
            (now, ceremony_id),
        )
        session_token, csrf_token = _new_session(conn, row["operator_id"], now)
        conn.commit()
        return {
            "operator": {"operator_id": row["operator_id"], "display_name": row["display_name"],
                         "role": row["role"]},
            "session_token": session_token,
            "csrf_token": csrf_token,
        }



def consume_rate_limit(db_path, bucket, limit, window_seconds=RATE_LIMIT_WINDOW_SECONDS, now=None):
    """Atomically count a bounded auth attempt; bucket values are never stored raw."""
    if not isinstance(bucket, str) or not bucket or len(bucket) > 4096:
        raise OperatorAuthError("Nieprawidłowy klucz limitu prób.")
    if not isinstance(limit, int) or limit < 1 or not isinstance(window_seconds, int) or window_seconds < 1:
        raise OperatorAuthError("Nieprawidłowa konfiguracja limitu prób.")
    now = time.time() if now is None else now
    digest = hashlib.sha256(bucket.encode("utf-8")).hexdigest()
    with _db(db_path) as conn:
        ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT window_started_at,attempts FROM arena_auth_rate_limits WHERE bucket_hash=?",
            (digest,),
        ).fetchone()
        if row is None or row["window_started_at"] + window_seconds <= now:
            conn.execute(
                "INSERT INTO arena_auth_rate_limits(bucket_hash,window_started_at,attempts) VALUES(?,?,1) "
                "ON CONFLICT(bucket_hash) DO UPDATE SET window_started_at=excluded.window_started_at,attempts=1",
                (digest, now),
            )
            remaining = limit - 1
            retry_after = 0
        elif row["attempts"] >= limit:
            remaining = 0
            retry_after = max(1, int(row["window_started_at"] + window_seconds - now))
        else:
            conn.execute(
                "UPDATE arena_auth_rate_limits SET attempts=attempts+1 WHERE bucket_hash=?",
                (digest,),
            )
            remaining = limit - int(row["attempts"]) - 1
            retry_after = 0
        conn.execute(
            "DELETE FROM arena_auth_rate_limits WHERE window_started_at < ?",
            (now - 86400,),
        )
        conn.execute("DELETE FROM arena_auth_challenges WHERE expires_at < ?", (now - 3600,))
        conn.execute("DELETE FROM arena_operator_enrollments WHERE expires_at < ?", (now - 86400,))
        conn.execute(
            "DELETE FROM arena_operator_sessions WHERE expires_at < ? OR revoked_at < ?",
            (now - 604800, now - 604800),
        )
        conn.commit()
    if retry_after:
        raise OperatorRateLimitError(retry_after)
    return remaining


class OperatorRateLimitError(OperatorAuthError):
    def __init__(self, retry_after):
        super().__init__("Przekroczono limit prób. Spróbuj ponownie później.")
        self.retry_after = int(retry_after)


def list_operators(db_path):
    with _db(db_path) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            """SELECT o.operator_id,o.display_name,o.role,o.active,o.created_at,
            COUNT(CASE WHEN c.revoked_at IS NULL THEN 1 END) AS active_credentials
            FROM arena_operators o LEFT JOIN arena_webauthn_credentials c
            ON c.operator_id=o.operator_id
            GROUP BY o.operator_id ORDER BY o.created_at,o.display_name"""
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["credentials"] = [dict(credential) for credential in conn.execute(
                """SELECT credential_id,created_at,last_used_at,sign_count,device_type,backed_up
                FROM arena_webauthn_credentials WHERE operator_id=? AND revoked_at IS NULL
                ORDER BY created_at""",
                (item["operator_id"],),
            ).fetchall()]
            result.append(item)
        return result


def revoke_credential(db_path, owner_operator_id, credential_id):
    if not isinstance(credential_id, str) or not credential_id or len(credential_id) > 4096:
        raise OperatorAuthError("Nieprawidłowy identyfikator poświadczenia.")
    now = time.time()
    with _db(db_path) as conn:
        ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        owner = conn.execute(
            "SELECT 1 FROM arena_operators WHERE operator_id=? AND role='owner' AND active=1",
            (owner_operator_id,),
        ).fetchone()
        if owner is None:
            raise OperatorAuthError("Wymagane aktywne konto właściciela.")
        row = conn.execute(
            """SELECT c.operator_id,o.role,o.active,
            (SELECT COUNT(*) FROM arena_webauthn_credentials c2
             WHERE c2.operator_id=c.operator_id AND c2.revoked_at IS NULL) AS active_credentials
            FROM arena_webauthn_credentials c JOIN arena_operators o
            ON o.operator_id=c.operator_id
            WHERE c.credential_id=? AND c.revoked_at IS NULL""",
            (credential_id,),
        ).fetchone()
        if row is None:
            raise OperatorAuthError("Nie znaleziono aktywnego poświadczenia.")
        if row["role"] == "owner" and row["active"] and row["active_credentials"] <= 1:
            raise OperatorAuthError("Nie można odebrać ostatniego aktywnego Passkey właściciela.")
        conn.execute(
            "UPDATE arena_webauthn_credentials SET revoked_at=? WHERE credential_id=? AND revoked_at IS NULL",
            (now, credential_id),
        )
        conn.execute(
            "UPDATE arena_operator_sessions SET revoked_at=? WHERE operator_id=? AND revoked_at IS NULL",
            (now, row["operator_id"]),
        )
        conn.commit()
        return {"operator_id": row["operator_id"], "sessions_revoked": True}


def suspend_operator(db_path, owner_operator_id, target_operator_id):
    if not isinstance(target_operator_id, str) or len(target_operator_id) > 64:
        raise OperatorAuthError("Nieprawidłowy identyfikator operatora.")
    now = time.time()
    with _db(db_path) as conn:
        ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        owner = conn.execute(
            "SELECT 1 FROM arena_operators WHERE operator_id=? AND role='owner' AND active=1",
            (owner_operator_id,),
        ).fetchone()
        if owner is None:
            raise OperatorAuthError("Wymagane aktywne konto właściciela.")
        target = conn.execute(
            "SELECT operator_id,role,active FROM arena_operators WHERE operator_id=?",
            (target_operator_id,),
        ).fetchone()
        if target is None:
            raise OperatorAuthError("Nie znaleziono operatora.")
        if target["operator_id"] == owner_operator_id:
            raise OperatorAuthError("Właściciel nie może zawiesić własnego konta.")
        if target["role"] == "owner" and target["active"]:
            active_owners = conn.execute(
                "SELECT COUNT(*) FROM arena_operators WHERE role='owner' AND active=1"
            ).fetchone()[0]
            if active_owners <= 1:
                raise OperatorAuthError("Nie można zawiesić ostatniego aktywnego właściciela.")
        conn.execute("UPDATE arena_operators SET active=0 WHERE operator_id=?", (target_operator_id,))
        conn.execute(
            "UPDATE arena_operator_sessions SET revoked_at=? WHERE operator_id=? AND revoked_at IS NULL",
            (now, target_operator_id),
        )
        conn.commit()
        return {"operator_id": target_operator_id, "sessions_revoked": True}

def get_session(db_path, session_token, now=None):
    if not isinstance(session_token, str) or len(session_token) > 256:
        return None
    now = time.time() if now is None else now
    session_hash = hashlib.sha256(session_token.encode()).hexdigest()
    with _db(db_path) as conn:
        row = conn.execute(
            """SELECT s.csrf_hash,s.created_at,s.last_seen_at,s.expires_at,
            o.operator_id,o.display_name,o.role,o.active
            FROM arena_operator_sessions s JOIN arena_operators o
            ON o.operator_id=s.operator_id
            WHERE s.session_hash=? AND s.revoked_at IS NULL""",
            (session_hash,),
        ).fetchone()
        if row is None or not row["active"] or row["expires_at"] <= now or row["last_seen_at"] + SESSION_IDLE_SECONDS <= now:
            return None
        conn.execute(
            "UPDATE arena_operator_sessions SET last_seen_at=? WHERE session_hash=? AND revoked_at IS NULL",
            (now, session_hash),
        )
        return {
            "operator_id": row["operator_id"], "display_name": row["display_name"],
            "role": row["role"], "csrf_hash": row["csrf_hash"],
        }


def verify_csrf(session, csrf_token):
    if not session or not isinstance(csrf_token, str) or len(csrf_token) > 256:
        return False
    candidate = hashlib.sha256(csrf_token.encode()).hexdigest()
    return secrets.compare_digest(candidate, session.get("csrf_hash", ""))


from http.cookies import SimpleCookie


def bind_verified_request(db_path, config, handler, require_csrf=False):
    """Attach actor/role only after opaque session, expiry, origin and CSRF checks."""
    try:
        cookies = SimpleCookie()
        cookies.load(handler.headers.get("Cookie", ""))
        morsel = cookies.get("arena_session")
        if morsel is None:
            return None
        session = get_session(db_path, morsel.value)
        if session is None:
            return None
        if require_csrf:
            if handler.headers.get("Origin") != config.origin:
                return None
            if not verify_csrf(session, handler.headers.get("X-Arena-CSRF", "")):
                return None
        handler._arena_verified_actor = session["operator_id"]
        handler._arena_verified_role = session["role"]
        return session
    except (AttributeError, OperatorAuthError, ValueError):
        return None


def revoke_session(db_path, session_token):
    if not isinstance(session_token, str) or len(session_token) > 256:
        return False
    digest = hashlib.sha256(session_token.encode()).hexdigest()
    with _db(db_path) as conn:
        cur = conn.execute(
            "UPDATE arena_operator_sessions SET revoked_at=? WHERE session_hash=? AND revoked_at IS NULL",
            (time.time(), digest),
        )
        return cur.rowcount == 1
