# SPDX-License-Identifier: LGPL-3.0-only
"""Server-verified authentication for the Algerian AI ERP Platform.

Remediation for the identity-header impersonation vulnerability in
``dz_erp/api.py`` (the former ``_authenticate`` trusted client-supplied
``X-User-Id`` / ``X-Company-Id`` headers with no credential whatsoever).

Design
------
Credentials are **opaque, high-entropy bearer tokens issued by the server**,
never self-asserted identity headers:

* ``POST /api/v1/auth/token`` exchanges a username/password for a token.
* The token is ``dzsess_`` + 256 bits of CSPRNG entropy, base64url encoded.
* Only the **SHA-256 digest** of the token is persisted in ``auth_tokens``;
  the plaintext is returned exactly once at issuance and is unrecoverable.
* Every request is verified **server-side** on each call: the token must exist,
  be unrevoked, be unexpired, and its owning user must still be active.
* ``company_id`` and ``role`` are **derived from the server-side user record**
  bound to the presented credential. They are never read from the request.

Opaque server-side tokens were chosen over stateless JWTs precisely because the
requirements demand *revocation*: a JWT remains valid until it expires even
after logout, user deactivation, or credential compromise, unless a secondary
blocklist (i.e. server-side state) is added anyway. Database-backed tokens give
immediate revocation and true expiry with no extra moving parts.

Passwords are stored as salted PBKDF2-HMAC-SHA256 (600,000 iterations, OWASP
2023 guidance) and compared in constant time.
"""

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .database import ERPDatabase

TOKEN_PREFIX = "dzsess_"
TOKEN_ENTROPY_BYTES = 32  # 256 bits of CSPRNG entropy
PBKDF2_ITERATIONS = 600_000  # OWASP recommended floor for PBKDF2-HMAC-SHA256
DEFAULT_TOKEN_TTL_SECONDS = 8 * 60 * 60  # 8 hours
MAX_FAILED_LOGINS = 5
LOGIN_LOCKOUT_SECONDS = 15 * 60  # 15 minutes
MAX_PASSWORD_LENGTH = 1024  # bound PBKDF2 work per request
PASSWORD_HASH_ALGORITHM = "pbkdf2_sha256"

# Roles recognised by the schema CHECK constraint on users.role.
VALID_ROLES = (
    "clerk",
    "warehouse",
    "approver",
    "accountant",
    "treasury",
    "auditor",
    "admin",
    "ocr_worker",
)


class AuthError(Exception):
    """Base class for authentication failures. Maps to HTTP 401."""

    status_code = 401
    code = "AUTHENTICATION_FAILED"
    message = "Authentication failed."

    def __init__(self, message: Optional[str] = None) -> None:
        super().__init__(message or self.message)
        self.detail = message or self.message


class MissingCredentialError(AuthError):
    code = "MISSING_CREDENTIAL"
    message = "Missing Authorization header. Expected 'Authorization: Bearer <token>'."


class MalformedCredentialError(AuthError):
    code = "MALFORMED_CREDENTIAL"
    message = "Malformed Authorization header. Expected 'Authorization: Bearer <token>'."


class InvalidCredentialError(AuthError):
    code = "INVALID_CREDENTIAL"
    message = "Authentication credential is invalid."


class RevokedCredentialError(AuthError):
    code = "REVOKED_CREDENTIAL"
    message = "Authentication credential has been revoked."


class ExpiredCredentialError(AuthError):
    code = "EXPIRED_CREDENTIAL"
    message = "Authentication credential has expired."


class InactiveUserError(AuthError):
    code = "INACTIVE_USER"
    message = "User account is inactive or revoked."


class AccountLockedError(AuthError):
    code = "ACCOUNT_LOCKED"
    message = "Account is temporarily locked due to repeated failed sign-in attempts."


@dataclass(frozen=True)
class AuthenticatedUser:
    """Principal resolved from a server-verified credential.

    ``company_id`` and ``role`` come from the ``users`` row that owns the
    presented token, so they cannot be influenced by request headers.
    """

    user_id: int
    company_id: int
    username: str
    role: str
    token_id: int
    token_prefix: str
    expires_at: str
    branch_id: Optional[int] = None


