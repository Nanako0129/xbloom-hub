"""The Scale tab (docs/PLAN.md, S3e) against the simulator, in a real browser."""
from __future__ import annotations

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser


def controls(page):
    button = lambda name: page.get_by_role("button", name=name, exact=True)   # noqa: E731
    return button("Use the scale"), button("Zero"), button("Leave")


def test_the_scale_tab_shows_the_weight_and_its_controls_work(ui, hub):
    ui.locator("#tabs a[data-tab='scale']").click()
    reading = ui.locator("#scale-reading svg.dots")
    expect(reading).to_have_attribute("aria-label", "Weight g 132.4")
    enter, zero, leave = controls(ui)
    expect(enter).to_be_enabled()
    expect(zero).to_be_enabled()
    expect(leave).to_be_disabled()

    enter.click()
    hub.wait_state("scale")
    expect(leave).to_be_enabled()
    expect(enter).to_be_disabled()
    expect(ui.locator("#scale-note")).to_contain_text("scale page")

    zero.click()
    expect(reading).to_have_attribute("aria-label", "Weight g 0.0")
    assert hub.state()["state"] == "scale"

    leave.click()
    hub.wait_state("idle")
    expect(leave).to_be_disabled()
    expect(enter).to_be_enabled()


def test_the_scale_controls_wait_while_a_recipe_is_loaded_or_the_machine_sleeps(ui, hub):
    assert hub.api("POST", "/api/machine/load", {"slug": "short"}).status_code == 200
    hub.wait_state("armed")
    ui.evaluate("() => { location.hash = '#/scale'; }")
    enter, zero, leave = controls(ui)
    expect(ui.locator("#scale-note")).to_contain_text("recipe is loaded")
    for button in (enter, zero, leave):
        expect(button).to_be_disabled()

    assert hub.api("POST", "/api/machine/cancel").status_code == 200
    hub.wait_state("idle")
    expect(enter).to_be_enabled()

    hub.at_machine("_on_raw", {"cmd": 8023, "activity": 10})
    hub.wait_state("asleep")
    expect(ui.locator("#scale-note")).to_contain_text("wake it")
    for button in (enter, zero, leave):
        expect(button).to_be_disabled()
