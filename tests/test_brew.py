"""S3 without hardware: the start permit, start and stop, and the brew record.

The simulator's brew is a guess at what the machine reports; these tests pin
the hub's rules, not the machine's behaviour.
"""
import asyncio
import json
import time

import pytest
from starlette.testclient import TestClient
from xbloom.ble import FFE1_UUID, CommandRefused, CommandUnanswered

from xbloom_hub import gate
from xbloom_hub.app import CSP, STATIC_DIR, check_allowed_hosts, create_app
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
        await machine.release()
        assert machine.state == "released"
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
    with TestClient(app) as c:
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
    assert len(names) == 8


def test_the_page_is_served_under_a_strict_policy(client):
    page = client.get("/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    for response in (page, client.get("/api/state"), client.get("/app.js"), client.get("/style.css")):
        assert response.headers["content-security-policy"] == CSP
        assert response.headers["x-content-type-options"] == "nosniff"
    assert "'unsafe-inline'" not in CSP and "'unsafe-eval'" not in CSP
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400


def test_the_page_has_nothing_inline_and_puts_no_data_in_innerhtml():
    import re

    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)      # only <script src=…>
    assert "<style" not in html and not re.search(r"\sstyle\s*=", html)
    assert not re.search(r"\son[a-z]+\s*=", html)                    # no onclick= and friends
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in script and "outerHTML" not in script
    assert "insertAdjacentHTML" not in script and "document.write" not in script
    assert "eval(" not in script and "new Function" not in script


def test_allowed_hosts_cannot_be_a_wildcard():
    for bad in ([], [""], ["*"], ["localhost", "*.example.com"], ["a", ""]):
        with pytest.raises(SystemExit):
            check_allowed_hosts(bad)
    assert check_allowed_hosts(["localhost", "hub.local"]) == ["localhost", "hub.local"]


# ---- interface text --------------------------------------------------------------
def _dictionaries():
    import re

    text = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    blocks = {}
    for code in ("zh-Hant", "en"):
        start = text.index(f'"{code}": {{') if code == "zh-Hant" else text.index("\n  en: {")
        end = text.index("\n  },", start)
        blocks[code] = dict(re.findall(r'"([a-z_.]+)":\s*"((?:[^"\\\\]|\\\\.)*)"', text[start:end]))
    return blocks


def test_every_language_has_the_same_keys_and_placeholders():
    import re

    blocks = _dictionaries()
    zh, en = blocks["zh-Hant"], blocks["en"]
    assert len(en) > 100 and set(zh) == set(en), set(zh) ^ set(en)
    for key in en:
        assert set(re.findall(r"\{(\w+)\}", zh[key])) == set(re.findall(r"\{(\w+)\}", en[key])), key
        assert zh[key].strip() and en[key].strip(), key


def test_the_page_uses_only_keys_that_exist_and_holds_no_text_of_its_own():
    import re

    keys = set(_dictionaries()["en"])
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    used = set(re.findall(r'\bt\("([a-z_.]+)"', script))
    used |= set(re.findall(r'data-i18n(?:-label)?="([a-z_.]+)"', html))
    assert len(used) > 80 and used <= keys, used - keys
    # Keys built at run time: every value the hub can send has an entry.
    for prefix, values in (("state", ("offline", "idle", "asleep", "armed", "brewing", "finished", "busy", "released")),
                           ("ending", ("finished", "stopped", "abandoned", "home", "asleep", "link_lost")),
                           ("error", ("unauthorized", "not_ready", "bad_ticket", "no_cup", "recipe_is_loaded",
                                      "recipe_invalid", "machine_refused", "machine_unanswered",
                                      "cancel_failed", "not_found", "foreign_origin")),
                           ("meta", ("bean", "roaster", "process", "roast", "source", "notes"))):
        for value in values:
            assert f"{prefix}.{value}" in keys, f"{prefix}.{value}"
    # All interface text lives in i18n.js: none in the script or the markup.
    han = re.compile(r"[\u4e00-\u9fff]")
    assert not han.search(script) and not han.search(html)
    # ... except the product's name, which is a name and is not translated.
    body = html.split("<body>")[1].replace(">xbloom hub<", "><")
    assert not re.search(r">\s*[A-Za-z][^<]*<", body)


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


def test_views_that_fetch_never_paint_directly():
    """A view that awaits can finish after the address has moved on. It paints
    through painter(), which checks; a bare show() there once drew a recipe
    page over the loaded screen."""
    import re

    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    views = re.findall(r"async function (view\w+)\(view, \w+\) \{\n(.*?)\n\}\n", script, flags=re.S)
    assert {name for name, _ in views} == {"viewRecipes", "viewRecipe", "viewHistory", "viewBrew", "viewCompare"}
    for name, body in views:
        assert "painter(view)" in body and "show(view" not in body, name


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