# --------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# --------------------------------------------------------------------------
# Password hashing
# --------------------------------------------------------------------------


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    """Return ``pbkdf2_sha256$<iterations>$<salt_b64>$<digest_b64>``."""
    if not isinstance(password, str) or not password:
        raise ValueError("Password must be a non-empty string.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"Password exceeds the maximum accepted length of {MAX_PASSWORD_LENGTH} characters.")
    if iterations < 1:
        raise ValueError("PBKDF2 iteration count must be >= 1.")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join(
        [
            PASSWORD_HASH_ALGORITHM,
            str(iterations),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, stored_hash: Optional[str]) -> bool:
    """Constant-time verification of ``password`` against a stored hash."""
    if not stored_hash or not isinstance(stored_hash, str):
        return False
    try:
        algorithm, iterations_s, salt_b64, digest_b64 = stored_hash.split("$")
        iterations = int(iterations_s)
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(digest_b64, validate=True)
    except (ValueError, TypeError):
        return False
    if algorithm != PASSWORD_HASH_ALGORITHM or iterations < 1 or not salt or not expected:
        return False
    candidate = (password or "").encode("utf-8")
    if len(candidate) > MAX_PASSWORD_LENGTH * 4:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", candidate, salt, iterations)
    return hmac.compare_digest(actual, expected)


# --------------------------------------------------------------------------
# Token primitives
# --------------------------------------------------------------------------


def generate_session_token() -> str:
    """Return a fresh 256-bit opaque bearer token."""
    raw = base64.urlsafe_b64encode(os.urandom(TOKEN_ENTROPY_BYTES)).decode("ascii").rstrip("=")
    return f"{TOKEN_PREFIX}{raw}"


def hash_token(token: str) -> str:
    """SHA-256 hex digest of a token; this is what gets persisted."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def parse_bearer_token(authorization: Optional[str]) -> str:
    """Extract the bearer token from an ``Authorization`` header value.

    The scheme is matched case-insensitively per RFC 7235.
    """
    if authorization is None or not authorization.strip():
        raise MissingCredentialError()
    parts = authorization.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise MalformedCredentialError()
    token = parts[1].strip()
    if not token:
        raise MalformedCredentialError()
    if any(ch.isspace() for ch in token):
        raise MalformedCredentialError()
    return token


_DUMMY_HASH: Optional[str] = None


def _dummy_password_hash() -> str:
    """A real-format hash used to equalise timing for unknown usernames."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(os.urandom(32).hex())
    return _DUMMY_HASH


# --------------------------------------------------------------------------
# Auth manager
# --------------------------------------------------------------------------


class AuthManager:
    """Issues, verifies, and revokes server-side bearer session tokens."""

    def __init__(
        self,
        db: ERPDatabase,
        *,
        token_ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
        pbkdf2_iterations: int = PBKDF2_ITERATIONS,
        max_failed_logins: int = MAX_FAILED_LOGINS,
        lockout_seconds: int = LOGIN_LOCKOUT_SECONDS,
    ) -> None:
        if token_ttl_seconds <= 0:
            raise ValueError("token_ttl_seconds must be positive.")
        self._db = db
        self._token_ttl_seconds = int(token_ttl_seconds)
        self._pbkdf2_iterations = int(pbkdf2_iterations)
        self._max_failed_logins = int(max_failed_logins)
        self._lockout_seconds = int(lockout_seconds)

    # -- user provisioning --------------------------------------------------

    def create_user(
        self,
        *,
        username: str,
        password: str,
        role: str,
        company_id: int,
        branch_id: Optional[int] = None,
        actor_user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        if role not in VALID_ROLES:
            raise ValueError(f"Unknown role '{role}'. Expected one of {list(VALID_ROLES)}.")
        password_hash = hash_password(password, iterations=self._pbkdf2_iterations)
        now = _utcnow()
        with self._db.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO users (company_id, branch_id, username, role, is_active,
                                   password_hash, failed_login_count, password_updated_at)
                VALUES (?, ?, ?, ?, 1, ?, 0, ?)
                """,
                (company_id, branch_id, username, role, password_hash, _iso(now)),
            )
            user_id = int(cur.lastrowid)
            if actor_user_id is not None:
                self._db.append_audit_event(
                    conn=conn,
                    company_id=company_id,
                    actor_user_id=actor_user_id,
                    action_type="AUTH_USER_CREATED",
                    target_model="users",
                    target_id=str(user_id),
                    after_state={"username": username, "role": role},
                )
        return {"user_id": user_id, "username": username, "role": role, "company_id": company_id}

    def set_password(self, user_id: int, new_password: str, *, actor_user_id: Optional[int] = None) -> None:
        """Rotate a user's password and revoke every token already issued to them."""
        password_hash = hash_password(new_password, iterations=self._pbkdf2_iterations)
        now = _utcnow()
        with self._db.transaction() as conn:
            user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                raise ValueError(f"User {user_id} does not exist.")
            conn.execute(
                "UPDATE users SET password_hash = ?, password_updated_at = ?, failed_login_count = 0, locked_until = NULL WHERE id = ?",
                (password_hash, _iso(now), user_id),
            )
            conn.execute(
                "UPDATE auth_tokens SET revoked_at = ?, revocation_reason = ? WHERE user_id = ? AND revoked_at IS NULL",
                (_iso(now), "PASSWORD_ROTATED", user_id),
            )
            if actor_user_id is not None:
                self._db.append_audit_event(
                    conn=conn,
                    company_id=int(user["company_id"]),
                    actor_user_id=actor_user_id,
                    action_type="AUTH_PASSWORD_ROTATED",
                    target_model="users",
                    target_id=str(user_id),
                    after_state={"username": user["username"], "tokens_revoked": True},
                )

    def deactivate_user(self, user_id: int, *, actor_user_id: Optional[int] = None, reason: str = "DEACTIVATED") -> int:
        """Disable a user and immediately revoke all of their live tokens."""
        now = _utcnow()
        with self._db.transaction() as conn:
            user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                raise ValueError(f"User {user_id} does not exist.")
            cur = conn.execute(
                "UPDATE auth_tokens SET revoked_at = ?, revocation_reason = ? WHERE user_id = ? AND revoked_at IS NULL",
                (_iso(now), f"USER_{reason}", user_id),
            )
            revoked = int(cur.rowcount or 0)
            conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user_id,))
            if actor_user_id is not None:
                self._db.append_audit_event(
                    conn=conn,
                    company_id=int(user["company_id"]),
                    actor_user_id=actor_user_id,
                    action_type="AUTH_USER_DEACTIVATED",
                    target_model="users",
                    target_id=str(user_id),
                    after_state={"username": user["username"], "reason": reason, "tokens_revoked": revoked},
                )
        return revoked

    # -- token issuance -----------------------------------------------------

    def issue_token(
        self,
        username: str,
        password: str,
        *,
        ttl_seconds: Optional[int] = None,
        issued_to_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Verify a username/password and mint a new bearer session token.

        Failure paths (unknown user, wrong password, locked account, inactive
        account) are audited and committed in their own transactions *before*
        the error is raised; raising from inside the verification transaction
        would roll the security-relevant audit record back with it.
        """
        ttl = self._token_ttl_seconds if ttl_seconds is None else int(ttl_seconds)
        if ttl <= 0:
            raise ValueError("ttl_seconds must be positive.")
        now = _utcnow()

        # Phase 1 — resolve the account and verify the credential.
        with self._db.transaction() as conn:
            user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        account = dict(user) if user is not None else None

        if account is None:
            # Burn the same PBKDF2 work so an unknown username is not
            # distinguishable from a wrong password by response timing.
            verify_password(password, _dummy_password_hash())
            raise InvalidCredentialError("Invalid username or password.")

        locked_until = _parse(account["locked_until"])
        if locked_until is not None and locked_until > now:
            self._audit_in_own_transaction(
                account,
                "AUTH_LOGIN_REJECTED_ACCOUNT_LOCKED",
                {"username": username, "locked_until": account["locked_until"]},
            )
            raise AccountLockedError()

        if not account["is_active"]:
            self._audit_in_own_transaction(account, "AUTH_LOGIN_REJECTED_INACTIVE", {"username": username})
            raise InactiveUserError()

        if not verify_password(password, account["password_hash"]):
            self._register_failed_login(account, username, now)
            raise InvalidCredentialError("Invalid username or password.")

        # Phase 2 — mint the token and reset the failure counters atomically.
        token = generate_session_token()
        token_hash = hash_token(token)
        token_prefix = token[: len(TOKEN_PREFIX) + 6]
        expires_at = now + timedelta(seconds=ttl)
        with self._db.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO auth_tokens (user_id, token_hash, token_prefix, issued_at, expires_at,
                                         issued_to_ip, user_agent)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (account["id"], token_hash, token_prefix, _iso(now), _iso(expires_at), issued_to_ip, user_agent),
            )
            token_id = int(cur.lastrowid)
            conn.execute(
                "UPDATE users SET failed_login_count = 0, locked_until = NULL WHERE id = ?",
                (account["id"],),
            )
            self._audit(
                conn,
                account,
                "AUTH_TOKEN_ISSUED",
                {
                    "username": account["username"],
                    "role": account["role"],
                    "token_id": token_id,
                    "token_prefix": token_prefix,
                    "expires_at": _iso(expires_at),
                },
            )

        # The plaintext token is returned exactly once and never persisted.
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_at": _iso(expires_at),
            "expires_in": ttl,
            "token_id": token_id,
            "user": {
                "user_id": int(account["id"]),
                "company_id": int(account["company_id"]),
                "username": account["username"],
                "role": account["role"],
            },
        }

    def _register_failed_login(self, account: Dict[str, Any], username: str, now: datetime) -> None:
        """Count a failed sign-in and lock the account past the threshold."""
        failed = int(account["failed_login_count"] or 0) + 1
        locked_until: Optional[str] = None
        action = "AUTH_LOGIN_FAILED"
        if failed >= self._max_failed_logins:
            locked_until = _iso(now + timedelta(seconds=self._lockout_seconds))
            failed = 0
            action = "AUTH_ACCOUNT_LOCKED"
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE users SET failed_login_count = ?, locked_until = ? WHERE id = ?",
                (failed, locked_until, account["id"]),
            )
            self._audit(conn, account, action, {"username": username, "locked_until": locked_until})

    def _audit_in_own_transaction(self, account: Dict[str, Any], action_type: str, after_state: Dict[str, Any]) -> None:
        with self._db.transaction() as conn:
            self._audit(conn, account, action_type, after_state)

    # -- token verification -------------------------------------------------

    def authenticate(self, authorization: Optional[str]) -> AuthenticatedUser:
        """Resolve the principal behind an ``Authorization`` header value.

        Raises a subclass of :class:`AuthError` for every failure mode. Identity
        attributes are read from the server-side ``users`` row, never from the
        request headers.
        """
        token = parse_bearer_token(authorization)
        token_hash = hash_token(token)
        now = _utcnow()

        principal: Optional[AuthenticatedUser] = None
        denial: Optional[AuthError] = None

        with self._db.transaction() as conn:
            row = conn.execute("SELECT * FROM auth_tokens WHERE token_hash = ?", (token_hash,)).fetchone()
            if row is None:
                denial = InvalidCredentialError()
            else:
                user = conn.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()

                if row["revoked_at"] is not None:
                    self._audit(
                        conn,
                        user,
                        "AUTH_CREDENTIAL_REVOKED_REJECTED",
                        {"token_id": int(row["id"]), "revoked_at": row["revoked_at"]},
                    )
                    denial = RevokedCredentialError()
                else:
                    expires_at = _parse(row["expires_at"])
                    if expires_at is None or expires_at <= now:
                        self._audit(
                            conn,
                            user,
                            "AUTH_CREDENTIAL_EXPIRED_REJECTED",
                            {"token_id": int(row["id"]), "expires_at": row["expires_at"]},
                        )
                        denial = ExpiredCredentialError()
                    elif user is None or not user["is_active"]:
                        self._audit(conn, user, "AUTH_USER_INACTIVE_REJECTED", {"token_id": int(row["id"])})
                        denial = InactiveUserError()
                    elif not hmac.compare_digest(row["token_hash"], token_hash):
                        # Defence in depth: constant-time digest confirmation.
                        denial = InvalidCredentialError()
                    else:
                        conn.execute(
                            "UPDATE auth_tokens SET last_used_at = ? WHERE id = ?",
                            (_iso(now), row["id"]),
                        )
                        principal = AuthenticatedUser(
                            user_id=int(user["id"]),
                            company_id=int(user["company_id"]),
                            branch_id=(int(user["branch_id"]) if user["branch_id"] is not None else None),
                            username=user["username"],
                            role=user["role"],
                            token_id=int(row["id"]),
                            token_prefix=row["token_prefix"],
                            expires_at=row["expires_at"],
                        )

        if denial is not None:
            raise denial
        assert principal is not None
        return principal

    # -- revocation ---------------------------------------------------------

    def revoke_token(self, token: str, *, reason: str = "USER_LOGOUT", actor_user_id: Optional[int] = None) -> bool:
        token_hash = hash_token(token)
        now = _utcnow()
        with self._db.transaction() as conn:
            row = (
                conn.execute(
                    "SELECT * FROM auth_tokens WHERE token_hash = ? AND revoked_at IS NULL",
                    (token_hash,),
                )
                .fetchone()
            )
            if row is None:
                return False
            user = conn.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
            conn.execute(
                "UPDATE auth_tokens SET revoked_at = ?, revocation_reason = ? WHERE id = ?",
                (_iso(now), reason, row["id"]),
            )
            self._audit(
                conn,
                user,
                "AUTH_TOKEN_REVOKED",
                {"token_id": int(row["id"]), "reason": reason},
                actor_user_id=actor_user_id,
            )
        return True

    def revoke_all_tokens_for_user(
        self, user_id: int, *, reason: str = "ADMIN_REVOCATION", actor_user_id: Optional[int] = None
    ) -> int:
        now = _utcnow()
        with self._db.transaction() as conn:
            user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                return 0
            cur = conn.execute(
                "UPDATE auth_tokens SET revoked_at = ?, revocation_reason = ? WHERE user_id = ? AND revoked_at IS NULL",
                (_iso(now), reason, user_id),
            )
            revoked = int(cur.rowcount or 0)
            self._audit(
                conn,
                user,
                "AUTH_ALL_TOKENS_REVOKED",
                {"reason": reason, "tokens_revoked": revoked},
                actor_user_id=actor_user_id,
            )
        return revoked

    def purge_expired_tokens(self) -> int:
        """Housekeeping: drop token rows whose expiry has already elapsed."""
        now = _utcnow()
        with self._db.transaction() as conn:
            cur = conn.execute("DELETE FROM auth_tokens WHERE expires_at <= ?", (_iso(now),))
        return int(cur.rowcount or 0)

    def list_active_tokens(self, user_id: int) -> List[Dict[str, Any]]:
        """Non-secret metadata about a user's live tokens (for support/audit)."""
        now = _utcnow()
        with self._db.transaction() as conn:
            rows = conn.execute(
                """
                SELECT id, token_prefix, issued_at, expires_at, revoked_at, last_used_at, issued_to_ip
                FROM auth_tokens WHERE user_id = ? AND revoked_at IS NULL AND expires_at > ?
                ORDER BY id DESC
                """,
                (user_id, _iso(now)),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- internal -----------------------------------------------------------

    def _audit(
        self,
        conn: Any,
        user: Any,
        action_type: str,
        after_state: Dict[str, Any],
        actor_user_id: Optional[int] = None,
    ) -> None:
        if user is None:
            # No resolvable tenant: nothing safe to attribute the event to.
            return
        self._db.append_audit_event(
            conn=conn,
            company_id=int(user["company_id"]),
            actor_user_id=actor_user_id if actor_user_id is not None else int(user["id"]),
            action_type=action_type,
            target_model="auth_tokens",
            target_id=str(after_state.get("token_id", after_state.get("username", "-"))),
            after_state=after_state,
        )
