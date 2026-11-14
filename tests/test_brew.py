"""S3 without hardware: the start permit, start and stop, and the brew record.

The simulator's brew is a guess at what the machine reports; these tests pin
the hub's rules, not the machine's behaviour.
"""
import asyncio
import pathlib
import json
import time

import pytest
from starlette.testclient import TestClient
from xbloom.ble import FFE1_UUID, CommandRefused, CommandUnanswered

from xbloom_hub import gate
import xbloom_hub.machine as machine_module
from xbloom_hub.app import CSP, check_allowed_hosts, create_app
from xbloom_hub.brews import BadBrewId, BrewStore
from xbloom_hub.machine import BadTicket, Machine, NoCup, NotReady
from xbloom_hub.recipes import RecipeStore, as_sent, checked, to_machine
from xbloom_hub.sim import SimListener, SimRadio, _SimClient

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
RECIPE = checked({
    "name": "Test", "cup_type": 2, "dose_g": 5, "ratio": "1:20",
    "grind": False, "grind_size": 40, "grinder_speed_rpm": 60,
    "pours": [{"volume_ml": 60, "temperature_c": 20, "pause_s": 1, "pattern": 1, "flow_rate": 3.0},
              {"volume_ml": 40, "temperature_c": 20, "pause_s": 0, "pattern": 1, "flow_rate": 3.0}],
})
ITEM = {"wire": to_machine(RECIPE), "recipe": RECIPE, "as_sent": as_sent(RECIPE)}


def frame(code: int) -> bytes:
    return bytes([0x58, 0x01, 0x01, code & 0xFF, code >> 8, 0, 0, 0, 0])


class Fast(SimListener):
    brew_seconds = 0.6


async def ready(tmp_path, listener=Fast):
    brews = BrewStore(tmp_path / "brews")
    machine = Machine(SimRadio(), listener, brews)
    task = asyncio.create_task(machine.run())
    assert await machine._wait_for(lambda: machine.state == "idle", 2)
    return machine, brews, task


async def armed(machine):
    loaded = await machine.load("test", lambda: ITEM)
    assert loaded["state"] == "armed"
    return loaded["ticket"]


def gate_log(machine):
    return machine._listener._client.gate_log


# ---- the start permit ------------------------------------------------------ #
def test_8002_passes_only_inside_a_permit_and_only_in_that_task():
    client = gate.make_gated(_SimClient)()

    async def run():
        for code in (8002, 40524, 8021, 3500, 4506):
            with pytest.raises(gate.GateRefused):
                await client.write_gatt_char(FFE1_UUID, frame(code))
        await client.write_gatt_char(FFE1_UUID, frame(40519))        # stop is always allowed

        async def other_task():
            with pytest.raises(gate.GateRefused):
                await client.write_gatt_char(FFE1_UUID, frame(8002))

        waiting = asyncio.create_task(asyncio.sleep(0))              # created before the permit
        with gate.start_permit():
            await client.write_gatt_char(FFE1_UUID, frame(8002))
            for code in (40524, 8021, 3500, 4506):                   # still refused inside
                with pytest.raises(gate.GateRefused):
                    await client.write_gatt_char(FFE1_UUID, frame(code))
            # A task that did not inherit the permit cannot use it.
            await asyncio.get_running_loop().create_task(other_task(), context=waiting.get_context())
        with pytest.raises(gate.GateRefused):                        # and it ends with the block
            await client.write_gatt_char(FFE1_UUID, frame(8002))

    asyncio.run(run())
    assert client.sent == [40519, 8002]


def test_the_librarys_own_start_paths_are_refused(tmp_path):
    async def run():
        machine, _, task = await ready(tmp_path)
        await armed(machine)
        client = machine._listener._client
        with pytest.raises(gate.GateRefused):                        # e.g. send_start()/brew()
            await client.write_gatt_char(FFE1_UUID, frame(8002))
        assert 8002 not in gate_log(machine)
        task.cancel()

    asyncio.run(run())


# ---- start ------------------------------------------------------------------- #
def test_start_needs_the_ticket_once_and_an_armed_machine(tmp_path):
    async def run():
        machine, brews, task = await ready(tmp_path)
        with pytest.raises(NotReady):
            await machine.start("anything")                          # nothing loaded
        ticket = await armed(machine)
        for wrong in (None, "", "nope", 5, ticket + "x"):
            with pytest.raises(BadTicket):
                await machine.start(wrong)
        assert machine.state == "armed" and 8002 not in gate_log(machine)

        started = await machine.start(ticket)
        assert started["state"] == "brewing" and gate_log(machine).count(8002) == 1
        with pytest.raises(NotReady):
            await machine.start(ticket)                              # consumed, and not armed
        assert await machine._wait_for(lambda: machine.state == "idle", 3)
        record = brews.get(brews.list()[0]["id"])
        assert record["reason"] == "finished" and record["hub_started"] is True
        assert record["recipe"]["name"] == "Test" and record["as_sent"]["total_ml"] == 100
        assert record["samples"]["water"][-1][1] == pytest.approx(100, abs=1)
        assert [e[1]["pour_index"] for e in record["events"] if e[1]["cmd"] == 40510] == [0, 1]
        assert gate_log(machine).count(8002) == 1
        task.cancel()

    asyncio.run(run())


