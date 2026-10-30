"""UI map: walk a client's app on the phone and record every screen it reaches.

For each screen it saves a screenshot, the raw accessibility tree and a flat
list of visible elements, plus the route of taps that got there. The recording
planner (``recording_plan.py``) matches its screens to the script and reads the
elements for placeholder text and empty fields.

It is built to fail closed. It ONLY taps:

- tab-bar items,
- list cells and large cards (navigation),
- the back/close control to return,

and never anything whose label, or any text inside it, could send, buy,
subscribe, follow, like, post, delete, report, block, call, request, sign
out, or open a keyboard. It never types, never uses the clipboard, never opens
or switches apps, and stops at once (no clean-up taps) if another app comes to
the front, an alert appears, or it cannot get back to where it came from.

The checks before a tap and the tap itself are separate WDA calls, so a screen
change in between cannot be ruled out completely; every tap is re-validated on
a fresh tree just before it is sent, and the frontmost app is checked again
right after.

Usage (the app must already be open on its first screen)::

    python agent-workspace/ui_map.py crawl --bundle-id com.example.app --slug client
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_OUT = Path("C:/CaptureRig/clients")

# Anything matching this, on the element or on any text inside it, is never tapped.
DENY = re.compile(
    r"\b("
    r"send|sent|submit|post|publish|share|reply|comment|message (?:them|now)|chat now|"
    r"buy|purchase|pay|checkout|check out|order|subscribe|subscription|upgrade|trial|start (?:free|my|your)|"
    r"restore|redeem|add to (?:cart|bag)|cart|bag|shop|shopping|get (?:started|it|premium|gold|plus)|"
    r"premium|pro|plus|gold|vip|plans?|pricing|price|offer|deal|free|unlock|membership|member|tier|"
    r"follow|unfollow|like|unlike|love|favou?rite|save|saved|bookmark|pin|star|rate|review|"
    r"join|accept|decline|confirm|approve|allow|agree|continue|next|done|ok|yes|no|"
    r"delete|remove|clear|reset|archive|mute|block|report|flag|hide|"
    r"call|facetime|video call|dial|email|text me|invite|request|book|schedule|"
    r"sign ?out|log ?out|logout|sign ?in|log ?in|sign ?up|register|create account|"
    r"edit|compose|write|new message|type|search|camera|photo library|upload|record|"
    r"settings|delete account|notifications? on|turn on|enable|"
    r"open in|safari|website|visit|link|https?://|www\.|\.com\b"
    r")\b",
    re.I,
)
# Back/close controls the crawler may use to return; only in the top bar.
BACK_LABELS = re.compile(r"^(back|close|cancel|dismiss|x|chevron\.left|chevron\.backward|arrow\.left|xmark)$", re.I)
BACK_NAMES = re.compile(r"^(back|backbutton|navigation ?back|close|xmark|chevron\.(left|backward))$", re.I)

_TAPPABLE_TYPES = {"Cell", "Button", "Other", "Image", "StaticText"}
_FIELD_TYPES = {"TextField", "SecureTextField", "TextView", "SearchField", "Keyboard", "Switch", "Slider",
                "Picker", "PickerWheel", "DatePicker", "Stepper", "SegmentedControl", "Toggle"}
_STOP_TYPES = {"Alert", "Sheet", "Keyboard"}


class CrawlStop(Exception):
    """Stop the whole crawl; nothing more may be tapped."""


def _t(node: dict) -> str:
    return str(node.get("type", "")).replace("XCUIElementType", "")


def _visible(node: dict) -> bool:
    return str(node.get("isVisible", "1")) in ("1", "true", "True")


def _enabled(node: dict) -> bool:
    return str(node.get("isEnabled", "1")) in ("1", "true", "True")


def _text(node: dict) -> str:
    return str(node.get("label") or node.get("name") or node.get("value") or "").strip()


@dataclass
class El:
    type: str
    text: str
    name: str
    value: str
    placeholder: str
    rect: dict
    path: str
    ancestors: list[str]
    inner: list[str]  # texts of all descendants
    inner_types: list[str]
    enabled: bool

    @property
    def center(self) -> tuple[float, float]:
        r = self.rect
        return r["x"] + r["width"] / 2, r["y"] + r["height"] / 2

    def flat(self) -> dict:
        return {"type": self.type, "text": self.text, "name": self.name, "value": self.value,
                "placeholder": self.placeholder, "rect": self.rect, "path": self.path}


def flatten(tree: dict) -> list[El]:
    """Visible elements with their ancestry and the texts inside them."""
    out: list[El] = []

    def walk(node: dict, ancestors: list[str], path: str) -> tuple[list[str], list[str]]:
        if not isinstance(node, dict):
            return [], []
        typ = _t(node)
        texts, types = [], []
        for i, ch in enumerate(node.get("children") or []):
            ct, cty = walk(ch, ancestors + [typ], f"{path}/{i}")
            texts += ct
            types += cty
        rect = node.get("rect") or {}
        own = _text(node)
        attrs = [str(node.get(k) or "").strip() for k in ("label", "name", "value")]
        attrs = [a for i, a in enumerate(attrs) if a and a not in attrs[:i]]
        if _visible(node) and rect.get("width", 0) > 0 and rect.get("height", 0) > 0:
            out.append(El(
                type=typ, text=own, name=str(node.get("name") or ""), value=str(node.get("value") or ""),
                placeholder=str(node.get("placeholderValue") or ""), rect=rect, path=path,
                ancestors=ancestors, inner=texts, inner_types=types, enabled=_enabled(node),
            ))
        return attrs + texts, [typ] + types

    walk(tree, [], "")
    return out


@dataclass
class Screen:
    id: str
    signature: str
    title: str
    headers: list[str]
    tabs: list[str]
    selected_tab: str
    texts: list[str]
    elements: list[dict]
    route: list[str]
    screenshot: str | None = None
    tree_file: str | None = None
    extra_pages: int = 0


def describe(els: list[El], width: float, height: float) -> dict:
    nav = [e for e in els if e.type == "NavigationBar"]
    title = ""
    if nav:
        title = nav[0].text or next((t for t in nav[0].inner if t and not BACK_LABELS.match(t)), "")
    statics = [e for e in els if e.type == "StaticText" and e.text and e.rect["y"] < height * 0.25]
    statics.sort(key=lambda e: (e.rect["y"], e.rect["x"]))
    if not title and statics:
        # A large-title screen without a nav-bar label: the biggest early text.
        title = max(statics[:6], key=lambda e: e.rect["height"]).text
    tab_buttons = [e for e in els if e.type == "Button" and "TabBar" in e.ancestors]
    tabs = [e.text for e in tab_buttons if e.text]
    selected = next((e.text for e in tab_buttons if e.value in ("1", "true", "selected")), "")
    headers = [title] + [e.text for e in statics[:4] if e.text != title]
    texts = []
    for e in els:
        for t in [e.text] + ([e.value] if e.value and e.value != e.text else []):
            if t and t not in texts:
                texts.append(t)
    return {"title": title, "headers": [h for h in headers if h], "tabs": tabs, "selected_tab": selected,
            "texts": texts}


def signature(desc: dict) -> str:
    """Identity of a screen: title, selected tab and its top texts, digits removed."""
    key = "|".join([desc["title"], desc["selected_tab"], *sorted(desc["headers"][:4])])
    key = re.sub(r"\d+", "#", key.lower())
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _login_screen(els: list[El]) -> bool:
    return any(e.type == "SecureTextField" for e in els)


def denied(e: El) -> bool:
    words = [e.text, e.name, e.value, *e.inner]
    if any(t and DENY.search(t) for t in words):
        return True
    if set(e.inner_types) & _FIELD_TYPES or e.type in _FIELD_TYPES:
        return True
    return False


_INTERACTIVE = {"Button", "Link", "Cell", "Switch", "Toggle", "TextField", "SecureTextField", "TextView",
                "SearchField", "Slider", "Stepper", "SegmentedControl", "Picker", "MenuItem", "Tab"}


def _inside(outer: El, inner: El) -> bool:
    return inner.path.startswith(outer.path + "/")


def clear_tap_point(e: El, els: list[El]) -> bool:
    """Nothing but `e` (and plain content inside it) is under its centre.

    A small Send/Follow/Like button sitting on a safe card would take the tap;
    any other interactive element containing the point, or any denied text
    there at all, makes the target unsafe.
    """
    x, y = e.center
    for o in els:
        if o is e or o.path == e.path:
            continue
        r = o.rect
        if not (r["x"] <= x <= r["x"] + r["width"] and r["y"] <= y <= r["y"] + r["height"]):
            continue
        if _inside(o, e):  # an ancestor of the target (a list, a cell, the window)
            # A cell around plain content is fine; a Button/Link around it is
            # what actually receives the tap, so it must be safe itself.
            if o.type in _INTERACTIVE and (o.type != "Cell" or denied(o)):
                return False
            continue
        if o.type in _INTERACTIVE:
            return False
        if any(DENY.search(t) for t in [o.text, o.name, o.value, *o.inner] if t):
            return False
    return True


def nav_eligible(e: El, els: list[El], width: float, height: float) -> bool:
    if not e.enabled or e.type not in _TAPPABLE_TYPES:
        return False
    if any(a in ("TabBar", "NavigationBar", "Toolbar", "Keyboard", "Alert", "Sheet") for a in e.ancestors):
        return False
    r = e.rect
    # Fully on screen, clear of the status bar and the Home-gesture strip.
    if r["x"] < 0 or r["y"] < 60 or r["x"] + r["width"] > width or r["y"] + r["height"] > height - 40:
        return False
    is_cell = e.type == "Cell"
    # A large card that carries its own text (a coach card, a plant card).
    # Never a Link: links leave the app (a retailer, Safari).
    is_card = e.type in ("Button", "Other") and r["width"] >= 100 and r["height"] >= 60 and bool(e.inner)
    if "Link" in e.inner_types or any(re.search(r"arrow\.up\.right|safari|external", t, re.I) for t in e.inner):
        return False
    if not (is_cell or is_card):
        return False
    if not (e.text or e.inner) or denied(e):
        return False
    return clear_tap_point(e, els)


def nav_candidates(els: list[El], width: float, height: float, max_cells: int = 2) -> list[El]:
    """Elements the crawler may tap to go deeper, in a fixed order."""
    out: list[El] = []
    for e in sorted(els, key=lambda e: (e.rect["y"], e.rect["x"])):
        if len(out) >= max_cells:
            break
        if not nav_eligible(e, els, width, height):
            continue
        # Nested cards: keep the outermost one only.
        if any(_contains(o.rect, e.rect) for o in out):
            continue
        out.append(e)
    return out


def tab_candidates(els: list[El]) -> list[El]:
    return [e for e in els if e.type == "Button" and "TabBar" in e.ancestors and e.enabled and e.text
            and not denied(e) and clear_tap_point(e, els)]


def back_control(els: list[El], width: float, height: float, parent_title: str | None = None) -> El | None:
    """The control that returns to the previous screen, or None.

    Exact back/close names anywhere in the top bar, else the LEFTMOST button
    of the navigation bar (iOS labels the system back button with the previous
    screen's title) - but only when that label IS the previous screen's
    title, so a leftmost "Heart" or "Menu" button is never taken for Back.
    Either way, nothing on the deny-list, and nothing else under the tap point.
    """
    tops = [e for e in els if e.type == "Button" and e.enabled
            and ("NavigationBar" in e.ancestors or e.rect["y"] + e.rect["height"] < 130)]
    for e in tops:
        if BACK_LABELS.fullmatch(e.text or "") or BACK_NAMES.fullmatch(e.name or ""):
            if not _back_denied(e) and clear_tap_point(e, els):
                return e
    nav = sorted((e for e in tops if "NavigationBar" in e.ancestors), key=lambda e: e.rect["x"])
    if nav and parent_title and nav[0].rect["x"] < width * 0.25 and (nav[0].text or "").strip() == parent_title.strip() \
            and not _back_denied(nav[0]) and clear_tap_point(nav[0], els):
        return nav[0]
    return None


def _back_denied(e: El) -> bool:
    # The previous screen's title is the back label, so plain "Back"/"Close"
    # words are fine; anything that reads as an action is not.
    words = [w for w in (e.text, e.name, e.value, *e.inner) if w and not BACK_LABELS.fullmatch(w)
             and not BACK_NAMES.fullmatch(w)]
    return any(DENY.search(w) for w in words) or bool(set(e.inner_types) & _FIELD_TYPES)


def _contains(a: dict, b: dict) -> bool:
    return a["x"] <= b["x"] and a["y"] <= b["y"] and a["x"] + a["width"] >= b["x"] + b["width"] \
        and a["y"] + a["height"] >= b["y"] + b["height"]


def _same(a: El, b: El) -> bool:
    # Loose on position: scrolling back up after reading below the fold can
    # leave the list a few points off where it was.
    return a.type == b.type and a.text == b.text and abs(a.rect["x"] - b.rect["x"]) < 40 \
        and abs(a.rect["y"] - b.rect["y"]) < 40


# --------------------------------------------------------------------------
# Phone access: the only gestures the crawler can make.


class Phone:
    """Narrow adapter over the harness helpers: read, screenshot, tap, scroll."""

    def __init__(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from phone_harness import helpers  # noqa: PLC0415

        self._h = helpers

    def bundle(self) -> str:
        return (self._h.current_app() or {}).get("bundleId") or ""

    def tree(self) -> dict:
        self._h._invalidate_tree()  # never act on a cached screen
        return self._h.ui_tree()

    def size(self) -> tuple[float, float]:
        s = self._h.screen_info()
        return s["width"], s["height"]

    def screenshot(self, path: str) -> None:
        self._h.screenshot(path)

    def tap(self, x: float, y: float) -> None:
        self._h.tap(x, y)

    def scroll_page(self, up: bool) -> None:
        # Middle of the screen only: never from an edge (edges are system gestures).
        w, h = self.size()
        a, b = (h * 0.7, h * 0.3) if up else (h * 0.3, h * 0.7)
        self._h.swipe(w / 2, a, w / 2, b, 0.4)

    def settle(self, seconds: float = 0.8) -> None:
        time.sleep(seconds)


@dataclass
class Crawl:
    phone: object
    bundle_id: str
    out_dir: Path
    max_depth: int = 4
    max_screens: int = 40
    max_actions: int = 100
    max_seconds: float = 600
    max_cells: int = 2
    extra_pages: int = 1
    screens: dict[str, Screen] = field(default_factory=dict)
    edges: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    actions: int = 0
    stopped: str | None = None
    _t0: float = 0.0
    _back_to: str | None = None
    # Sign-in pause: 0 = stop at a login screen; N = wait up to N minutes for
    # the producer to sign in by hand, then carry on.
    wait_login_minutes: float = 0
    account: str = "default"
    login_poll: float = 3.0
    clock: object = time.monotonic
    sleep: object = time.sleep
    out: object = print  # where the WAITING_FOR_LOGIN / LOGIN_DONE lines go

    # -- guards ------------------------------------------------------------

    def _check_app(self, when: str) -> None:
        got = self.phone.bundle()
        if got != self.bundle_id:
            raise CrawlStop(f"{got or 'no app'} is in front ({when}), not {self.bundle_id}")

    def _check_budget(self) -> None:
        if self.actions >= self.max_actions:
            raise CrawlStop(f"action limit {self.max_actions} reached")
        if self.clock() - self._t0 > self.max_seconds:
            raise CrawlStop(f"time limit {self.max_seconds:.0f} s reached")

    def _read(self) -> tuple[dict, list[El]]:
        self._check_app("before reading")
        tree = self.phone.tree()
        self._check_app("after reading")
        els = flatten(tree)
        if _login_screen(els):
            # Signing in is the producer's job: the crawler never types, and never
            # touches a login screen. With --wait-for-login it waits for the
            # producer to sign in by hand; otherwise it stops.
            if not self.wait_login_minutes:
                raise CrawlStop("a login screen (password field) is showing; sign in on the phone first")
            self._wait_for_login()
            if self.screens:
                # Signed out mid-crawl: the screens behind us are stale.
                raise CrawlStop("the app asked to sign in again mid-crawl; signed in, run the crawl again")
            return self._read()
        stop = [e for e in els if e.type in _STOP_TYPES]
        if stop:
            raise CrawlStop(f"{stop[0].type} on screen ({stop[0].text or stop[0].name or 'no label'}); "
                            "nothing else is tapped")
        return tree, els

    def _wait_for_login(self) -> None:
        """Announce, then watch (reads only, no gestures) until signed in."""
        sentinel = self.out_dir / "WAITING_FOR_LOGIN"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        sentinel.write_text(self.account, encoding="utf-8")
        self.out(f"WAITING_FOR_LOGIN account={self.account}")
        start = self.clock()
        deadline = start + self.wait_login_minutes * 60
        try:
            while True:
                if self.clock() > deadline:
                    raise CrawlStop(f"sign-in for account '{self.account}' not done within "
                                    f"{self.wait_login_minutes:g} min")
                self.sleep(self.login_poll)
                # A password manager may be in front for a moment: just keep waiting.
                if self.phone.bundle() != self.bundle_id:
                    continue
                els = flatten(self.phone.tree())
                if self.phone.bundle() != self.bundle_id:
                    continue
                if not _login_screen(els) and not any(e.type == "Keyboard" for e in els):
                    break
        finally:
            sentinel.unlink(missing_ok=True)
        # Time spent waiting for a human does not count against the crawl.
        self._t0 += self.clock() - start
        self.out("LOGIN_DONE")

    def _tap(self, target: El, why: str, role: str) -> None:
        """Re-find the target on a fresh tree, re-check it is still safe for its
        role ("nav", "tab" or "back"), tap its centre, then re-check the app."""
        self._check_budget()
        _, els = self._read()
        w, h = self.size
        fresh = next((e for e in els if _same(e, target)), None)
        if fresh is None:
            raise CrawlStop(f"'{target.text}' moved or vanished before the tap ({why})")
        if role == "nav":
            ok = nav_eligible(fresh, els, w, h)
        elif role == "tab":
            ok = any(t.path == fresh.path for t in tab_candidates(els))
        else:
            b = back_control(els, w, h, self._back_to)
            ok = b is not None and b.path == fresh.path
        if not ok:
            raise CrawlStop(f"'{fresh.text or fresh.name}' is no longer safe to tap ({why})")
        self._check_app("before tap")
        x, y = fresh.center
        self.phone.tap(x, y)
        self.actions += 1
        self.phone.settle()
        self._check_app("after tap")

    # -- capture -----------------------------------------------------------

    def _capture(self, route: list[str]) -> Screen:
        tree, els = self._read()
        w, h = self.size
        desc = describe(els, w, h)
        sig = signature(desc)
        if sig in self.screens:
            return self.screens[sig]
        if len(self.screens) >= self.max_screens:
            raise CrawlStop(f"screen limit {self.max_screens} reached")
        sid = f"S{len(self.screens) + 1:02d}"
        shots = self.out_dir / "screens"
        shots.mkdir(parents=True, exist_ok=True)
        shot = shots / f"{sid}.png"
        self._shoot(shot)
        (shots / f"{sid}.json").write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
        elements = [e.flat() for e in els if e.text or e.type in ("Image", "TextField", "SecureTextField",
                                                                       "TextView", "SearchField")]
        sc = Screen(id=sid, signature=sig, title=desc["title"] or sid, headers=desc["headers"], tabs=desc["tabs"],
                    selected_tab=desc["selected_tab"], texts=desc["texts"], elements=elements,
                    route=list(route), screenshot=str(shot), tree_file=str(shots / f"{sid}.json"))
        self.screens[sig] = sc
        self._more_pages(sc)
        return sc

    def _shoot(self, path: Path) -> None:
        self._check_app("before screenshot")
        self.phone.screenshot(str(path))
        self._check_app("after screenshot")

    def _scroll(self, up: bool) -> None:
        self._check_budget()
        self._check_app("before scroll")
        self.phone.scroll_page(up=up)
        self.actions += 1
        self.phone.settle(0.6)
        self._check_app("after scroll")

    def _more_pages(self, sc: Screen) -> None:
        """Scroll down to read text below the fold, then come back up."""
        for n in range(self.extra_pages):
            self._scroll(up=True)
            _, els = self._read()
            new = [e.flat() for e in els if (e.text or e.type == "Image") and e.flat() not in sc.elements]
            if not new:
                break
            sc.elements += new
            for e in new:
                if e["text"] and e["text"] not in sc.texts:
                    sc.texts.append(e["text"])
            sc.extra_pages = n + 1
            self._shoot(Path(sc.screenshot).with_name(f"{sc.id}-p{n + 2}.png"))
        for _ in range(sc.extra_pages):
            self._scroll(up=False)

    # -- walk --------------------------------------------------------------

    def run(self) -> dict:
        self._t0 = self.clock()
        try:
            self._check_app("at start")
            self.size = self.phone.size()
            root = self._capture([])
            _, els = self._read()
            tabs = tab_candidates(els)
            if not tabs:
                self._explore(root, 1)
            for tab in tabs:
                if tab.text == root.selected_tab:
                    top = root
                    top.route = [tab.text]
                else:
                    self._tap(tab, f"tab {tab.text}", "tab")
                    top = self._capture([tab.text])
                if top is not root:
                    self.edges.append({"from": root.id, "to": top.id, "via": tab.text, "kind": "tab"})
                self._explore(top, 1)
        except CrawlStop as e:
            self.stopped = str(e)
        except Exception as e:  # a WDA error, STOP file, or timeout: report, never retry
            self.stopped = f"{type(e).__name__}: {e}"
        return self.result()

    def _explore(self, here: Screen, depth: int) -> None:
        if depth > self.max_depth:
            return
        _, els = self._read()
        w, h = self.size
        cands = nav_candidates(els, w, h, self.max_cells)
        for e in els:
            if e.type == "Cell" and e not in cands and denied(e):
                self.skipped.append({"screen": here.id, "label": e.text or (e.inner[:1] or [""])[0], "why": "deny-list"})
        for cand in cands:
            label = cand.text or (cand.inner[0] if cand.inner else cand.type)
            self._tap(cand, label, "nav")
            there = self._capture(here.route + [label])
            if there.signature == here.signature:
                continue  # the tap did not navigate
            self.edges.append({"from": here.id, "to": there.id, "via": label, "kind": "tap"})
            if len(there.route) == len(here.route) + 1:  # first time we reached it
                self._explore(there, depth + 1)
            self._go_back(here)

    def _go_back(self, expected: Screen) -> None:
        _, els = self._read()
        w, h = self.size
        back = back_control(els, w, h, expected.title)
        if back is None:
            raise CrawlStop(f"no back control to return to '{expected.title}'")
        self._back_to = expected.title
        self._tap(back, "back", "back")
        _, els = self._read()
        if signature(describe(els, w, h)) != expected.signature:
            raise CrawlStop(f"back did not return to '{expected.title}'")

    def result(self) -> dict:
        return {
            "version": 1, "bundle_id": self.bundle_id, "stopped": self.stopped, "actions": self.actions,
            "account": self.account,
            "screens": [vars(s) for s in self.screens.values()], "edges": self.edges, "skipped": self.skipped,
            "limits": {"depth": self.max_depth, "screens": self.max_screens, "actions": self.max_actions,
                       "cells_per_screen": self.max_cells},
        }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Map a client app's screens (safe taps only).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("crawl")
    c.add_argument("--bundle-id", required=True, help="the app's bundle id; it must already be in front")
    c.add_argument("--slug", required=True)
    c.add_argument("--out", help="output folder (default C:/CaptureRig/clients/<slug>/ui-map)")
    c.add_argument("--max-depth", type=int, default=4)
    c.add_argument("--max-screens", type=int, default=40)
    c.add_argument("--max-actions", type=int, default=100)
    c.add_argument("--max-seconds", type=float, default=600)
    c.add_argument("--cells", type=int, default=2, help="list rows/cards to open per screen")
    c.add_argument("--account", default="default", help="label of the account signed in (never a password)")
    c.add_argument("--wait-for-login", type=float, default=0, metavar="MINUTES",
                   help="at a login screen, wait this long for a person to sign in instead of stopping")
    a = ap.parse_args(argv)
    if not re.fullmatch(r"[a-z0-9-]+", a.slug):
        raise SystemExit("slug must be lowercase letters, digits and dashes")
    sub_dir = "ui-map" if a.account == "default" else f"ui-map-{re.sub(r'[^a-z0-9-]+', '-', a.account.lower()).strip('-')}"
    out = Path(a.out) if a.out else DEFAULT_OUT / a.slug / sub_dir
    crawl = Crawl(phone=Phone(), bundle_id=a.bundle_id, out_dir=out, max_depth=a.max_depth,
                  max_screens=a.max_screens, max_actions=a.max_actions, max_seconds=a.max_seconds,
                  max_cells=a.cells, wait_login_minutes=a.wait_for_login, account=a.account,
                  out=lambda line: print(line, flush=True))
    result = crawl.run()
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / "ui-map.json.tmp"
    tmp.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out / "ui-map.json")
    print(f"{len(result['screens'])} screen(s), {result['actions']} action(s) -> {out / 'ui-map.json'}")
    if result["stopped"]:
        print(f"stopped: {result['stopped']}")
        if "sign" in result["stopped"]:
            return 3  # a sign-in is still needed
    return 0


if __name__ == "__main__":
    sys.exit(main())
