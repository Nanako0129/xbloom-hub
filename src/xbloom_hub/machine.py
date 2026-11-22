"""Owner of the machine's single BLE link.

One held session (xbloom-py's XBloomModeListener) at a time. Everything else
in the hub asks this object; nothing else touches the radio.
"""
from __future__ import annotations

import asyncio
import json
import datetime
import hmac
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
# 35 is what this machine reported for a whole brew with the grinder off
# (docs/S3-RESULTS.md). 34 is what xbloom-py and the Home Assistant
# integration name for grinding and brewing; not yet seen here.
_BREWING = {34, 35}
# The screen the machine shows after a brew, for about 20 s, before home.
_FINISHED = 36
# The machine reports water in thousandths of a millilitre (25 ml arrived as
# 25000.0; the official app divides by 1000 as well).
WATER_UNITS_PER_ML = 1000
# The machine's "+ WATER" screen. Measured 2026-11-15: a start with the tank
# low was refused (no_water), then 40522, then 12. 12 has also been seen on
# waking, so the hub reads "no water" from 40522, never from 12 alone.
_WATER_SCREEN = 12
CMD_NO_WATER = 40522        # RD_ErrorLackOfWater (xbloom spec.FAULTS)
# Values known to be something other than a brew: home, asleep, recipe ready,
# finished, the water screen, 29 (after the handshake), 4 and 5 (tare).
_NOT_A_BREW = _HOME | {_ASLEEP, _RECIPE_READY, _FINISHED, _WATER_SCREEN, 29, 4, 5}
# What the machine reports on its scale page (docs/PLAN.md, S3e), measured
# 2026-11-15: 8003 → 4, then 5 a second later; a zero there → 4 → 5; 8014 →
# 1 (home). Home is not among them, so leaving the page at the machine clears
# the hub's scale flag. 4 and 5 also follow the machine's own tare key on any
# screen, so they never join _HOME: as home they would close a running
# brew's record and take its Stop away.
SCALE_PAGE = frozenset({4, 5})
TARE_SETTLE = frozenset({4, 5})   # reported after a zero, on any screen (ble.py:735)
# The machine sets its scale back to zero about 5 s into a brew, with water
# already in the cup (docs/S3-RESULTS.md: 5.6 g, 4.95 g and 5.7 g were lost
# that way). A reading that falls by more than REZERO_DROP_G to within
# REZERO_BAND_G of zero, and is not followed by one below that band, is taken
# as such a reset, and what was on the scale is carried over. The second
# reading is there for a cup being lifted, which is expected to pass through
# zero on its way down; what a lifted cup reads has not been measured on
# this machine.
REZERO_DROP_G = 2.0
REZERO_BAND_G = (-0.5, 1.5)
# Before the first water there is no coffee in the cup, whatever the scale
# reads: 2026-11-15, with the grinder on, the scale read -3.24 g while grinding
# and the machine zeroed it itself the moment the first pour began. A brew's
# coffee weight is therefore 0 until water is seen (WATER_SEEN_ML), and its
# zero is the scale's reading at that point; a re-zero after that is carried.
WATER_SEEN_ML = 0.5
# After the hub sends a start, any other activity value counts as a brew for
# this long: a start the hub sent must not brew with no record and no Stop
# because the guess above was wrong.
START_WINDOW_S = 30.0
CHECKPOINT_S = 10.0

CMD_POUR_START, CMD_FINISHED, CMD_ABANDONED = 40510, 40512, 40513
CMD_POUR_END = 40511
# A brew whose link drops is held this long: if the link is back and the
# machine still brewing, the same record goes on, with the gap marked; the
# page's chart and its Stop are the same brew's. 2026-11-14: a release in the
# middle of a brew had cut one cup into two records.
RESUME_WINDOW_S = 180
# A live link carries a frame several times a second (9.5/s measured, idle,
# 2026-11-15; longest gap 0.69 s). The official app drops a link after 2 s
# without one and connects again. A link this long silent is dead even if the
# proxy still calls it connected (2026-11-14: "busy" for an hour, the machine
# showing no link): the session ends and the hub searches again.
SILENT_LINK_S = 10.0
RESUME_CHECK_S = 20       # how long after a resume a lower pour or water count means another cup
WATER_BACK_ML = 5.0       # a water count this far below the last one is not the same cup
CMD_WEIGHT, CMD_WATER, CMD_STATUS = 20501, 40523, 40521

