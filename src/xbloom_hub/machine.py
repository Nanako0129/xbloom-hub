"""Owner of the machine's single BLE link.

One held session (xbloom-py's XBloomModeListener) at a time. Everything else
in the hub asks this object; nothing else touches the radio.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time

from . import gate

log = logging.getLogger(__name__)

# Machine-activity codes (notification 8023). 1/65/31 are named in
# xbloom-studio; 10 was observed in S0 after five idle minutes.
_HOME = {1, 65}
_RECIPE_READY = 31
_ASLEEP = 10

TICKET_TTL_S = 600


class NotReady(RuntimeError):
    """The machine is not in a state that allows the request."""


class CancelFailed(RuntimeError):
    """The machine did not answer the quit-recipe command."""


def _default_listener(*args, **kwargs):
    from xbloom.mode_listener import XBloomModeListener

    return XBloomModeListener(*args, **kwargs)


class Machine:
    def __init__(self, radio, listener_factory=_default_listener) -> None:
        self._radio = radio
        self._factory = listener_factory
        self._listener = None
        self._lifecycle: asyncio.Queue | None = None
        self._released = False
        self._wake = asyncio.Event()
        self._no_session = asyncio.Event()
        self._no_session.set()
        self._lock = asyncio.Lock()
        self._changed = asyncio.Event()
        self._subscribers: set[asyncio.Queue] = set()
        self._info: dict = {}
        self._activity: int | None = None
        self._weight: float | None = None
        self._recipe: dict | None = None       # what this hub loaded, while armed
        self._preload_weight: float | None = None
        self._ticket: dict | None = None
        self._error: str | None = None

    # ---- state ---------------------------------------------------------- #
    @property
    def state(self) -> str:
        if self._released:
            return "released"
        if self._listener is None:
            return "offline"
        if self._activity in _HOME:
            return "idle"
        if self._activity == _ASLEEP:
            return "asleep"
        # 31 also follows a pause in a brew started at the machine, so it only
        # counts as armed when this hub put the recipe there.
        if self._activity == _RECIPE_READY and self._recipe is not None:
            return "armed"
        return "busy"

    def snapshot(self) -> dict:
        """Public state. Never includes the start ticket."""
        return {
            "state": self.state,
            "activity": self._activity,
            "weight_g": self._weight,
            "preload_weight_g": self._preload_weight,
            "recipe": self._recipe,
            "error": self._error,
            **self._info,
        }

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def _publish(self) -> None:
        self._changed.set()
        snap = self.snapshot()
        for queue in self._subscribers:
            if queue.full():           # a slow reader loses the oldest state
                queue.get_nowait()
            queue.put_nowait(snap)

    def _on_raw(self, decoded: dict) -> None:
        for key in ("fw_version", "model", "mode", "water_enough"):
            if key in decoded:
                self._info[key] = decoded[key]
        if "weight_g" in decoded:
            self._weight = decoded["weight_g"]
        if "activity" in decoded:
            was_armed = self.state == "armed"
            self._activity = decoded["activity"]
            if was_armed and self.state != "armed":
                # The machine left the recipe screen: the load is over and its
                # start ticket is void (invariant 3).
                self._clear_load()
        self._publish()

    def _clear_load(self) -> None:
        self._recipe = None
        self._preload_weight = None
        self._ticket = None

    async def _wait_for(self, predicate, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while not predicate():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except asyncio.TimeoutError:
                return predicate()
        return True

    # ---- link ----------------------------------------------------------- #
    async def run(self) -> None:
        """Hold the link for as long as the hub runs; reconnect when it drops."""
        # Shown until the radio is up: an unreachable proxy or a wrong key
        # otherwise looks like a plain "offline" with no reason.
        self._error = "radio_starting"
        try:
            await self._radio.start()
        except Exception as err:  # noqa: BLE001
            log.exception("radio failed to start")
            self._error = f"radio_failed: {type(err).__name__}"
            self._publish()
            raise
        self._error = None
        backoff = 1.0
        while True:
            if self._released:
                await self._wake.wait()
                self._wake.clear()
                continue
            try:
                ready = await self._session()
            except Exception as err:  # noqa: BLE001
                # A bug here must not leave the hub silently offline forever.
                log.exception("session crashed")
                self._error = f"session_crashed: {type(err).__name__}"
                self._publish()
                ready = False
            backoff = 1.0 if ready else min(backoff * 2, 30.0)
            await asyncio.sleep(backoff)

    async def _session(self) -> bool:
        """One connection. Returns True if it reached a usable state."""
        device = await self._radio.find_device()
        if device is None:
            self._error = "machine_not_found"
            self._publish()
            return False
        if self._released:
            # release() arrived during the search: do not connect at all.
            return True

        lifecycle: asyncio.Queue = asyncio.Queue()
        self._lifecycle = lifecycle
        self._no_session.clear()
        self._activity = None
        self._weight = None

        def on_raw(decoded: dict) -> None:
            # Notifications keep arriving until the link is really closed;
            # ones from a session that is over must not pass for current state.
            if self._lifecycle is lifecycle:
                self._on_raw(decoded)

        async def resolver():
            return device

        async def on_event(_event: dict) -> None:
            return None

        listener = self._factory(
            resolver, "hub", lambda decoded: decoded, on_event,
            lambda phase, payload: lifecycle.put_nowait((phase, payload)),
            # xbloom-py drops the link after 300 s of silence by default; the
            # hub is meant to hold it (security review P2-3).
            idle_timeout_s=float("inf"),
            on_raw=on_raw,
        )
        ready = False
        try:
            await listener.start()
            while True:
                phase, payload = await lifecycle.get()
                if phase == "ready":
                    # xbloom-py keeps the client on a private attribute; this
                    # is the only way to see what class the session got.
                    client = getattr(listener, "_client", None)
                    if not gate.session_is_gated(client):
                        log.error("session client is not gated; refusing to use it")
                        self._error = "gate_missing"
                        return False
                    self._error = None
                    self._listener = listener
                    ready = True
                    self._publish()
                elif phase in ("failed", "auto_stopped", "released"):
                    if phase == "failed":
                        self._error = payload.get("reason")
                    return ready
        finally:
            self._lifecycle = None
            self._listener = None
            try:
                await listener.stop()
            finally:
                self._activity = None
                self._weight = None
                self._clear_load()
                self._no_session.set()
                self._publish()

    async def release(self) -> None:
        """Drop the link and stay off it, so the phone app can have the machine."""
        self._released = True
        if self._lifecycle is not None:
            self._lifecycle.put_nowait(("released", {}))
        # Return only once the link is closed, so the phone app can connect.
        try:
            await asyncio.wait_for(self._no_session.wait(), 10)
        except asyncio.TimeoutError:
            log.warning("release: the session did not close within 10 s")
        self._publish()

    async def acquire(self) -> None:
        self._released = False
        self._wake.set()
        self._publish()

    # ---- commands ------------------------------------------------------- #
    async def load(self, slug: str, recipe: dict) -> dict:
        """Send a validated recipe without starting it. Returns the start ticket."""
        async with self._lock:
            if self.state not in ("idle", "asleep"):
                raise NotReady(f"cannot load while {self.state}")
            listener = self._listener
            # Entering the recipe screen tares the scale, so this is the last
            # honest reading of what is on it (docs/S0-RESULTS.md, item 2).
            self._preload_weight = self._weight
            self._recipe = {"slug": slug, "name": recipe.get("name")}
            try:
                await listener.send_prepare(recipe)
            except BaseException:
                self._clear_load()
                self._publish()
                raise
            if self._listener is not listener:
                # The link dropped while the recipe was going out. That
                # session is gone, so there is nothing to issue a ticket for.
                self._clear_load()
                raise NotReady("the link dropped during the load")
            digest = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()
            ticket = secrets.token_urlsafe(16)
            self._ticket = {
                "ticket": ticket, "recipe_sha256": digest,
                "expires": time.monotonic() + TICKET_TTL_S,
            }
            await self._wait_for(lambda: self.state == "armed", 5)
            self._publish()
            return {"ticket": ticket, "expires_in_s": TICKET_TTL_S, **self.snapshot()}

    async def cancel(self) -> dict:
        """Take back a loaded recipe (command 8017)."""
        from xbloom.ble import packet_quit_recipe

        async with self._lock:
            # 8017's effect anywhere but the recipe-start screen is unknown.
            if self.state != "armed":
                raise NotReady(f"nothing to cancel while {self.state}")
            if not await self._listener.send_confirmed("quit_recipe", packet_quit_recipe()):
                raise CancelFailed("the machine did not answer the quit command")
            await self._wait_for(lambda: self.state != "armed", 5)
            return self.snapshot()
