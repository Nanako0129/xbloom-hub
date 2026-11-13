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
from xbloom_hub.recipes import BadSlug, RecipeInvalid, RecipeStore
from xbloom_hub.sim import SimListener, SimRadio, _SimClient

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
RECIPE = {
    "name": "Test", "cup_type": 2, "dose_g": 5.0, "ratio": "1:5",
    "grinder_size": 40, "grinder_size_enabled": 2, "rpm": 60, "pour_count": 1,
    "pours": [{"volume_ml": 25, "temperature_c": 20, "pause_s": 0, "pattern": 1,
               "flow_rate": 3.0, "agitate_before": 2, "agitate_after": 2}],
}


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
            await machine.load("x", RECIPE)
        task.cancel()

    asyncio.run(run())


def test_load_cancel_release_acquire_on_simulator():
    async def run():
        machine = Machine(SimRadio(), SimListener)
        task = asyncio.create_task(machine.run())
        assert await machine._wait_for(lambda: machine.state == "idle", 2)
        with pytest.raises(NotReady):
            await machine.cancel()                       # cancel only while armed

        loaded = await machine.load("test", RECIPE)
        assert loaded["state"] == "armed" and loaded["ticket"]
        assert loaded["preload_weight_g"] == 132.4      # read before the tare
        assert "ticket" not in machine.snapshot()
        assert machine._listener._client.gate_log == [8100, 8102, 8104, 8004]
        with pytest.raises(NotReady):
            await machine.load("test", RECIPE)           # no load on top of a load

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
            await machine.load("test", RECIPE)
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
    assert {r["slug"] for r in store.list()} == {"list", "broken"}
    for slug in ("list", "broken"):
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
    assert client.put("/api/recipes/..%2Fx", json=RECIPE, headers=AUTH).status_code in (400, 404)
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
