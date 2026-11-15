"""S3e without hardware: the scale page, its three codes, and the scale state.

The simulator's answers to 8003, 8500 and 8014 are the library's notes, not a
measurement on the machine; these tests pin the hub's rules.
"""
import asyncio
import time

import pytest
from starlette.testclient import TestClient
from xbloom.ble import FFE1_UUID

from xbloom_hub import gate
from xbloom_hub.app import create_app
from xbloom_hub.brews import BrewStore
from xbloom_hub.machine import Machine, NotReady
from xbloom_hub.recipes import RecipeStore, as_sent, checked, to_machine
from xbloom_hub.sim import SimListener, SimRadio, _SimClient

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
RECIPE = checked({
    "name": "Test", "cup_type": 2, "dose_g": 5, "ratio": "1:5",
    "grind": False, "grind_size": 40, "grinder_speed_rpm": 60,
    "pours": [{"volume_ml": 25, "temperature_c": 20, "pause_s": 0, "pattern": 1, "flow_rate": 3.0}],
})
ITEM = {"wire": to_machine(RECIPE), "recipe": RECIPE, "as_sent": as_sent(RECIPE)}
SCALE_CODES = {8003, 8500, 8014}


def frame(code: int) -> bytes:
    return bytes([0x58, 0x01, 0x01, code & 0xFF, code >> 8, 0, 0, 0, 0])


async def ready(tmp_path=None, listener=SimListener):
    machine = Machine(SimRadio(), listener, BrewStore(tmp_path / "brews") if tmp_path else None)
    task = asyncio.create_task(machine.run())
    assert await machine._wait_for(lambda: machine.state == "idle", 2)
    return machine, task


def sent(machine) -> list[int]:
    return machine._listener._client.gate_log


def activity(machine, value: int) -> None:
    machine._listener._on_raw({"cmd": 8023, "activity": value})


async def refused(machine, method: str) -> None:
    """The method answers NotReady and nothing reaches the gate."""
    before = list(sent(machine))
    with pytest.raises(NotReady):
        await getattr(machine, method)()
    assert sent(machine) == before, method


# ---- the gate ------------------------------------------------------------- #
def test_the_gate_allows_exactly_these_codes():
    assert gate.ALLOWED == {8100, 8102, 8104, 8001, 8004, 8017, 40519, 8003, 8500, 8014}
    for code in SCALE_CODES:
        assert gate.check(FFE1_UUID, frame(code)) == code
    for code in (8002, 8005, 8006, 8007, 8012, 8016, 8021, 8022, 4508, 4510, 3505, 8010,
                 11511, 40524):
        with pytest.raises(gate.GateRefused):
            gate.check(FFE1_UUID, frame(code))
    with pytest.raises(gate.GateRefused):
        gate.check(FFE1_UUID, b"\x00\x01\x02")
    client = gate.make_gated(_SimClient)()

    async def run():
        with pytest.raises(gate.GateRefused):             # no permit
            await client.write_gatt_char(FFE1_UUID, frame(8002))

    asyncio.run(run())
    assert client.sent == []


# ---- preconditions ------------------------------------------------------ #
def test_each_command_refuses_outside_its_states_and_sends_nothing(tmp_path):
    async def run():
        machine, task = await ready(tmp_path)
        # idle: no Leave
        await refused(machine, "scale_quit")
        # asleep: none of the three
        activity(machine, 10)
        assert machine.state == "asleep"
        for method in ("scale_enter", "tare", "scale_quit"):
            await refused(machine, method)
        activity(machine, 1)
        # armed: none of the three
        await machine.load("test", lambda: ITEM)
        assert machine.state == "armed"
        for method in ("scale_enter", "tare", "scale_quit"):
            await refused(machine, method)
        # brewing: none of the three
        machine._listener.knob_start()
        assert await machine._wait_for(lambda: machine.state == "brewing", 2)
        for method in ("scale_enter", "tare", "scale_quit"):
            await refused(machine, method)
        assert not SCALE_CODES & set(sent(machine))
        task.cancel()

    asyncio.run(run())


def test_busy_and_finished_take_no_scale_command(tmp_path):
    async def run():
        machine, task = await ready(tmp_path)
        for value, state in ((3, "busy"), (36, "finished")):
            activity(machine, value)
            assert machine.state == state
            for method in ("scale_enter", "tare", "scale_quit"):
                await refused(machine, method)
        task.cancel()

    asyncio.run(run())


