"""Owner of the machine's single BLE link.

One held session (xbloom-py's XBloomModeListener) at a time. Everything else
in the hub asks this object; nothing else touches the radio.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
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
# What xbloom-py and the Home Assistant integration report for grinding and
# brewing. Not yet seen by this project on its own machine.
_BREWING = 34
# Values known to be something other than a brew: home, asleep, after the
# handshake, tare, recipe ready, finished.
_NOT_A_BREW = {1, 65, 10, 29, 4, 5, 31, 36}
# After the hub sends a start, any other activity value counts as a brew for
# this long: a start the hub sent must not brew with no record and no Stop
# because the guess above was wrong.
START_WINDOW_S = 30.0
CHECKPOINT_S = 10.0

CMD_POUR_START, CMD_FINISHED, CMD_ABANDONED = 40510, 40512, 40513
CMD_CUP_ON, CMD_CUP_OFF = 9002, 9008
CMD_WEIGHT, CMD_WATER, CMD_STATUS = 20501, 40523, 40521

TICKET_TTL_S = 600


class NotReady(RuntimeError):
    """The machine is not in a state that allows the request."""


class CancelFailed(RuntimeError):
    """The machine did not answer the quit-recipe command."""


class BadTicket(RuntimeError):
    """The start ticket is missing, wrong, already used or expired."""


class NoCup(RuntimeError):
    """The machine's last cradle event says the cup was taken off."""


def _default_listener(*args, **kwargs):
    from xbloom.mode_listener import XBloomModeListener

    return XBloomModeListener(*args, **kwargs)