TICKET_TTL_S = 600


class NotReady(RuntimeError):
    """The machine is not in a state that allows the request."""


class CancelFailed(RuntimeError):
    """The machine did not answer the quit-recipe command."""


class BadTicket(RuntimeError):
    """The start ticket is missing, wrong, already used or expired."""


def _default_listener(*args, **kwargs):
    from xbloom.mode_listener import XBloomModeListener

    return XBloomModeListener(*args, **kwargs)


class Machine:
    def __init__(self, radio, listener_factory=_default_listener, brews=None) -> None:
        self._radio = radio
        self._factory = listener_factory
        self._brews = brews                    # BrewStore, or None to keep nothing
        self._brew: dict | None = None         # the open brew record
        self._suspended: tuple | None = None   # (brew, when) held across a link drop
        self._brew_saved = 0.0
        self._held: dict | None = None         # the loaded recipe, kept until home
        self._started_at: float | None = None  # when the hub last wrote 8002
        self._stop_lock = asyncio.Lock()
        self._water: float | None = None
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
        self._finished = False          # a brew finished and the machine has not moved on
        self._no_water = False          # 40522 seen, and the machine has not left that screen
        self._weight: float | None = None
        self._recipe: dict | None = None       # what this hub loaded, while armed
        self._preload_weight: float | None = None
        self._ticket: dict | None = None
        self._error: str | None = None
        # This hub put the machine on its scale page with a confirmed 8003, and
        # nothing since says it left. Set only in scale_enter().
        self._on_scale = False

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
        # 40522 alone is a fault the machine may raise elsewhere; the state is
        # the water screen it leads to.
        if self._no_water and self._activity == _WATER_SCREEN:
            return "no_water"
        # A fact the hub set, not an activity value alone: 4 and 5 also follow
        # a press of the machine's own tare key on any screen.
        if self._on_scale and self._activity in SCALE_PAGE:
            return "scale"
        if self._activity in _HOME:
            return "idle"
        # The settle after a zero (4, then 5) off the scale page, with no load
        # held: still idle to the page. Only here, never in _HOME, which would
        # let a tare key close a brew's record.
        if self._activity in TARE_SETTLE and self._held is None and self._started_at is None:
            return "idle"
        if self._activity == _ASLEEP:
            return "asleep"
        # 31 also follows a pause in a brew started at the machine, so it only
        # counts as armed when this hub put the recipe there.
        if self._activity == _RECIPE_READY and self._recipe is not None:
            return "armed"
        # The brew ended about 5 s before the machine changed to 36.
        if self._activity == _FINISHED or self._finished:
            return "finished"
        return "busy"

    def snapshot(self) -> dict:
        """Public state, readable by any allowed client. Never includes the start
        ticket or the machine's serial number."""
        brew = self._brew
        return {
            "state": self.state,
            # Names the load that is on the machine now, not the ticket: a page
            # holding a ticket offers Start only while this is its own load's.
            # Random per load, so it survives no restart and repeats no earlier one.
            "load_id": self._ticket["load_id"] if self._ticket else None,
            "activity": self._activity,
            "weight_g": self._weight,
            "water_ml": self._water,
            "preload_weight_g": self._preload_weight,
            "recipe": self._recipe,
            "brew": None if brew is None else {
                "id": brew["id"], "started_at": brew["started_at"],
                "elapsed_s": round(time.monotonic() - brew["_t0"], 1),
                "pour": brew["pour"], "slug": brew["slug"],
                # A page that sees this change fetches the record: a short drop
                # shows no jump in elapsed_s, yet its gap must break the line.
                "gaps": len(brew.get("gaps") or []),
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
        if isinstance(decoded.get("water_ml"), (int, float)):
            decoded = {**decoded, "water_ml": round(decoded["water_ml"] / WATER_UNITS_PER_ML, 1)}
        elif "water_ml" in decoded:
            decoded = {k: v for k, v in decoded.items() if k != "water_ml"}
        if (self._brew is not None and isinstance(decoded.get("water_ml"), (int, float))
                and decoded["water_ml"] > WATER_SEEN_ML):
            self._brew["_poured"] = True
        if "weight_g" in decoded and self._brew is not None:
            decoded = {**decoded, "weight_g": self._carried(decoded["weight_g"])}
        if "weight_g" in decoded:
            self._weight = decoded["weight_g"]
        if "water_ml" in decoded:
            self._water = decoded["water_ml"]

        previous = self._activity
        if "activity" in decoded:
            was_armed = self.state == "armed"
            self._activity = decoded["activity"]
            if self._activity not in SCALE_PAGE:
                self._on_scale = False      # brewer, grinder, asleep, anything new
            if self._activity != previous:
                self._finished = False      # the machine has moved on, wherever to
            if self._activity != previous and self._activity != _WATER_SCREEN:
                # Refilled, or the screen was left. A change only: the 40522
                # comes while the machine still reports the screen before 12.
                self._no_water = False
            if was_armed and self.state != "armed":
                # The machine left the recipe screen: the load is over and its
                # start ticket is void (invariant 3). The recipe itself is
                # held for a brew that may be starting.
                self._clear_load()
        if cmd == CMD_NO_WATER:
            # Arrives before the machine changes to 12. The machine leaves the recipe screen for
            # "+ WATER", so a load ends here, as above.
            was_armed = self.state == "armed"
            self._no_water = True
            if was_armed:
                self._clear_load()
        activity = self._activity

        # A brew held across a link drop, and the machine past it now: it
        # finished during the gap (the screen after a brew), or it is home or
        # asleep. Other values (29 is seen on connecting) leave it held.
        if (self._brew is None and self._suspended is not None and "activity" in decoded
                and (activity == _FINISHED or activity in _HOME or activity == _ASLEEP)):
            held = self._take_suspended()
            if held is not None and activity == _FINISHED:
                self._resume_brew(held)
                self._close_brew("stopped" if held["stop_sent"] else "finished")
            # Otherwise its file already says link_lost.

        # A brew opens only on what the machine reports.
        if self._brew is None:
            changed = "activity" in decoded and activity != previous
            after_start = (self._started_at is not None
                           and time.monotonic() - self._started_at < START_WINDOW_S)
            if cmd == CMD_POUR_START:
                self._open_brew("pour_started")
            elif changed and activity in _BREWING and previous not in _BREWING:
                self._open_brew(f"activity_{activity}")
            elif changed and after_start and activity not in _NOT_A_BREW:
                self._open_brew(f"activity_{activity}_after_start")

        if self._brew is not None:
            self._not_another_cup(decoded)
        if self._brew is not None:
            self._record(decoded)
            if cmd == CMD_FINISHED:
                self._close_brew("finished")
            elif cmd == CMD_ABANDONED:
                # 40513 also comes when the screen is left right after the
                # last pour, before any 40512 (2026-11-14: 2 s after the
                # 40511 of a one-pour recipe). Every pour ended is a brew done.
                self._close_brew("finished" if self._all_poured() else "abandoned")
            elif "activity" in decoded and activity == _FINISHED:
                # The 40512 was missed, or the hub joined just after a brew.
                self._close_brew("stopped" if self._brew["stop_sent"] else "finished")
            elif "activity" in decoded and activity in _HOME:
                self._close_brew("stopped" if self._brew["stop_sent"] else "home")
            elif "activity" in decoded and activity == _ASLEEP:
                self._close_brew("asleep")

        if "activity" in decoded and (activity in _HOME or activity == _ASLEEP):
            self._held = None               # home again: nothing left to attach
            self._started_at = None
        self._publish()

    def _all_poured(self) -> bool:
        brew = self._brew
        pours = len(((brew.get("recipe") or {}).get("pours")) or [])
        ended = sum(1 for _, event in brew["events"] if event.get("cmd") == CMD_POUR_END)
        return pours > 0 and ended >= pours

    def _clear_load(self) -> None:
        self._recipe = None
        self._preload_weight = None
        self._ticket = None

    # ---- brew record ---------------------------------------------------- #
    @staticmethod
    def _now() -> str:
        # With a colon in the offset: what every browser's Date accepts.
        return datetime.datetime.now().astimezone().isoformat(timespec="seconds")

    def _suspend_brew(self) -> None:
        """The link is gone with a brew running: keep it for a reconnect. Its
        file says link_lost meanwhile, so a hub that dies here leaves it closed."""
        brew = self._brew
        # The silence began at the last reading, not when the drop was noticed.
        heard = [s[-1][0] for s in brew["samples"].values() if s] + [e[0] for e in brew["events"][-1:]]
        gap_from = max(heard) if heard else round(time.monotonic() - brew["_t0"], 2)
        self._close_brew("link_lost")
        brew["_gap_from"] = gap_from
        self._suspended = (brew, time.monotonic())
        log.info("brew %s held for a reconnect", brew["id"])

    def _not_another_cup(self, decoded: dict) -> None:
        """Soon after a resume: a pour index or a water count lower than when
        the link dropped is a different cup. The resumed record goes back to
        link_lost, and a new one opens for this cup."""
        brew = self._brew
        was = brew.get("_was")
        if was is None:
            return
        pour, water, until = was
        if time.monotonic() > until:
            brew.pop("_was", None)
            return
        lower_pour = (decoded.get("cmd") == CMD_POUR_START and pour is not None
                      and isinstance(decoded.get("pour_index"), int) and decoded["pour_index"] < pour)
        lower_water = (decoded.get("cmd") == CMD_WATER and water is not None
                       and isinstance(decoded.get("water_ml"), (int, float))
                       and decoded["water_ml"] < water - WATER_BACK_ML)
        if not (lower_pour or lower_water):
            return
        gap_from, gap_to = brew["gaps"].pop()
        for series in brew["samples"].values():
            series[:] = [p for p in series if p[0] <= gap_from]
        brew["events"] = [e for e in brew["events"] if e[0] <= gap_from]
        brew.pop("_was", None)
        self._close_brew("link_lost")
        log.info("brew %s was another cup after the gap: a new record opens", brew["id"])
        self._open_brew("another_cup_after_gap")

    def _take_suspended(self):
        """The brew held across a link drop, if still within the window."""
        held, self._suspended = self._suspended, None
        if held and time.monotonic() - held[1] < RESUME_WINDOW_S:
            return held[0]
        return None

    def _resume_brew(self, brew: dict) -> None:
        brew.setdefault("gaps", []).append([brew.pop("_gap_from"), round(time.monotonic() - brew["_t0"], 2)])
        brew["reason"] = brew["ended_at"] = brew["duration_s"] = None
        water = brew["samples"]["water"]
        # Where this cup was: a pour or a water count lower than this, soon
        # after the link is back, is another cup started in the gap.
        brew["_was"] = (brew["pour"], water[-1][1] if water else None,
                        time.monotonic() + RESUME_CHECK_S)
        self._brew = brew
        self._finished = False
        self._weight = None               # read again through the carry, not raw
        self._clear_load()
        self._started_at = None
        self._water = None
        self._save_brew()
        log.info("brew %s resumed after a gap", brew["id"])

    def _open_brew(self, opened_by: str) -> None:
        self._on_scale = False
        resumed = self._take_suspended()
        if resumed is not None:
            self._resume_brew(resumed)
            return
        held = self._held or {}
        hub_started = (self._started_at is not None
                       and time.monotonic() - self._started_at < START_WINDOW_S)
        # The load is over whichever signal said so: a pour can be reported
        # while the activity value still reads 31, and the ticket must not
        # outlive the brew (invariant 3). The start window closes here too, or
        # an unknown activity value after a short brew would open a second
        # record for something that is not a brew.
        self._clear_load()
        self._started_at = None
        self._brew = {
            "id": self._brews.new_id() if self._brews else time.strftime("%Y%m%d-%H%M%S-none"),
            "started_at": self._now(),
            "ended_at": None, "reason": None, "duration_s": None,
            "opened_by": opened_by, "hub_started": hub_started,
            "stop_sent": False, "pour": None,
            "slug": held.get("slug"), "recipe": held.get("recipe"),
            "as_sent": held.get("as_sent"),
            "samples": {"weight": [], "water": []}, "events": [],
            # [seconds, grams carried over] for each scale reset seen.
            "rezeroed": [], "_raw_weight": None, "_pending": None, "_carry": 0.0,
            "_base": 0.0, "_poured": False,
            "_t0": time.monotonic(),
        }
        # The last water reading belongs to whatever came before this brew.
        # Unknown until the machine reports again, rather than a wrong number.
        self._water = None
        self._save_brew()
        log.info("brew %s opened (%s)", self._brew["id"], opened_by)

    def _carried(self, raw: float) -> float:
        """The weight in the cup since the brew opened, across the machine's
        own reset of its scale."""
        brew = self._brew
        if not brew["_poured"]:
            brew["_base"] = brew["_raw_weight"] = raw      # the zero follows the scale until water
            return 0.0
        last, pending = brew["_raw_weight"], brew["_pending"]
        brew["_raw_weight"] = raw
        if pending is not None:
            brew["_pending"] = None
            if raw >= REZERO_BAND_G[0]:         # it stayed at zero or rose: a reset
                brew["_carry"] += pending
                brew["rezeroed"].append(
                    [round(time.monotonic() - brew["_t0"], 2), round(pending, 2)])
        elif (last is not None and last - raw > REZERO_DROP_G
                and REZERO_BAND_G[0] <= raw <= REZERO_BAND_G[1]):
            brew["_pending"] = last
            return round(last + brew["_carry"] - brew["_base"], 2)      # held for this one reading
        return round(raw + brew["_carry"] - brew["_base"], 2)

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
        brew["ended_at"] = self._now()
        brew["duration_s"] = round(time.monotonic() - brew["_t0"], 1)
        self._save_brew()
        log.info("brew %s closed (%s)", brew["id"], reason)
        self._brew = None
        self._finished = reason == "finished"

    def open_record(self, brew_id: str) -> dict | None:
        """The brew still running, as it is now, if that is the one asked for.
        Its file is written only every CHECKPOINT_S; a page joining mid-brew
        needs the readings up to this moment."""
        brew = self._brew
        if brew is None or brew["id"] != brew_id:
            return None
        return json.loads(json.dumps({k: v for k, v in brew.items() if not k.startswith("_")}))

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
        self._finished = False
        self._no_water = False
        self._weight = None
        self._water = None

        heard = [time.monotonic()]

        def on_raw(decoded: dict) -> None:
            # Notifications keep arriving until the link is really closed;
            # ones from a session that is over must not pass for current state.
            if self._lifecycle is lifecycle:
                heard[0] = time.monotonic()
                self._on_raw(decoded)

        async def watch_silence() -> None:
            while True:
                await asyncio.sleep(SILENT_LINK_S / 4)
                if time.monotonic() - heard[0] > SILENT_LINK_S:
                    log.warning("no frame from the machine for %.0f s: the link is dead", SILENT_LINK_S)
                    lifecycle.put_nowait(("failed", {"reason": "silent_link"}))
                    return

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
        watchdog = None
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
                    heard[0] = time.monotonic()
                    if watchdog is None:            # one per session, however often it says ready
                        watchdog = asyncio.create_task(watch_silence())
                    self._publish()
                elif phase in ("failed", "auto_stopped", "released"):
                    if phase == "failed":
                        self._error = payload.get("reason")
                    return ready
        finally:
            if watchdog is not None:
                watchdog.cancel()
            self._lifecycle = None
            if self._brew is not None:
                self._suspend_brew()                # before the listener goes: no gap
            self._listener = None
            try:
                await listener.stop()
            finally:
                self._held = None
                self._started_at = None
                self._on_scale = False
                self._activity = None
                self._finished = False
                self._no_water = False
                self._weight = None
                self._water = None
                self._clear_load()
                self._no_session.set()
                self._publish()

    async def release(self) -> None:
        """Drop the link and stay off it, so the phone app can have the machine.
        Not with a recipe loaded or a brew running: that would cut the brew's
        record and take its Stop away."""
        # Under the lock that load() and start() hold: no load arms the machine
        # between this check and the release.
        async with self._lock:
            if self.state in ("armed", "brewing"):
                raise NotReady(f"the link is not released while {self.state}")
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
            # One ticket per load. What it stands for cannot change under it:
            # a loaded recipe can be neither saved over nor deleted.
            ticket = secrets.token_urlsafe(16)
            self._ticket = {"ticket": ticket, "expires": time.monotonic() + TICKET_TTL_S,
                            "load_id": secrets.token_hex(8)}
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
        from xbloom.ble import CMD_EXECUTE, CommandRefused, CommandUnanswered, _build_frame

        async with self._lock:
            if self.state != "armed":
                raise NotReady(f"cannot start while {self.state}")
            issued = self._ticket
            if (issued is None or not isinstance(ticket, str)
                    or not hmac.compare_digest(ticket.encode(), issued["ticket"].encode())):
                raise BadTicket("this start ticket is not the one issued for the loaded recipe")
            if time.monotonic() > issued["expires"]:
                self._ticket = None
                self._publish()                 # its load_id goes from every page at once
                raise BadTicket("the start ticket has expired; cancel and load again")
            # Consumed before anything is sent: a start is never repeated.
            self._ticket = None
            self._started_at = time.monotonic()
            self._publish()
            try:
                with gate.start_permit():
                    # Only this task may write 8002, and only inside this
                    # block. send_confirmed re-sends only to a sleeping machine.
                    answered = await self._listener.send_confirmed(
                        "execute", _build_frame(CMD_EXECUTE))
            except CommandRefused:
                self._started_at = None     # it did not start; no window, no credit
                raise
            except (CommandUnanswered, gate.GateRefused):
                raise
            except Exception as err:  # noqa: BLE001  a link error, not a reply
                raise NotReady(f"the link failed during the start: {type(err).__name__}") from err
            if not answered:
                raise CommandUnanswered("execute")
            await self._wait_for(lambda: self._brew is not None, 3)
            return self.snapshot()

    async def stop(self) -> dict:
        """Stop the running brew (40519)."""
        from xbloom.ble import CMD_BREW_STOP, CommandRefused, CommandUnanswered, _build_frame

        brew, listener = self._brew, self._listener
        if brew is None or listener is None:
            raise NotReady(f"no brew to stop while {self.state}")
        if self._stop_lock.locked():
            raise NotReady("a stop is already on its way")
        async with self._stop_lock:
            # Marked before the send, because the machine can report home
            # before the send returns; taken back if the stop was not taken, so
            # a brew the hub never stopped is not recorded as stopped.
            brew["stop_sent"] = True
            try:
                answered = await listener.send_confirmed("brew_stop", _build_frame(CMD_BREW_STOP))
            except (CommandRefused, gate.GateRefused):
                brew["stop_sent"] = False
                raise
            except Exception as err:  # noqa: BLE001  a link error, not a reply
                brew["stop_sent"] = False
                raise NotReady(f"the link failed during the stop: {type(err).__name__}") from err
            if not answered:
                brew["stop_sent"] = False
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

    # ---- the scale (docs/PLAN.md, S3e) ------------------------------------ #
    # None of these grinds, pours or heats. Each checks its state under the
    # lock load() and start() hold, never _stop_lock: a scale command must not
    # delay a Stop. The hub sends none of them while armed or brewing.
    async def _scale_send(self, name: str, frame: bytes) -> None:
        from xbloom.ble import CommandRefused, CommandUnanswered

        try:
            answered = await self._listener.send_confirmed(name, frame)
        except (CommandRefused, gate.GateRefused):
            raise
        except Exception as err:  # noqa: BLE001  a link error, not a reply
            raise NotReady(f"the link failed during {name}: {type(err).__name__}") from err
        if not answered:
            raise CommandUnanswered(name)

    def _scale_free(self, what: str) -> None:
        """A load held or a start just sent: the machine may be on its recipe
        screen whatever the hub reads, and 8014 there reaches the firmware's
        shared quit handler."""
        if self._held is not None or self._started_at is not None:
            raise NotReady(f"cannot {what} with a recipe loaded or a start just sent")

    async def scale_enter(self) -> dict:
        """Put the machine on its scale page (8003). Only from idle."""
        from xbloom.ble import packet_scale_enter

        async with self._lock:
            if self.state != "idle":
                raise NotReady(f"cannot go to the scale while {self.state}")
            self._scale_free("go to the scale")
            listener = self._listener
            await self._scale_send("scale_enter", packet_scale_enter())
            if self._listener is not listener:
                # The session that sent it is gone: nothing to set a flag for.
                raise NotReady("the link dropped while going to the scale")
            self._on_scale = True
            self._publish()
            return self.snapshot()

    async def tare(self) -> dict:
        """Zero the scale (8500). Idle or on the scale page; never asleep (a
        zero would wake the machine to no use), armed or brewing."""
        from xbloom.ble import packet_tare

        async with self._lock:
            if self.state not in ("idle", "scale"):
                raise NotReady(f"cannot zero the scale while {self.state}")
            self._scale_free("zero the scale")
            await self._scale_send("tare", packet_tare())
            return self.snapshot()

    async def scale_quit(self) -> dict:
        """Leave the scale page for home (8014). Only on the scale page."""
        from xbloom.ble import packet_quit_scale

        async with self._lock:
            if self.state != "scale":
                raise NotReady(f"not on the scale page while {self.state}")
            self._scale_free("leave the scale")
            await self._scale_send("scale_quit", packet_quit_scale())
            self._on_scale = False
            self._publish()
            return self.snapshot()
