"""The dashboard's views, beyond the flows in test_dashboard.py.

Built by `frontend/npm.sh run build`. The same probe as everywhere: any CSP
violation, script error or unhandled rejection fails the test.
"""
from __future__ import annotations

import httpx
import pytest
from playwright.sync_api import expect

from .conftest import TOKEN



@pytest.fixture
def next_ui(ui):
    """The dashboard, as every browser test opens it (conftest.py's `ui`)."""
    return ui

def test_the_token_leaves_the_address_and_the_page(next_ui, hub):
    assert next_ui.evaluate("location.hash") == "#/"
    assert next_ui.evaluate("location.pathname") == "/"
    assert TOKEN not in next_ui.locator("body").inner_text()
    assert next_ui.evaluate("localStorage.getItem('xbloom-hub-token')") == TOKEN


def test_a_token_link_opened_in_a_showing_tab_is_taken_too(next_ui, hub):
    next_ui.evaluate("localStorage.removeItem('xbloom-hub-token')")
    next_ui.evaluate(f"location.hash = '#t={TOKEN}'")
    next_ui.wait_for_function("() => location.hash === '#/'")
    assert next_ui.evaluate("localStorage.getItem('xbloom-hub-token')") == TOKEN
    assert TOKEN not in next_ui.locator("body").inner_text()


def test_the_shell_follows_the_machine(next_ui, hub):
    expect(next_ui.locator("#tabs a")).to_have_count(4)
    expect(next_ui.locator("#machine .state")).to_contain_text("Ready")
    assert next_ui.evaluate("document.body.dataset.state") == "idle"
    assert hub.api("POST", "/api/machine/load", {"slug": "short"}).status_code == 200
    expect(next_ui.locator("#machine .state")).to_contain_text("Loaded")
    expect(next_ui.locator("#machine .muted")).to_contain_text("Short test")


def test_the_console_reads_and_switches_language(next_ui, hub):
    next_ui.evaluate("location.hash = '#/console'")
    expect(next_ui.get_by_role("heading", level=1)).to_be_visible()
    expect(next_ui.locator("#tabs a[aria-current='page']")).to_have_attribute("data-tab", "console")
    assert hub.api("POST", "/api/machine/load", {"slug": "short"}).status_code == 200
    expect(next_ui.locator("#view pre.log").last).to_contain_text("8102")
    next_ui.locator("#language").select_option("zh-Hant")
    expect(next_ui.locator("#tabs")).to_contain_text("配方")
    assert next_ui.evaluate("document.documentElement.lang") == "zh-Hant"
    # Step 6: the link can be released from here, as on the current page.
    expect(next_ui.get_by_role("button", name="讓出連線").first).to_be_visible()      # in the language just chosen


def test_the_dashboard_and_every_asset_are_served_with_the_policy(hub):
    import re
    page = httpx.get(hub.base + "/", timeout=5)
    assert page.status_code == 200 and "content-security-policy" in page.headers
    assets = re.findall(r'(?:src|href)="\./(assets/[^"]+)"', page.text)
    assert assets, page.text
    for asset in assets:
        answer = httpx.get(f"{hub.base}/{asset}", timeout=5)
        assert answer.status_code == 200 and "content-security-policy" in answer.headers, asset
    assert httpx.get(hub.base + "/next/", timeout=5).status_code == 404       # the old address is gone

def _no_markup(page) -> None:
    assert page.evaluate("window.__pwned") is None
    assert page.locator("#view img, #view script, #view [onerror], #view [onmouseover]").count() == 0


def test_history_lists_brews_as_text_and_compares_two(next_ui, hub):
    from .conftest import HOSTILE
    next_ui.evaluate("location.hash = '#/history'")
    rows = next_ui.locator("#view .list li")
    expect(rows).to_have_count(2)
    expect(next_ui.get_by_text(HOSTILE).first).to_be_visible()
    _no_markup(next_ui)
    compare = next_ui.get_by_role("button", name="Compare the two selected")
    expect(compare).to_be_disabled()
    rows.nth(0).locator("input").check()
    rows.nth(1).locator("input").check()
    expect(compare).to_be_enabled()
    compare.click()
    next_ui.wait_for_function("() => location.hash.startsWith('#/compare/')")
    expect(next_ui.locator("#view .echart-canvas canvas").first).to_be_visible()
    expect(next_ui.locator("#view .legend .key")).to_have_count(4)
    _no_markup(next_ui)