class Machine:
    def __init__(self, radio, listener_factory=_default_listener, brews=None) -> None:
        self._radio = radio
        self._factory = listener_factory
        self._brews = brews                    # BrewStore, or None to keep nothing
        self._brew: dict | None = None         # the open brew record
        self._brew_saved = 0.0
        self._held: dict | None = None         # the loaded recipe, kept until home
        self._started_at: float | None = None  # when the hub last wrote 8002
        self._stop_lock = asyncio.Lock()
        self._water: float | None = None
        self._cup: bool | None = None          # last cradle event; None = none seen
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
        if self._brew is not None:
            # Whatever the activity value, including 31 during a pause.
            return "brewing"
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
        """Public state, readable without the token. Never includes the start
        ticket or the machine's serial number."""
        brew = self._brew
        return {
            "state": self.state,
            "activity": self._activity,
            "weight_g": self._weight,
            "water_ml": self._water,
            "cup": self._cup,
            "preload_weight_g": self._preload_weight,
            "recipe": self._recipe,
            "brew": None if brew is None else {
                "id": brew["id"], "started_at": brew["started_at"],
                "elapsed_s": round(time.monotonic() - brew["_t0"], 1),
                "pour": brew["pour"], "slug": brew["slug"],
                "recipe_name": (brew["recipe"] or {}).get("name"),
            },
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
        cmd = decoded.get("cmd")
        if "weight_g" in decoded:
            self._weight = decoded["weight_g"]
        if "water_ml" in decoded:
            self._water = decoded["water_ml"]
        if cmd == CMD_CUP_ON:
            self._cup = True
        elif cmd == CMD_CUP_OFF:
            self._cup = False

        previous = self._activity
        if "activity" in decoded:
            was_armed = self.state == "armed"
            self._activity = decoded["activity"]
            if was_armed and self.state != "armed":
                # The machine left the recipe screen: the load is over and its
                # start ticket is void (invariant 3). The recipe itself is
                # held for a brew that may be starting.
                self._clear_load()
        activity = self._activity

        # A brew opens only on what the machine reports.
        if self._brew is None:
            changed = "activity" in decoded and activity != previous
            after_start = (self._started_at is not None
                           and time.monotonic() - self._started_at < START_WINDOW_S)
            if cmd == CMD_POUR_START:
                self._open_brew("pour_started")
            elif changed and activity == _BREWING:
                self._open_brew("activity_34")
            elif changed and after_start and activity not in _NOT_A_BREW:
                self._open_brew(f"activity_{activity}_after_start")

        if self._brew is not None:
            self._record(decoded)
            if cmd == CMD_FINISHED:
                self._close_brew("finished")
            elif cmd == CMD_ABANDONED:
                self._close_brew("abandoned")
            elif "activity" in decoded and activity in _HOME:
                self._close_brew("stopped" if self._brew["stop_sent"] else "home")
            elif "activity" in decoded and activity == _ASLEEP:
                self._close_brew("asleep")

        if "activity" in decoded and (activity in _HOME or activity == _ASLEEP):
            self._held = None               # home again: nothing left to attach
            self._started_at = None
        self._publish()

    def _clear_load(self) -> None:
        self._recipe = None
        self._preload_weight = None
        self._ticket = None

    # ---- brew record ---------------------------------------------------- #
    def _open_brew(self, opened_by: str) -> None:
        held = self._held or {}
        self._brew = {
            "id": self._brews.new_id() if self._brews else time.strftime("%Y%m%d-%H%M%S-none"),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "ended_at": None, "reason": None, "duration_s": None,
            "opened_by": opened_by, "hub_started": self._started_at is not None,
            "stop_sent": False, "pour": None,
            "slug": held.get("slug"), "recipe": held.get("recipe"),
            "as_sent": held.get("as_sent"),
            "samples": {"weight": [], "water": []}, "events": [],
            "_t0": time.monotonic(),
        }
        self._save_brew()
        log.info("brew %s opened (%s)", self._brew["id"], opened_by)

    def _record(self, decoded: dict) -> None:
        brew = self._brew
        t = round(time.monotonic() - brew["_t0"], 2)
        cmd = decoded.get("cmd")
        if cmd == CMD_WEIGHT and "weight_g" in decoded:
            brew["samples"]["weight"].append([t, decoded["weight_g"]])
        elif cmd == CMD_WATER and "water_ml" in decoded:
            brew["samples"]["water"].append([t, decoded["water_ml"]])
        elif cmd != CMD_STATUS:             # the heartbeat carries the serial number
            if cmd == CMD_POUR_START:
                brew["pour"] = decoded.get("pour_index")
            brew["events"].append(
                [t, {k: v for k, v in decoded.items() if k not in ("serial", "model")}])
        if time.monotonic() - self._brew_saved > CHECKPOINT_S:
            self._save_brew()

    def _close_brew(self, reason: str) -> None:
        brew = self._brew
        brew["reason"] = reason
        brew["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        brew["duration_s"] = round(time.monotonic() - brew["_t0"], 1)
        self._save_brew()
        log.info("brew %s closed (%s)", brew["id"], reason)
        self._brew = None

    def _save_brew(self) -> None:
        self._brew_saved = time.monotonic()
        if self._brews is None:
            return
        try:
            self._brews.write({k: v for k, v in self._brew.items() if not k.startswith("_")})
        except OSError:
            log.exception("could not write the brew record")

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
        self._water = None
        self._cup = None

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
                if self._brew is not None:
                    self._close_brew("link_lost")
                self._held = None
                self._started_at = None
                self._activity = None
                self._weight = None
                self._water = None
                self._cup = None
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
    async def load(self, slug: str, read) -> dict:
        """Send a validated recipe without starting it. Returns the start ticket.

        `read()` returns `RecipeStore.for_load()`'s dictionary (the encoder's
        input under "wire", the recipe as checked and as sent), or None if the
        recipe is gone. It is called here, under the lock and after the slug has been
        marked as loading, so the content that goes out is the content stored
        at that moment: a save racing with a load is either seen by it or
        refused, never silently left behind under the same name.
        """
        async with self._lock:
            if self.state not in ("idle", "asleep"):
                raise NotReady(f"cannot load while {self.state}")
            listener = self._listener
            # Entering the recipe screen tares the scale, so this is the last
            # honest reading of what is on it (docs/S0-RESULTS.md, item 2).
            self._preload_weight = self._weight
            self._recipe = {"slug": slug, "name": None}
            try:
                item = read()                   # no await between the mark and the read
                if item is None:
                    raise NotReady(f"recipe '{slug}' is no longer in the library")
                recipe = item["wire"]
                self._recipe["name"] = recipe.get("name")
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
            if not await self._wait_for(lambda: self.state == "armed", 5):
                # The frames were accepted but the machine has not said it is
                # waiting. Do not report a load, and do not keep the slug
                # marked: if it does arm later it shows as busy and sleeps out.
                self._clear_load()
                self._publish()
                raise NotReady("the recipe was sent but the machine did not report it is "
                               "waiting")
            # Kept until the machine is home again, so a brew that starts from
            # here, by the hub or at the knob, knows what it is brewing.
            self._held = {"slug": slug, "recipe": item.get("recipe"),
                          "as_sent": item.get("as_sent")}
            self._publish()
            return {"ticket": ticket, "expires_in_s": TICKET_TTL_S, **self.snapshot()}

    async def start(self, ticket: object) -> dict:
        """Start the loaded recipe. The only place 8002 is allowed out.

        Changes no state by itself: the machine's own reports open the brew.
        """
        from xbloom.ble import CMD_EXECUTE, CommandUnanswered, _build_frame

        async with self._lock:
            if self.state != "armed":
                raise NotReady(f"cannot start while {self.state}")
            issued = self._ticket
            if (issued is None or not isinstance(ticket, str)
                    or not hmac.compare_digest(ticket.encode(), issued["ticket"].encode())):
                raise BadTicket("this start ticket is not the one issued for the loaded recipe")
            if time.monotonic() > issued["expires"]:
                self._ticket = None
                raise BadTicket("the start ticket has expired; cancel and load again")
            if self._cup is False:
                raise NoCup("the machine reported the cup was taken off")
            # Consumed before anything is sent: a start is never repeated.
            self._ticket = None
            self._started_at = time.monotonic()
            with gate.start_permit():
                # send_confirmed re-sends only while the machine is asleep, so
                # 8002 is written once while it is awake.
                answered = await self._listener.send_confirmed(
                    "execute", _build_frame(CMD_EXECUTE))
            if not answered:
                raise CommandUnanswered("execute")
            await self._wait_for(lambda: self._brew is not None, 3)
            return self.snapshot()

    async def stop(self) -> dict:
        """Stop the running brew (40519)."""
        from xbloom.ble import CMD_BREW_STOP, CommandUnanswered, _build_frame

        if self._brew is None:
            raise NotReady(f"no brew to stop while {self.state}")
        if self._stop_lock.locked():
            raise NotReady("a stop is already on its way")
        async with self._stop_lock:
            self._brew["stop_sent"] = True
            if not await self._listener.send_confirmed("brew_stop", _build_frame(CMD_BREW_STOP)):
                raise CommandUnanswered("brew_stop")
            await self._wait_for(lambda: self._brew is None, 5)
            return self.snapshot()

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
