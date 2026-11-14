"""Replay, brush zoom, event marks, history sparklines / filters / calendar,
dragging pours, and the progress of the pour being poured. Behaviour seen in a
real browser; the probe in conftest.py's `ui` fails any test that makes the
page report a CSP violation or a script error.
"""
from __future__ import annotations

import datetime
import json
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import RECIPES, _pour, _recipe, _record

BREW = "20261102-080000-bbbb"           # the fixture's: 12 s, water 12 ml at 4 s, pour start 0.6 s, pour end 8 s
# conftest.py's chart geometry, from Chart.svelte: the plot's left edge, right edge, and the flags' row.
GRID_LEFT, GRID_RIGHT, FLAG_Y, AXIS_S = 44, 16, 14, 30        # the time axis is 30 s for a brew this short


def clock(seconds: float) -> str:
    s = round(seconds)
    return f"{s // 60}:{s % 60:02d}"


def seed(hub, brew_id: str, started: str, recipe: str | None = None, rezeroed=None) -> None:
    record = _record(brew_id, started)
    if recipe:          # a recipe of its own: its name, and its slug
        record["recipe"] = {**record["recipe"], "name": recipe}
        record["slug"] = recipe.lower()
    if rezeroed:
        record["rezeroed"] = rezeroed
    (Path(hub.machine._brews._dir) / f"{brew_id}.json").write_text(json.dumps(record), encoding="utf-8")


def day_ago(days: int) -> tuple[str, str]:
    """Noon UTC that many days back: the same calendar day in any time zone a person lives in."""
    d = datetime.datetime.now(datetime.timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0) - datetime.timedelta(days=days)
    return d.strftime("%Y%m%d-%H%M%S"), d.isoformat(timespec="seconds")


def open_record(page, brew_id: str = BREW):
    page.evaluate(f"location.hash = '#/history/{brew_id}'")
    canvas = page.locator("#view .echart-canvas")
    expect(canvas.locator("canvas").first).to_be_visible()
    page.wait_for_timeout(1000)                         # the drawing animation
    return canvas


def plot_x(box, seconds: float) -> float:
    return box["x"] + GRID_LEFT + seconds / AXIS_S * (box["width"] - GRID_LEFT - GRID_RIGHT)


# ---- 1. replay ----------------------------------------------------------------
def test_replay_plays_and_scrubs_and_the_reading_follows(ui, hub):
    open_record(ui)
    scrub = ui.locator("#view input.scrub")
    expect(scrub).to_have_value("12")                   # at rest the whole brew is drawn
    play = ui.get_by_role("button", name="Replay")
    play.click()
    expect(ui.get_by_role("button", name="Pause")).to_be_visible()
    ui.wait_for_function("() => { const v = +document.querySelector('.scrub').value; return v > 0.4 && v < 11; }")
    ui.get_by_role("button", name="Pause").click()
    value = float(scrub.input_value())
    assert 0 < value < 12
    expect(ui.locator("#view .reading .time")).to_have_text(clock(value))
    # Dragged to any moment: the reading is the brew's at that moment (water 12 ml, coffee 8 g at 4 s).
    scrub.fill("4")
    expect(ui.locator("#view .reading .time")).to_have_text("0:04")
    text = ui.locator("#view .reading").inner_text()
    assert "12 ml" in text and "8 g" in text, text
    # Played on from there, it runs to the end at several times real speed, and rests on the whole brew.
    ui.get_by_role("button", name="Replay").click()
    expect(scrub).to_have_value("12", timeout=6000)
    expect(ui.get_by_role("button", name="Replay")).to_be_visible()
    # At the end the replay is over: the whole brew, and no moment read.
    expect(ui.locator("#view .reading .time")).to_have_count(0)


def test_reduced_motion_plays_nothing_but_the_scrubber_works(ui, hub):
    ui.emulate_media(reduced_motion="reduce")
    ui.reload()
    open_record(ui)
    expect(ui.locator("#view input.scrub")).to_be_visible()
    expect(ui.get_by_role("button", name="Replay")).to_have_count(0)
    ui.locator("#view input.scrub").fill("4")
    expect(ui.locator("#view .reading .time")).to_have_text("0:04")


