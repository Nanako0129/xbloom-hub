"""Show that each safety test of the dashboard guards something.

For every guard below: copy the page, remove that guard from the copy, and run
the browser test that covers it against the copy. The test must fail. Then run
it against the page as it is; it must pass. Prints one line per guard and
exits non-zero if any guard is not held by its test.

    uv run python tools/browser_mutations.py [--browser webkit]

docs/PLAN.md, S3c, step 1 and finding C9. When the page is rebuilt, the edits
below are redone for the new code.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "xbloom_hub" / "static"
TESTS = "tests/browser/test_dashboard.py"

# name -> (test, [(text in app.js, replacement)])
GUARDS = {
    "one request at a time": ("test_one_request_at_a_time", [
        ("  if (busy) return;\n  workingText", "  workingText"),
        ('{ disabled: busy || !canLoad, on: { click: () => act(() => load(slug)',
         '{ disabled: !canLoad, on: { click: () => act(() => load(slug)'),
        ('{ disabled: busy || !info, on: { click: () => confirmStart(info) } }',
         '{ disabled: !info, on: { click: () => confirmStart(info) } }'),
    ]),
    "Stop not held behind a start (disabled)": ("test_stop_is_not_held_behind_a_start", [
        ('{ id: "stop", class: "danger", disabled: stopping,', '{ id: "stop", class: "danger", disabled: busy || stopping,'),
    ]),
    "Stop not held behind a start (ignores the press)": ("test_stop_is_not_held_behind_a_start", [
        ("  if (stopping) return;\n  if (!(await ask(", "  if (stopping || busy) return;\n  if (!(await ask("),
    ]),
    "a start's answer does not rebuild the brewing view": ("test_a_starts_answer_mid_brew_leaves_stop_alone", [
        ('brewing ? "" : busy, brewing ? "" : (flash ? flash.text : "")', 'busy, flash ? flash.text : ""'),
    ]),
    "Stop is one node through a brew": ("test_stop_is_the_same_node_through_a_brew", [
        # Rebuild the view on every snapshot, as a careless rewrite would.
        ('? [s.state, s.error, slug, s.brew && s.brew.id,', '? [s.state, s.error, slug, s.brew && s.brew.elapsed_s,'),
    ]),
    "Start only for this page's own load": ("test_start_is_offered_only_for_this_pages_own_load", [
        # Judge by recipe instead of by load: a reload of the same recipe elsewhere looks like ours.
        ('state.state === "armed" && state.load_id === ticket.loadId', 'state.state === "armed" && state.recipe && state.recipe.slug === ticket.slug'),
    ]),
    "the confirmation shows the hub's recipe": ("test_the_confirmation_shows_the_recipe_the_hub_holds", [
        ('const body = [h("p", {}, t("start.body")), recipeFacts(info), ', 'const body = [h("p", {}, t("start.body")), '),
    ]),
}


# The same guards in the rebuilt dashboard (frontend/src), each taken out of a
# copy of the sources, which is then built and served at /next/.
# name -> (test, [(file under frontend/src, text, replacement)])
NEXT_GUARDS = {
    "one request at a time": ("test_one_request_at_a_time", [
        ("lib/actions.svelte.ts", "  if (work.busy) return;\n", ""),
        ("pages/Recipe.svelte", "disabled={work.busy || !canLoad}", "disabled={!canLoad}"),
        ("pages/Home.svelte", "<button disabled={work.busy || !info}", "<button disabled={!info}"),
    ]),
    "Stop not held behind a start (disabled)": ("test_stop_is_not_held_behind_a_start", [
        ("pages/Home.svelte", 'id="stop" class="danger" disabled={work.stopping}', 'id="stop" class="danger" disabled={work.busy || work.stopping}'),
    ]),
    "Stop not held behind a start (ignores the press)": ("test_stop_is_not_held_behind_a_start", [
        ("pages/Home.svelte", "if (!work.stopping && await ask(", "if (!work.stopping && !work.busy && await ask("),
    ]),
    "a start's answer does not rebuild the brewing view": ("test_a_starts_answer_mid_brew_leaves_stop_alone", [
        ("pages/Home.svelte", '{:else if name === "brewing" && s.brew}\n', '{:else if name === "brewing" && s.brew}\n{#key work.busy}\n'),
        ("pages/Home.svelte", '{:else if name === "armed" && s.recipe}', '{/key}\n{:else if name === "armed" && s.recipe}'),
    ]),
    "Stop is one node through a brew": ("test_stop_is_the_same_node_through_a_brew", [
        ("pages/Home.svelte", '      <!-- Not held behind another request, and the same element for the whole brew. -->\n      <div class="actions">',
         '      {#key s.brew.elapsed_s}\n      <div class="actions">'),
        ("pages/Home.svelte", "          {t(\"action.stop\")}</button>\n      </div>", "          {t(\"action.stop\")}</button>\n      </div>\n      {/key}"),
    ]),
    "Start only for this page's own load": ("test_start_is_offered_only_for_this_pages_own_load", [
        ("lib/actions.svelte.ts", ' && s.load_id === held.loadId', ""),
    ]),
    "the confirmation shows the hub's recipe": ("test_the_confirmation_shows_the_recipe_the_hub_holds", [
        ("pages/Home.svelte", 'yes: t("action.start"), info })', 'yes: t("action.start") })'),
    ]),
}

# A fixed place for the mutant copies of the new page, so its node_modules
# volume (named after the path, frontend/npm.sh) is installed once.
MUTANT_ROOT = Path(tempfile.gettempdir()) / "xbloom-hub-mutant-next"


def run(test: str, static: Path | None, browser: str, page: str = "current", web: Path | None = None) -> bool:
    env = {**os.environ}
    if static is not None:
        env["XBLOOM_TEST_STATIC_DIR"] = str(static)
    if web is not None:
        env["XBLOOM_TEST_WEB_DIR"] = str(web)
    done = subprocess.run(
        [sys.executable, "-m", "pytest", f"{TESTS}::{test}", "-m", "browser", "--browser", browser,
         "-k", page, "-q", "-p", "no:cacheprovider", "-rs"], cwd=ROOT, env=env, capture_output=True, text=True)
    # A skipped test proved nothing: count it as not passing.
    return done.returncode == 0 and " skipped" not in done.stdout


def build_next_mutant(edits, install: bool = False) -> Path | None:
    """Copy frontend/ (without node_modules), apply the edits, build. Returns the
    build directory, or None if an edit does not apply or the build fails."""
    front = MUTANT_ROOT / "frontend"
    web = MUTANT_ROOT / "src" / "xbloom_hub" / "web"
    # Overwrite in place rather than delete and copy: a directory deleted and
    # made again under the same path was at times seen empty by the container
    # (OrbStack's file sharing), and the build then found no package.json.
    shutil.copytree(ROOT / "frontend", front, ignore=shutil.ignore_patterns("node_modules"), dirs_exist_ok=True)
    web.mkdir(parents=True, exist_ok=True)
    for file, old, new in edits:
        path = front / "src" / file
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            print(f"    not found in {file}: {old[:60]!r}")
            return None
        path.write_text(text.replace(old, new), encoding="utf-8")
    if install:
        done = subprocess.run([str(front / "npm.sh"), "ci"], capture_output=True, text=True)
        if done.returncode != 0:
            print(f"    npm ci failed: {(done.stdout + done.stderr).strip()[-400:]}")
            return None
    done = subprocess.run([str(front / "npm.sh"), "run", "build"], capture_output=True, text=True)
    if done.returncode != 0:
        print(f"    build failed: {(done.stdout + done.stderr).strip()[-400:]}")
        return None
    return web


def main_next(browser: str) -> bool:
    held = True
    # The baseline is built here from the sources as they are, the same way
    # as the mutants, and installed once: not a build left in web/ that may
    # be stale or missing (a missing build would skip, and a skip passes).
    clean = build_next_mutant([], install=True)
    if clean is None:
        print("BROKEN    next: the sources as they are did not build")
        return False
    baseline = {test: run(test, None, browser, "next", clean) for test, _ in NEXT_GUARDS.values()}
    for test, passed in baseline.items():
        if not passed:
            print(f"BROKEN    next: {test} fails on the page as it is")
            held = False
    for name, (test, edits) in NEXT_GUARDS.items():
        if not baseline[test]:
            continue
        web = build_next_mutant(edits)
        if web is None:
            print(f"CANNOT APPLY  next: {name}: the guard is not where this tool expects it, or the copy did not build")
            held = False
            continue
        without = run(test, None, browser, "next", web)
        ok = not without
        held &= ok
        print(f"{'HELD' if ok else 'NOT HELD':9} next: {name}: without the guard {'passed' if without else 'failed'}, with it passed")
    return held


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", default="chromium")
    parser.add_argument("--page", choices=["current", "next", "both"], default="both")
    arguments = parser.parse_args()
    browser = arguments.browser
    held = True
    if arguments.page in ("next", "both"):
        held &= main_next(browser)
    if arguments.page == "next":
        return 0 if held else 1
    # Each test once against the page as it is: it must pass there.
    baseline = {test: run(test, None, browser, "current") for test, _ in GUARDS.values()}
    for test, passed in baseline.items():
        if not passed:
            print(f"BROKEN    {test} fails on the page as it is: no guard can be judged by it")
            held = False
    with tempfile.TemporaryDirectory(prefix="xbloom-mutant-") as tmp:
        for number, (name, (test, edits)) in enumerate(GUARDS.items()):
            if not baseline[test]:
                continue
            copy = Path(tmp) / f"guard-{number}"
            shutil.copytree(STATIC, copy)
            script = (copy / "app.js").read_text(encoding="utf-8")
            for old, new in edits:
                if script.count(old) != 1:
                    print(f"CANNOT APPLY  {name}: the guard is not where this tool expects it")
                    held = False
                    break
                script = script.replace(old, new)
            else:
                (copy / "app.js").write_text(script, encoding="utf-8")
                without = run(test, copy, browser, "current")
                with_guard = baseline[test]
                ok = (not without) and with_guard
                held &= ok
                print(f"{'HELD' if ok else 'NOT HELD':9} {name}: without the guard "
                      f"{'passed' if without else 'failed'}, with it {'passed' if with_guard else 'failed'}")
    return 0 if held else 1


if __name__ == "__main__":
    raise SystemExit(main())
