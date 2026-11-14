"""Read the phone's current screen as a list of UI elements. Never taps.

Usage: phone_ui.py            list elements (anything that looks like a key is masked)
       phone_ui.py find TEXT  print "x y" of the centre of the first element whose
                        text or description is exactly TEXT (case-insensitive)
"""
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

KEY = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")


def tree() -> ET.Element:
    # uiautomator can fail ("could not get idle state" on an animating screen)
    # and still exit 0, leaving the previous dump in place. Coordinates from a
    # stale screen send the next tap to the wrong place, so remove the file
    # first and require the success line.
    subprocess.run(["adb", "shell", "rm", "-f", "/sdcard/ui.xml"], check=True, capture_output=True)
    dump = subprocess.run(["adb", "shell", "uiautomator", "dump", "/sdcard/ui.xml"],
                          check=True, capture_output=True)
    if b"dumped to" not in dump.stdout:
        raise SystemExit(f"uiautomator did not dump the screen: {dump.stdout.decode().strip()}")
    xml = subprocess.run(["adb", "exec-out", "cat", "/sdcard/ui.xml"],
                         check=True, capture_output=True).stdout.decode()
    return ET.fromstring(xml)


def centre(node: ET.Element) -> tuple[int, int]:
    x1, y1, x2, y2 = map(int, re.findall(r"\d+", node.get("bounds", "[0,0][0,0]")))
    return (x1 + x2) // 2, (y1 + y2) // 2


def main() -> None:
    root = tree()
    nodes = list(root.iter("node"))
    if len(sys.argv) > 2 and sys.argv[1] == "find":
        want = sys.argv[2].lower()
        # Exact match only: a substring match once hit a paragraph of help
        # text instead of the field it described, and the tap went there.
        for n in nodes:
            if want in ((n.get("text") or "").lower(), (n.get("content-desc") or "").lower()):
                print(*centre(n))
                return
        raise SystemExit(1)
    print("package:", nodes[0].get("package") if nodes else "?")
    for n in nodes:
        text, desc = n.get("text") or "", n.get("content-desc") or ""
        interactive = any(n.get(k) == "true" for k in ("clickable", "checkable", "scrollable"))
        if not (text or desc or interactive):
            continue
        text, desc = KEY.sub("<masked-key>", text), KEY.sub("<masked-key>", desc)
        flags = "".join(f for f, k in (("C", "clickable"), ("K", "checkable"),
                                       ("+", "checked"), ("S", "scrollable")) if n.get(k) == "true")
        cls = (n.get("class") or "").split(".")[-1][:12]
        print(f"{text[:56]!r:58} {desc[:24]!r:26} {cls:12} {flags:4} {centre(n)}")


main()