def test_a_record_reads_under_the_pointer_zooms_and_hides_a_line(next_ui, hub):
    from .conftest import HOSTILE
    next_ui.evaluate("location.hash = '#/history/20261102-080000-bbbb'")
    canvas = next_ui.locator("#view .echart-canvas")
    expect(canvas.locator("canvas").first).to_be_visible()
    expect(next_ui.get_by_role("heading", level=1)).to_have_text(HOSTILE)
    next_ui.wait_for_timeout(1000)                          # the drawing animation
    box = canvas.bounding_box()
    next_ui.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.4)
    reading = next_ui.locator("#view .reading")
    expect(reading.locator(".time")).to_be_visible()
    text = reading.inner_text()
    assert "ml" in text and " g" in text, text                # water and coffee at that moment
    # Scroll to zoom, then a double click shows the whole brew again.
    next_ui.keyboard.down("Control")
    next_ui.mouse.wheel(0, -400)
    next_ui.keyboard.up("Control")
    next_ui.wait_for_timeout(300)
    next_ui.mouse.dblclick(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.4)
    # A legend key hides its line, and the reading drops it.
    coffee = next_ui.locator("#view .legend .key", has_text="Coffee")
    coffee.click()
    expect(coffee).to_have_attribute("aria-pressed", "false")
    next_ui.mouse.move(box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.4)
    expect(reading).not_to_contain_text(" g")
    _no_markup(next_ui)


def test_a_record_that_is_not_there_says_so(next_ui, hub):
    next_ui.evaluate("location.hash = '#/history/20991231-000000-zzzz'")
    expect(next_ui.locator("#view .notice.bad")).to_be_visible()


# ---- step 4: recipes, a recipe, the editor -------------------------------------

def test_the_recipe_list_and_a_recipe_show_strangers_text_as_text(next_ui, hub):
    from .conftest import HOSTILE, RECIPES
    next_ui.evaluate("location.hash = '#/recipes'")
    expect(next_ui.locator("#view .recipe")).to_have_count(len(RECIPES))
    expect(next_ui.get_by_text(HOSTILE).first).to_be_visible()
    _no_markup(next_ui)
    next_ui.evaluate("location.hash = '#/recipes/hostile'")
    expect(next_ui.get_by_text(RECIPES["hostile"]["meta"]["roaster"])).to_be_visible()
    expect(next_ui.locator("#view .echart-canvas canvas").first).to_be_visible()
    expect(next_ui.locator("#view a[href^='javascript']")).to_have_count(0)
    _no_markup(next_ui)
    expect(next_ui.get_by_role("button", name="Load onto machine")).to_be_enabled()
    expect(next_ui.get_by_role("button", name="Delete")).to_be_visible()


def test_only_an_https_source_is_a_link_on_next(next_ui, hub):
    from .conftest import RECIPES
    for slug in ("src-data", "src-upper"):
        next_ui.evaluate(f"location.hash = '#/recipes/{slug}'")
        expect(next_ui.get_by_text(RECIPES[slug]["meta"]["source"])).to_be_visible()
        expect(next_ui.locator("#view dd a")).to_have_count(0)
    next_ui.evaluate("location.hash = '#/recipes/src-ok'")
    link = next_ui.locator("#view dd a[href='https://example.com/ok']")
    expect(link).to_have_count(1)
    assert set(link.get_attribute("rel").split()) >= {"noopener", "noreferrer"}


def test_the_editor_keeps_typing_through_a_state_change_and_saves(next_ui, hub):
    next_ui.evaluate("location.hash = '#/recipes/new'")
    texts = next_ui.locator(".form input[type='text']")
    expect(texts.first).to_be_visible()
    texts.nth(0).fill("Next editor test")
    texts.nth(1).fill("short")
    # The machine changes state while someone types: nothing typed is lost.
    assert hub.api("POST", "/api/machine/load", {"slug": "long"}).status_code == 200
    expect(next_ui.locator("#machine .state")).to_contain_text("Loaded")
    assert texts.nth(0).input_value() == "Next editor test"
    next_ui.get_by_role("button", name="Save").click()
    expect(next_ui.get_by_text("A recipe already uses this id.")).to_be_visible()
    assert hub.api("GET", "/api/recipes/short").json()["name"] == "Short test"
    texts.nth(1).fill("next-editor-test")
    next_ui.get_by_role("button", name="Save").click()
    next_ui.wait_for_function("() => location.hash === '#/recipes/next-editor-test'")
    saved = hub.api("GET", "/api/recipes/next-editor-test").json()
    assert saved["name"] == "Next editor test" and len(saved["pours"]) == 2


def test_the_editor_previews_and_reorders_pours(next_ui, hub):
    next_ui.evaluate("location.hash = '#/recipes/new/short'")
    pours = next_ui.locator(".pour-edit")
    expect(pours).to_have_count(1)
    next_ui.get_by_role("button", name="Add a pour").click()
    expect(pours).to_have_count(2)
    heading = next_ui.locator("#view h2", has_text="Preview")
    before = heading.inner_text()
    pours.nth(1).locator("input[type='number']").first.fill("50")
    expect(heading).not_to_have_text(before)                      # the preview follows the typing
    expect(next_ui.locator("#edit-total")).to_contain_text("75 ml")
    pours.nth(1).get_by_role("button", name="Move earlier").click()
    assert pours.nth(0).locator("input[type='number']").first.input_value() == "50"
    assert pours.nth(1).locator("input[type='number']").first.input_value() == "25"


