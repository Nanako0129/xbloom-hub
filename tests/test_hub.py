"""No hardware. The machine is the simulator, which writes through the real gate."""
import asyncio
import time

import pytest
import xbloom.ble as ble
from starlette.testclient import TestClient
from xbloom.ble import FFE1_UUID

from xbloom_hub import gate
from xbloom_hub.app import create_app, read_token
from xbloom_hub.machine import Machine, NotReady
from xbloom_hub.recipes import BadSlug, RecipeInvalid, RecipeStore, as_sent, checked, to_machine
from xbloom_hub.sim import SimListener, SimRadio, _SimClient

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
RECIPE = {      # the hub's one recipe shape; grinder off, 25 ml of unheated water
    "name": "Test", "cup_type": 2, "dose_g": 5.0, "ratio": "1:5",
    "grind": False, "grind_size": 40, "grinder_speed_rpm": 60,
    "pours": [{"volume_ml": 25, "temperature_c": 20, "pause_s": 0, "pattern": 1,
               "flow_rate": 3.0, "agitate_before": 2, "agitate_after": 2}],
}
WIRE = to_machine(checked(RECIPE))
ITEM = {"wire": WIRE, "recipe": checked(RECIPE), "as_sent": None}   # what Machine.load() reads
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def frame(code: int) -> bytes:
    return bytes([0x58, 0x01, 0x01, code & 0xFF, code >> 8, 0, 0, 0, 0])


# ---- gate ---------------------------------------------------------------- #
def test_gate_against_every_command_the_library_knows():
    codes = {v for k, v in vars(ble).items() if k.startswith("CMD_") and isinstance(v, int)}
    assert {8002, 4506, 40524, 8021, 3500, 11510} <= codes | {8021, 11510}
    client = gate.make_gated(_SimClient)()

    async def run():
        for code in sorted(codes | {8021, 11510}):
            try:
                await client.write_gatt_char(FFE1_UUID, frame(code))
                passed = True
            except gate.GateRefused:
                passed = False
            assert passed == (code in gate.ALLOWED), code
        for bad in (b"", b"\x00\x01\x02\x03\x04\x05", None, "8002"):
            with pytest.raises(gate.GateRefused):
                await client.write_gatt_char(FFE1_UUID, bad)
        with pytest.raises(gate.GateRefused):   # allowed code, wrong characteristic
            await client.write_gatt_char("0000ffe3-0000-1000-8000-00805f9b34fb", frame(8100))

    asyncio.run(run())
    assert set(client.sent) == gate.ALLOWED & codes     # nothing refused reached the radio
    for mover in (8002, 4506, 40524, 8021, 3500, 11510):
        assert mover not in gate.ALLOWED


def test_gate_install_is_idempotent_and_covers_both_lookups():
    import bleak
    import bleak_retry_connector

    before = (bleak.BleakClient, bleak_retry_connector.BleakClient)
    try:
        gate.install()
        first = bleak.BleakClient
        gate.install()
        assert bleak.BleakClient is first is bleak_retry_connector.BleakClient
        assert first.xbloom_gated and len([c for c in first.__mro__
                                           if getattr(c, "__qualname__", "").endswith("Gated")]) == 1
    finally:
        bleak.BleakClient, bleak_retry_connector.BleakClient = before


# ---- machine ------------------------------------------------------------- #
def test_ungated_session_is_never_usable():
    class Ungated(SimListener):
        client_class = _SimClient

    async def run():
        machine = Machine(SimRadio(), Ungated)
        task = asyncio.create_task(machine.run())
        await asyncio.sleep(0.3)
        assert machine.state == "offline"
        assert machine.snapshot()["error"] == "gate_missing"
        with pytest.raises(NotReady):
            await machine.load("x", lambda: ITEM)
        task.cancel()

    asyncio.run(run())


