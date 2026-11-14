"""The dashboard's flows, against the simulator, in a real browser.

The first group holds the safety behaviour itself (docs/PLAN.md, S3c, step 1).
Each of those is shown to fail when the guard it covers is removed from a copy
of the page; the record of that is in the pull request that added them.
"""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from .conftest import HOSTILE, RECIPES, TOKEN

pytestmark = pytest.mark.browser


def go(page, hub, hash_: str) -> None:
    page.evaluate("(h) => { location.hash = h; }", hash_)
    page.wait_for_function("(h) => location.hash === h", arg=hash_)


def load_from_page(page, hub, slug: str) -> None:
    go(page, hub, f"#/recipes/{slug}")
    page.get_by_role("button", name="Load onto machine").click()
    hub.wait_state("armed")
    expect(page.get_by_role("button", name="Start brew")).to_be_visible()


def confirm(page) -> None:
    expect(page.locator("#confirm")).to_be_visible()
    page.locator("#confirm-yes").click()


def requests_to(page, path: str) -> list:
    """POSTs to `path` from now on. Playwright's sync API delivers them only
    while one of its own calls runs, so read the list after one."""
    seen = []
    page.on("request", lambda r: seen.append(r) if r.method == "POST" and r.url.endswith(path) else None)
    return seen


# ---- the safety behaviour ---------------------------------------------------------

def test_one_request_at_a_time(ui, hub):
    """A second press while the first request is on its way sends nothing."""
    held = []
    ui.route("**/api/machine/load", lambda route: held.append(route))
    loads = requests_to(ui, "/api/machine/load")
    go(ui, hub, "#/recipes/short")
    button = ui.get_by_role("button", name="Load onto machine")
    button.click()
    ui.wait_for_timeout(300)
    button.click(force=True, no_wait_after=True)          # whatever state it is in
    ui.wait_for_timeout(500)
    assert len(loads) == 1
    for route in held:
        route.continue_()
    ui.unroute("**/api/machine/load")
    hub.wait_state("armed")

    held.clear()
    ui.route("**/api/machine/start", lambda route: held.append(route))
    starts = requests_to(ui, "/api/machine/start")
    ui.get_by_role("button", name="Start brew").click()
    confirm(ui)
    ui.wait_for_timeout(300)
    ui.get_by_role("button", name="Start brew").click(force=True, no_wait_after=True)
    if ui.locator("#confirm[open]").count():
        ui.locator("#confirm-yes").click()
    ui.wait_for_timeout(500)
    assert len(starts) == 1
    for route in held:
        route.continue_()
    hub.wait_state("brewing")


def test_stop_is_not_held_behind_a_start(ui, hub):
    """A start still waiting for its answer does not keep Stop from working."""
    load_from_page(ui, hub, "long")
    held = []
    ui.route("**/api/machine/start", lambda route: held.append(route))
    ui.get_by_role("button", name="Start brew").click()
    confirm(ui)
    ui.wait_for_timeout(300)
    assert held, "the start request was not sent"
    hub.at_machine("knob_start")                           # the brew begins anyway
    hub.wait_state("brewing")
    stop = ui.locator("#stop")
    expect(stop).to_be_enabled()
    # The start's answer arriving mid-brew must not replace or disable Stop.
    handle = stop.element_handle()
    for route in held:
        route.continue_()
    held.clear()
    ui.wait_for_function("() => !('busy' in document.body.dataset)")
    ui.wait_for_timeout(500)
    assert handle.evaluate("(e) => e.isConnected && !e.disabled")
    stops = requests_to(ui, "/api/machine/stop")
    stop.click()
    confirm(ui)
    hub.wait_state("idle")
    ui.wait_for_timeout(200)            # the sync API hands page events over only while it runs
    assert len(stops) == 1
    for route in held:
        route.continue_()


def test_stop_is_the_same_node_through_a_brew(ui, hub):
    load_from_page(ui, hub, "long")
    ui.get_by_role("button", name="Start brew").click()
    confirm(ui)
    hub.wait_state("brewing")
    handle = ui.locator("#stop").element_handle()
    ui.wait_for_timeout(1500)                               # several snapshots
    assert handle.evaluate("(e) => e.isConnected && !e.disabled")
    handle.click()
    confirm(ui)
    hub.wait_state("idle")
    brews = hub.api("GET", "/api/brews").json()
    assert brews[0]["reason"] == "stopped"