def test_editing_an_existing_recipe_loads_it(next_ui, hub):
    next_ui.evaluate("location.hash = '#/recipes/long/edit'")
    expect(next_ui.locator(".pour-edit")).to_have_count(1)
    assert next_ui.locator(".form input[type='text']").first.input_value() == "Long test"
    # An existing recipe keeps its id: no id field.
    expect(next_ui.locator(".form input[placeholder='my-recipe']")).to_have_count(0)



# ---- step 5: home at rest, armed, brewing, finished ----------------------------

def test_home_at_rest_shows_the_scale_recipes_and_the_last_brew(next_ui, hub):
    from .conftest import HOSTILE
    expect(next_ui.get_by_role("heading", name="Ready to brew")).to_be_visible()
    expect(next_ui.locator("#view .scale svg.dots")).to_be_visible()
    expect(next_ui.locator("#view .home-pick .recipe")).to_have_count(6)
    expect(next_ui.locator("#view .home-past").get_by_text(HOSTILE).first).to_be_visible()
    expect(next_ui.locator("#view .tile")).to_have_count(2)
    _no_markup(next_ui)


def test_armed_by_another_client_offers_load_again_not_start(next_ui, hub):
    assert hub.api("POST", "/api/machine/load", {"slug": "short"}).status_code == 200
    expect(next_ui.get_by_role("heading", name="Short test")).to_be_visible()
    expect(next_ui.locator("#view .readouts .readout")).to_have_count(3)
    expect(next_ui.get_by_role("button", name="Load again")).to_be_visible()
    expect(next_ui.get_by_role("button", name="Start brew")).to_have_count(0)


def test_a_brew_follows_live_then_shows_its_end(next_ui, hub):
    loaded = hub.api("POST", "/api/machine/load", {"slug": "long"}).json()
    assert hub.api("POST", "/api/machine/start", {"ticket": loaded["ticket"]}).status_code == 200
    expect(next_ui.get_by_role("heading", name="Long test")).to_be_visible()
    water = next_ui.locator("#view .readouts .readout").nth(1).locator("svg.dots")
    node = water.element_handle()
    first = water.get_attribute("aria-label")
    next_ui.wait_for_timeout(1500)
    # The same element, its value moved on: the dots turn, nothing is rebuilt.
    assert node.evaluate("(e) => e.isConnected") and water.get_attribute("aria-label") != first
    expect(next_ui.locator("#view .echart-canvas canvas").first).to_be_visible()
    expect(next_ui.locator("#view .pours li[data-now='true']")).to_have_count(1)
    expect(next_ui.locator("#stop")).to_be_enabled()
    expect(next_ui.get_by_role("heading", name="Brew finished")).to_be_visible(timeout=20000)
    expect(next_ui.locator("#view svg.dot-cup")).to_be_visible()
    expect(next_ui.locator("#view .readouts .readout")).to_have_count(3)
    hub.wait_state("idle", timeout=20)
    expect(next_ui.get_by_role("heading", name="Ready to brew")).to_be_visible()
    _no_markup(next_ui)



def test_a_machine_not_heard_from_is_not_called_busy(next_ui, hub):
    # As after a hub restart beside a sleeping machine: connected, nothing reported.
    def forget():
        hub.machine._activity = None
        hub.machine._publish()
    done = __import__("threading").Event()
    hub.loop.call_soon_threadsafe(lambda: (forget(), done.set()))
    assert done.wait(5)
    expect(next_ui.get_by_role("heading", name="Waiting for the machine")).to_be_visible()
    expect(next_ui.locator("#machine .state")).to_contain_text("Not heard from")


def test_the_sidebar_stays_in_view_on_a_long_page(next_ui, hub):
    """On a desktop the sidebar fills the window and stays put while the page
    scrolls (it once scrolled away: a later `inset: auto` reset its `top`)."""
    next_ui.set_viewport_size({"width": 1600, "height": 800})
    next_ui.evaluate("location.hash = '#/recipes/new'")
    expect(next_ui.locator(".pour-edit").first).to_be_visible()
    next_ui.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    next_ui.wait_for_timeout(200)
    assert next_ui.evaluate("scrollY") > 0, "the page is not long enough to test this"
    top, bottom = next_ui.evaluate("(() => { const r = document.getElementById('side').getBoundingClientRect(); return [r.top, r.bottom]; })()")
    assert abs(top) < 1 and abs(bottom - 800) < 1, (top, bottom)