def test_load_cancel_release_acquire_on_simulator():
    async def run():
        machine = Machine(SimRadio(), SimListener)
        task = asyncio.create_task(machine.run())
        assert await machine._wait_for(lambda: machine.state == "idle", 2)
        with pytest.raises(NotReady):
            await machine.cancel()                       # cancel only while armed

        loaded = await machine.load("test", lambda: ITEM)
        assert loaded["state"] == "armed" and loaded["ticket"]
        assert loaded["preload_weight_g"] == 132.4      # read before the tare
        assert "ticket" not in machine.snapshot()
        assert machine._listener._client.gate_log == [8100, 8102, 8104, 8004]
        with pytest.raises(NotReady):
            await machine.load("test", lambda: ITEM)           # no load on top of a load

        assert (await machine.cancel())["state"] == "idle"
        assert machine._ticket is None and machine.snapshot()["recipe"] is None

        await machine.release()
        assert machine.state == "released"
        await asyncio.sleep(1.2)                         # must not re-take the link
        assert machine.state == "released"
        await machine.acquire()
        assert await machine._wait_for(lambda: machine.state == "idle", 3)
        task.cancel()

    asyncio.run(run())


def test_release_during_the_device_search_never_connects():
    class SlowRadio(SimRadio):
        async def find_device(self, timeout: float = 30.0):
            await asyncio.sleep(0.4)
            return object()

    started = []

    class Counting(SimListener):
        async def start(self):
            started.append(1)
            await super().start()

    async def run():
        machine = Machine(SlowRadio(), Counting)
        task = asyncio.create_task(machine.run())
        await asyncio.sleep(0.1)                 # still searching
        await machine.release()
        await asyncio.sleep(1.5)
        assert machine.state == "released" and started == [] and machine._listener is None
        await machine.acquire()
        assert await machine._wait_for(lambda: machine.state == "idle", 3)
        task.cancel()

    asyncio.run(run())


def test_link_drop_during_load_issues_no_ticket():
    class Dropping(SimListener):
        async def send_prepare(self, recipe):
            await super().send_prepare(recipe)
            self._on_lifecycle("failed", {"reason": "connection_lost"})
            await asyncio.sleep(0.1)             # let the session end first

    async def run():
        machine = Machine(SimRadio(), Dropping)
        task = asyncio.create_task(machine.run())
        assert await machine._wait_for(lambda: machine.state == "idle", 2)
        with pytest.raises(NotReady):
            await machine.load("test", lambda: ITEM)
        assert machine._ticket is None and machine._recipe is None
        task.cancel()

    asyncio.run(run())


def test_stale_notifications_from_a_closed_session_are_ignored():
    async def run():
        machine = Machine(SimRadio(), SimListener)
        task = asyncio.create_task(machine.run())
        assert await machine._wait_for(lambda: machine.state == "idle", 2)
        old = machine._listener
        await machine.release()
        old._on_raw({"cmd": 8023, "activity": 1})   # late frame from the old link
        assert machine.snapshot()["activity"] is None
        task.cancel()

    asyncio.run(run())


def test_file_that_is_not_a_recipe_is_refused_not_fatal(tmp_path):
    store = RecipeStore(tmp_path)
    (tmp_path / "list.yaml").write_text("- 1\n- 2\n")
    (tmp_path / "broken.yaml").write_text("a: [1\n")
    (tmp_path / "binary.yaml").write_bytes(b"\xff\xfe\x00name")
    assert {r["slug"] for r in store.list()} == {"list", "broken", "binary"}
    for slug in ("list", "broken", "binary"):
        with pytest.raises(RecipeInvalid):
            store.get(slug)


# ---- recipes ------------------------------------------------------------- #
def test_slug_cannot_leave_the_directory(tmp_path):
    store = RecipeStore(tmp_path / "recipes")
    for slug in ("../x", "..", "a/b", "/etc/passwd", "", "A", "x.yaml", "-x", "a" * 65, None):
        with pytest.raises(BadSlug):
            store.put(slug, RECIPE)
    store.put("ok-1", RECIPE)
    assert [p.name for p in (tmp_path / "recipes").iterdir()] == ["ok-1.yaml"]
    assert not list(tmp_path.glob("*.yaml"))