def test_ticket_is_void_after_cancel_another_load_or_expiry(tmp_path, monkeypatch):
    async def run():
        machine, _, task = await ready(tmp_path)
        first = await armed(machine)
        await machine.cancel()
        second = await armed(machine)
        with pytest.raises(BadTicket):
            await machine.start(first)                               # from the cancelled load
        import xbloom_hub.machine as m
        real = time.monotonic
        monkeypatch.setattr(m.time, "monotonic", lambda: real() + m.TICKET_TTL_S + 1)
        with pytest.raises(BadTicket):
            await machine.start(second)                              # expired
        monkeypatch.setattr(m.time, "monotonic", real)
        with pytest.raises(BadTicket):
            await machine.start(second)                              # and gone for good
        assert 8002 not in gate_log(machine)
        task.cancel()

    asyncio.run(run())


def test_no_start_when_the_cup_was_taken_off(tmp_path):
    async def run():
        machine, _, task = await ready(tmp_path)
        assert machine.snapshot()["cup"] is None                     # no event seen yet
        ticket = await armed(machine)
        machine._listener.cup(False)
        with pytest.raises(NoCup):
            await machine.start(ticket)
        assert machine.state == "armed" and 8002 not in gate_log(machine)
        machine._listener.cup(True)
        assert (await machine.start(ticket))["state"] == "brewing"   # the ticket was not consumed
        task.cancel()

    asyncio.run(run())


def test_refused_start_leaves_it_armed_and_cancellable(tmp_path):
    class Refusing(Fast):
        start_answer = "refuse"

    async def run():
        machine, brews, task = await ready(tmp_path, Refusing)
        ticket = await armed(machine)
        with pytest.raises(CommandRefused):
            await machine.start(ticket)
        assert machine.state == "armed" and brews.list() == []
        with pytest.raises(BadTicket):
            await machine.start(ticket)                              # consumed all the same
        assert (await machine.cancel())["state"] == "idle"
        await armed(machine)                                         # and a load works again
        task.cancel()

    asyncio.run(run())


def test_unanswered_start(tmp_path):
    class Ignoring(Fast):
        start_answer = "ignore"                                      # never took it

    class Lost(Fast):
        start_answer = "lost"                                        # took it, echo lost

    async def run():
        machine, brews, task = await ready(tmp_path / "a", Ignoring)
        with pytest.raises(CommandUnanswered):
            await machine.start(await armed(machine))
        assert machine.state == "armed" and brews.list() == []
        assert (await machine.cancel())["state"] == "idle"
        task.cancel()

        machine, brews, task = await ready(tmp_path / "b", Lost)
        with pytest.raises(CommandUnanswered):
            await machine.start(await armed(machine))
        assert await machine._wait_for(lambda: machine.state == "brewing", 1)
        assert (await machine.stop())["state"] == "idle"             # Stop is there
        assert brews.get(brews.list()[0]["id"])["reason"] == "stopped"
        task.cancel()

    asyncio.run(run())


# ---- when a brew opens and closes ---------------------------------------------- #
def test_knob_start_keeps_the_loaded_recipe_and_one_with_nothing_loaded_has_none(tmp_path):
    async def run():
        machine, brews, task = await ready(tmp_path)
        await armed(machine)
        machine._listener.knob_start()                               # no 8002 from the hub
        assert await machine._wait_for(lambda: machine.state == "brewing", 1)
        assert machine.snapshot()["brew"]["recipe_name"] == "Test"
        assert await machine._wait_for(lambda: machine.state == "idle", 3)
        machine._listener.knob_start()                               # nothing loaded this time
        assert await machine._wait_for(lambda: machine.state == "brewing", 1)
        assert await machine._wait_for(lambda: machine.state == "idle", 3)
        newest, older = (brews.get(b["id"]) for b in brews.list())
        assert (older["recipe"]["name"], older["hub_started"]) == ("Test", False)
        assert newest["recipe"] is None and newest["slug"] is None
        assert 8002 not in gate_log(machine)
        task.cancel()

    asyncio.run(run())


def test_only_brew_signals_open_a_record(tmp_path):
    async def run():
        machine, brews, task = await ready(tmp_path)
        raw = machine._listener._on_raw
        for decoded in ({"cmd": 8023, "activity": 29}, {"cmd": 9007}, {"cmd": 8023, "activity": 4},
                        {"cmd": 8023, "activity": 5}, {"cmd": 8023, "activity": 31},
                        {"cmd": 8023, "activity": 36}, {"cmd": 40511}, {"cmd": 40512},
                        {"cmd": 40513}, {"cmd": 8023, "activity": 77},   # unknown, no hub start
                        {"cmd": 8023, "activity": 10}, {"cmd": 8023, "activity": 1}):
            raw(decoded)
            assert machine._brew is None, decoded
        assert brews.list() == []
        await armed(machine)                                          # a load still works
        task.cancel()

    asyncio.run(run())


