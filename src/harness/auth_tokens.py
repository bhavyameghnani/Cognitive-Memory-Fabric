"""Signed identity tokens: minimal HS256 JWT issue/verify using only the standard library.

Why this exists: CMF has no authentication of its own, and request headers such as X-CMF-Actor-Id / X-CMF-Actor-Role prove nothing (any caller can send
them).  Identity now comes ONLY from the claims of a token whose HMAC-SHA256 signature verifies against a configured secret:

    Authorization: Bearer <header>.<payload>.<signature>        payload = {"sub": actor id, "role": role, "exp": unix seconds, ...}

The token is issued by whatever authenticates people in front of CMF (an identity gateway, or `python -m harness.auth_tokens` for local testing); CMF only
verifies it.  Rules enforced here (each has a regression test):
  * the secret must be configured and at least MIN_SECRET_CHARS long;
  * the header algorithm must be exactly HS256 ("none", HS512, anything else is refused) and the signature is compared in constant time BEFORE the payload is trusted;
  * required claims: sub (non-empty string), role (non-empty string), exp (a real number, not a bool); expired tokens are refused;
  * nbf is honoured when present; iss / aud are enforced when the deployment configures them;
  * tokens longer than MAX_TOKEN_CHARS are refused before any parsing.
Whether the role is a KNOWN role is deliberately left to the PermissionGate (it answers 403 invalid_role), so a signed but meaningless role still gets nothing.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import time
from typing import Any, Optional

MIN_SECRET_CHARS = 32
MAX_TOKEN_CHARS = 8192


class AuthError(ValueError):
    """The token cannot be trusted.  The message is safe to show to the caller (it never contains the secret or the claims)."""


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    try:
        return base64.b64decode(s.replace("-", "+").replace("_", "/") + "=" * (-len(s) % 4), validate=True)
    except (binascii.Error, ValueError):
        raise AuthError("malformed token encoding") from None


def _check_secret(secret: Any) -> bytes:
    if not isinstance(secret, str) or len(secret) < MIN_SECRET_CHARS:
        raise AuthError(f"signing secret is missing or shorter than {MIN_SECRET_CHARS} characters")
    return secret.encode("utf-8")


def _sign(key: bytes, signing_input: str) -> bytes:
    return hmac.new(key, signing_input.encode("ascii"), hashlib.sha256).digest()


def issue_token(secret: str, actor: str, role: str, ttl_seconds: float = 3600, *, issuer: Optional[str] = None, audience: Optional[str] = None,
                now: Optional[float] = None, extra: Optional[dict] = None) -> str:
    key = _check_secret(secret)
    t = time.time() if now is None else now
    claims: dict = {"sub": actor, "role": role, "iat": int(t), "exp": int(t + ttl_seconds)}
    if issuer:
        claims["iss"] = issuer
    if audience:
        claims["aud"] = audience
    claims.update(extra or {})
    head = _b64e(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64e(json.dumps(claims, separators=(",", ":")).encode())
    return f"{head}.{body}.{_b64e(_sign(key, head + '.' + body))}"


def _number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def verify_token(token: Any, secret: str, *, now: Optional[float] = None, leeway: float = 0, issuer: Optional[str] = None, audience: Optional[str] = None) -> dict:
    """Return the verified claims or raise AuthError."""
    key = _check_secret(secret)
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_CHARS:
        raise AuthError("token is missing or too large")
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        raise AuthError("token must have three non-empty parts")
    head_b, body_b, sig_b = parts
    try:
        header = json.loads(_b64d(head_b))
    except (ValueError, UnicodeDecodeError):
        raise AuthError("malformed token header") from None
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        raise AuthError("unsupported token algorithm (only HS256 is accepted)")
    if not hmac.compare_digest(_b64d(sig_b), _sign(key, head_b + "." + body_b)):
        raise AuthError("invalid token signature")
    try:                                                   # only now is the payload trusted enough to parse
        claims = json.loads(_b64d(body_b))
    except (ValueError, UnicodeDecodeError):
        raise AuthError("malformed token payload") from None
    if not isinstance(claims, dict):
        raise AuthError("token payload must be an object")
    for name in ("sub", "role"):
        v = claims.get(name)
        if not isinstance(v, str) or not v.strip():
            raise AuthError(f"required claim '{name}' is missing or empty")
    if not _number(claims.get("exp")):
        raise AuthError("required claim 'exp' is missing or not a number")
    t = time.time() if now is None else now
    if claims["exp"] + leeway <= t:
        raise AuthError("token has expired")
    if "nbf" in claims and (not _number(claims["nbf"]) or claims["nbf"] - leeway > t):
        raise AuthError("token is not valid yet")
    if issuer and claims.get("iss") != issuer:
        raise AuthError("token issuer is not accepted")
    if audience:
        aud = claims.get("aud")
        if not (aud == audience or (isinstance(aud, list) and audience in aud)):
            raise AuthError("token audience is not accepted")
    return claims


def main(argv=None) -> int:
    """Mint a token for LOCAL testing:  python -m harness.auth_tokens --actor ADVISOR-001 --role advisor   (secret from CMF_AUTH_JWT_SECRET)."""
    ap = argparse.ArgumentParser(prog="harness.auth_tokens", description=main.__doc__)
    ap.add_argument("--actor", required=True)
    ap.add_argument("--role", required=True)
    ap.add_argument("--ttl", type=float, default=3600, help="lifetime in seconds (default 3600)")
    a = ap.parse_args(argv)
    try:
        print(issue_token(os.environ.get("CMF_AUTH_JWT_SECRET", ""), a.actor, a.role, a.ttl, issuer=os.environ.get("CMF_AUTH_ISSUER") or None,
                          audience=os.environ.get("CMF_AUTH_AUDIENCE") or None))
    except AuthError as e:
        print(f"cannot issue a token: {e}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