def test_invalid_recipe_is_refused(tmp_path):
    store = RecipeStore(tmp_path)
    with pytest.raises(RecipeInvalid) as err:
        store.put("hot", {**RECIPE, "pours": [{**RECIPE["pours"][0], "temperature_c": 150}]})
    assert "pours.0.temperature_c" in err.value.errors
    assert store.list() == []


# ---- http ---------------------------------------------------------------- #
def test_token_must_exist_and_be_non_empty(tmp_path):
    with pytest.raises(SystemExit):
        read_token({})
    with pytest.raises(SystemExit):
        read_token({"XBLOOM_HUB_TOKEN_FILE": str(tmp_path / "missing")})
    empty = tmp_path / "empty"
    empty.write_text(" \n")
    with pytest.raises(SystemExit):
        read_token({"XBLOOM_HUB_TOKEN_FILE": str(empty)})
    empty.write_text("abc\n")
    assert read_token({"XBLOOM_HUB_TOKEN_FILE": str(empty)}) == "abc"


@pytest.fixture
def client(tmp_path):
    machine = Machine(SimRadio(), SimListener)
    app = create_app(machine, RecipeStore(tmp_path), TOKEN, ["testserver"])
    with TestClient(app) as c:
        c.machine = machine
        deadline = time.monotonic() + 3
        while c.get("/api/state").json()["state"] != "idle":
            assert time.monotonic() < deadline
            time.sleep(0.05)
        yield c


def test_auth_host_and_origin(client):
    assert client.get("/api/state").status_code == 200           # reads need no token
    for headers in ({}, {"Authorization": "Bearer "}, {"Authorization": "Bearer wrong"},
                    {"Authorization": TOKEN}, {"Authorization": f"Basic {TOKEN}"}):
        assert client.post("/api/machine/cancel", headers=headers).status_code == 401
    assert client.post(f"/api/machine/cancel?token={TOKEN}").status_code == 401
    assert client.get("/api/state", headers={"Host": "evil.example"}).status_code == 400
    foreign = {"Origin": "http://evil.example", **AUTH}
    assert client.post("/api/machine/cancel", headers=foreign).status_code == 403
    assert client.get("/api/state", headers={"Origin": "null"}).status_code == 403
    own = {"Origin": "http://testserver", **AUTH}
    assert client.post("/api/machine/cancel", headers=own).status_code == 409   # reached the app


def test_recipe_and_load_flow_over_http(client):
    bad = {**RECIPE, "dose_g": 99}
    assert client.put("/api/recipes/bad", json=bad, headers=AUTH).status_code == 422
    assert client.put("/api/recipes/..%2Fx", json=RECIPE, headers=AUTH).status_code in (400, 404, 405)
    assert client.put("/api/recipes/Bad_Slug", json=RECIPE, headers=AUTH).status_code == 400
    assert client.put("/api/recipes/test", json=RECIPE, headers=AUTH).status_code == 200
    assert client.get("/api/recipes").json() == [{"slug": "test", "name": "Test"}]

    assert client.post("/api/machine/load", json={"slug": "nope"}, headers=AUTH).status_code == 404
    loaded = client.post("/api/machine/load", json={"slug": "test"}, headers=AUTH)
    assert loaded.status_code == 200 and loaded.json()["state"] == "armed"
    assert "ticket" in loaded.json() and "ticket" not in client.get("/api/state").json()
    assert client.post("/api/machine/load", json={"slug": "test"}, headers=AUTH).status_code == 409
    assert client.post("/api/machine/cancel", headers=AUTH).json()["state"] == "idle"
    assert client.delete("/api/recipes/test", headers=AUTH).json() == {"deleted": True}