# ---- 2. drag across the plot to zoom ---------------------------------------------
def test_dragging_across_the_plot_zooms_to_that_time_range(ui, hub):
    canvas = open_record(ui)
    box = canvas.bounding_box()
    y = box["y"] + box["height"] * 0.5
    assert ui.locator("#view .legend .zoomed").count() == 0
    ui.mouse.move(plot_x(box, 3), y)
    ui.mouse.down()
    ui.mouse.move(plot_x(box, 6), y, steps=6)
    ui.mouse.move(plot_x(box, 9), y, steps=6)
    ui.mouse.up()
    chip = ui.locator("#view .legend .zoomed")
    expect(chip).to_be_visible()
    low, high = (int(m) for m in re.findall(r"0:(\d\d)", chip.inner_text()))       # "Zoomed to 0:03–0:09, click to reset"
    assert 2 <= low <= 4 and 8 <= high <= 10, chip.inner_text()
    chip.click()
    expect(chip).to_have_count(0)
    # The double click is still the other way back.
    ui.mouse.move(plot_x(box, 12), y)
    ui.mouse.down()
    ui.mouse.move(plot_x(box, 20), y, steps=8)
    ui.mouse.up()
    expect(chip).to_be_visible()
    ui.mouse.dblclick(plot_x(box, 15), y)
    expect(chip).to_have_count(0)


# ---- 3. marks of events -----------------------------------------------------------
def test_marks_for_pour_start_end_and_rezero_and_a_click_sets_the_reading(ui, hub):
    seed(hub, "20261103-080000-cccc", "2026-11-03T08:00:00+08:00", rezeroed=[[5.0, 20.0]])
    canvas = open_record(ui, "20261103-080000-cccc")
    box = canvas.bounding_box()
    legend = ui.locator("#view .legend")
    for name in ("Pour start", "Pour end", "Scale re-zero"):
        expect(legend.get_by_text(name)).to_be_visible()
    reading = ui.locator("#view .reading")
    for seconds, title in ((8.0, "Pour 1 ends"), (0.6, "Pour 1 starts"), (5.0, "Scale re-zeroed (20 g carried over)")):
        ui.mouse.click(plot_x(box, seconds), box["y"] + FLAG_Y)
        expect(reading.locator(".mark-name")).to_have_text(title)
        expect(reading.locator(".time")).to_have_text(clock(seconds))
        ui.mouse.move(box["x"] - 40, box["y"] - 40)                 # off the chart: the clicked moment stays read
        expect(reading.locator(".mark-name")).to_have_text(title)
    # Another brew: the mark pinned on this one is not carried over.
    open_record(ui)
    expect(reading.locator(".mark-name")).to_have_count(0)
    expect(reading.locator(".time")).to_have_count(0)


def test_marks_are_numbered_by_the_machines_pour_index(ui, hub):
    # The 40510 of the first pour was missed: the one seen is pour 2's.
    seed(hub, "20261104-080000-dddd", "2026-11-04T08:00:00+08:00")
    path = Path(hub.machine._brews._dir) / "20261104-080000-dddd.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["events"] = [[0.6, {"cmd": 40510, "pour_index": 1}], [8.0, {"cmd": 40511}]]
    path.write_text(json.dumps(record), encoding="utf-8")
    canvas = open_record(ui, "20261104-080000-dddd")
    box = canvas.bounding_box()
    reading = ui.locator("#view .reading")
    for seconds, title in ((0.6, "Pour 2 starts"), (8.0, "Pour 2 ends")):
        ui.mouse.click(plot_x(box, seconds), box["y"] + FLAG_Y)
        expect(reading.locator(".mark-name")).to_have_text(title)


