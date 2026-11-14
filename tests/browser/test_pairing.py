"""Pairing a device by QR code (docs/PLAN.md, S3d), in a real browser.

The test hub is reached at 127.0.0.1, a loopback address, so the panel shows
its warning above the QR code; a phone at the hub's network address does not
see it (checked on the owner's phone).
"""
from __future__ import annotations

import pytest
from playwright.sync_api import expect

from .conftest import PROBE, TOKEN
from .test_dashboard import go, load_from_page

pytestmark = pytest.mark.browser


@pytest.fixture
def phone(browser):
    """A second browser that has never seen the token, watched like `ui`."""
    context = browser.new_context()
    page = context.new_page()
    problems: list[str] = []
    page.on("console", lambda m: problems.append(m.text) if m.text.startswith("[probe] ") else None)
    page.on("pageerror", lambda error: problems.append(f"pageerror {error}"))
    page.add_init_script(PROBE)
    page.add_init_script("try { localStorage.setItem('xbloom-hub-lang', 'en'); } catch (_) {}")
    yield page
    context.close()
    assert not problems, problems


def show_code(ui, hub) -> str:
    go(ui, hub, "#/console")
    with ui.expect_response(lambda r: r.url.endswith("/api/pair/start")) as answer:
        ui.locator("#pair-start").click()
    return answer.value.json()["code"]


def test_a_phone_pairs_by_the_code_and_can_then_load(ui, hub, phone):
    code = show_code(ui, hub)
    expect(ui.locator("svg.qr")).to_be_visible()
    expect(ui.locator("#pair-loopback")).to_be_visible()       # opened at 127.0.0.1

    phone.goto(f"{hub.base}/#p={code}")
    expect(phone.locator("#pair-claim")).to_have_text("This device is paired.")
    assert code not in phone.evaluate("location.href")
    assert phone.evaluate("localStorage.getItem('xbloom-hub-token')") == TOKEN
    load_from_page(phone, hub, "short")

    # The panel that showed the code sees the phone has paired.
    expect(ui.locator("#pair-paired")).to_be_visible(timeout=6000)
    expect(ui.locator("svg.qr")).to_have_count(0)


def test_an_unpaired_device_is_told_where_to_pair(hub, phone):
    phone.goto(f"{hub.base}/#/recipes/short")
    phone.get_by_role("button", name="Load onto machine").click()
    expect(phone.get_by_text("This device is not paired")).to_be_visible()
    assert hub.state()["state"] == "idle"


def test_a_failed_claim_keeps_the_token_this_device_has(ui, hub):
    ui.goto(f"{hub.base}/#p={'A' * 22}")
    expect(ui.locator("#pair-claim")).to_have_text(
        "The pairing code was used or has expired. Make a new QR code on a paired device.")
    assert "#p=" not in ui.evaluate("location.href")
    assert ui.evaluate("localStorage.getItem('xbloom-hub-token')") == TOKEN
    load_from_page(ui, hub, "short")


def test_leaving_the_console_voids_the_code_shown(ui, hub):
    import httpx
    show_code(ui, hub)
    status = lambda: httpx.post(f"{hub.base}/api/pair/status",
                                headers={"Authorization": f"Bearer {TOKEN}"}).json()["state"]
    assert status() == "waiting"
    go(ui, hub, "#/recipes")
    ui.wait_for_timeout(500)
    assert status() == "none"


def test_at_loopback_the_code_names_the_hubs_network_address(ui, hub):
    # As the owner's hub answers: an allowed host a phone can reach.
    def with_host(route):
        answer = route.fetch()
        route.fulfill(response=answer, json={**answer.json(), "hosts": ["192.0.2.10"]})

    ui.route("**/api/pair/start", with_host)
    show_code(ui, hub)
    expect(ui.locator("svg.qr")).to_be_visible()
    expect(ui.locator("#pair-host")).to_have_text("The phone will open 192.0.2.10.")
    expect(ui.locator("#pair-loopback")).to_have_count(0)


def test_an_unpaired_device_sees_why_it_cannot_add_one(hub, phone):
    phone.goto(f"{hub.base}/#/console")
    expect(phone.locator("#pair")).to_contain_text("This device is not paired, so it cannot add others.")
    expect(phone.locator("#pair-start")).to_have_count(0)