# ---- one recipe shape (safety invariant 4) -------------------------------- #
def test_only_the_hub_shape_is_accepted():
    for key, value in (("grinder_size", 40), ("rpm", 250), ("grinder_size_enabled", 1),
                       ("pour_count", 1), ("bypass_volume_ml", 30), ("anything", "x")):
        with pytest.raises(RecipeInvalid) as err:
            checked({**RECIPE, key: value})
        assert err.value.errors == {key: "unknown_field"}
    with pytest.raises(RecipeInvalid) as err:
        checked({**RECIPE, "pours": [{**RECIPE["pours"][0], "rpm": 250}]})
    assert err.value.errors == {"pours.0.rpm": "unknown_field"}
    for bad in ({"grind": "yes"}, {"grind": 1},
                {"pours": [{**RECIPE["pours"][0], "agitate_before": 3}]},
                {"pours": [{**RECIPE["pours"][0], "agitate_after": True}]},
                {"grinder_speed_rpm": 95}, {"grind_size": 81}, {"ratio": 5}):
        with pytest.raises(RecipeInvalid):
            checked({**RECIPE, **bad})


def test_the_encoder_gets_exactly_the_validated_values():
    from xbloom.ble import build_brew_frames, frame_command_code

    on = checked({**RECIPE, "grind": True, "grind_size": 62, "grinder_speed_rpm": 90})
    wire = to_machine(on)
    assert (wire["grinder_size"], wire["rpm"], wire["grinder_size_enabled"]) == (62, 90, 1)
    assert set(wire) == {"name", "cup_type", "dose_g", "grinder_size",
                         "grinder_size_enabled", "rpm", "pours"}
    assert [frame_command_code(f) for f in build_brew_frames(wire)[1:4]] == [8102, 8104, 8001]
    off = to_machine(checked(RECIPE))
    assert off["grinder_size_enabled"] == 2
    assert [frame_command_code(f) for f in build_brew_frames(off)[1:4]] == [8102, 8104, 8004]


def test_recipe_text_is_cleaned_and_bounded(tmp_path):
    ok = checked({**RECIPE, "name": "  Guji\x00\x1b[31m natural\n ",
                  "meta": {"bean": "Guji\x07", "notes": "n" * 500}})
    assert ok["name"] == "Guji[31m natural" and ok["meta"]["bean"] == "Guji"
    assert "meta" not in to_machine(ok)
    for bad in ({"name": "n" * 81}, {"meta": {"notes": "n" * 501}}, {"meta": {"other": "x"}},
                {"meta": {"bean": 5}}, {"meta": "text"}):
        with pytest.raises(RecipeInvalid):
            checked({**RECIPE, **bad})
    (tmp_path / "hand.yaml").write_text("name: \"evil\\x1b[2J\"\n")
    assert RecipeStore(tmp_path).list() == [{"slug": "hand", "name": "evil[2J"}]


def test_old_shape_file_is_refused_on_load(client, tmp_path):
    (tmp_path / "old.yaml").write_text(
        "name: Old\ncup_type: 2\ndose_g: 5.0\nratio: '1:5'\ngrinder_size: 40\n"
        "grinder_size_enabled: 2\nrpm: 60\npour_count: 1\npours:\n- {volume_ml: 25, "
        "temperature_c: 20, pause_s: 0, pattern: 1, flow_rate: 3.0}\n")
    refused = client.post("/api/machine/load", json={"slug": "old"}, headers=AUTH)
    assert refused.status_code == 422 and "grinder_size" in refused.json()["fields"]
    assert client.get("/api/state").json()["state"] == "idle"


# ---- mcp ------------------------------------------------------------------ #
def rpc(client, method, params=None, headers=AUTH):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return client.post("/mcp", json=body, headers={**MCP_HEADERS, **headers})


