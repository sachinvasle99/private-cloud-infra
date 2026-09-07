"""
Authentication — session-based auth with bcrypt password hashing.
Roles: admin (full access), read (view only).
"""

import hashlib
import hmac
import logging
import os
import secrets
import time
from typing import Optional, Dict

log = logging.getLogger(__name__)

# Session secret — generate once per process
SESSION_SECRET = os.environ.get("SESSION_SECRET", secrets.token_hex(32))
SESSION_EXPIRY = 86400 * 7  # 7 days

# In-memory session store: {token: {username, role, expires}}
_sessions: Dict[str, Dict] = {}


def validate_password(password: str) -> str:
    """Validate password policy. Returns error message or empty string if valid."""
    if len(password) < 8:
        return "Password must be at least 8 characters"
    if not any(c.isupper() for c in password):
        return "Password must contain at least one uppercase letter"
    if not any(c.islower() for c in password):
        return "Password must contain at least one lowercase letter"
    if not any(c.isdigit() for c in password):
        return "Password must contain at least one number"
    if not any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in password):
        return "Password must contain at least one special character (!@#$%^&*)"
    return ""


def hash_password(password: str) -> str:
    """Hash password with salt using SHA-256 (no bcrypt dependency needed)."""
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}:{h}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify password against stored hash."""
    if ":" not in stored_hash:
        return False
    salt, h = stored_hash.split(":", 1)
    return hmac.compare_digest(h, hashlib.sha256((salt + password).encode()).hexdigest())


def create_session(username: str, role: str) -> str:
    """Create a session token."""
    token = secrets.token_hex(32)
    _sessions[token] = {
        "username": username,
        "role": role,
        "expires": time.time() + SESSION_EXPIRY,
    }
    return token


def get_session(token: str) -> Optional[Dict]:
    """Get session data from token. Returns None if expired or invalid."""
    if not token or token not in _sessions:
        return None
    session = _sessions[token]
    if time.time() > session["expires"]:
        del _sessions[token]
        return None
    return session


def delete_session(token: str):
    """Logout — delete session."""
    _sessions.pop(token, None)


def cleanup_sessions():
    """Remove expired sessions."""
    now = time.time()
    expired = [t for t, s in _sessions.items() if now > s["expires"]]
    for t in expired:
        del _sessions[t]


def setup_default_admin(store):
    """Create default admin user if no users exist."""
    if store.user_count() == 0:
        default_pass = os.environ.get("ADMIN_PASSWORD", "admin123")
        store.create_user("admin", hash_password(default_pass), "admin")
        log.info("Created default admin user (username: admin, password: %s)",
                 "***" if default_pass != "admin123" else "admin123 — CHANGE THIS!")
