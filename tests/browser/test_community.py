"""Community recipes on the page (docs/PLAN.md, S3b), against the faked service."""
from __future__ import annotations

import pytest
from playwright.sync_api import expect

from .test_dashboard import go

pytestmark = pytest.mark.browser


def test_search_open_save_and_find_it_in_the_library(ui, hub):
    go(ui, hub, "#/recipes")
    ui.get_by_role("link", name="Community recipes").click()
    cards = ui.locator("a.community-card")
    expect(cards).to_have_count(2)
    # What strangers wrote is text, never markup.
    expect(cards.first.locator(".name")).to_have_text("Kenya <b>AA</b>")
    cards.first.click()
    expect(ui.locator("h1")).to_have_text("Kenya <b>AA</b>")
    expect(ui.locator(".prose")).to_contain_text("<script>window.__pwned=9</script>Bright.")
    assert ui.evaluate("window.__pwned") is None
    ui.locator("#community-slug").fill("kenya-aa")
    ui.get_by_role("button", name="Save to my recipes").click()
    ui.wait_for_function("(h) => location.hash === h", arg="#/recipes/kenya-aa")
    expect(ui.locator("h1")).to_have_text("Kenya <b>AA</b>")
    assert hub.state()["state"] == "idle"                 # saving never loads the machine
    go(ui, hub, "#/recipes")
    expect(ui.locator("a.recipe", has_text="Kenya <b>AA</b>")).to_be_visible()


def test_a_recipe_that_cannot_be_imported_says_why(ui, hub):
    go(ui, hub, "#/community/5151")
    expect(ui.locator("#not-importable")).to_contain_text("bypass water")
    expect(ui.get_by_role("button", name="Save to my recipes")).to_have_count(0)


def test_a_share_link_imports(ui, hub):
    go(ui, hub, "#/community")
    ui.locator("#share-link").fill("https://share-h5.xbloom.com/?id=AAAAAAAAAAAAAAAAAAAAAA%3D%3D")
    ui.locator("#share-slug").fill("from-link")
    ui.get_by_role("button", name="Import").click()
    ui.wait_for_function("(h) => location.hash === h", arg="#/recipes/from-link")


def test_a_link_to_another_host_is_refused(ui, hub):
    go(ui, hub, "#/community")
    ui.locator("#share-link").fill("https://evil.example/?id=AAAAAAAAAAAAAAAAAAAAAA%3D%3D")
    ui.locator("#share-slug").fill("evil")
    ui.get_by_role("button", name="Import").click()
    expect(ui.locator("#flash")).to_have_text("That is not an xBloom recipe share link.")


def test_next_stops_at_the_hubs_last_page(ui, hub):
    def at_page_50(route):
        answer = route.fetch()
        body = answer.json()
        route.fulfill(response=answer, json={**body, "page": 50, "total_pages": 80})
    ui.route("**/api/community/search?*", at_page_50)
    go(ui, hub, "#/community")
    expect(ui.locator("a.community-card")).to_have_count(2)
    expect(ui.get_by_role("button", name="Next")).to_be_disabled()


def test_coming_back_from_a_recipe_keeps_the_search(ui, hub):
    searches = []
    ui.on("request", lambda r: searches.append(r.url) if "/api/community/search" in r.url else None)
    go(ui, hub, "#/community")
    expect(ui.locator("a.community-card")).to_have_count(2)
    ui.locator("input[type=search]").fill("kenya")
    ui.get_by_role("button", name="Search").click()
    ui.wait_for_timeout(300)
    asked = len(searches)
    for back in ("link", "browser"):
        ui.locator("a.community-card").first.click()
        expect(ui.locator("#community-slug")).to_be_visible()
        if back == "link":
            ui.get_by_role("link", name="Back to community recipes").click()
        else:
            ui.go_back()
        expect(ui.locator("a.community-card")).to_have_count(2)
        expect(ui.locator("input[type=search]")).to_have_value("kenya")
    ui.wait_for_timeout(300)
    assert len(searches) == asked, searches        # nothing asked again of xBloom