def call(client, tool, **arguments):
    reply = rpc(client, "tools/call", {"name": tool, "arguments": arguments})
    assert reply.status_code == 200, reply.text
    result = reply.json()["result"]
    assert not result.get("isError"), result
    if "structuredContent" in result:
        return result["structuredContent"]
    import json
    return json.loads(result["content"][0]["text"])


def test_mcp_needs_the_token_on_every_method(client):
    assert rpc(client, "tools/list", headers={}).status_code == 401
    assert client.get("/mcp", headers=MCP_HEADERS).status_code == 401
    assert client.delete("/mcp").status_code == 401
    assert rpc(client, "tools/list", headers={**AUTH, "Host": "evil.example"}).status_code == 400
    assert rpc(client, "tools/list", headers={**AUTH, "Origin": "http://evil.example"}).status_code == 403
    assert rpc(client, "tools/list").status_code == 200


def test_mcp_offers_exactly_these_tools(client):
    tools = rpc(client, "tools/list").json()["result"]["tools"]
    assert {t["name"] for t in tools} == {
        "list_recipes", "get_recipe", "validate_recipe", "save_recipe", "delete_recipe",
        "machine_state", "load_recipe", "cancel_load"}


def test_mcp_design_save_load_cancel(client):
    hot = {**RECIPE, "name": "Guji", "grind": True, "grind_size": 62, "grinder_speed_rpm": 90,
           "meta": {"bean": "Guji natural"}}
    bad = call(client, "save_recipe", slug="guji", recipe={**hot, "grind_size": 120})
    assert (bad["ok"], bad["error"], bad["fields"]) == (
        False, "recipe_invalid", {"grind_size": "grind_out_of_range"})
    assert call(client, "validate_recipe", recipe={**hot, "rpm": 90})["fields"] == {"rpm": "unknown_field"}
    assert call(client, "list_recipes")["recipes"] == []            # nothing was stored
    assert call(client, "save_recipe", slug="Bad Slug", recipe=hot)["error"] == "bad_slug"

    assert call(client, "save_recipe", slug="guji", recipe=hot)["ok"]
    assert call(client, "get_recipe", slug="guji")["recipe"]["meta"] == {"bean": "Guji natural"}
    assert call(client, "cancel_load")["error"] == "not_ready"       # nothing loaded yet
    assert call(client, "load_recipe", slug="nope")["error"] == "not_found"

    loaded = call(client, "load_recipe", slug="guji")
    assert loaded["state"] == "armed" and loaded["recipe"]["slug"] == "guji"
    assert "ticket" not in str(loaded) and "ticket" not in str(call(client, "machine_state"))
    assert call(client, "load_recipe", slug="guji")["error"] == "not_ready"
    assert client.machine._listener._client.gate_log == [8100, 8102, 8104, 8001]   # grinder on
    assert call(client, "cancel_load")["state"] == "idle"
    assert client.machine._listener._client.gate_log[-1] == 8017
    assert call(client, "delete_recipe", slug="guji") == {"ok": True, "deleted": True}


# ---- what is validated is what is sent ------------------------------------ #
def test_fractions_the_encoder_would_change_are_refused():
    pour = RECIPE["pours"][0]
    for bad, field in (({"dose_g": 6.5}, "dose_g"), ({"grind_size": 40.9}, "grind_size"),
                       ({"cup_type": True}, "cup_type"),
                       ({"pours": [{**pour, "temperature_c": 92.9}]}, "pours.0.temperature_c"),
                       ({"pours": [{**pour, "volume_ml": 24.6}]}, "pours.0.volume_ml"),
                       ({"pours": [{**pour, "pattern": True}]}, "pours.0.pattern"),
                       ({"pours": [{**pour, "flow_rate": 3.25}]}, "pours.0.flow_rate"),
                       ({"name": None}, "name"), ({"name": ["x"]}, "name")):
        with pytest.raises(RecipeInvalid) as err:
            checked({**RECIPE, **bad})
        assert field in err.value.errors, (bad, err.value.errors)
    ok = checked({**RECIPE, "dose_g": 5.0, "cup_type": 2.0,
                  "pours": [{**pour, "volume_ml": 25.0, "flow_rate": 3}]})
    assert (ok["dose_g"], ok["cup_type"], ok["pours"][0]["volume_ml"]) == (5, 2, 25)
    assert all(type(v) is int for v in (ok["dose_g"], ok["cup_type"], ok["pours"][0]["volume_ml"]))
    assert checked({**RECIPE, "name": "a\u202eb\u2028c\x9fd"})["name"] == "ab cd"


