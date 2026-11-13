"""Where the Bluetooth radio comes from: an ESPHome proxy, or this computer."""
from __future__ import annotations

import asyncio
import time

from . import gate


def _is_xbloom(device, adv) -> bool:
    name = (getattr(device, "name", None) or getattr(adv, "local_name", None) or "").upper()
    return name.startswith("XBLOOM")


class ProxyRadio:
    """An ESPHome Bluetooth proxy reached by IP address, Noise-encrypted.

    There is no plaintext path: without a key anyone on the network could
    drive the machine through the proxy and bypass the hub (invariant 5).
    """

    def __init__(self, host: str, noise_psk: str) -> None:
        if not host or not noise_psk:
            raise ValueError("the proxy needs both an address and a Noise key")
        self._config = {"address": host, "noise_psk": noise_psk}
        self._conn = None

    async def start(self) -> None:
        import habluetooth
        from bleak_esphome.connection_manager import APIConnectionManager

        await habluetooth.BluetoothManager().async_setup()
        gate.install()
        # Reconnects to the proxy by itself and re-registers its scanner.
        self._conn = APIConnectionManager(self._config)
        await self._conn.start()

    async def stop(self) -> None:
        if self._conn is not None:
            await self._conn.stop()

    async def find_device(self, timeout: float = 30.0):
        # bleak.BleakScanner hears nothing through the proxy outside Home
        # Assistant (docs/S0-RESULTS.md, item 4); read the scanners' own tables.
        import habluetooth

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for scanner in habluetooth.get_manager().async_current_scanners():
                for device, adv in scanner.discovered_devices_and_advertisement_data.values():
                    if _is_xbloom(device, adv):
                        return device
            await asyncio.sleep(1)
        return None


class LocalRadio:
    """This computer's own Bluetooth. For development next to the machine."""

    async def start(self) -> None:
        gate.install()

    async def stop(self) -> None:
        return None

    async def find_device(self, timeout: float = 30.0):
        import bleak

        return await bleak.BleakScanner.find_device_by_filter(_is_xbloom, timeout=timeout)
