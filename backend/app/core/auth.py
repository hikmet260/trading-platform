from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Passwords must contain at least 12 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + _b64(salt) + "$" + _b64(digest)


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode("utf-8"), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(actual, _unb64(expected))
    except (ValueError, TypeError):
        return False


def issue_access_token(user_id: str, tenant_id: str, secret: str, ttl_seconds: int = 1800) -> str:
    issued = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": user_id, "tid": tenant_id, "iat": issued, "exp": issued + ttl_seconds}
    header_encoded = _b64(json.dumps(header, separators=(",", ":")).encode())
    payload_encoded = _b64(json.dumps(payload, separators=(",", ":")).encode())
    body = f"{header_encoded}.{payload_encoded}"
    signature = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64(signature)}"


def verify_access_token(token: str, secret: str) -> dict[str, str | int]:
    try:
        header_part, payload_part, signature_part = token.split(".")
        body = f"{header_part}.{payload_part}"
        expected = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(signature_part)):
            raise ValueError("Invalid signature")
        header = json.loads(_unb64(header_part))
        payload = json.loads(_unb64(payload_part))
        if header != {"alg": "HS256", "typ": "JWT"}:
            raise ValueError("Unsupported token algorithm")
        if not isinstance(payload.get("sub"), str) or not isinstance(payload.get("tid"), str):
            raise ValueError("Token subject or tenant is invalid")
        if int(payload.get("exp", 0)) <= int(time.time()):
            raise ValueError("Token expired")
        return payload
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid or expired access token") from exc


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
