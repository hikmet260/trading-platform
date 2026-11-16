from __future__ import annotations

import json

from cryptography.fernet import Fernet, InvalidToken

from app.settings import settings


class CredentialVault:
    """Authenticated encryption for broker secrets at rest using a Fernet key."""

    def __init__(self, key: str | None = None) -> None:
        encoded_key = key if key is not None else settings.broker_credentials_key
        if not encoded_key:
            raise RuntimeError("BROKER_CREDENTIALS_KEY must be configured before broker credentials can be stored")
        try:
            self._fernet = Fernet(encoded_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise ValueError("BROKER_CREDENTIALS_KEY must be a valid Fernet key") from exc

    def encrypt(self, credentials: dict[str, str]) -> str:
        if not credentials or any(not key or not isinstance(value, str) for key, value in credentials.items()):
            raise ValueError("Credentials must be a non-empty map of names to strings")
        payload = json.dumps(credentials, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return self._fernet.encrypt(payload.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> dict[str, str]:
        try:
            value = json.loads(self._fernet.decrypt(token.encode("ascii")))
        except (InvalidToken, UnicodeEncodeError, json.JSONDecodeError) as exc:
            raise ValueError("Stored broker credentials could not be authenticated") from exc
        if not isinstance(value, dict) or any(not isinstance(key, str) or not isinstance(item, str)
                                              for key, item in value.items()):
            raise ValueError("Stored credential payload is invalid")
        return value