def test_zero_and_leave_refuse_with_a_load_held_or_a_start_just_sent(tmp_path):
    async def run():
        machine, task = await ready(tmp_path)
        await machine.scale_enter()
        assert machine.state == "scale"
        machine._held = {"slug": "test", "recipe": None, "as_sent": None}
        for method in ("tare", "scale_quit"):
            await refused(machine, method)
        machine._held = None
        machine._started_at = time.monotonic()
        for method in ("tare", "scale_quit"):
            await refused(machine, method)
        machine._started_at = None
        await machine.scale_quit()
        assert machine.state == "idle"
        task.cancel()

    asyncio.run(run())


# ---- the scale state ------------------------------------------------------ #
def test_the_tare_key_at_home_is_not_the_scale_page(tmp_path):
    async def run():
        machine, task = await ready(tmp_path)
        for value in (4, 5):
            activity(machine, value)
            assert machine.state != "scale"
        await refused(machine, "scale_quit")
        assert 8014 not in sent(machine)
        task.cancel()

    asyncio.run(run())


def test_a_tare_key_during_a_brew_keeps_its_record_open(tmp_path):
    async def run():
        machine, task = await ready(tmp_path)
        machine._listener._on_raw({"cmd": 40510, "pour_index": 0})
        assert machine.state == "brewing"
        brew_id = machine._brew["id"]
        for value in (4, 5):
            activity(machine, value)
            assert machine._brew is not None and machine._brew["id"] == brew_id
            assert machine.state == "brewing"
        task.cancel()

    asyncio.run(run())


def test_the_flag_survives_only_scale_page_values(tmp_path):
    async def run():
        machine, task = await ready(tmp_path)
        for value in (3, 30):
            await machine.scale_enter()
            assert machine.state == "scale"
            activity(machine, value)
            assert machine.state != "scale"
            await refused(machine, "scale_quit")
            activity(machine, 1)                  # back home: the flag is gone, not waiting
            assert machine.state == "idle"
            await refused(machine, "scale_quit")
        for value in (31, 35, 10):
            await machine.scale_enter()
            activity(machine, value)
            assert machine._on_scale is False, value
            activity(machine, 1)                  # 35 opened a brew; home closes it
            assert machine.state == "idle", value
        # Values on the scale page keep it (measured: 4, then 5); home clears it.
        await machine.scale_enter()
        for value in (5, 4, 5):
            activity(machine, value)
            assert machine.state == "scale", value
        activity(machine, 1)
        assert machine.state == "idle" and machine._on_scale is False
        task.cancel()

    asyncio.run(run())


def test_the_flag_clears_on_a_link_drop_and_on_release(tmp_path):
    async def run():
        machine, task = await ready(tmp_path)
        await machine.scale_enter()
        old = machine._listener
        machine._lifecycle.put_nowait(("failed", {"reason": "link_lost_in_test"}))
        assert await machine._wait_for(
            lambda: machine._listener is not None and machine._listener is not old, 5)
        assert machine._on_scale is False and machine.state == "idle"

        await machine.scale_enter()
        await machine.release()
        assert machine._on_scale is False
        await machine.acquire()
        assert await machine._wait_for(lambda: machine.state == "idle", 5)
        task.cancel()

    asyncio.run(run())


def test_a_brew_opening_clears_the_flag(tmp_path):
    async def run():
        machine, task = await ready(tmp_path)
        await machine.scale_enter()
        machine._listener._on_raw({"cmd": 40510, "pour_index": 0})   # no activity change
        assert machine.state == "brewing"
        machine._listener._on_raw({"cmd": 40512})
        assert machine._brew is None
        activity(machine, 1)
        assert machine.state == "idle"
        task.cancel()

    asyncio.run(run())


def test_the_scale_state_needs_a_scale_page_value_whatever_the_flag(tmp_path):
    class Elsewhere(SimListener):
        async def send_confirmed(self, name, frame):
            answered = await super().send_confirmed(name, frame)
            if name == "scale_enter":                  # someone at the machine moved on
                self._on_raw({"cmd": 8023, "activity": 3})
            return answered

    async def run():
        machine, task = await ready(tmp_path, Elsewhere)
        await machine.scale_enter()
        assert machine.state != "scale"
        await refused(machine, "scale_quit")
        task.cancel()

    asyncio.run(run())


