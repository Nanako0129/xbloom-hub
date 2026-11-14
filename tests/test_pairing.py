"""Pairing a device by QR code (docs/PLAN.md, S3d): the hub's side."""
import asyncio
import json
import logging

import httpx
import pytest
from starlette.testclient import TestClient

from xbloom_hub import pairing as pairing_module
from xbloom_hub.app import create_app, parse_networks
from xbloom_hub.machine import Machine
from xbloom_hub.pairing import BadPairingCode, Pairing
from xbloom_hub.recipes import RecipeStore
from xbloom_hub.sim import SimListener, SimRadio

TOKEN = "pairing-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PHONE = "192.0.2.7"        # a documentation address, inside the networks allowed below


def make_app(tmp_path, machine=None):
    return create_app(machine or Machine(SimRadio(), SimListener), RecipeStore(tmp_path), TOKEN, ["testserver"],
                      allowed_networks=parse_networks("127.0.0.0/8,192.0.2.0/24"))


@pytest.fixture
def client(tmp_path):
    machine = Machine(SimRadio(), SimListener)
    with TestClient(make_app(tmp_path, machine), client=(PHONE, 50000)) as c:
        c.machine = machine
        yield c


def start(client) -> str:
    answer = client.post("/api/pair/start", headers=AUTH)
    assert answer.status_code == 200
    return answer.json()["code"]


def claim(client, code, **kwargs):
    return client.post("/api/pair/claim", json={"code": code}, **kwargs)


def test_start_cancel_and_status_need_the_token(client):
    for path in ("/api/pair/start", "/api/pair/cancel", "/api/pair/status"):
        assert client.post(path).status_code == 401
        assert client.post(path, headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_a_code_claims_once_and_gives_the_token(client):
    code = start(client)
    assert len(code) == 22
    assert client.post("/api/pair/status", headers=AUTH).json() == {"state": "waiting"}
    won = claim(client, code)
    assert won.status_code == 200 and won.json() == {"token": TOKEN}
    assert client.post("/api/pair/status", headers=AUTH).json() == {"state": "claimed"}
    assert claim(client, code).status_code == 403


def test_the_token_is_not_in_the_start_answer(client):
    answer = client.post("/api/pair/start", headers=AUTH)
    assert TOKEN not in answer.text
    assert set(answer.json()) == {"code", "expires_in_s"}


def test_every_bad_code_gets_the_same_answer(client, monkeypatch):
    used = start(client)
    assert claim(client, used).status_code == 200
    answers = [claim(client, used),                              # used
               claim(client, "A" * 22),                          # wrong
               claim(client, "short"),                           # malformed
               claim(client, 12345),                             # not a string
               client.post("/api/pair/claim", content=b"not json"),
               client.post("/api/pair/claim", json=["a list"])]
    monkeypatch.setattr(pairing_module, "LIFETIME_S", -1)
    answers.append(claim(client, start(client)))                 # expired
    for answer in answers:
        assert answer.status_code == 403
        assert answer.json() == answers[0].json()
        assert "token" not in answer.json()


def test_a_newer_start_or_a_cancel_voids_the_code(client):
    first = start(client)
    second = start(client)
    assert claim(client, first).status_code == 403
    assert client.post("/api/pair/cancel", headers=AUTH).json() == {"state": "none"}
    assert claim(client, second).status_code == 403


def test_two_claims_racing_for_one_code_give_one_token(tmp_path):
    app = make_app(tmp_path)

    async def run():
        transport = httpx.ASGITransport(app=app, client=(PHONE, 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            code = (await c.post("/api/pair/start", headers=AUTH)).json()["code"]
            answers = await asyncio.gather(*(c.post("/api/pair/claim", json={"code": code}) for _ in range(8)))
        return sorted(a.status_code for a in answers)

    assert asyncio.run(run()) == [200] + [403] * 7


def test_the_exemption_is_exactly_the_claim(client):
    code = start(client)
    # Near the claim, but not it: the token is needed.
    assert client.post("/api/pair/claim/", json={"code": code}).status_code == 401
    assert client.post("/api/pair/startx").status_code == 401
    # The claim keeps the Origin check of every write.
    assert claim(client, code, headers={"Origin": "http://evil.example"}).status_code == 403
    # Reads stay open, but a GET reaches no claim: no token, and the code is not spent.
    read = client.get("/api/pair/claim")
    assert not 200 <= read.status_code < 300 and TOKEN not in read.text
    assert claim(client, code, headers={"Origin": "http://testserver"}).status_code == 200


def test_an_oversized_claim_is_refused_before_it_is_read(client):
    big = json.dumps({"code": "A" * 22, "pad": "x" * 2000}).encode()
    assert len(big) > 1024
    stated = client.post("/api/pair/claim", content=big, headers={"Content-Type": "application/json"})
    assert stated.status_code == 413 and stated.json()["error"] == "body_too_large"

    def chunks():                       # no Content-Length: sent chunked
        for i in range(0, len(big), 256):
            yield big[i:i + 256]

    streamed = client.post("/api/pair/claim", content=chunks(), headers={"Content-Type": "application/json"})
    assert streamed.status_code == 413 and streamed.json()["error"] == "body_too_large"


def test_each_claim_logs_one_line_with_the_address_and_no_secret(client, caplog):
    code = start(client)
    caplog.set_level(logging.INFO, logger="xbloom_hub.app")
    for attempt in (code, code):        # won, then lost
        caplog.clear()
        claim(client, attempt)
        lines = [r.getMessage() for r in caplog.records if r.name == "xbloom_hub.app"]
        assert len(lines) == 1, lines
        assert PHONE in lines[0] and code not in lines[0] and TOKEN not in lines[0]


def test_the_code_is_in_no_read_event_or_log(client, caplog):
    caplog.set_level(logging.DEBUG)
    code = start(client)
    for path in ("/api/state", "/api/gate", "/api/recipes", "/api/brews", "/api/library", "/api/pair/claim"):
        assert code not in client.get(path).text
    # What the event stream sends is this snapshot (app.py, events).
    assert code not in json.dumps(client.machine.snapshot())
    assert code not in claim(client, "A" * 22).text
    claim(client, code)
    assert all(code not in record.getMessage() for record in caplog.records)


def test_the_code_is_not_kept_in_clear_and_expires():
    now = [100.0]
    p = Pairing(clock=lambda: now[0])
    code = p.start()
    assert code not in repr(vars(p)) and code.encode() not in b"".join(
        v for v in vars(p).values() if isinstance(v, bytes))
    assert p.status() == "waiting"
    now[0] += pairing_module.LIFETIME_S
    assert p.status() == "none"
    with pytest.raises(BadPairingCode):
        p.claim(code)
