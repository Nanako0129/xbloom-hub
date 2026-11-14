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


def run(test: str, static: Path | None, browser: str) -> bool:
    env = {**os.environ}
    if static is not None:
        env["XBLOOM_TEST_STATIC_DIR"] = str(static)
    done = subprocess.run(
        [sys.executable, "-m", "pytest", f"{TESTS}::{test}", "-m", "browser", "--browser", browser,
         "-q", "-p", "no:cacheprovider"], cwd=ROOT, env=env, capture_output=True, text=True)
    return done.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", default="chromium")
    browser = parser.parse_args().browser
    held = True
    # Each test once against the page as it is: it must pass there.
    baseline = {test: run(test, None, browser) for test, _ in GUARDS.values()}
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
                without = run(test, copy, browser)
                with_guard = baseline[test]
                ok = (not without) and with_guard
                held &= ok
                print(f"{'HELD' if ok else 'NOT HELD':9} {name}: without the guard "
                      f"{'passed' if without else 'failed'}, with it {'passed' if with_guard else 'failed'}")
    return 0 if held else 1


if __name__ == "__main__":
    raise SystemExit(main())