def test_unknown_activity_counts_only_after_a_hub_start(tmp_path):
    class Odd(Fast):
        brewing_activity = 77                                         # not the value we expect

        async def _brew(self):                                        # and no pour events
            self._on_raw({"cmd": 8023, "activity": 77})
            await asyncio.sleep(0.2)
            self._on_raw({"cmd": 40512})
            self._on_raw({"cmd": 8023, "activity": 1})

    async def run():
        machine, brews, task = await ready(tmp_path, Odd)
        await machine.start(await armed(machine))
        assert await machine._wait_for(lambda: machine.state == "idle", 2)
        record = brews.get(brews.list()[0]["id"])
        assert record["opened_by"] == "activity_77_after_start" and record["reason"] == "finished"
        task.cancel()

    asyncio.run(run())


def test_a_finished_brew_is_not_reopened_by_lingering_activity(tmp_path):
    class Lingering(Fast):
        linger_at_brewing = 5

    async def run():
        machine, brews, task = await ready(tmp_path, Lingering)
        await machine.start(await armed(machine))
        assert await machine._wait_for(lambda: machine.state == "idle", 3)
        await asyncio.sleep(0.2)
        assert len(brews.list()) == 1 and machine._brew is None
        await armed(machine)
        task.cancel()

    asyncio.run(run())


def drop_link(machine):
    """The link failing under the session, as a BLE drop does."""
    machine._lifecycle.put_nowait(("failed", {"reason": "link_lost_in_test"}))


async def reconnected(machine, old_listener):
    assert await machine._wait_for(
        lambda: machine._listener is not None and machine._listener is not old_listener, 5)
    return machine._listener._on_raw


def test_asleep_and_link_loss_close_a_record(tmp_path):
    async def run():
        machine, brews, task = await ready(tmp_path)
        machine._listener._on_raw({"cmd": 40510, "pour_index": 0})
        assert machine.state == "brewing"
        machine._listener._on_raw({"cmd": 8023, "activity": 31})      # a pause: still brewing
        assert machine.state == "brewing"
        machine._listener._on_raw({"cmd": 8023, "activity": 10})
        assert machine.state == "asleep" and brews.list()[0]["reason"] == "asleep"

        machine._listener._on_raw({"cmd": 40510, "pour_index": 0})
        assert machine.state == "brewing"
        drop_link(machine)
        assert await machine._wait_for(lambda: machine._brew is None, 2)
        assert [b["reason"] for b in brews.list()] == ["link_lost", "asleep"]   # newest first
        task.cancel()

    asyncio.run(run())


def test_stop_only_while_brewing_and_one_at_a_time(tmp_path):
    class Slow(SimListener):
        brew_seconds = 5

    async def run():
        machine, brews, task = await ready(tmp_path, Slow)
        with pytest.raises(NotReady):
            await machine.stop()
        await machine.start(await armed(machine))
        first = asyncio.create_task(machine.stop())
        await asyncio.sleep(0)
        results = await asyncio.gather(first, machine.stop(), return_exceptions=True)
        assert sum(isinstance(r, NotReady) for r in results) <= 1
        assert machine.state == "idle" and gate_log(machine).count(40519) >= 1
        assert brews.get(brews.list()[0]["id"])["reason"] == "stopped"
        task.cancel()

    asyncio.run(run())


def test_record_is_on_disk_while_brewing_and_never_holds_the_serial(tmp_path):
    class Slow(SimListener):
        brew_seconds = 5

    async def run():
        machine, brews, task = await ready(tmp_path, Slow)
        await machine.start(await armed(machine))
        listed = brews.list()
        assert len(listed) == 1 and listed[0]["ended_at"] is None     # written at open
        machine._listener._on_raw({"cmd": 40521, "serial": "SECRETSERIAL", "fw_version": "X"})
        machine._listener._on_raw({"cmd": 9999, "serial": "SECRETSERIAL"})
        queue = machine.subscribe()
        machine._listener._on_raw({"cmd": 8023, "activity": 34})
        await machine.stop()
        text = json.dumps(brews.get(listed[0]["id"])) + json.dumps(machine.snapshot())
        while not queue.empty():
            text += json.dumps(queue.get_nowait())
        assert "SECRETSERIAL" not in text and "SIM0000000000" not in text
        task.cancel()

    asyncio.run(run())


def test_brew_ids_are_the_hubs_own(tmp_path):
    store = BrewStore(tmp_path)
    for bad in ("../x", "x", "20260101-000000-abc", "20260101-000000-ABCD", "", None,
                "20260101-000000-abcd/../../x"):
        with pytest.raises(BadBrewId):
            store.get(bad)
    assert store.get(store.new_id()) is None


# ---- http and the page --------------------------------------------------------- #
@pytest.fixture
def client(tmp_path):
    machine = Machine(SimRadio(), Fast, BrewStore(tmp_path / "brews"))
    store = RecipeStore(tmp_path / "recipes")
    store.put("test", RECIPE)
    app = create_app(machine, store, TOKEN, ["testserver"], brews=machine._brews)
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        deadline = time.monotonic() + 3
        while c.get("/api/state").json()["state"] != "idle":
            assert time.monotonic() < deadline
            time.sleep(0.05)
        c.machine = machine
        yield c


