"""Browser tests: the dashboard in a real browser against a simulator hub.

The hub runs in this process, on its own event loop in a thread, so a test can
make the simulated machine do what a person at the machine would (lift the
cup, press the knob). Nothing here talks to a real machine or a real token.

Run them explicitly; the default suite leaves them out:

    uv run pytest tests/browser -m browser --browser chromium --browser webkit

XBLOOM_TEST_STATIC_DIR points the hub at another copy of the page, which is
how a guard is shown to matter: remove it from a copy, and its test fails.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
import yaml

from xbloom_hub import app as hub_app
from xbloom_hub.brews import BrewStore
from xbloom_hub.machine import Machine
from xbloom_hub.recipes import RecipeStore
from xbloom_hub.sim import SimListener, SimRadio

TOKEN = "browser-test-token"
HERE = Path(__file__).parent

def pytest_collection_modifyitems(config, items):
    """Every test under this directory is a browser test, whatever its module
    says, so the default run (`-m 'not browser'`) never starts a browser."""
    for item in items:
        if HERE in item.path.parents:
            item.add_marker(pytest.mark.browser)


# Markup that would run if it ever became markup, and text a template might
# interpolate. It must only ever be seen as text.
HOSTILE = '<img src=x onerror="window.__pwned=1">{a|b}'


def _pour(volume, pause=0):
    return {"volume_ml": volume, "temperature_c": 20, "pause_s": pause, "pattern": 2,
            "flow_rate": 3.0, "agitate_before": 2, "agitate_after": 2}


def _recipe(name, dose, ratio, pours, meta=None):
    out = {"name": name, "cup_type": 2, "dose_g": dose, "ratio": ratio, "grind": False,
           "grind_size": 60, "grinder_speed_rpm": 80, "pours": pours}
    if meta:
        out["meta"] = meta
    return out


RECIPES = {
    "short": _recipe("Short test", 5, "1:5", [_pour(25)]),
    "long": _recipe("Long test", 5, "1:20", [_pour(100)]),
    "hostile": _recipe(HOSTILE, 5, "1:5", [_pour(25)], {
        "notes": "<script>window.__pwned=2</script>" + "n" * 400,
        "roaster": "<b onmouseover=\"window.__pwned=3\">roaster</b>",
        "bean": "{x|bean}", "process": "<i>process</i>", "roast": "light",
        "source": "javascript:window.__pwned=4"}),
    "src-data": _recipe("Source data", 5, "1:5", [_pour(25)], {"source": "data:text/html,<b>x</b>"}),
    "src-upper": _recipe("Source upper", 5, "1:5", [_pour(25)], {"source": "HTTPS://example.com/upper"}),
    "src-ok": _recipe("Source ok", 5, "1:5", [_pour(25)], {"source": "https://example.com/ok"}),
}


def _record(brew_id, started):
    recipe = RECIPES["hostile"]
    return {
        "id": brew_id, "started_at": started, "ended_at": started, "reason": "finished",
        "duration_s": 12.0, "opened_by": "activity_35", "hub_started": True, "stop_sent": False,
        "pour": 0, "slug": "hostile", "recipe": recipe,
        "as_sent": {"dose_g": 5, "ratio": "1:5", "pour_volumes_ml": [25], "total_ml": 25},
        "samples": {"water": [[0.5, 0.0], [4.0, 12.0], [8.0, 25.0], [12.0, 25.0]],
                    "weight": [[0.5, 0.0], [4.0, 8.0], [8.0, 18.0], [12.0, 22.0]]},
        "events": [[0.0, {"cmd": 8023, "activity": 35}], [0.6, {"cmd": 40510, "pour_index": 0}],
                   [8.0, {"cmd": 40511}], [12.0, {"cmd": 40512}]],
        "rezeroed": [],
    }


class Sim(SimListener):
    brew_seconds = 6.0          # long enough to see and stop, short enough to wait for
    done_seconds = 1.0


class Hub:
    def __init__(self, base: str, loop: asyncio.AbstractEventLoop, machine: Machine) -> None:
        self.base = base
        self.loop = loop
        self.machine = machine

    def at_machine(self, action: str, *args) -> None:
        """Do what a person at the machine does: `cup` (False lifts it), `knob_start`."""
        done = threading.Event()

        def run():
            getattr(self.machine._listener, action)(*args)
            done.set()

        self.loop.call_soon_threadsafe(run)
        assert done.wait(5)

    def api(self, method: str, path: str, body: dict | None = None) -> httpx.Response:
        return httpx.request(method, self.base + path, json=body, timeout=10,
                             headers={"Authorization": f"Bearer {TOKEN}"})

    def state(self) -> dict:
        return httpx.get(self.base + "/api/state", timeout=5).json()

    def wait_state(self, want: str, timeout: float = 15) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snap = self.state()
            if snap["state"] == want:
                return snap
            time.sleep(0.1)
        raise AssertionError(f"hub never reached {want}; last {self.state()['state']}")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def hub(tmp_path, monkeypatch):
    if other := os.environ.get("XBLOOM_TEST_STATIC_DIR"):
        monkeypatch.setattr(hub_app, "STATIC_DIR", Path(other))
    if other := os.environ.get("XBLOOM_TEST_WEB_DIR"):
        monkeypatch.setattr(hub_app, "WEB_DIR", Path(other))
    (tmp_path / "recipes").mkdir()
    for slug, recipe in RECIPES.items():
        (tmp_path / "recipes" / f"{slug}.yaml").write_text(
            yaml.safe_dump(recipe, allow_unicode=True), encoding="utf-8")
    (tmp_path / "brews").mkdir()
    for brew_id, started in (("20261101-080000-aaaa", "2026-11-01T08:00:00+08:00"),
                             ("20261102-080000-bbbb", "2026-11-02T08:00:00+08:00")):
        (tmp_path / "brews" / f"{brew_id}.json").write_text(
            json.dumps(_record(brew_id, started), ensure_ascii=False), encoding="utf-8")

    radio = SimRadio()
    brews = BrewStore(tmp_path / "brews")
    machine = Machine(radio, Sim, brews)
    store = RecipeStore(tmp_path / "recipes")
    application = hub_app.create_app(machine, store, TOKEN, ["127.0.0.1"], radio, brews)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(application, host="127.0.0.1", port=port,
                                           log_level="warning", timeout_graceful_shutdown=1))
    loop = asyncio.new_event_loop()

    async def serve():
        try:
            await server.serve()
        finally:
            # What the simulated machine still has running ends with the hub,
            # on this loop and in this thread (the test's own thread has
            # Playwright's loop running and cannot run another).
            others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for other in others:
                other.cancel()
            _, stuck = await asyncio.wait(others, timeout=5) if others else (None, set())
            for task in stuck:
                print(f"simulator task did not stop: {task.get_coro()}")

    def run():
        try:
            loop.run_until_complete(serve())
        finally:
            loop.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    hub = Hub(f"http://127.0.0.1:{port}", loop, machine)
    deadline = time.monotonic() + 15
    while True:
        try:
            if hub.state()["state"] == "idle":
                break
        except httpx.HTTPError:
            pass
        assert time.monotonic() < deadline, "the simulator hub did not come up"
        time.sleep(0.1)
    yield hub
    server.should_exit = True
    thread.join(10)
    assert not thread.is_alive(), "the simulator hub did not stop"


# Every problem the page reports, from every document it loads: a CSP
# violation, a script error, an unhandled rejection. Installed before any
# script of the page runs.
PROBE = """
(() => {
  const say = (text) => console.error('[probe] ' + text);
  document.addEventListener('securitypolicyviolation',
    (e) => say('csp ' + e.violatedDirective + ' ' + e.blockedURI + ' ' + e.sourceFile + ':' + e.lineNumber));
  window.addEventListener('error', (e) => say('error ' + e.message));
  window.addEventListener('unhandledrejection', (e) => say('rejection ' + e.reason));
})();
"""


@pytest.fixture(params=["", "next/"], ids=["current", "next"])
def ui(request, page, hub):
    """The dashboard, opened with the token, in English, watched for problems.
    Every test runs against the current page at / and the rebuilt one at
    /next/ (docs/PLAN.md, S3c: the switch waits until both pass)."""
    if request.param and not (hub_app.WEB_DIR / "index.html").is_file():
        pytest.skip("the new dashboard is not built (frontend/npm.sh run build)")
    problems: list[str] = []

    def on_console(message):
        text = message.text
        if text.startswith("[probe] ") or "Content Security Policy" in text or "Refused to" in text:
            problems.append(text)

    page.on("console", on_console)
    page.on("pageerror", lambda error: problems.append(f"pageerror {error}"))
    page.add_init_script(PROBE)
    # English unless a test has chosen otherwise.
    page.add_init_script(
        "try { if (!localStorage.getItem('xbloom-hub-lang')) localStorage.setItem('xbloom-hub-lang', 'en'); } catch (_) {}")
    page.goto(f"{hub.base}/{request.param}#t={TOKEN}")
    page.wait_for_selector("#machine .state")
    page.problems = problems
    yield page
    assert not problems, problems