# ---- 4. history: sparklines, filters, the calendar ----------------------------------
def test_history_rows_have_sparklines_and_the_filters_narrow_the_list(ui, hub):
    (a_id, a_at), (c_id, c_at) = day_ago(3), day_ago(9)
    seed(hub, f"{a_id}-aaaa", a_at, "Alpha")
    seed(hub, f"{a_id[:-1]}1-bbbb", a_at, "Alpha")
    seed(hub, f"{c_id}-cccc", c_at, "Beta")
    ui.evaluate("location.hash = '#/history'")
    rows = ui.locator("#view .list li")
    expect(rows).to_have_count(5)
    expect(ui.locator("#view .list li svg.spark")).to_have_count(5)
    for d in ui.locator("#view .list li svg.spark path.line").evaluate_all("els => els.map(e => e.getAttribute('d'))"):
        assert d.startswith("M") and "L" in d, d              # a curve of the brew's own readings
    options = ui.locator("#view .filters select option").all_inner_texts()
    assert {"All recipes", "Alpha", "Beta"} <= set(options)
    ui.locator("#view .filters select").select_option("Alpha")
    expect(rows).to_have_count(2)
    expect(ui.locator("#view .list .title").first).to_have_text("Alpha")
    ui.get_by_role("button", name="Clear filters").click()
    expect(rows).to_have_count(5)
    day = c_at[:10]
    ui.locator("#view .filters input[type=date]").nth(0).fill(day)
    ui.locator("#view .filters input[type=date]").nth(1).fill(day)
    expect(rows).to_have_count(1)
    expect(ui.locator("#view .list .title")).to_have_text("Beta")
    ui.locator("#view .filters input[type=date]").nth(1).fill("")
    expect(rows).to_have_count(3)       # from Beta's day on: Beta, and the two Alpha brews (the fixture's two are older)
    ui.get_by_role("button", name="Clear filters").click()
    ui.locator("#view .filters input[type=date]").nth(0).fill("2999-01-01")
    expect(ui.get_by_text("No brews match these filters.")).to_be_visible()


def test_the_calendar_shows_brew_days_and_a_click_filters_to_one(ui, hub):
    (a_id, a_at), (c_id, c_at) = day_ago(3), day_ago(9)
    seed(hub, f"{a_id}-aaaa", a_at, "Alpha")
    seed(hub, f"{a_id[:-1]}1-bbbb", a_at, "Alpha")
    seed(hub, f"{c_id}-cccc", c_at, "Beta")
    ui.evaluate("location.hash = '#/history'")
    cal = ui.locator("#view svg.cal")
    expect(cal).to_be_visible()
    lit = cal.locator("circle[role=button]")
    assert lit.count() >= 2                                       # my two days (the two older fixture brews too, while they are within 26 weeks)
    assert cal.locator("circle.d").count() > 150                  # every day of 26 weeks is a dot
    twice = cal.locator("circle.l2")
    expect(twice).to_have_count(1)                                # the one day with two brews
    twice.hover()
    expect(ui.locator("#view .cal-read")).to_contain_text("2 brews")
    twice.click()
    rows = ui.locator("#view .list li")
    expect(rows).to_have_count(2)
    expect(ui.locator("#view .filters input[type=date]").nth(0)).to_have_value(a_at[:10])
    expect(ui.locator("#view .filters input[type=date]").nth(1)).to_have_value(a_at[:10])
    expect(cal.locator("circle.sel")).to_have_count(1)
    ui.locator("#view svg.cal circle.sel").click()                # the same day again: back to every brew
    expect(rows).to_have_count(5)
    # Keys reach it too.
    twice.focus()
    ui.keyboard.press("Enter")
    expect(rows).to_have_count(2)


@pytest.mark.parametrize("hash_", ["#/history", f"#/history/{BREW}", "#/recipes/new"])
def test_nothing_overflows_a_phone(ui, hub, hash_):
    ui.set_viewport_size({"width": 390, "height": 844})
    ui.evaluate(f"location.hash = '{hash_}'")
    ui.wait_for_timeout(800)
    assert ui.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), hash_


