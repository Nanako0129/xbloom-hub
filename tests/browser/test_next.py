"""The rebuilt dashboard at /next/ (docs/PLAN.md, S3c, step 2).

Built by `frontend/npm.sh run build`; skipped when there is no build. The same
probe as the current page's tests: any CSP violation, script error or
unhandled rejection fails the test.
"""
from __future__ import annotations

import httpx
import pytest
from playwright.sync_api import expect

from xbloom_hub import app as hub_app

from .conftest import PROBE, TOKEN

pytestmark = pytest.mark.skipif(not (hub_app.WEB_DIR / "index.html").is_file(),
                                reason="the new dashboard is not built (frontend/npm.sh run build)")


@pytest.fixture
def next_ui(page, hub):
    problems: list[str] = []

    def on_console(message):
        text = message.text
        if text.startswith("[probe] ") or "Content Security Policy" in text or "Refused to" in text:
            problems.append(text)

    page.on("console", on_console)
    page.on("pageerror", lambda error: problems.append(f"pageerror {error}"))
    page.add_init_script(PROBE)
    page.add_init_script(
        "try { if (!localStorage.getItem('xbloom-hub-lang')) localStorage.setItem('xbloom-hub-lang', 'en'); } catch (_) {}")
    page.goto(f"{hub.base}/next/#t={TOKEN}")
    page.wait_for_selector("#machine .state")
    yield page
    assert not problems, problems


def test_the_token_leaves_the_address_and_the_page(next_ui, hub):
    assert next_ui.evaluate("location.hash") == "#/"
    assert next_ui.evaluate("location.pathname") == "/next/"
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
    # No control that changes the machine on /next/ until the safety flows move (C11).
    expect(next_ui.locator("#view button")).to_have_count(0)


def test_a_page_not_moved_yet_links_to_the_current_one(next_ui, hub):
    next_ui.evaluate("location.hash = '#/recipes/short'")
    link = next_ui.locator("#view a")
    expect(link).to_have_attribute("href", "/#/recipes/short")


def test_both_dashboards_are_served_with_the_policy(hub):
    old = httpx.get(hub.base + "/", timeout=5)
    assert "app.js" in old.text and "content-security-policy" in old.headers
    new = httpx.get(hub.base + "/next/", timeout=5)
    assert new.status_code == 200 and "content-security-policy" in new.headers
    import re
    assets = re.findall(r'(?:src|href)="\./(assets/[^"]+)"', new.text)
    assert assets, new.text
    for asset in assets:
        answer = httpx.get(f"{hub.base}/next/{asset}", timeout=5)
        assert answer.status_code == 200 and "content-security-policy" in answer.headers, asset


# ---- step 3: history, a record, compare, on ECharts ---------------------------

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
    compare = next_ui.locator("#view a.pill")
    expect(compare).to_have_attribute("aria-disabled", "true")
    rows.nth(0).locator("input").check()
    rows.nth(1).locator("input").check()
    expect(compare).to_have_attribute("aria-disabled", "false")
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
    next_ui.mouse.wheel(0, -400)
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