def test_start_is_offered_only_for_this_pages_own_load(ui, hub):
    """The same recipe loaded again elsewhere is not this page's load."""
    load_from_page(ui, hub, "short")
    assert hub.api("POST", "/api/machine/cancel").status_code == 200
    hub.wait_state("idle")
    # The page has seen the machine leave the recipe screen.
    expect(ui.get_by_role("heading", name="Ready to brew")).to_be_visible()
    assert hub.api("POST", "/api/machine/load", {"slug": "short"}).status_code == 200
    hub.wait_state("armed")
    expect(ui.get_by_role("button", name="Load again")).to_be_visible()
    expect(ui.get_by_role("button", name="Start brew")).to_have_count(0)


def test_the_confirmation_shows_the_recipe_the_hub_holds(ui, hub):
    load_from_page(ui, hub, "short")
    info = hub.api("GET", "/api/recipes/short/plan").json()
    ui.get_by_role("button", name="Start brew").click()
    dialog = ui.locator("#confirm")
    expect(dialog).to_be_visible()
    expect(ui.locator("#confirm-title")).to_contain_text(info["recipe"]["name"])
    facts = dialog.locator("dd")
    values = [facts.nth(i).inner_text() for i in range(facts.count())]
    end = int(round(info["plan"]["pour_end_s"]))
    assert values[0] == f"{info['as_sent']['dose_g']} g"
    assert values[1] == f"{info['as_sent']['total_ml']} ml"
    assert values[2] == f"{len(info['recipe']['pours'])}, about {end // 60}:{end % 60:02d}"
    assert values[3] == "No grinding"
    ui.locator("#confirm-no").click()


# ---- the rest of the page ------------------------------------------------------------

def test_open_with_the_token_leaves_no_trace(ui, hub):
    assert "t=" not in ui.evaluate("location.hash")
    assert TOKEN not in ui.locator("body").inner_text()


def test_load_start_follow_and_finish(ui, hub):
    load_from_page(ui, hub, "short")
    ui.get_by_role("button", name="Start brew").click()
    confirm(ui)
    hub.wait_state("brewing")
    expect(ui.locator("#stop")).to_be_visible()
    expect(ui.locator("#live-readouts")).to_be_visible()
    expect(ui.get_by_role("heading", name="Brew finished")).to_be_visible(timeout=20000)
    hub.wait_state("idle", timeout=20)
    expect(ui.get_by_role("heading", name="Ready to brew")).to_be_visible()
    assert hub.api("GET", "/api/brews").json()[0]["reason"] == "finished"


def test_a_lifted_cup_keeps_the_ticket(ui, hub):
    load_from_page(ui, hub, "short")
    hub.at_machine("cup", False)
    ui.get_by_role("button", name="Start brew").click()
    confirm(ui)
    expect(ui.get_by_text("The machine reports the cup was taken off")).to_be_visible()
    assert hub.state()["state"] == "armed"
    hub.at_machine("cup", True)
    ui.get_by_role("button", name="Start brew").click()
    confirm(ui)
    hub.wait_state("brewing")


def test_a_load_made_elsewhere_cannot_be_started_here(ui, hub):
    assert hub.api("POST", "/api/machine/load", {"slug": "short"}).status_code == 200
    hub.wait_state("armed")
    expect(ui.get_by_role("button", name="Load again")).to_be_visible()
    expect(ui.get_by_role("button", name="Start brew")).to_have_count(0)


def test_cancel(ui, hub):
    load_from_page(ui, hub, "short")
    ui.get_by_role("button", name="Cancel load").click()
    hub.wait_state("idle")
    expect(ui.get_by_role("heading", name="Ready to brew")).to_be_visible()


def _no_markup(page) -> None:
    assert page.evaluate("window.__pwned") is None
    # Only what the hostile strings would have made: the page's own <b> and <i> are fine.
    assert page.locator("#view img, #view script, #view [onerror], #view [onmouseover]").count() == 0