# ---- 6. drag a pour card to reorder ---------------------------------------------------
def test_dragging_a_pour_card_reorders_the_pours_and_the_preview_follows(ui, hub):
    ui.set_viewport_size({"width": 1300, "height": 2400})         # both cards on screen: a drag the page must scroll for is not what is tested
    ui.evaluate("location.hash = '#/recipes/new'")
    cards = ui.locator("#view .pour-edit")
    expect(cards).to_have_count(2)
    volumes = lambda: [int(v) for v in ui.locator("#view .pour-edit").evaluate_all(
        "els => els.map(e => e.querySelector('input.slider-number').value)")]
    preview = ui.locator("#view h2", has_text="Preview")
    assert volumes() == [45, 180] and "1:36" in preview.inner_text()
    cards.nth(0).locator(".pour-head").drag_to(cards.nth(1))
    expect(preview).to_contain_text("1:06")                        # the long pour first, and the pause after the short one
    assert volumes() == [180, 45]
    # The buttons stay for the keyboard.
    ui.get_by_role("button", name="Move later").first.click()
    assert volumes() == [45, 180]
    expect(preview).to_contain_text("1:36")
    # A slider is still a slider: pressing on one does not pick the card up.
    ui.locator("#view .pour-edit").first.locator(".slider-range").first.hover()
    ui.mouse.down()
    assert ui.evaluate("document.querySelector('.pour-edit').draggable") is False
    ui.mouse.up()


# ---- 7. the pour being poured, while brewing --------------------------------------------
def test_the_brewing_view_shows_each_pours_progress_and_start_against_the_plan(ui, hub):
    recipe = _recipe("Two pours", 5, "1:8", [_pour(20, 1), _pour(20)])
    assert hub.api("PUT", "/api/recipes/two?create=1", recipe).status_code == 200
    ui.evaluate("location.hash = '#/recipes/two'")
    ui.get_by_role("button", name="Load onto machine").click()
    hub.wait_state("armed")
    ui.get_by_role("button", name="Start brew").click()
    expect(ui.locator("#confirm")).to_be_visible()
    ui.locator("#confirm-yes").click()
    hub.wait_state("brewing")
    now = ui.locator("#view .pours li[data-now]")
    bar = now.locator("[role=progressbar]")
    expect(bar).to_be_visible()
    ui.wait_for_function("() => { const b = document.querySelector('.pours li[data-now] [role=progressbar]'); return b && +b.getAttribute('aria-valuenow') > 0; }")
    # The second pour: the first is marked done, the second's bar fills, and says when it began against the plan.
    ui.wait_for_function("() => document.querySelectorAll('.pours li')[1]?.dataset.now === 'true'", timeout=10000)
    expect(ui.locator("#view .pours li").nth(0).locator("[role=img]")).to_be_visible()          # the done mark
    expect(ui.locator("#view .pours li").nth(1).locator(".diff")).to_have_text(re.compile(r"^[+−]\d+ s$"))
    ui.wait_for_function("() => { const b = document.querySelector('.pours li[data-now] [role=progressbar]'); return b && +b.getAttribute('aria-valuenow') > 20; }")
    hub.wait_state("idle", timeout=20)


def test_a_page_that_missed_reports_fills_them_from_the_record(ui, hub, monkeypatch):
    # A phone that put the page in the background, then came back: the stream
    # reconnects, the brew is further on, and the page asks the hub for the record.
    from .conftest import Sim
    monkeypatch.setattr(Sim, "brew_seconds", 25.0)        # still brewing when the page is back
    recipe = _recipe("Long one", 5, "1:8", [_pour(40)])
    assert hub.api("PUT", "/api/recipes/long-one?create=1", recipe).status_code == 200
    ui.evaluate("location.hash = '#/recipes/long-one'")
    ui.get_by_role("button", name="Load onto machine").click()
    hub.wait_state("armed")
    ui.get_by_role("button", name="Start brew").click()
    expect(ui.locator("#confirm")).to_be_visible()
    ui.locator("#confirm-yes").click()
    brew_id = hub.wait_state("brewing")["brew"]["id"]
    asked = []
    ui.on("request", lambda r: asked.append(r.url) if r.url.endswith(f"/api/brews/{brew_id}") else None)
    # The hub says nothing for 4 s (the stream stays open): the page hears no
    # report, as one in the background does. Offline emulation does not cut an
    # open stream, so it cannot stand in for this.
    hub.machine._publish = lambda: None
    ui.wait_for_timeout(4000)
    del hub.machine._publish                              # its own method again
    for _ in range(40):
        if asked:
            break
        ui.wait_for_timeout(250)
    assert asked, "the page did not fetch the record after missing reports"
    assert hub.state()["state"] == "brewing"              # asked while brewing: not the finished view's fetch
    hub.api("POST", "/api/machine/stop")                  # not left brewing for 25 s
