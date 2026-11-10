"""Account store (PostgreSQL): scrypt password hashes + signed, expiring bearer tokens.

Tokens carry a ``ver`` that must match the account's ``token_ver``; changing a password bumps it,
which signs the user out everywhere. This is a compact single-instance design, not a replacement
for a real identity provider.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time

from psycopg.errors import UniqueViolation

from . import db
from .config import AUTH_CACHE_SECONDS

SECRET = os.getenv("AUTH_SECRET", "")
TOKEN_TTL = 86400 * 7
MIN_PASSWORD = 10
MAX_PASSWORD = 256  # scrypt is deliberately slow: do not let someone hash megabytes of input
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}
_DUMMY_SALT = b"\x00" * 16
_verify_cache: dict[str, tuple[str, int, float]] = (
    {}
)  # sha256(token) -> (user id, ver, valid until)
_cache_lock = threading.Lock()
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def secret_problem(secret: str | None = None) -> str | None:
    """Return why the auth secret is unusable, or None if it is fine."""
    s = SECRET if secret is None else secret
    if not s:
        return "AUTH_SECRET is not set."
    if len(s) < 32:
        return "AUTH_SECRET must be at least 32 characters."
    if s.lower().startswith(("replace", "changeme", "change_me", "your_", "secret")):
        return "AUTH_SECRET is still a placeholder - generate a random one."
    return None


def _require_secret() -> str:
    problem = secret_problem()
    if problem:
        raise RuntimeError(
            f'{problem} Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    return SECRET


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)


def user_id_for(email: str) -> str:
    return hashlib.sha256(normalize_email(email).encode()).hexdigest()[:24]


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _check_password_rules(email: str, password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"Password must contain at least {MIN_PASSWORD} characters.")
    if len(password) > MAX_PASSWORD:
        raise ValueError(f"Password must be at most {MAX_PASSWORD} characters.")
    if password.strip().lower() == email:
        raise ValueError("Password must not be the same as the email address.")


def register(email: str, password: str) -> str:
    email = normalize_email(email)
    if not _EMAIL_RE.fullmatch(email) or len(email) > 254:
        raise ValueError("Enter a valid email address.")
    _check_password_rules(email, password)
    salt = os.urandom(16)
    uid = user_id_for(email)
    try:
        with db.autocommit() as conn:
            conn.execute(
                "INSERT INTO users (id, email, salt, password_hash) VALUES (%s, %s, %s, %s)",
                (uid, email, salt, _hash(password, salt)),
            )
    except UniqueViolation:
        raise ValueError("An account with this email already exists.") from None
    return uid


def login(email: str, password: str) -> str:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT id, salt, password_hash, token_ver, is_active FROM users WHERE email = %s",
            (normalize_email(email),),
        ).fetchone()
        # Hash even for unknown emails so response time does not reveal which accounts exist.
        digest = _hash(password[:MAX_PASSWORD], bytes(row["salt"]) if row else _DUMMY_SALT)
        if (
            not row
            or not row["is_active"]
            or not hmac.compare_digest(digest, bytes(row["password_hash"]))
        ):
            raise ValueError("Incorrect email or password.")
        conn.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (row["id"],))
    return token_for(row["id"], row["token_ver"])


def change_password(uid: str, old_password: str, new_password: str) -> str:
    """Change the password (needs the old one) and return a fresh token. Old tokens stop working."""
    with db.connection() as conn:
        row = conn.execute(
            "SELECT email, salt, password_hash, token_ver FROM users "
            "WHERE id = %s AND is_active FOR UPDATE",
            (uid,),
        ).fetchone()
        if not row or not hmac.compare_digest(
            _hash(old_password[:MAX_PASSWORD], bytes(row["salt"])), bytes(row["password_hash"])
        ):
            raise ValueError("Current password is incorrect.")
        _check_password_rules(row["email"], new_password)
        token = _store_new_password(conn, uid, new_password, row["token_ver"])
    _forget_cached_tokens(
        uid
    )  # after the commit: this process stops accepting the old tokens at once
    return token


def admin_set_password(email: str, new_password: str) -> None:
    """For the operator CLI (scripts/reset_password.py): no old password needed."""
    email = normalize_email(email)
    with db.connection() as conn:
        row = conn.execute(
            "SELECT id, token_ver FROM users WHERE email = %s FOR UPDATE", (email,)
        ).fetchone()
        if not row:
            raise ValueError("No account with that email.")
        _check_password_rules(email, new_password)
        _store_new_password(conn, row["id"], new_password, row["token_ver"])
    _forget_cached_tokens(row["id"])


def _forget_cached_tokens(uid: str) -> None:
    with _cache_lock:
        for key in [k for k, v in _verify_cache.items() if v[0] == uid]:
            del _verify_cache[key]


def _store_new_password(conn, uid: str, new_password: str, old_ver: int) -> str:
    salt = os.urandom(16)
    conn.execute(
        "UPDATE users SET salt = %s, password_hash = %s, token_ver = %s WHERE id = %s",
        (salt, _hash(new_password, salt), old_ver + 1, uid),
    )
    return token_for(uid, old_ver + 1)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _sign(payload: str) -> str:
    return _b64(hmac.new(_require_secret().encode(), payload.encode(), hashlib.sha256).digest())


def token_for(uid: str, ver: int = 0) -> str:
    now = int(time.time())
    payload = _b64(
        json.dumps({"sub": uid, "iat": now, "exp": now + TOKEN_TTL, "ver": ver}).encode()
    )
    return payload + "." + _sign(payload)


def verify(token: str) -> str:
    if secret_problem() or not token or "." not in token:
        raise ValueError("Authentication required.")
    payload, sig = token.split(".", 1)
    if not hmac.compare_digest(sig, _sign(payload)):
        raise ValueError("Invalid authentication token.")
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * ((4 - len(payload) % 4) % 4)))
    except ValueError:
        raise ValueError("Invalid authentication token.") from None
    if data.get("exp", 0) < time.time():
        raise ValueError("Session expired. Please sign in again.")
    key = hashlib.sha256(token.encode()).hexdigest()
    now = time.monotonic()
    if AUTH_CACHE_SECONDS > 0:
        with _cache_lock:
            hit = _verify_cache.get(key)
        if hit and hit[2] > now and hit[1] == data.get("ver", 0):
            return hit[0]
    with db.autocommit() as conn:
        row = conn.execute(
            "SELECT token_ver FROM users WHERE id = %s AND is_active", (data["sub"],)
        ).fetchone()
    if not row or row["token_ver"] != data.get("ver", 0):
        raise ValueError("Session is no longer valid. Please sign in again.")
    if AUTH_CACHE_SECONDS > 0:
        with _cache_lock:
            if len(_verify_cache) > 5000:
                _verify_cache.clear()
            _verify_cache[key] = (data["sub"], row["token_ver"], now + AUTH_CACHE_SECONDS)
    return data["sub"]
