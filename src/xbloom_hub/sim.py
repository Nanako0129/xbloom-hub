"""A stand-in machine for tests and UI work: `Machine(SimRadio(), SimListener)`.

It goes through the real gate, so the simulated path exercises the same
allow-list as the hardware path.
"""
from __future__ import annotations

import asyncio

from xbloom.ble import (
    CMD_HANDSHAKE, FFE1_UUID, HANDSHAKE_DATA, _build_frame, build_brew_frames,
    frame_command_code,
)

from . import gate


class SimRadio:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def find_device(self, timeout: float = 30.0):
        return object()


class _SimClient:
    def __init__(self) -> None:
        self.sent: list[int] = []
        self.is_connected = True

    async def write_gatt_char(self, char_specifier, data, response=None):
        self.sent.append(frame_command_code(bytes(data)))


class SimListener:
    """The part of XBloomModeListener's interface that Machine uses."""

    client_class = gate.make_gated(_SimClient)

    def __init__(self, resolver, mode_name, notification_filter, on_event, on_lifecycle,
                 idle_timeout_s=300.0, on_raw=None) -> None:
        self._on_lifecycle = on_lifecycle
        self._on_raw = on_raw or (lambda decoded: None)
        self._client = None
        self._weight = 132.4
        self._ticker: asyncio.Task | None = None

    async def start(self) -> None:
        self._client = self.client_class()
        await self._client.write_gatt_char(
            FFE1_UUID, _build_frame(CMD_HANDSHAKE, list(HANDSHAKE_DATA)))
        self._on_lifecycle("ready", {"summary": {}})
        self._on_raw({"cmd": 40521, "fw_version": "SIM", "mode": "pro", "water_enough": 1})
        self._on_raw({"cmd": 20501, "weight_g": self._weight})
        self._on_raw({"cmd": 8023, "activity": 1})
        self._ticker = asyncio.create_task(self._tick())

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(1)
            self._on_raw({"cmd": 20501, "weight_g": self._weight})

    async def stop(self) -> None:
        if self._ticker is not None:
            self._ticker.cancel()
            self._ticker = None

    async def send_prepare(self, recipe: dict) -> None:
        for frame in build_brew_frames(recipe)[1:4]:
            await self._client.write_gatt_char(FFE1_UUID, frame)
        self._weight = 0.0   # the real machine tares on entering the recipe screen
        self._on_raw({"cmd": 20501, "weight_g": self._weight})
        self._on_raw({"cmd": 8023, "activity": 31})

    async def send_confirmed(self, name: str, frame: bytes) -> bool:
        await self._client.write_gatt_char(FFE1_UUID, frame)
        if frame_command_code(frame) == 8017:
            self._on_raw({"cmd": 8023, "activity": 1})
        return True