def test_text_from_recipes_and_records_stays_text(ui, hub):
    for hash_ in ("#/recipes", "#/recipes/hostile", "#/history", "#/history/20261102-080000-bbbb",
                  "#/compare/20261101-080000-aaaa/20261102-080000-bbbb", "#/"):
        go(ui, hub, hash_)
        expect(ui.get_by_text(HOSTILE).first).to_be_visible()
        _no_markup(ui)
    go(ui, hub, "#/recipes/hostile")
    expect(ui.get_by_text(RECIPES["hostile"]["meta"]["roaster"])).to_be_visible()
    expect(ui.locator("#view a[href^='javascript']")).to_have_count(0)
    ui.get_by_text(RECIPES["hostile"]["meta"]["roaster"]).hover()
    _no_markup(ui)


def test_only_an_https_source_is_a_link(ui, hub):
    for slug in ("src-data", "src-upper"):
        go(ui, hub, f"#/recipes/{slug}")
        expect(ui.get_by_text(RECIPES[slug]["meta"]["source"])).to_be_visible()
        expect(ui.locator("#view dd a")).to_have_count(0)
    go(ui, hub, "#/recipes/src-ok")
    link = ui.locator("#view a[href='https://example.com/ok']")
    expect(link).to_have_count(1)
    assert set(link.get_attribute("rel").split()) >= {"noopener", "noreferrer"}


def test_the_editor_saves_refuses_a_taken_id_and_keeps_the_typing(ui, hub):
    go(ui, hub, "#/recipes")
    ui.get_by_role("link", name="New recipe").click()
    texts = ui.locator(".form input[type='text']")
    expect(texts.first).to_be_visible()
    texts.nth(0).fill("Editor test")
    texts.nth(1).fill("short")
    ui.get_by_role("button", name="Save").click()
    expect(ui.get_by_text("in_use")).to_be_visible()
    assert texts.nth(0).input_value() == "Editor test"
    assert hub.api("GET", "/api/recipes/short").json()["name"] == "Short test"
    texts.nth(1).fill("editor-test")
    ui.get_by_role("button", name="Save").click()
    ui.wait_for_function("() => location.hash === '#/recipes/editor-test'")
    assert hub.api("GET", "/api/recipes/editor-test").json()["name"] == "Editor test"


def test_history_a_record_and_compare(ui, hub):
    go(ui, hub, "#/history")
    boxes = ui.locator("#view input[type='checkbox']")
    expect(boxes).to_have_count(2)
    boxes.nth(0).check()
    boxes.nth(1).check()
    ui.get_by_role("button", name="Compare the two selected").click()
    ui.wait_for_function("() => location.hash.startsWith('#/compare/')")
    expect(ui.locator("#view .chart svg").first).to_be_visible()
    go(ui, hub, "#/history/20261102-080000-bbbb")
    chart = ui.locator("#view .chart svg").first
    expect(chart).to_be_visible()
    chart.hover()
    _no_markup(ui)


def test_the_console_lists_what_was_written(ui, hub):
    load_from_page(ui, hub, "short")
    go(ui, hub, "#/console")
    expect(ui.locator("#view").get_by_text(re.compile(r"\b8102\b")).first).to_be_visible()


def test_both_languages_have_every_word(ui, hub):
    for language, tab in (("zh-Hant", "配方"), ("en", "Recipes")):
        ui.evaluate("(l) => localStorage.setItem('xbloom-hub-lang', l)", language)
        ui.reload()
        ui.wait_for_selector("#machine .state")
        expect(ui.locator("#tabs")).to_contain_text(tab)
        for hash_ in ("#/recipes", "#/recipes/short", "#/history", "#/console", "#/"):
            # This route's view, not the last one's: clear the arrival marker,
            # change the route, and wait for arrive() to set it again.
            ui.evaluate("() => { delete document.getElementById('view').dataset.enter; }")
            go(ui, hub, hash_)
            ui.wait_for_function("() => 'enter' in document.getElementById('view').dataset")
            ui.wait_for_function("() => !document.getElementById('view').innerText.includes(t('loading'))")
            ui.wait_for_timeout(200)
            text = ui.locator("body").inner_text()
            for planted in (HOSTILE, *RECIPES["hostile"]["meta"].values()):
                text = text.replace(planted, "")
            # A key shown instead of its text, such as "home.ready".
            assert not re.search(r"\b[a-z]+\.[a-z_]+\b", text.replace("example.com", "")), (language, hash_)