def test_start_and_stop_over_http_and_not_over_mcp(client):
    assert client.post("/api/machine/start", json={"ticket": "x"}).status_code == 401
    assert client.post("/api/machine/stop").status_code == 401
    loaded = client.post("/api/machine/load", json={"slug": "test"}, headers=AUTH).json()
    assert client.post("/api/machine/start", json={"ticket": "bad"}, headers=AUTH).status_code == 403
    assert client.post("/api/machine/start", json={}, headers=AUTH).status_code == 403
    started = client.post("/api/machine/start", json={"ticket": loaded["ticket"]}, headers=AUTH)
    assert started.status_code == 200 and started.json()["state"] == "brewing"
    state = client.get("/api/state").json()
    assert state["brew"]["recipe_name"] == "Test" and "ticket" not in json.dumps(state)
    deadline = time.monotonic() + 4
    while client.get("/api/state").json()["state"] != "idle":
        assert time.monotonic() < deadline
        time.sleep(0.05)
    listed = client.get("/api/brews").json()
    assert len(listed) == 1 and listed[0]["reason"] == "finished"
    record = client.get(f"/api/brews/{listed[0]['id']}").json()
    assert record["samples"]["weight"] and "ticket" not in json.dumps(record)
    assert client.get("/api/brews/nope").status_code == 400
    assert client.post("/api/machine/stop", headers=AUTH).status_code == 409

    tools = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                        headers={**AUTH, "Accept": "application/json, text/event-stream"})
    names = {t["name"] for t in tools.json()["result"]["tools"]}
    assert not names & {"start", "start_brew", "stop", "stop_brew", "brew"}
    assert len(names) == 11          # the eight of S2 and three for the community hub (S3b)


