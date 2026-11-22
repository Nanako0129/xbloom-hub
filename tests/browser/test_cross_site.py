"""A page on another origin, in a real browser, cannot make the hub act.

There is no token since the "No pairing" change (docs/PLAN.md, 2026-11-22):
the network list is the gate, and Guard keeps other sites out by a foreign
Origin and by the X-Hub-Request header. This serves a hostile page from a
second plain-http origin and lets the browser send what such a page can: an
<img>, a no-cors fetch, and a form post whose text/plain body is JSON.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from . import conftest
from .conftest import _free_port

HOSTILE_PAGE = """<!doctype html><meta charset="utf-8"><title>elsewhere</title>
<iframe name="sink" hidden></iframe>
<img src="{hub}/api/community/search?keyword=kenya">
<img src="{hub}/mcp">
<form id="f" method="post" action="{hub}/api/machine/load" enctype="text/plain" target="sink">
<input name='{{"slug":"short","x":"' value='"}}'></form>
<script>
fetch("{hub}/mcp", {{mode: "no-cors"}}).catch(() => {{}});
fetch("{hub}/api/machine/load", {{method: "POST", mode: "no-cors", body: JSON.stringify({{slug: "short"}})}}).catch(() => {{}});
document.getElementById("f").submit();
</script>
"""


@pytest.fixture
def elsewhere(hub):
    page_html = HOSTILE_PAGE.format(hub=hub.base).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page_html)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", _free_port()), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()
    thread.join(5)


def test_a_page_on_another_origin_cannot_make_the_hub_act(page, hub, elsewhere):
    sent: set[str] = set()
    answers: dict[str, list[int]] = {}

    def key(request):
        return request.method + " " + request.url[len(hub.base):]

    def on_request(request):
        if request.url.startswith(hub.base):
            sent.add(key(request))

    def on_response(response):
        if response.url.startswith(hub.base):
            answers.setdefault(key(response.request), []).append(response.status)

    page.on("request", on_request)
    page.on("response", on_response)
    calls_before = len(conftest.XBLOOM_CALLS)
    # Not "load": were GET /mcp let through, its stream would never end.
    page.goto(elsewhere, wait_until="commit")
    # The browser sends every one of them. Chromium does not hand an <img> a
    # JSON answer (it blocks the body), so not every answer is seen here; the
    # hub side is checked below instead.
    expected = {"GET /api/community/search?keyword=kenya", "GET /mcp", "POST /api/machine/load"}
    # GET /mcp has no hub-side counter, so its refusal must be seen here: the
    # no-cors fetch gets an answer the page cannot read but the browser reports.
    for _ in range(50):
        if expected <= sent and {"GET /mcp", "POST /api/machine/load"} <= answers.keys():
            break
        page.wait_for_timeout(100)
    page.wait_for_timeout(500)
    assert expected <= sent, sent
    assert answers.get("GET /mcp") and answers.get("POST /api/machine/load"), answers
    for request, statuses in answers.items():
        assert statuses and all(status == 403 for status in statuses), (request, statuses)
    # The community search never ran: the hub called xBloom not once.
    assert conftest.XBLOOM_CALLS[calls_before:] == []
    # Nothing was loaded: the machine is where it was.
    assert hub.state()["state"] == "idle"
    assert hub.state()["recipe"] is None
