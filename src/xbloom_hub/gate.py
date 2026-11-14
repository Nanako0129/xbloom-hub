"""The one place a GATT write can pass (docs/PLAN.md, safety invariant 1).

xbloom-py writes through ``client.write_gatt_char`` and nothing else, and it
picks the client class from ``bleak.BleakClient`` at connect time. Replacing
that class with a subclass whose ``write_gatt_char`` checks an allow-list
therefore covers every command the library can send, including the ones it
writes without going through ``write_confirmed``.
"""
from __future__ import annotations

import logging

from xbloom.ble import FFE1_UUID, frame_command_code

log = logging.getLogger(__name__)

# handshake, bypass+dose, set-cup, recipe (grinding / grinder off)
LOAD = frozenset({8100, 8102, 8104, 8001, 8004})
# quit the recipe-start screen; unverified on hardware until S1's attended test
CANCEL = frozenset({8017})
# Start (8002) and resume (40524) are deliberately in no list yet.
ALLOWED = LOAD | CANCEL

_FFE1 = FFE1_UUID.lower()


class GateRefused(RuntimeError):
    """A write the gate would not pass. Nothing reached the radio."""


def check(char_specifier: object, data: object) -> int:
    """Return the frame's command code, or raise GateRefused."""
    uuid = str(getattr(char_specifier, "uuid", char_specifier)).lower()
    try:
        code = frame_command_code(bytes(data))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        code = None
    if uuid != _FFE1 or code not in ALLOWED:
        raise GateRefused(f"write refused: characteristic={uuid} code={code}")
    return code


def make_gated(base: type) -> type:
    class Gated(base):  # type: ignore[misc, valid-type]
        xbloom_gated = True

        async def write_gatt_char(self, char_specifier, data, response=None):
            code = check(char_specifier, data)
            self.__dict__.setdefault("gate_log", []).append(code)
            log.info("gate pass %s", code)
            return await super().write_gatt_char(char_specifier, data, response=response)

    return Gated


def install() -> None:
    """Put the gated class everywhere a client class is picked up from.

    Call after habluetooth's setup (which installs its own wrapper in the same
    two places) and before any connection. Safe to call again.
    """
    import bleak
    import bleak_retry_connector

    if not getattr(bleak.BleakClient, "xbloom_gated", False):
        bleak.BleakClient = make_gated(bleak.BleakClient)
    bleak_retry_connector.BleakClient = bleak.BleakClient


def session_is_gated(client: object) -> bool:
    """True when this connected client is gated and its handshake went through
    the gate. Checked on every connect: a session that fails this is unusable.
    """
    return (
        getattr(type(client), "xbloom_gated", False) is True
        and getattr(client, "gate_log", [])[:1] == [8100]
    )