def test_the_page_is_served_under_a_strict_policy(client):
    page = client.get("/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    import re
    from xbloom_hub.app import BUILD_COMMAND, WEB_DIR
    if not (WEB_DIR / "index.html").is_file():
        pytest.skip(f"the dashboard is not built ({BUILD_COMMAND}); the browser tests check it too")
    assets = re.findall(r'(?:src|href)="\./(assets/[^"]+)"', page.text)
    assert assets, page.text
    for response in (page, client.get("/api/state"), *(client.get(f"/{a}") for a in assets)):
        assert response.status_code == 200
        assert response.headers["content-security-policy"] == CSP
        assert response.headers["x-content-type-options"] == "nosniff"
    assert "'unsafe-inline'" not in CSP and "'unsafe-eval'" not in CSP
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400


def test_allowed_hosts_cannot_be_a_wildcard():
    for bad in ([], [""], ["*"], ["localhost", "*.example.com"], ["a", ""]):
        with pytest.raises(SystemExit):
            check_allowed_hosts(bad)
    assert check_allowed_hosts(["localhost", "hub.local"]) == ["localhost", "hub.local"]


# ---- interface text --------------------------------------------------------------
FRONTEND = pathlib.Path(__file__).resolve().parents[1] / "frontend"

def _dictionaries():
    import re

    text = (FRONTEND / "src" / "lib" / "messages.ts").read_text(encoding="utf-8")
    blocks = {}
    for code in ("zh-Hant", "en"):
        start = text.index(f'"{code}": {{') if code == "zh-Hant" else text.index("\n  en: {")
        end = text.index("\n  },", start)
        # Keys may hold digits (cup_type.1, pattern.3).
        blocks[code] = dict(re.findall(r'"([a-z0-9_.]+)":\s*"((?:[^"\\\\]|\\\\.)*)"', text[start:end]))
    return blocks


def test_every_language_has_the_same_keys_and_placeholders():
    import re

    blocks = _dictionaries()
    zh, en = blocks["zh-Hant"], blocks["en"]
    assert len(en) > 100 and set(zh) == set(en), set(zh) ^ set(en)
    for key in en:
        assert set(re.findall(r"\{(\w+)\}", zh[key])) == set(re.findall(r"\{(\w+)\}", en[key])), key
        assert zh[key].strip() and en[key].strip(), key


# Every reason the hub gives for being offline (machine.py's self._error).
def _reasons():
    import re
    text = (pathlib.Path(__file__).resolve().parents[1] / "src" / "xbloom_hub" / "machine.py").read_text(encoding="utf-8")
    return tuple(sorted(set(re.findall(r'self\._error = "([a-z_]+)"', text))))


REASONS = _reasons()


def test_the_page_uses_only_keys_that_exist_and_holds_no_text_of_its_own():
    import re

    keys = set(_dictionaries()["en"])
    sources = [p.read_text(encoding="utf-8") for p in (FRONTEND / "src").rglob("*")
               if p.suffix in (".svelte", ".ts") and p.name != "messages.ts"]
    used = set()
    for text in sources:
        used |= set(re.findall(r'\bt\("([a-z_.]+)"', text))
    assert len(used) > 80 and used <= keys, used - keys
    # Keys built at run time: every value the hub can send has an entry.
    for prefix, values in (("state", ("offline", "idle", "asleep", "armed", "brewing", "finished", "busy", "released", "unheard")),
                           ("ending", ("finished", "stopped", "abandoned", "home", "asleep", "link_lost")),
                           ("error", ("unauthorized", "not_ready", "bad_ticket", "no_cup", "recipe_is_loaded",
                                      "recipe_invalid", "recipe_exists", "machine_refused", "machine_unanswered",
                                      "cancel_failed", "not_found", "foreign_origin")),
                           ("meta", ("bean", "roaster", "process", "roast", "source", "notes")),
                           ("cup_type", ("1", "2", "3", "4")), ("pattern", ("1", "2", "3")),
                           ("tab", ("home", "recipes", "history", "console", "label")),
                           ("reason", REASONS)):
        for value in values:
            assert f"{prefix}.{value}" in keys, f"{prefix}.{value}"
    # All interface text lives in the dictionaries: no Chinese in the sources.
    han = re.compile(r"[\u4e00-\u9fff]")
    for path in (FRONTEND / "src").rglob("*"):
        if path.suffix in (".svelte", ".ts") and path.name != "messages.ts":
            assert not han.search(path.read_text(encoding="utf-8")), path

def test_library_lists_what_a_card_shows_and_survives_a_bad_file(client, tmp_path):
    (tmp_path / "recipes" / "broken.yaml").write_text("name: Broken\ndose_g: 99\n")
    library = {r["slug"]: r for r in client.get("/api/library").json()}
    assert library["test"]["valid"] is True and library["test"]["total_ml"] == 100
    assert [p["volume_ml"] for p in library["test"]["pours"]] == [60, 40]
    assert library["test"]["ratio"] == "1:20" and library["test"]["pour_end_s"] > 0
    assert library["broken"] == {"slug": "broken", "name": "Broken", "valid": False}
    # A hand-made file whose name is not a slug, a list, bytes, a directory:
    # each is shown as unusable or skipped, and the library still answers.
    recipes = tmp_path / "recipes"
    (recipes / "Bad Name.yaml").write_text((recipes / "test.yaml").read_text())
    (recipes / "list.yaml").write_text("- 1\n")
    (recipes / "bytes.yaml").write_bytes(b"\xff\xfe")
    (recipes / "dir.yaml").mkdir()
    again = client.get("/api/library")
    assert again.status_code == 200
    by_slug = {r["slug"]: r for r in again.json()}
    assert by_slug["Bad Name"]["valid"] is False and by_slug["test"]["valid"] is True
    assert by_slug["list"]["valid"] is False and by_slug["bytes"]["valid"] is False


# ---- from the S3 pre-push review and verification -------------------------------
def test_a_task_created_inside_the_permit_cannot_start_later():
    client = gate.make_gated(_SimClient)()

    async def run():
        async def later():
            await asyncio.sleep(0.05)                        # after the block has ended
            await client.write_gatt_char(FFE1_UUID, frame(8002))

        async def at_once():                                 # during the block, before the owner writes
            await client.write_gatt_char(FFE1_UUID, frame(8002))

        with gate.start_permit():
            inherited = asyncio.create_task(later())         # copies the context, permit and all
            early = asyncio.create_task(at_once())
            with pytest.raises(gate.GateRefused):            # not its permit, even while open
                await early
            await client.write_gatt_char(FFE1_UUID, frame(8002))
            await client.write_gatt_char(FFE1_UUID, frame(8002))     # the owner's own re-send
        with pytest.raises(gate.GateRefused):
            await inherited
        with pytest.raises(gate.GateRefused):                # the owner too, once the block has ended
            await client.write_gatt_char(FFE1_UUID, frame(8002))

        with pytest.raises(RuntimeError):
            with gate.start_permit():
                kept = asyncio.create_task(later())
                raise RuntimeError("inside the block")
        with pytest.raises(gate.GateRefused):                # an exception closes it as well
            await kept

    asyncio.run(run())
    assert client.sent == [8002, 8002]


def test_a_brew_that_opens_while_still_at_31_voids_the_ticket(tmp_path):
    async def run():
        machine, _, task = await ready(tmp_path)
        ticket = await armed(machine)
        raw = machine._listener._on_raw
        raw({"cmd": 40510, "pour_index": 0})                 # pour reported, activity still 31
        assert machine.state == "brewing" and machine._ticket is None
        assert machine.snapshot()["recipe"] is None
        raw({"cmd": 8023, "activity": 1})
        assert machine.state == "idle"
        raw({"cmd": 8023, "activity": 31})                   # the machine shows 31 again
        assert machine.state == "busy"                       # not armed: nothing is loaded
        with pytest.raises(NotReady):
            await machine.start(ticket)
        assert 8002 not in gate_log(machine)
        task.cancel()

    asyncio.run(run())


def test_an_unknown_activity_after_a_short_brew_opens_nothing(tmp_path):
    async def run():
        machine, brews, task = await ready(tmp_path)
        await machine.start(await armed(machine))            # the 30 s window is running
        raw = machine._listener._on_raw
        machine._listener._brewing.cancel()                  # drive the rest by hand
        raw({"cmd": 40512})
        assert machine._brew is None
        raw({"cmd": 8023, "activity": 37})                   # unknown, seconds after the start
        assert machine._brew is None and machine.state == "busy"
        assert len(brews.list()) == 1 and brews.list()[0]["reason"] == "finished"
        raw({"cmd": 8023, "activity": 1})
        await armed(machine)                                 # and a load works again
        task.cancel()

    asyncio.run(run())


def test_stop_during_teardown_and_a_stop_the_machine_did_not_take(tmp_path):
    class Deaf(SimListener):
        brew_seconds = 5

        async def send_confirmed(self, name, frame):
            from xbloom.ble import frame_command_code
            if frame_command_code(frame) == 40519:
                await self._client.write_gatt_char(FFE1_UUID, frame)
                return False                                 # written, never answered
            return await super().send_confirmed(name, frame)

    async def run():
        machine, brews, task = await ready(tmp_path, Deaf)
        await machine.start(await armed(machine))
        with pytest.raises(CommandUnanswered):
            await machine.stop()
        assert machine.state == "brewing"
        machine._listener._on_raw({"cmd": 8023, "activity": 1})      # ends by itself later
        assert brews.get(brews.list()[0]["id"])["reason"] == "home"  # not "stopped"

        machine._listener._on_raw({"cmd": 40510, "pour_index": 0})
        listener, machine._listener = machine._listener, None        # the link is going down
        with pytest.raises(NotReady):
            await machine.stop()
        machine._listener = listener
        task.cancel()

    asyncio.run(run())


def test_a_refused_start_gets_no_credit_for_a_later_knob_start(tmp_path):
    class Refusing(Fast):
        start_answer = "refuse"

    async def run():
        machine, brews, task = await ready(tmp_path, Refusing)
        with pytest.raises(CommandRefused):
            await machine.start(await armed(machine))
        machine._listener._on_raw({"cmd": 8023, "activity": 77})     # unknown value: no window now
        assert machine._brew is None
        machine._listener._on_raw({"cmd": 8023, "activity": 31})
        machine._listener.knob_start()
        assert await machine._wait_for(lambda: machine.state == "brewing", 1)
        assert await machine._wait_for(lambda: machine.state == "idle", 3)
        assert brews.get(brews.list()[0]["id"])["hub_started"] is False
        task.cancel()

    asyncio.run(run())


def test_record_times_are_iso_8601_with_a_colon_in_the_offset(tmp_path):
    import datetime
    import re

    async def run():
        machine, brews, task = await ready(tmp_path)
        await machine.start(await armed(machine))
        assert await machine._wait_for(lambda: machine.state == "idle", 3)
        record = brews.get(brews.list()[0]["id"])
        for key in ("started_at", "ended_at"):
            assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d", record[key]), record[key]
            datetime.datetime.fromisoformat(record[key])
        task.cancel()

    asyncio.run(run())


def test_a_brew_file_that_is_not_a_record_does_not_break_the_list(tmp_path):
    store = BrewStore(tmp_path)
    (tmp_path / "20260101-000000-000a.json").write_text("[1, 2]")
    (tmp_path / "20260101-000000-001a.json").write_text("not json")
    assert store.list() == []


def test_as_measured_water_in_thousandths_and_the_screen_after_a_brew(tmp_path):
    # docs/S3-RESULTS.md: 35 while brewing, 40512, 36 some seconds later, home.
    async def run():
        machine, brews, task = await ready(tmp_path)
        await machine.start(await armed(machine))
        machine._listener._brewing.cancel()
        raw = machine._listener._on_raw
        raw({"cmd": 8023, "activity": 35})
        raw({"cmd": 40510, "pour_index": 0})
        raw({"cmd": 40523, "water_ml": 25000.0})
        assert machine.snapshot()["water_ml"] == 25.0
        raw({"cmd": 40512})
        assert machine.state == "finished"                   # the machine still says 35
        raw({"cmd": 8023, "activity": 36})
        assert machine.state == "finished"
        raw({"cmd": 40513})
        raw({"cmd": 8023, "activity": 1})
        assert machine.state == "idle"
        assert len(brews.list()) == 1
        record = brews.get(brews.list()[0]["id"])
        assert record["reason"] == "finished" and record["samples"]["water"][-1][1] == 25.0
        task.cancel()

    asyncio.run(run())


def test_the_scale_reset_during_a_brew_is_carried_over(tmp_path):
    # As measured: 5.61 g in the cup, then 0.0, then on to 16.9 g.
    async def run():
        machine, brews, task = await ready(tmp_path)
        await machine.start(await armed(machine))
        machine._listener._brewing.cancel()
        raw = machine._listener._on_raw
        raw({"cmd": 40510, "pour_index": 0})
        for grams in (0.0, 4.53, 5.61, 0.0, 1.54, 16.9):
            raw({"cmd": 20501, "weight_g": grams})
        assert machine.snapshot()["weight_g"] == 22.51
        raw({"cmd": 20501, "weight_g": 0.3})                 # a reading near zero, then far
        raw({"cmd": 20501, "weight_g": -180.0})              # below it: not a reset (values invented)
        raw({"cmd": 20501, "weight_g": 16.9})
        raw({"cmd": 40512})
        record = brews.get(brews.list()[0]["id"])
        assert [g for _, g in record["samples"]["weight"]][-3:] == [22.51, -174.39, 22.51]
        assert len(record["rezeroed"]) == 1 and record["rezeroed"][0][1] == 5.61
        raw({"cmd": 20501, "weight_g": 3.0})                 # after the brew: as the machine says
        assert machine.snapshot()["weight_g"] == 3.0
        task.cancel()

    asyncio.run(run())


def test_each_load_has_its_own_public_id_and_the_ticket_stays_private(tmp_path):
    """A page tells its own load from any other by the load id in the state."""
    async def run():
        machine, _, task = await ready(tmp_path)
        assert machine.snapshot()["load_id"] is None
        first = await machine.load("test", lambda: ITEM)
        assert first["load_id"] and machine.snapshot()["load_id"] == first["load_id"]
        assert first["ticket"] not in str(machine.snapshot())
        await machine.cancel()
        assert machine.snapshot()["load_id"] is None
        second = await machine.load("test", lambda: ITEM)
        assert second["load_id"] and second["load_id"] != first["load_id"]
        await machine.start(second["ticket"])
        assert machine.snapshot()["load_id"] is None           # the ticket is spent
        task.cancel()

    asyncio.run(run())



def test_a_running_brew_is_served_as_it_is_now(tmp_path):
    """A page joining mid-brew gets the readings up to now, not the last checkpoint."""
    async def run():
        machine, brews, task = await ready(tmp_path)
        await machine.start(await armed(machine))
        assert await machine._wait_for(lambda: machine.state == "brewing", 2)
        brew_id = machine._brew["id"]
        machine._listener._on_raw({"cmd": 20501, "weight_g": 3.3})
        record = machine.open_record(brew_id)
        assert record["samples"]["weight"][-1][1] == 3.3
        assert not any(key.startswith("_") for key in record)
        assert machine.open_record("20990101-000000-zzzz") is None
        task.cancel()

    asyncio.run(run())


def test_leaving_the_screen_after_the_last_pour_is_a_finished_brew(tmp_path):
    # As measured 2026-11-14: 40511, then 40513 two seconds later, no 40512.
    async def run():
        machine, brews, task = await ready(tmp_path)
        for pour_ended, reason in ((True, "finished"), (False, "abandoned")):
            await machine.start(await armed(machine))
            machine._listener._brewing.cancel()
            raw = machine._listener._on_raw
            raw({"cmd": 8023, "activity": 35})
            pours = len(ITEM["recipe"]["pours"])
            for index in range(pours):
                raw({"cmd": 40510, "pour_index": index})
                if pour_ended or index < pours - 1:        # unfinished: the last pour never ends
                    raw({"cmd": 40511})
            raw({"cmd": 40513})
            raw({"cmd": 8023, "activity": 1})
            newest = brews.get(brews.list()[0]["id"])
            assert newest["reason"] == reason, (pour_ended, newest["reason"])
        task.cancel()

    asyncio.run(run())


# ---- the history list's sparkline ----------------------------------------------------
def test_list_carries_a_thinned_water_curve_for_each_brew(tmp_path):
    from xbloom_hub.brews import SPARK_POINTS, BrewStore

    store = BrewStore(tmp_path)
    long_water = [[i * 0.5, i * 1.5] for i in range(300)]
    for brew_id, water in (("20261101-080000-aaaa", long_water), ("20261102-080000-bbbb", [[0.5, 1.0], [2.0, 4.0]]),
                           ("20261103-080000-cccc", [])):
        store.write({"id": brew_id, "started_at": "2026-11-01T08:00:00+08:00", "samples": {"water": water, "weight": []}})
    by_id = {row["id"]: row for row in store.list()}
    spark = by_id["20261101-080000-aaaa"]["spark"]
    assert SPARK_POINTS == 24 and len(spark) <= SPARK_POINTS
    assert spark[0] == long_water[0] and spark[-1] == long_water[-1]          # both ends kept
    assert all(point in long_water for point in spark) and spark == sorted(spark)
    assert by_id["20261101-080000-aaaa"]["water_ml"] == long_water[-1][1]       # the other fields are as before
    assert by_id["20261102-080000-bbbb"]["spark"] == [[0.5, 1.0], [2.0, 4.0]]   # short series: as they are
    assert by_id["20261103-080000-cccc"]["spark"] == []



# ---- a brew across a link drop (2026-11-14: one cup split into two records) ---- #
def test_the_link_is_not_released_with_a_recipe_loaded_or_a_brew_running(tmp_path):
    async def run():
        machine, brews, task = await ready(tmp_path)
        ticket = await armed(machine)
        with pytest.raises(NotReady):
            await machine.release()
        machine._listener._brewing.cancel() if getattr(machine._listener, "_brewing", None) else None
        await machine.start(ticket)
        machine._listener._brewing.cancel()
        machine._listener._on_raw({"cmd": 8023, "activity": 35})
        with pytest.raises(NotReady):
            await machine.release()
        assert machine.state == "brewing" and not machine._released
        task.cancel()

    asyncio.run(run())


def test_a_brew_goes_on_in_the_same_record_when_the_link_comes_back(tmp_path, monkeypatch):
    async def run():
        machine, brews, task = await ready(tmp_path)
        await machine.start(await armed(machine))
        machine._listener._brewing.cancel()
        raw = machine._listener._on_raw
        raw({"cmd": 8023, "activity": 35})
        raw({"cmd": 40510, "pour_index": 0})
        raw({"cmd": 40523, "water_ml": 40000.0})
        first = machine.snapshot()["brew"]["id"]
        monkeypatch.setattr(type(machine._listener), "start_activity", 35)   # the machine as the link comes back
        old = machine._listener
        drop_link(machine)
        raw = await reconnected(machine, old)
        raw({"cmd": 8023, "activity": 35})                      # still brewing
        assert machine.state == "brewing" and machine.snapshot()["brew"]["id"] == first
        raw({"cmd": 40523, "water_ml": 120000.0})
        raw({"cmd": 40512})
        assert len(brews.list()) == 1
        record = brews.get(first)
        assert record["reason"] == "finished" and record["recipe"] is not None
        (gap_from, gap_to), = record["gaps"]
        assert 0 <= gap_from < gap_to
        assert [p[1] for p in record["samples"]["water"]] == [40.0, 120.0]
        task.cancel()

    asyncio.run(run())


def test_a_brew_that_finished_during_the_gap_is_finished(tmp_path, monkeypatch):
    async def run():
        machine, brews, task = await ready(tmp_path)
        await machine.start(await armed(machine))
        machine._listener._brewing.cancel()
        machine._listener._on_raw({"cmd": 8023, "activity": 35})
        first = machine.snapshot()["brew"]["id"]
        monkeypatch.setattr(type(machine._listener), "start_activity", 36)   # the machine as the link comes back
        old = machine._listener
        drop_link(machine)
        raw = await reconnected(machine, old)
        raw({"cmd": 8023, "activity": 36})                      # the screen after a brew
        record = brews.get(first)
        assert len(brews.list()) == 1 and record["reason"] == "finished" and record["gaps"]
        task.cancel()

    asyncio.run(run())


def test_a_link_back_too_late_starts_a_new_record(tmp_path, monkeypatch):
    async def run():
        machine, brews, task = await ready(tmp_path)
        await machine.start(await armed(machine))
        machine._listener._brewing.cancel()
        machine._listener._on_raw({"cmd": 8023, "activity": 35})
        first = machine.snapshot()["brew"]["id"]
        monkeypatch.setattr(machine_module, "RESUME_WINDOW_S", 0)
        monkeypatch.setattr(type(machine._listener), "start_activity", 35)   # the machine as the link comes back
        old = machine._listener
        drop_link(machine)
        raw = await reconnected(machine, old)
        raw({"cmd": 8023, "activity": 35})
        assert machine.snapshot()["brew"]["id"] != first
        assert brews.get(first)["reason"] == "link_lost"
        task.cancel()

    asyncio.run(run())


def test_a_passing_value_on_reconnecting_does_not_drop_the_held_brew(tmp_path, monkeypatch):
    # 29 is seen as the link comes up (2026-11-14), before the machine's real state.
    async def run():
        machine, brews, task = await ready(tmp_path)
        await machine.start(await armed(machine))
        machine._listener._brewing.cancel()
        machine._listener._on_raw({"cmd": 8023, "activity": 35})
        first = machine.snapshot()["brew"]["id"]
        monkeypatch.setattr(type(machine._listener), "start_activity", 29)
        old = machine._listener
        drop_link(machine)
        raw = await reconnected(machine, old)
        raw({"cmd": 8023, "activity": 35})
        assert machine.snapshot()["brew"]["id"] == first and len(brews.list()) == 1
        task.cancel()

    asyncio.run(run())


def test_another_cup_after_the_gap_gets_its_own_record(tmp_path, monkeypatch):
    async def run():
        machine, brews, task = await ready(tmp_path)
        await machine.start(await armed(machine))
        machine._listener._brewing.cancel()
        raw = machine._listener._on_raw
        raw({"cmd": 8023, "activity": 35})
        raw({"cmd": 40510, "pour_index": 1})
        raw({"cmd": 40523, "water_ml": 150000.0})
        first = machine.snapshot()["brew"]["id"]
        last_water_t = brews.get(first) and machine._brew["samples"]["water"][-1][0]
        monkeypatch.setattr(type(machine._listener), "start_activity", 35)
        old = machine._listener
        drop_link(machine)
        raw = await reconnected(machine, old)
        assert machine.snapshot()["brew"]["id"] == first and machine.snapshot()["brew"]["gaps"] == 1
        raw({"cmd": 40523, "water_ml": 20000.0})               # far less water: another cup
        second = machine.snapshot()["brew"]["id"]
        assert second != first
        old_record = brews.get(first)
        assert old_record["reason"] == "link_lost" and not old_record.get("gaps")
        assert [p[1] for p in old_record["samples"]["water"]] == [150.0]
        assert machine._brew["samples"]["water"][-1][1] == 20.0
        task.cancel()

    asyncio.run(run())


def test_a_stop_sent_before_the_drop_is_stopped_when_found_finished(tmp_path, monkeypatch):
    async def run():
        machine, brews, task = await ready(tmp_path)
        await machine.start(await armed(machine))
        machine._listener._brewing.cancel()
        raw = machine._listener._on_raw
        raw({"cmd": 8023, "activity": 35})
        raw({"cmd": 40523, "water_ml": 30000.0})
        machine._brew["stop_sent"] = True
        first = machine.snapshot()["brew"]["id"]
        monkeypatch.setattr(type(machine._listener), "start_activity", 36)
        old = machine._listener
        drop_link(machine)
        await reconnected(machine, old)
        record = brews.get(first)
        assert record["reason"] == "stopped"
        # The gap starts at the last reading, not when the drop was noticed.
        assert record["gaps"][0][0] == record["samples"]["water"][-1][0]
        task.cancel()

    asyncio.run(run())
