from __future__ import annotations

import hashlib
import hmac

from server.config import ADMIN_PASSWORD

_TOKEN_SALT = b"sscout-admin-v1"


def admin_token(password: str) -> str:
    if not password:
        return ""
    return hmac.new(password.encode("utf-8"), _TOKEN_SALT, hashlib.sha256).hexdigest()


def verify_admin_token(token: str) -> bool:
    if not ADMIN_PASSWORD or not token:
        return False
    expected = admin_token(ADMIN_PASSWORD)
    return hmac.compare_digest(token, expected)


def admin_configured() -> bool:
    return bool(ADMIN_PASSWORD)