def test_a_link_dropped_inside_scale_enter_leaves_no_flag(tmp_path):
    class Dropping(SimListener):
        async def send_confirmed(self, name, frame):
            answered = await super().send_confirmed(name, frame)
            if name == "scale_enter":
                self._on_lifecycle("failed", {"reason": "connection_lost"})
                await asyncio.sleep(0.1)          # the session ends before the answer returns
            return answered

    async def run():
        machine, task = await ready(tmp_path, Dropping)
        old = machine._listener
        with pytest.raises(NotReady):
            await machine.scale_enter()
        assert machine._on_scale is False
        assert await machine._wait_for(
            lambda: machine._listener is not None and machine._listener is not old, 5)
        assert machine.state == "idle"            # not a stale scale on the new link
        task.cancel()

    asyncio.run(run())


def test_simulator_enter_zero_leave(tmp_path):
    async def run():
        machine, task = await ready(tmp_path)
        assert (await machine.scale_enter())["state"] == "scale"
        assert machine.snapshot()["weight_g"] == 132.4
        assert (await machine.tare())["weight_g"] == 0
        assert machine.state == "scale"
        assert (await machine.scale_quit())["state"] == "idle"
        assert [c for c in sent(machine) if c in SCALE_CODES] == [8003, 8500, 8014]
        await machine.load("test", lambda: ITEM)          # a load works again
        task.cancel()

    asyncio.run(run())


def test_zero_at_home_without_the_scale_page(tmp_path):
    async def run():
        machine, task = await ready(tmp_path)
        assert (await machine.tare())["state"] == "idle"
        assert sent(machine)[-1] == 8500
        task.cancel()

    asyncio.run(run())


# ---- http ----------------------------------------------------------------- #
@pytest.fixture
def client(tmp_path):
    machine = Machine(SimRadio(), SimListener)
    app = create_app(machine, RecipeStore(tmp_path), TOKEN, ["testserver"])
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        deadline = time.monotonic() + 3
        while c.get("/api/state").json()["state"] != "idle":
            assert time.monotonic() < deadline
            time.sleep(0.05)
        c.machine = machine
        yield c


def test_routes_need_the_token_and_post(client):
    before = list(sent(client.machine))
    for path in ("/api/scale/enter", "/api/scale/tare", "/api/scale/quit"):
        assert client.post(path).status_code == 401, path
        assert client.post(path, headers={"Authorization": "Bearer wrong"}).status_code == 401, path
        assert client.get(path).status_code == 405, path
        assert client.get(path, headers=AUTH).status_code == 405, path
    assert sent(client.machine) == before
    assert client.post("/api/scale/quit", headers=AUTH).json()["error"] == "not_ready"
    assert client.post("/api/scale/enter", headers=AUTH).json()["state"] == "scale"
    assert client.post("/api/scale/tare", headers=AUTH).json()["weight_g"] == 0
    assert client.post("/api/scale/quit", headers=AUTH).json()["state"] == "idle"


def test_machine_state_names_the_scale_state(client):
    tools = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                        headers={**AUTH, "Accept": "application/json, text/event-stream"}).json()
    described = {t["name"]: t["description"] for t in tools["result"]["tools"]}
    assert "scale (" in described["machine_state"]
    assert not {"scale_enter", "tare", "scale_quit"} & set(described)


def test_a_zero_at_home_reads_idle_through_its_settle(tmp_path):
    # Review of 55e765a: 8500 from idle makes the machine report 4, then 5.
    async def run():
        machine, task = await ready(tmp_path)
        raw = machine._listener._on_raw
        raw({"cmd": 8023, "activity": 4})
        assert machine.state == "idle"
        raw({"cmd": 8023, "activity": 5})
        assert machine.state == "idle"
        task.cancel()

    asyncio.run(run())


def test_going_to_the_scale_refuses_with_a_start_just_sent(tmp_path):
    async def run():
        machine, task = await ready(tmp_path)
        machine._started_at = 1.0                   # as just after a start
        with pytest.raises(NotReady):
            await machine.scale_enter()
        task.cancel()

    asyncio.run(run())



def test_leaving_the_scale_page_at_the_machine_clears_the_scale_state(tmp_path):
    # Measured 2026-11-15: the scale page reports 4 then 5; home is 1.
    async def run():
        machine, task = await ready(tmp_path)
        await machine.scale_enter()
        assert machine.state == "scale"
        machine._listener._on_raw({"cmd": 8023, "activity": 1})   # the knob, at the machine
        assert machine.state == "idle"
        with pytest.raises(NotReady):
            await machine.scale_quit()
        task.cancel()

    asyncio.run(run())
