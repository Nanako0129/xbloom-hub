"""Where the Bluetooth radio comes from: an ESPHome proxy, or this computer."""
from __future__ import annotations

import asyncio
import logging
import time

from . import gate

log = logging.getLogger(__name__)
STALE_CHECK_S = 10


async def drop_stale_links(host: str, noise_psk: str, client_class=None) -> int:
    """Disconnect every link the proxy holds, before this hub takes it.

    Only the hub uses the proxy, so at start any link it holds was left by a
    hub that stopped without closing it (the phone proxy keeps a link when
    its API client goes). The machine, still connected, stops advertising and
    a new hub never finds it: seen three times on 2026-11-14 after restarts.
    Returns how many were dropped; a failure is logged and the hub goes on.
    """
    if client_class is None:
        from aioesphomeapi import APIClient as client_class
    client = client_class(host, 6053, None, noise_psk=noise_psk)
    try:
        await asyncio.wait_for(client.connect(login=True), STALE_CHECK_S)
        got = asyncio.get_running_loop().create_future()

        def on_free(free, limit, allocated) -> None:
            if not got.done():
                got.set_result(list(allocated))

        unsubscribe = client.subscribe_bluetooth_connections_free(on_free)
        try:
            allocated = await asyncio.wait_for(got, STALE_CHECK_S)
            for address in allocated:
                await client.bluetooth_device_disconnect(address)
        finally:
            unsubscribe()
        if allocated:
            log.info("dropped %d link(s) a previous hub left on the proxy", len(allocated))
        return len(allocated)
    except Exception as err:   # never a reason not to start
        log.warning("could not check the proxy for stale links: %s", type(err).__name__)
        return 0
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


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
        await drop_stale_links(self._config["address"], self._config["noise_psk"])
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
