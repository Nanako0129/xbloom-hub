"""Pairing a device by QR code: a short-lived, single-use code for the token.

docs/PLAN.md, S3d. A paired page asks for a code and shows it as a QR code;
the phone that opens it trades the code for the hub's token once. One code is
alive at a time, kept only as its SHA-256, in memory: a restart voids it.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time

LIFETIME_S = 120
# secrets.token_urlsafe(16) is 22 characters of this alphabet.
CODE = re.compile(r"[A-Za-z0-9_-]{22}")


class BadPairingCode(PermissionError):
    """Wrong, malformed, used or expired: one name for all, so a caller
    cannot tell which."""


def _digest(code: str) -> bytes:
    return hashlib.sha256(code.encode()).digest()


class Pairing:
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._digest: bytes | None = None
        self._expires = 0.0
        self._claimed = False

    def start(self) -> str:
        """A new code; the one before it, if any, is void."""
        code = secrets.token_urlsafe(16)
        self._digest, self._expires, self._claimed = _digest(code), self._clock() + LIFETIME_S, False
        return code

    def cancel(self) -> None:
        self._digest, self._claimed = None, False

    def status(self) -> str:
        if self._claimed:
            return "claimed"
        return "waiting" if self._digest and self._clock() < self._expires else "none"

    def claim(self, code: object) -> None:
        """Spend the code or raise. Synchronous on purpose: the check and the
        forgetting happen with no await between them, so two claims racing
        for one code cannot both win (the hub runs one event loop)."""
        alive = self._digest is not None and self._clock() < self._expires
        matches = (isinstance(code, str) and CODE.fullmatch(code) is not None and alive
                   and hmac.compare_digest(_digest(code), self._digest))
        if not matches:
            raise BadPairingCode("bad pairing code")
        self._digest, self._claimed = None, True
