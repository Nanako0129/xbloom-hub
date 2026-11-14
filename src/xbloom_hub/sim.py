"""A stand-in machine for tests and UI work: `Machine(SimRadio(), SimListener)`.

It goes through the real gate, so the simulated path exercises the same
allow-list as the hardware path. Its brew is a guess at what the machine
reports (activity 34, a 40510 per pour, rising water and weight, 40512, then
home); the real sequence has not been observed yet.
"""
from __future__ import annotations

import asyncio
import os

from xbloom.ble import (
    CMD_HANDSHAKE, FFE1_UUID, HANDSHAKE_DATA, CommandRefused, _build_frame,
    build_brew_frames, frame_command_code,
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
    brew_seconds = float(os.environ.get("XBLOOM_SIM_BREW_S", "45"))
    start_answer = "accept"         # accept | refuse | lost (taken, no echo) | ignore
    brewing_activity = 35
    linger_at_brewing = 0           # status frames still saying "brewing" after the end
    # How long the screen after a brew stays. The machine: about 5 s still at
    # the brewing value, then 20 s at 36. Short by default so tests are quick.
    done_seconds = float(os.environ.get("XBLOOM_SIM_DONE_S", "0.05"))

    def __init__(self, resolver, mode_name, notification_filter, on_event, on_lifecycle,
                 idle_timeout_s=300.0, on_raw=None) -> None:
        self._on_lifecycle = on_lifecycle
        self._on_raw = on_raw or (lambda decoded: None)
        self._client = None
        self._weight = 132.4
        self._water = 0.0
        self._recipe: dict | None = None
        self._ticker: asyncio.Task | None = None
        self._brewing: asyncio.Task | None = None

    # What the machine reports on connecting: home; a test of a link that comes
    # back in the middle of a brew sets 35 (or 36 for one finished meanwhile).
    start_activity = 1

    async def start(self) -> None:
        self._client = self.client_class()
        await self._client.write_gatt_char(
            FFE1_UUID, _build_frame(CMD_HANDSHAKE, list(HANDSHAKE_DATA)))
        self._on_lifecycle("ready", {"summary": {}})
        self._on_raw({"cmd": 40521, "fw_version": "SIM", "mode": "pro", "water_enough": 1,
                      "serial": "SIM0000000000"})
        self._on_raw({"cmd": 20501, "weight_g": self._weight})
        self._on_raw({"cmd": 8023, "activity": self.start_activity})
        self._ticker = asyncio.create_task(self._tick())

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(0.2)
            self._on_raw({"cmd": 20501, "weight_g": round(self._weight, 1)})
            self._on_raw({"cmd": 40523, "water_ml": round(self._water * 1000, 1)})

    async def stop(self) -> None:
        for task in (self._ticker, self._brewing):
            if task is not None:
                task.cancel()
        self._ticker = self._brewing = None

    async def send_prepare(self, recipe: dict) -> None:
        for frame in build_brew_frames(recipe)[1:4]:
            await self._client.write_gatt_char(FFE1_UUID, frame)
        self._recipe = recipe
        self._weight = 0.0   # the real machine tares on entering the recipe screen
        self._on_raw({"cmd": 20501, "weight_g": self._weight})
        self._on_raw({"cmd": 8023, "activity": 31})

    async def send_confirmed(self, name: str, frame: bytes) -> bool:
        code = frame_command_code(frame)
        await self._client.write_gatt_char(FFE1_UUID, frame)
        if code == 8017:
            self._recipe = None
            self._on_raw({"cmd": 8023, "activity": 1})
        elif code == 8002:
            if self.start_answer == "refuse":
                raise CommandRefused("execute", "machine_busy")
            if self.start_answer == "ignore":
                return False
            self._brewing = asyncio.create_task(self._brew())
            return self.start_answer != "lost"
        elif code == 40519:
            if self._brewing is not None:
                self._brewing.cancel()
                self._brewing = None
            self._on_raw({"cmd": 8023, "activity": 1})
        return True

    # ---- things a person does at the machine ---------------------------- #
    def knob_start(self) -> None:
        self._brewing = asyncio.create_task(self._brew())

    def cup(self, on: bool) -> None:
        self._on_raw({"cmd": 9002 if on else 9008})

    async def _brew(self) -> None:
        pours = (self._recipe or {}).get("pours") or [
            {"volume_ml": 60, "pause_s": 1}, {"volume_ml": 120, "pause_s": 0}]
        total = sum(float(p["volume_ml"]) for p in pours) or 1.0
        pause_total = sum(float(p.get("pause_s", 0)) for p in pours)
        # Scale the recipe's own timing into brew_seconds.
        scale = self.brew_seconds / (total / 3.2 + pause_total)
        self._water = 0.0
        self._on_raw({"cmd": 8023, "activity": self.brewing_activity})
        for index, pour in enumerate(pours):
            self._on_raw({"cmd": 40510, "pour_index": index})
            volume = float(pour["volume_ml"])
            steps = max(1, int(volume / 3.2 * scale / 0.1))
            for _ in range(steps):
                await asyncio.sleep(0.1)
                self._water += volume / steps
                self._weight = max(0.0, self._water * 0.86 - 12)     # the bed holds some back
            await asyncio.sleep(float(pour.get("pause_s", 0)) * scale)
        self._on_raw({"cmd": 40523, "water_ml": round(self._water * 1000, 1)})
        self._on_raw({"cmd": 20501, "weight_g": round(self._weight, 1)})
        self._on_raw({"cmd": 40511})
        self._on_raw({"cmd": 40512})
        for _ in range(self.linger_at_brewing):
            self._on_raw({"cmd": 8023, "activity": self.brewing_activity})
        await asyncio.sleep(self.done_seconds / 5)
        self._on_raw({"cmd": 8023, "activity": 36})
        await asyncio.sleep(self.done_seconds)
        self._on_raw({"cmd": 40513})                 # also sent on leaving this screen
        self._recipe = None
        self._on_raw({"cmd": 8023, "activity": 1})
