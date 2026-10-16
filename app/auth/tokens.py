"""JWT issuing and verification.

Demo mode issues tokens for synthetic personas via ``/auth/demo-login``. In a
real deployment an external identity provider would issue tokens; the API only
needs the verified ``sub`` claim, which maps to ``external_auth_subject``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.config import get_settings
from app.schemas.errors import Unauthorized

ALGORITHM = "HS256"
ISSUER = "voltcart-support-demo"
AUDIENCE = "voltcart-support-api"


def issue_token(subject: str, kind: str) -> tuple[str, datetime]:
    settings = get_settings()
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=settings.jwt_ttl_minutes)
    claims = {"sub": subject, "kind": kind, "iat": now, "exp": expires, "iss": ISSUER, "aud": AUDIENCE}
    return jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM), expires


def verify_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["sub", "exp", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("TOKEN_EXPIRED", "Authentication token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise Unauthorized("INVALID_TOKEN", "Authentication token is invalid.") from exc