def test_as_sent_stays_close_for_every_dose_and_ratio():
    from xbloom.ble import build_brew_frames
    from xbloom.spec import CUP_DOSE

    worst = 0.0
    for cup in (2, 3):
        for dose in range(int(CUP_DOSE[cup].min), int(CUP_DOSE[cup].max) + 1):
            for half in range(10, 51):                      # 1:5 .. 1:25 in 0.5 steps
                n = half / 2
                total = int(dose * n)                       # whole ml, within 0.5 of dose x n
                first = min(total // 2, 240)
                rest, pours = total - first, [first]
                while rest > 0:
                    pours.append(min(rest, 240))
                    rest -= pours[-1]
                recipe = checked({**RECIPE, "cup_type": cup, "dose_g": dose, "ratio": f"1:{n:g}",
                                  "pours": [{**RECIPE["pours"][0], "volume_ml": v} for v in pours]})
                sent = as_sent(recipe)
                assert sent["dose_g"] == dose
                # The encoder may move the ratio by a tenth or two to satisfy
                # the firmware; a clamped ratio byte would be far off.
                assert abs(float(sent["ratio"][2:]) - n) < 0.31, (cup, dose, n, sent)
                worst = max(worst, abs(sent["total_ml"] - total))
                build_brew_frames(to_machine(recipe))       # and it encodes
    assert worst <= 3, worst
    print("largest shift in total water:", worst, "ml")


def test_token_helper_fails_closed_on_whitespace(tmp_path):
    import subprocess

    token = tmp_path / "t"
    for content, ok in (("   \n", False), ("", False), ("abc\n", True)):
        token.write_text(content)
        run = subprocess.run(["tools/mcp-auth-headers.sh"], capture_output=True, text=True,
                             env={"XBLOOM_HUB_TOKEN_FILE": str(token), "PATH": "/usr/bin:/bin"})
        assert (run.returncode == 0) == ok, (content, run.stderr)
    assert run.stdout.strip() == '{"Authorization": "Bearer abc"}'


# ---- from the pre-push review -------------------------------------------- #
def test_ratio_is_never_filled_in_or_snapped():
    for ratio in (None, "", "1:16.2", "1:nan", "1:inf", "1:1e1", "16", 16, "1:4.5", "1:25.5"):
        recipe = {k: v for k, v in RECIPE.items() if k != "ratio"}
        if ratio is not None:
            recipe["ratio"] = ratio
        with pytest.raises(RecipeInvalid) as err:
            checked(recipe)
        assert "ratio" in err.value.errors, (ratio, err.value.errors)
    assert checked({**RECIPE, "ratio": "1:5.0"})["ratio"] == "1:5"


def test_absurd_numbers_are_refused_not_crashes():
    pour = RECIPE["pours"][0]
    for bad in ({"pours": [{**pour, "flow_rate": float("inf")}]},
                {"pours": [{**pour, "flow_rate": float("nan")}]},
                {"pours": [{**pour, "volume_ml": 10 ** 400}]},
                {"pours": [{**pour, "temperature_c": float("inf")}]},
                {"dose_g": 10 ** 400}, {"dose_g": float("nan")}):
        with pytest.raises(RecipeInvalid):
            checked({**RECIPE, **bad})


def test_text_keeps_its_word_breaks_and_joiners():
    ok = checked({**RECIPE, "name": "bloom\n45 ml",
                  "meta": {"notes": "first\r\nthen\ttwo  pours", "bean": "a\u200db"}})
    assert ok["name"] == "bloom 45 ml"
    assert ok["meta"] == {"notes": "first then two pours", "bean": "a\u200db"}


def test_a_loaded_recipe_cannot_be_changed_or_deleted(client):
    assert call(client, "save_recipe", slug="one", recipe=RECIPE)["ok"]
    assert call(client, "load_recipe", slug="one")["state"] == "armed"
    assert call(client, "save_recipe", slug="one", recipe={**RECIPE, "name": "x"})["error"] == "recipe_is_loaded"
    assert call(client, "delete_recipe", slug="one")["error"] == "recipe_is_loaded"
    assert client.put("/api/recipes/one", json=RECIPE, headers=AUTH).status_code == 409
    assert client.delete("/api/recipes/one", headers=AUTH).status_code == 409
    assert call(client, "save_recipe", slug="two", recipe=RECIPE)["ok"]      # other slugs are free
    assert call(client, "cancel_load")["state"] == "idle"
    assert call(client, "delete_recipe", slug="one")["deleted"]


def test_load_is_not_ok_unless_the_machine_says_armed(tmp_path, monkeypatch):
    import xbloom_hub.machine as machine_module

    class Silent(SimListener):                      # takes the recipe, never reports 31
        async def send_prepare(self, recipe):
            return None

    monkeypatch.setattr(machine_module.Machine, "_wait_for",
                        lambda self, predicate, timeout: asyncio.sleep(0, result=predicate()))
    machine = Machine(SimRadio(), Silent)
    app = create_app(machine, RecipeStore(tmp_path), TOKEN, ["testserver"])
    with TestClient(app) as c:
        deadline = time.monotonic() + 3
        while machine.state != "idle":
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert call(c, "save_recipe", slug="one", recipe=RECIPE)["ok"]
        loaded = call(c, "load_recipe", slug="one")
        assert loaded["ok"] is False and loaded["error"] == "not_ready"
        assert c.post("/api/machine/load", json={"slug": "one"}, headers=AUTH).status_code == 409
        # and the slug is not left locked by a load that never armed
        assert call(c, "save_recipe", slug="one", recipe=RECIPE)["ok"]


def test_same_failure_has_the_same_name_on_rest_and_mcp(client):
    rest = client.post("/api/machine/cancel", headers=AUTH).json()["error"]
    assert rest == call(client, "cancel_load")["error"] == "not_ready"


def test_a_save_cannot_slip_in_while_a_load_reads_the_recipe(tmp_path):
    from xbloom_hub import errors

    store = RecipeStore(tmp_path)
    store.put("r", RECIPE)
    seen = {}

    async def run():
        machine = Machine(SimRadio(), SimListener)
        task = asyncio.create_task(machine.run())
        assert await machine._wait_for(lambda: machine.state == "idle", 2)

        def read():
            # This is the moment the content is taken. A save to the same slug
            # must already be refused here, and another slug must not be.
            try:
                errors.guard_loaded(machine, "r")
            except errors.RecipeLoaded:
                seen["blocked"] = True
            errors.guard_loaded(machine, "other")
            return store.for_load("r")

        assert (await machine.load("r", read))["state"] == "armed"
        with pytest.raises(NotReady):                       # gone from the library
            await machine.cancel() and None
            await machine.load("r", lambda: None)
        assert machine.snapshot()["recipe"] is None
        task.cancel()

    asyncio.run(run())
    assert seen == {"blocked": True}


def test_name_cannot_be_only_invisible_characters():
    with pytest.raises(RecipeInvalid) as err:
        checked({**RECIPE, "name": "\u200d\u200c"})
    assert "name" in err.value.errors
