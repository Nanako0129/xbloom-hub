"""Show that each safety test of the dashboard guards something.

For every guard below: copy frontend/ (the dashboard's sources), take the
guard out of the copy, build the copy in the container (frontend/npm.sh), and
run the browser test that covers it against that build. The test must fail.
The same test must pass against a build of the sources as they are. Prints
one line per guard and exits non-zero if any guard is not held by its test.

    uv run python tools/browser_mutations.py [--browser webkit]

docs/PLAN.md, S3c, step 1 and findings C6 and C9. A guard that moves in the
code needs its edit here moved with it: the tool says CANNOT APPLY then.
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
TESTS = "tests/browser/test_dashboard.py"

# name -> (test, [(file under frontend/src, text, replacement)])
GUARDS = {
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


# A fixed place for the copies, so its node_modules volume (named after the
# path, frontend/npm.sh) is installed once.
MUTANT_ROOT = Path(tempfile.gettempdir()) / "xbloom-hub-mutant-next"


def run(test: str, browser: str, web: Path) -> bool:
    env = {**os.environ, "XBLOOM_TEST_WEB_DIR": str(web)}
    done = subprocess.run(
        [sys.executable, "-m", "pytest", f"{TESTS}::{test}", "-m", "browser", "--browser", browser,
         "-q", "-p", "no:cacheprovider", "-rs"], cwd=ROOT, env=env, capture_output=True, text=True)
    # A skipped test proved nothing: count it as not passing.
    return done.returncode == 0 and " skipped" not in done.stdout


def build(edits, install: bool = False) -> Path | None:
    """Copy frontend/ (without node_modules), apply the edits, build. Returns the
    build directory, or None (saying why) if an edit does not apply or a step fails."""
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
    steps = ([["ci"]] if install else []) + [["run", "build"]]
    for step in steps:
        done = subprocess.run([str(front / "npm.sh"), *step], capture_output=True, text=True)
        if done.returncode != 0:
            print(f"    npm {' '.join(step)} failed: {(done.stdout + done.stderr).strip()[-400:]}")
            return None
    return web


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", default="chromium")
    browser = parser.parse_args().browser
    held = True
    # The baseline is a build of the sources as they are, made the same way as
    # the mutants: not whatever build is left in src/xbloom_hub/web.
    clean = build([], install=True)
    if clean is None:
        print("BROKEN    the sources as they are did not build")
        return 1
    baseline = {test: run(test, browser, clean) for test, _ in GUARDS.values()}
    for test, passed in baseline.items():
        if not passed:
            print(f"BROKEN    {test} fails on the page as it is: no guard can be judged by it")
            held = False
    for name, (test, edits) in GUARDS.items():
        if not baseline[test]:
            continue
        web = build(edits)
        if web is None:
            print(f"CANNOT APPLY  {name}")
            held = False
            continue
        without = run(test, browser, web)
        ok = not without
        held &= ok
        print(f"{'HELD' if ok else 'NOT HELD':9} {name}: without the guard {'passed' if without else 'failed'}, with it passed")
    # Leave the copy as the sources are, so the next run starts from them.
    build([])
    return 0 if held else 1


if __name__ == "__main__":
    raise SystemExit(main())
