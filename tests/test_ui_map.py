"""UI-map crawler (agent-workspace/ui_map.py) against a fake app: what it may
tap, what it must never tap, and when it must stop. No phone, no WDA."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "agent-workspace"))

import ui_map as um  # noqa: E402

W, H = 440, 956
BUNDLE = "com.example.plantpal"


def node(type_, label="", x=0, y=0, w=0, h=0, children=(), value="", name=""):
    return {"type": f"XCUIElementType{type_}", "label": label, "name": name or label, "value": value,
            "rect": {"x": x, "y": y, "width": w, "height": h}, "isVisible": "1", "isEnabled": "1",
            "children": list(children)}


def tabbar(selected):
    return node("TabBar", "", 0, 870, W, 86, [
        node("Button", "Garden", 20, 880, 100, 60, value="1" if selected == "Garden" else "0"),
        node("Button", "Chat", 170, 880, 100, 60, value="1" if selected == "Chat" else "0"),
    ])


def navbar(title, back=None):
    kids = [node("StaticText", title, 150, 60, 140, 30)]
    if back:
        kids.insert(0, node("Button", back, 8, 60, 80, 30))
    return node("NavigationBar", title, 0, 50, W, 50, kids)


def app(*kids):
    return node("Application", "Plantpal", 0, 0, W, H, [node("Window", "", 0, 0, W, H, list(kids))])


SCREENS = {
    "garden": app(navbar("My jungle"),
                  node("Cell", "Fern", 0, 150, W, 80, [node("StaticText", "Fern", 20, 170, 200, 30)]),
                  node("Cell", "Lorem ipsum", 0, 240, W, 80),
                  node("Cell", "Go Premium", 0, 330, W, 80, [node("StaticText", "Subscribe now", 20, 350, 200, 30)]),
                  tabbar("Garden")),
    "fern": app(navbar("Fern", back="My jungle"), node("StaticText", "Water every 3 days", 20, 200, 300, 30),
                node("Button", "Follow", 20, 300, 80, 40), tabbar("Garden")),
    "lorem": app(navbar("Lorem ipsum", back="My jungle"), node("Image", "", 20, 200, 80, 80, name="person.crop.circle"),
                 tabbar("Garden")),
    "chat": app(navbar("Chats"), node("Cell", "Dr. Leaf", 0, 150, W, 80), tabbar("Chat")),
    "thread": app(navbar("Dr. Leaf", back="Chats"), node("StaticText", "No messages yet", 20, 300, 300, 30),
                  node("TextField", "Message", 20, 800, 300, 40, value=""), tabbar("Chat")),
}
# What each tap leads to: (screen, label) -> screen
LINKS = {
    ("garden", "Fern"): "fern", ("garden", "Lorem ipsum"): "lorem", ("garden", "Chat"): "chat",
    ("fern", "My jungle"): "garden", ("lorem", "My jungle"): "garden", ("fern", "Chat"): "chat",
    ("chat", "Dr. Leaf"): "thread", ("chat", "Garden"): "garden", ("thread", "Chats"): "chat",
}


class FakePhone:
    def __init__(self, start="garden", screens=None, links=None):
        self.at = start
        self.screens = screens or SCREENS
        self.links = links or LINKS
        self.taps = []
        self.bundle_id = BUNDLE
        self.after_tap = None  # hook: lambda phone, label: ...

    def bundle(self):
        return self.bundle_id

    def tree(self):
        return self.screens[self.at]

    def size(self):
        return W, H

    def screenshot(self, path):
        Path(path).write_bytes(b"png")

    def tap(self, x, y):
        hit = None
        for e in um.flatten(self.tree()):
            r = e.rect
            if r["x"] <= x <= r["x"] + r["width"] and r["y"] <= y <= r["y"] + r["height"] and e.text:
                hit = e  # deepest wins: flatten is post-order, so keep the first match
                break
        label = hit.text if hit else "?"
        self.taps.append(label)
        nxt = self.links.get((self.at, label))
        if nxt:
            self.at = nxt
        if self.after_tap:
            self.after_tap(self, label)

    def scroll_page(self, up):
        pass

    def settle(self, seconds=0.0):
        pass

    # The crawler must never find these.
    def type_text(self, *_):
        raise AssertionError("typed")


def crawl(tmp_path, phone=None, **kw):
    phone = phone or FakePhone()
    c = um.Crawl(phone=phone, bundle_id=BUNDLE, out_dir=tmp_path, **kw)
    return c.run(), phone


def test_maps_every_safe_screen_and_returns(tmp_path):
    res, phone = crawl(tmp_path)
    titles = [s["title"] for s in res["screens"]]
    assert titles == ["My jungle", "Fern", "Lorem ipsum", "Chats", "Dr. Leaf"]
    assert res["stopped"] is None
    fern = next(s for s in res["screens"] if s["title"] == "Fern")
    assert fern["route"] == ["Garden", "Fern"]
    assert Path(fern["screenshot"]).exists() and Path(fern["tree_file"]).exists()


def test_never_taps_denied_or_action_buttons(tmp_path):
    res, phone = crawl(tmp_path)
    for bad in ("Go Premium", "Subscribe now", "Follow", "Message"):
        assert bad not in phone.taps
    assert any(s["label"] == "Go Premium" for s in res["skipped"])


def test_deny_list_reads_text_inside_the_element():
    cell = um.flatten(node("Cell", "Gold plan", 0, 200, W, 80, [node("StaticText", "Start free trial", 10, 210, 200, 30)]))
    outer = next(e for e in cell if e.type == "Cell")
    assert um.denied(outer)
    assert not um.denied(next(e for e in um.flatten(node("Cell", "Fern", 0, 200, W, 80)) if e.type == "Cell"))


def test_stops_when_another_app_comes_to_front(tmp_path):
    phone = FakePhone()

    def leave(p, label):
        if label == "Fern":
            p.bundle_id = "com.apple.mobilesafari"

    phone.after_tap = leave
    res, phone = crawl(tmp_path, phone)
    assert "mobilesafari" in res["stopped"]
    assert phone.taps[-1] == "Fern"  # nothing after the switch, not even a back tap


def test_refuses_to_start_on_another_app(tmp_path):
    phone = FakePhone()
    phone.bundle_id = "com.apple.springboard"
    res, phone = crawl(tmp_path, phone)
    assert phone.taps == [] and res["screens"] == []
    assert "springboard" in res["stopped"]


def test_stops_on_a_login_screen(tmp_path):
    screens = dict(SCREENS, garden=app(navbar("Sign in"), node("TextField", "Email", 20, 200, 300, 40),
                                       node("SecureTextField", "Password", 20, 260, 300, 40)))
    res, phone = crawl(tmp_path, FakePhone(screens=screens))
    assert "sign in on the phone first" in res["stopped"]
    assert phone.taps == []


def test_stops_on_an_alert(tmp_path):
    screens = dict(SCREENS, fern=app(node("Alert", "Allow notifications?", 40, 300, 360, 200,
                                          [node("Button", "Allow", 60, 440, 140, 40)])))
    res, phone = crawl(tmp_path, FakePhone(screens=screens))
    assert "Alert" in res["stopped"]
    assert "Allow" not in phone.taps


def test_stops_when_back_does_not_return(tmp_path):
    links = dict(LINKS)
    links[("fern", "My jungle")] = "chat"  # back goes somewhere unexpected
    res, phone = crawl(tmp_path, FakePhone(links=links))
    assert "back did not return" in res["stopped"]


def test_limits(tmp_path):
    res, _ = crawl(tmp_path, max_screens=2)
    assert "screen limit" in res["stopped"]
    res, _ = crawl(tmp_path / "b", max_actions=1)
    assert "action limit" in res["stopped"]


def test_back_control_and_signature():
    els = um.flatten(SCREENS["fern"])
    back = um.back_control(els, W, H, parent_title="My jungle")
    assert back is not None and back.text == "My jungle"
    a = um.signature(um.describe(um.flatten(SCREENS["garden"]), W, H))
    b = um.signature(um.describe(um.flatten(SCREENS["fern"]), W, H))
    assert a != b


def test_planner_reads_a_crawl(tmp_path):
    import recording_plan as rp

    res, _ = crawl(tmp_path)
    found = rp.find_placeholders(res)
    cats = {(f["screen_title"], f["category"]) for f in found}
    assert ("Lorem ipsum", "default avatar") in cats
    assert ("Dr. Leaf", "empty state") in cats
    assert ("Dr. Leaf", "empty field") in cats


# --- review round 1: each hole that would have let a dangerous tap through ---


def _els(*kids):
    return um.flatten(app(*kids))


def test_back_named_like_back_but_labelled_buy_is_not_back():
    els = _els(node("NavigationBar", "Shop", 0, 50, W, 50, [node("Button", "Buy", 8, 60, 80, 30, name="backToShop")]))
    assert um.back_control(els, W, H) is None


def test_subscription_cards_are_never_candidates():
    els = _els(node("Button", "Go Premium", 0, 200, W, 120, [node("StaticText", "Go Premium", 20, 220, 200, 30)]),
               node("Other", "Gold plan", 0, 340, W, 120, [node("StaticText", "Gold plan", 20, 360, 200, 30)]))
    assert um.nav_candidates(els, W, H) == []


def test_a_like_button_hidden_inside_a_cell_blocks_the_cell():
    els = _els(node("Cell", "Coach Ana", 0, 200, W, 100,
                    [node("Button", "Heart", 180, 230, 80, 40, name="Like")]))
    assert um.nav_candidates(els, W, H) == []


def test_a_button_over_the_cell_centre_blocks_the_cell():
    # Not inside the cell in the tree, but drawn over its centre.
    els = _els(node("Cell", "Coach Ana", 0, 200, W, 100, [node("StaticText", "Coach Ana", 20, 210, 200, 30)]),
               node("Button", "Ask", 180, 230, 80, 40))
    assert um.nav_candidates(els, W, H) == []


def test_tap_rechecks_safety_on_the_fresh_tree(tmp_path):
    phone = FakePhone()
    safe = um.flatten(SCREENS["garden"])
    fern = next(e for e in safe if e.type == "Cell" and e.text == "Fern")
    # Between choosing and tapping, a Follow button lands on the Fern row.
    phone.screens = dict(SCREENS, garden=app(navbar("My jungle"),
                                             node("Cell", "Fern", 0, 150, W, 80),
                                             node("Button", "Follow", 180, 170, 80, 40), tabbar("Garden")))
    c = um.Crawl(phone=phone, bundle_id=BUNDLE, out_dir=tmp_path)
    c.size = (W, H)
    c._t0 = __import__("time").monotonic()
    try:
        c._tap(fern, "Fern", "nav")
    except um.CrawlStop as e:
        assert "no longer safe" in str(e)
    else:
        raise AssertionError("tapped a row with a Follow button over it")
    assert phone.taps == []


def test_scrolling_checks_the_app_first(tmp_path):
    phone = FakePhone()
    calls = []
    phone.scroll_page = lambda up: calls.append(up)
    real_shot = phone.screenshot

    def shot_then_leave(path):
        real_shot(path)
        phone.bundle_id = "com.apple.mobilesafari"

    phone.screenshot = shot_then_leave
    res, _ = crawl(tmp_path, phone)
    assert "mobilesafari" in res["stopped"]
    assert calls == []  # no swipe on the other app


# --- review round 2 ---


def test_links_are_never_tapped():
    els = _els(node("Link", "Nordstrom", 0, 200, W, 100, [node("StaticText", "Nordstrom", 20, 220, 200, 30)]),
               node("Other", "Item", 0, 320, W, 100, [node("Link", "Nordstrom", 20, 340, 200, 30)]))
    assert um.nav_candidates(els, W, H) == []


def test_leftmost_nav_button_is_back_only_with_the_previous_title():
    bar = node("NavigationBar", "Ana", 0, 50, W, 50, [node("Button", "Heart", 8, 60, 60, 30)])
    assert um.back_control(_els(bar), W, H, parent_title="Coaches") is None
    bar = node("NavigationBar", "Ana", 0, 50, W, 50, [node("Button", "Coaches", 8, 60, 90, 30)])
    assert um.back_control(_els(bar), W, H, parent_title="Coaches").text == "Coaches"
    assert um.back_control(_els(bar), W, H) is None  # no parent known: no guess


def test_a_send_button_wrapping_a_card_blocks_it():
    els = _els(node("Button", "Send", 0, 200, W, 140,
                    [node("Other", "Coach Ana", 0, 200, W, 120, [node("StaticText", "Coach Ana", 20, 220, 200, 30)])]))
    assert um.nav_candidates(els, W, H) == []


# --- sign-in pause ---

LOGIN = app(navbar("Sign in"), node("TextField", "Email", 20, 200, 300, 40),
            node("SecureTextField", "Password", 20, 260, 300, 40))


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _login_crawl(tmp_path, phone, minutes=5, on_sleep=None):
    clock, lines = Clock(), []

    def sleep(s):
        clock.sleep(s)
        if on_sleep:
            on_sleep(clock.t)

    c = um.Crawl(phone=phone, bundle_id=BUNDLE, out_dir=tmp_path, wait_login_minutes=minutes, account="member",
                 clock=clock, sleep=sleep, out=lines.append)
    return c.run(), lines, c


def test_waits_for_a_person_to_sign_in_then_maps(tmp_path):
    phone = FakePhone(screens=dict(SCREENS, login=LOGIN), start="login")
    seen = []

    def human(t):  # the producer signs in after ~10 s; nothing is tapped meanwhile
        seen.append((tmp_path / "WAITING_FOR_LOGIN").exists())
        assert phone.taps == []
        if t >= 9:
            phone.at = "garden"

    res, lines, _ = _login_crawl(tmp_path, phone, on_sleep=human)
    assert lines == ["WAITING_FOR_LOGIN account=member", "LOGIN_DONE"]
    assert all(seen) and not (tmp_path / "WAITING_FOR_LOGIN").exists()
    assert res["stopped"] is None and res["account"] == "member"
    assert res["screens"][0]["title"] == "My jungle"


def test_sign_in_timeout_stops_cleanly(tmp_path):
    phone = FakePhone(screens=dict(SCREENS, login=LOGIN), start="login")
    res, lines, _ = _login_crawl(tmp_path, phone, minutes=1)
    assert "not done within 1 min" in res["stopped"]
    assert lines == ["WAITING_FOR_LOGIN account=member"]
    assert phone.taps == [] and not (tmp_path / "WAITING_FOR_LOGIN").exists()


def test_a_password_manager_in_front_is_waited_out(tmp_path):
    phone = FakePhone(screens=dict(SCREENS, login=LOGIN), start="login")

    def human(t):
        phone.bundle_id = "com.1password" if t < 6 else BUNDLE
        if t >= 9:
            phone.at = "garden"

    res, lines, _ = _login_crawl(tmp_path, phone, on_sleep=human)
    assert res["stopped"] is None and lines[-1] == "LOGIN_DONE"
