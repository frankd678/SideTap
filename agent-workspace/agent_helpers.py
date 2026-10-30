"""Agent workspace: add your own helpers here.

This file is auto-loaded into every phone-harness script's namespace,
after the built-in helpers. Example:

def open_wifi_settings():
    open_app("Settings")
    wait_stable()
    tap_text("Wi-Fi")
"""

import time

# iOS screen recording through Control Center.
#
# The pull is the viewer's measured Control gesture (viewer.html, btn-control):
# Control Center pages vertically, and a pull past 0.3h lands on the music page.
# The record control's accessibility label is "Screen Recording" (read off the
# Control Center tree on iOS 27.0, 2026-10-29). Tapping it starts a 3s countdown;
# tapping it while recording stops at once and iOS saves the clip to Photos.
# Control Center is closed by tapping its empty bottom area: WDA's touches never
# reach the home-indicator zone, so the swipe-up close is not available.
# The clip includes the Control Center open/close at both ends; trim in post.
#
# Only the Control Center BUTTON may be tapped: while recording, the Dynamic
# Island carries "Screen Recording" elements too and they come first in the tree,
# so a first-match tap hit the island and never stopped the recording (2026-10-29).
# The button's value is "1" while recording and empty otherwise; both helpers
# read it back before closing Control Center and raise if the tap did not take.

_RECORD_LABEL = "Screen Recording"
_COUNTDOWN_SECONDS = 3.5


# Control Center never reads as still (its live tiles keep animating), so
# wait_stable() there ran to its timeout: 9.7s per open, measured 2026-10-29.
# Poll the record button's own bounded lookup instead (~0.35s per look).
_CC_TIMEOUT = 5.0


def _on_screen_center(eid):
    """Centre of the element if it is inside the screen, else None.

    Control Center's buttons exist in the tree while it is still sliding in
    (Screen Mirroring was found at y=-140 on 2026-10-30), so "found" is not "visible".
    """
    import math

    if eid is None:
        return None
    try:
        r = client()._session_request("GET", f"/element/{eid}/rect")
    except WDAError:
        return None
    if not (
        all(isinstance(r.get(k), (int, float)) and math.isfinite(r[k]) for k in ("x", "y", "width", "height"))
        and r["width"] > 0 and r["height"] > 0
    ):
        return None
    x, y = r["x"] + r["width"] / 2, r["y"] + r["height"] / 2
    s = screen_info()
    if not (0 < x < s["width"] and 0 < y < s["height"]):
        return None
    return x, y


def _visible(chain):
    return _on_screen_center(client().find_first(chain)) is not None


def _wait_chain(chain, present, timeout=_CC_TIMEOUT):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _visible(chain) == present:
            return True
    return False


def _open_control_center(probe=None, label=None):
    # A failed open is NOT closed for you: the close is a blind tap at the
    # bottom of the screen, which lands in the app underneath if Control Center
    # never opened. Left open on that rare failure, it is visible and harmless.
    # `probe` is the class chain of a control that proves Control Center is up.
    probe, label = probe or _RECORD_CHAIN, label or _RECORD_LABEL
    s = screen_info()
    w, h = s["width"], s["height"]
    swipe(w * 0.92, 1, w * 0.92, h * 0.3, seconds=0.3)
    if not _wait_chain(probe, True):
        raise WDAError(
            f"Control Center did not open, or has no '{label}' control "
            "(add it under Settings > Control Center)."
        )


def _close_control_center(probe=None):
    # Raises when it stays open: a take replayed into the overlay would tap
    # Control Center tiles instead of the app.
    # Never tap blind: if Control Center is not visibly up, the bottom tap
    # lands in whatever is underneath. On the Home Screen that is the Dock,
    # and on 2026-10-30 it opened Messages.
    probe = probe or _RECORD_CHAIN
    if not _visible(probe):
        return
    s = screen_info()
    w, h = s["width"], s["height"]
    # The mirroring sheet sits over Control Center, so one tap can close only
    # the sheet; a second (gated again) closes Control Center. NEVER a swipe up
    # from the bottom edge: that is the Home gesture, and on 2026-10-30 it took
    # the phone out of the take's app twice.
    for _ in range(2):
        if not _visible(probe):
            return
        tap(w / 2, h * 0.93)
        if _wait_chain(probe, False):
            return
    raise WDAError("Control Center did not close.")


# A bounded class-chain lookup, never ui_tree(): a full Control Center /source
# is 12-16s on iOS 27 and the old helpers read it twice per toggle. The chain
# names the Button type, which is what keeps the Dynamic Island's same-label
# "Other" elements out of it.
_RECORD_CHAIN = f'**/XCUIElementTypeButton[`label == "{_RECORD_LABEL}"`]'


def _record_button():
    """(element id, value) of the Control Center record Button, or None."""
    c = client()
    eid = c.find_first(_RECORD_CHAIN)
    if eid is None:
        return None
    return eid, c.element_value(eid)


def _recording_state(value):
    """True/False for the button's two known values; anything else is an error."""
    if value == "1":
        return True
    if value == "":
        return False
    raise WDAError(f"Unexpected '{_RECORD_LABEL}' button value {value!r}.")


def _cleanup(fn, *args):
    """Run a cleanup step from a finally block without hiding the real error.

    With an exception already on its way out, a cleanup failure is printed and
    the original keeps propagating; otherwise the cleanup failure is raised.
    """
    import sys

    failing = sys.exc_info()[0] is not None
    try:
        return fn(*args)
    except Exception as e:
        if not failing:
            raise
        print(f"[cleanup] {getattr(fn, '__name__', fn)} also failed: {e}")


def _toggle_recording(want_on):
    import math

    _open_control_center()
    try:
        found = _record_button()
        if found is None:
            raise WDAError(f"'{_RECORD_LABEL}' button vanished from Control Center.")
        eid, value = found
        if _recording_state(value) == want_on:
            raise WDAError(f"Screen recording is already {'on' if want_on else 'off'}.")
        _tap_element(eid, f"'{_RECORD_LABEL}' button")
        if want_on:
            time.sleep(_COUNTDOWN_SECONDS)
        after = _record_button()
        if after is None or _recording_state(after[1]) != want_on:
            raise WDAError(f"Screen recording did not turn {'on' if want_on else 'off'}.")
    finally:
        _cleanup(_close_control_center)


def start_recording():
    """Start iOS screen recording from Control Center; returns once it is recording."""
    _toggle_recording(True)


def stop_recording():
    """Stop iOS screen recording from Control Center; iOS saves the clip to Photos."""
    _toggle_recording(False)


# Two-pass takes.
#
# A label tap costs a full /source (12-14s on iOS 27), which wrecks the pacing of
# a recorded take. So a take is rehearsed once with dry_run_take(), which finds
# every label, performs the step and saves the point coordinates it used, then
# replay_take() runs only coordinate taps, swipes and sleeps: no screen reads.
#
# Steps:
#   {"op": "tap", "label": "General", "index": 0, "after": 0.8}
#   {"op": "tap", "x": 220, "y": 400, "after": 0.8}
#   {"op": "swipe", "direction": "up", "amount": 0.35, "seconds": 1.2, "after": 0.5}
#   {"op": "wait", "seconds": 1.5}
#   {"op": "back", "after": 0.9}
# "back" taps the top-left navigation Button, never a label: a back tap by label
# ("Settings" on the General screen) matched a mid-screen row and opened Software
# Update in rehearsal (2026-10-29). Any step may carry "expect": a label the
# rehearsal must see after the step, or it stops before saving anything.
# "after" is the pause once the gesture returns. "up" moves the content up
# (finger travels bottom to top). Replay must start from the same screen,
# orientation and layout the rehearsal started from.

_TAKE_VERSION = 1


def _swipe_points(step, w, h):
    a = float(step.get("amount", 0.35))
    lo, hi = h * (0.5 - a / 2), h * (0.5 + a / 2)
    y1, y2 = (hi, lo) if step.get("direction", "up") == "up" else (lo, hi)
    return [w / 2, y1, w / 2, y2]


def _num(v, lo=0.0, hi=None):
    import math

    return (
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        and v >= lo and (hi is None or v <= hi)
    )


def _check_steps(steps, resolved):
    """Validate a whole take before anything moves the phone."""
    if not isinstance(steps, list) or not steps:
        raise WDAError("A take needs a non-empty list of steps.")
    ops = ("tap", "swipe", "wait") if resolved else ("tap", "swipe", "wait", "back")
    for i, s in enumerate(steps):
        op = s.get("op") if isinstance(s, dict) else None
        if op not in ops:
            raise WDAError(f"Step {i}: unknown op {op!r}.")
        for k in ("after", "seconds"):
            if k in s and not _num(s[k]):
                raise WDAError(f"Step {i}: {k} must be a non-negative number.")
        if "expect" in s and not (isinstance(s["expect"], str) and s["expect"].strip()):
            raise WDAError(f"Step {i}: expect must be a label.")
        if resolved:
            if op == "wait" and not _num(s.get("seconds")):
                raise WDAError(f"Step {i}: saved wait has no seconds.")
            if op != "wait" and not _num(s.get("after")):
                raise WDAError(f"Step {i}: saved step has no after.")
            if op == "tap" and not (_num(s.get("x")) and _num(s.get("y"))):
                raise WDAError(f"Step {i}: saved tap has no valid x/y.")
            if op == "swipe" and not (
                isinstance(s.get("points"), list) and len(s["points"]) == 4
                and all(_num(p) for p in s["points"]) and _num(s.get("seconds"), lo=0.05)
            ):
                raise WDAError(f"Step {i}: saved swipe has no valid points/seconds.")
            continue
        if op == "swipe":
            if s.get("direction", "up") not in ("up", "down"):
                raise WDAError(f"Step {i}: direction must be 'up' or 'down'.")
            if "amount" in s and not _num(s["amount"], lo=0.05, hi=0.9):
                raise WDAError(f"Step {i}: amount must be between 0.05 and 0.9.")
        if op == "tap":
            if "label" in s:
                if not (isinstance(s["label"], str) and s["label"].strip()):
                    raise WDAError(f"Step {i}: label must be text.")
                if "index" in s and not (isinstance(s["index"], int) and not isinstance(s["index"], bool) and s["index"] >= 0):
                    raise WDAError(f"Step {i}: index must be a non-negative integer.")
            elif not (_num(s.get("x")) and _num(s.get("y"))):
                raise WDAError(f"Step {i}: a tap needs a label or valid x and y.")


def _resolve_label(i, step):
    """One exact match for the step's label; several only with an explicit index.

    Exact only: a substring fallback can rehearse a different row. Hits at the
    same point are one element (a Cell and its StaticText), not an ambiguity.
    """
    hits, seen = [], set()
    for r in find_text(step["label"], exact=True):
        key = (round(r["x"]), round(r["y"]))
        if key not in seen:
            seen.add(key)
            hits.append(r)
    if not hits:
        raise WDAError(f"Step {i}: {step['label']!r} is not on screen.")
    if "index" not in step:
        if len(hits) > 1:
            where = ", ".join(f"({round(r['x'])}, {round(r['y'])})" for r in hits)
            raise WDAError(f"Step {i}: {step['label']!r} matches {len(hits)} places {where}; add an index.")
        return hits[0]
    if step["index"] >= len(hits):
        raise WDAError(f"Step {i}: {step['label']!r} has no match number {step['index']}.")
    return hits[step["index"]]


def dry_run_take(steps, path):
    """Rehearse steps on the phone, resolving each label once; save the take to path."""
    import json
    import os

    _check_steps(steps, resolved=False)
    app = client().active_app()["bundleId"]
    s = screen_info()
    w, h = s["width"], s["height"]
    saved = []
    for i, step in enumerate(steps):
        op = step["op"]
        out = {"op": op, "after": float(step.get("after", 0.5))}
        if op == "tap":
            if "label" in step:
                hit = _resolve_label(i, step)
                out.update(label=step["label"], x=hit["x"], y=hit["y"])
            else:
                out.update(x=float(step["x"]), y=float(step["y"]))
            tap(out["x"], out["y"])
            wait_stable()
        elif op == "back":
            backs = [r for r in ocr() if r.get("type") == "Button" and r["y"] < 130 and r["x"] < w / 3]
            if not backs:
                raise WDAError(f"Step {i}: no back button at the top left.")
            out.update(op="tap", label=f"<back: {backs[0]['text']}>", x=backs[0]["x"], y=backs[0]["y"])
            tap(out["x"], out["y"])
            wait_stable()
        elif op == "swipe":
            out.update(points=_swipe_points(step, w, h), seconds=float(step.get("seconds", 1.2)))
            swipe(*out["points"], seconds=out["seconds"])
            wait_stable()
        else:
            out = {"op": "wait", "seconds": float(step.get("seconds", 1.0))}
        # Rehearse the timing too, so a saved take has actually been run as saved.
        time.sleep(out["seconds"] if op == "wait" else out["after"])
        if "expect" in step and not find_text(step["expect"], exact=True):
            raise WDAError(f"Step {i}: expected {step['expect']!r} on screen afterwards; stopping before saving.")
        saved.append(out)
    take = {"version": _TAKE_VERSION, "app": app, "screen": {"width": w, "height": h}, "steps": saved}
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(take, f, indent=2)
    os.replace(tmp, path)
    return path


def _load_take(path):
    """Read and fully validate a saved take, including the phone's current size."""
    import json

    with open(path, encoding="utf-8") as f:
        take = json.load(f)
    if not isinstance(take, dict) or take.get("version") != _TAKE_VERSION:
        raise WDAError(f"Unsupported take file {path!r}.")
    scr = take.get("screen")
    if not (isinstance(scr, dict) and _num(scr.get("width"), lo=1) and _num(scr.get("height"), lo=1)):
        raise WDAError("Take has no valid screen size.")
    _check_steps(take.get("steps"), resolved=True)
    now = screen_info()
    if (now["width"], now["height"]) != (scr["width"], scr["height"]):
        raise WDAError(
            f"Take was rehearsed at {scr['width']}x{scr['height']} points, "
            f"the phone is at {now['width']}x{now['height']} (rotated?)."
        )
    if not isinstance(take.get("app", _DEFAULT_TAKE_APP), str):
        raise WDAError("Take has an invalid 'app'.")
    return take


# Takes saved before the app was recorded were all rehearsed in Settings.
_DEFAULT_TAKE_APP = "com.apple.Preferences"


def _require_app(take):
    """Refuse to replay unless the take's app is frontmost.

    A take is blind coordinates: on 2026-10-30 a Settings take replayed on the
    Home Screen tapped whatever icons sat under General and About.
    """
    want = take.get("app", _DEFAULT_TAKE_APP)
    got = client().active_app().get("bundleId")
    if got != want:
        raise WDAError(f"Take expects {want!r} in front but {got!r} is; open it at the take's start screen first.")


def replay_take(path, record=False):
    """Replay a saved take with fixed timing and no screen reads; optionally record it."""
    from phone_harness import config

    take = _load_take(path)
    _require_app(take)
    c = client()
    recording = False
    if record:
        start_recording()
        recording = True
    t0 = time.time()
    failure = None
    try:
        # Control Center can leave something else in front; check again.
        _require_app(take)
        c.set_wait_for_idle(0)
        for s in take["steps"]:
            if s["op"] == "tap":
                tap(s["x"], s["y"])
            elif s["op"] == "swipe":
                swipe(*s["points"], seconds=s["seconds"])
            pause = s["seconds"] if s["op"] == "wait" else s["after"]
            time.sleep(pause)
            # A gesture after a 30s+ lull mints a fresh WDA session (see
            # wda_client), which comes back with the default idle wait.
            if pause >= 20:
                c.set_wait_for_idle(0)
    except BaseException as e:
        failure = e
        raise
    finally:
        elapsed = time.time() - t0
        # Both cleanups always run, and neither hides the take's own error.
        errors = []
        for name, undo in (
            ("restore idle wait", lambda: c.set_wait_for_idle(config.WDA_IDLE_WAIT)),
            ("stop recording", stop_recording if recording else None),
        ):
            if undo is None:
                continue
            try:
                undo()
            except Exception as e:
                errors.append((name, e))
        for name, e in errors:
            print(f"[replay_take] {name} failed: {e}")
        if errors and failure is None:
            raise errors[0][1]
    return elapsed


# Pull the newest screen recording off the phone with go-ios file access (AFC).
#
# Screen recordings land in DCIM/NNNAPPLE as IMG_XXXX.MP4 (camera video is .MOV),
# and iOS numbers both the folders and the files upward, so the newest recording
# is the highest-numbered .MP4 in the highest folder holding one. `fsync tree`
# gives names only, no dates, which is why this goes by number.

_TAKES_DIR = r"C:\CaptureRig\takes"


def _afc_names(path):
    import subprocess
    from phone_harness import device

    out = subprocess.run(
        [device.ios_path(), "fsync", "tree", f"--path={path}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    return [
        line[2:].strip()
        for line in (out.stdout + out.stderr).splitlines()
        if line.startswith("|-")
    ]


def pull_latest_recording(dest=_TAKES_DIR):
    """Copy the newest IMG_XXXX.MP4 from the phone to dest; returns its local path."""
    import os
    import re
    import subprocess
    from phone_harness import device

    folders = sorted(
        (n.rstrip("/") for n in _afc_names("/DCIM") if re.fullmatch(r"\d{3}APPLE/", n)),
        reverse=True,
    )
    for folder in folders:
        clips = [n for n in _afc_names(f"/DCIM/{folder}") if re.fullmatch(r"IMG_\d{4}\.MP4", n, re.I)]
        if clips:
            name = max(clips, key=lambda n: int(n[4:8]))
            break
    else:
        raise WDAError("No IMG_XXXX.MP4 screen recording found under DCIM.")
    os.makedirs(dest, exist_ok=True)
    local = os.path.join(dest, name)
    if not os.path.exists(local):
        subprocess.run(
            [device.ios_path(), "fsync", "pull", f"--srcPath=/DCIM/{folder}/{name}", f"--dstPath={dest}"],
            capture_output=True, timeout=600,
        )
    if not os.path.exists(local):
        raise WDAError(f"go-ios did not pull /DCIM/{folder}/{name}.")
    return local


# AirPlay capture: the phone mirrors to a receiver on this PC and the PC records,
# so iOS's own recorder never runs and never draws its red indicator.
# Plan: PLAN-airplay.md (GPT/Codex). UNTESTED ON DEVICE as of 2026-10-29: the
# iOS 27 labels below are educated guesses, kept as named constants so the
# first device run can correct them in one place, and every step fails loudly.
#
# Two receivers:
#   "uxplay"  UxPlay (FDH2/UxPlay, MSYS2 UCRT64 build). -mp4 records the phone's
#             own stream with arrival timestamps, no re-encode. AirPlay sends a
#             frame only when the screen changes, so a raw -vdmp dump remuxed at
#             a fixed 60 fps would compress static stretches; the dump is kept
#             beside the mp4 as a backup only.
#   "window"  any receiver window (ApowerMirror), recorded off the desktop with
#             ffmpeg gdigrab + NVENC. Re-encodes and depends on the window size.

_MIRROR_LABEL = "Screen Mirroring"      # Control Center control (unverified, iOS 27)
_STOP_MIRROR_LABEL = "Stop Mirroring"   # button in the mirroring sheet (unverified)
_MIRROR_CHAIN = f'**/XCUIElementTypeButton[`label == "{_MIRROR_LABEL}"`]'
_STOP_MIRROR_CHAIN = f'**/XCUIElementTypeButton[`label == "{_STOP_MIRROR_LABEL}"`]'
_MIRROR_TIMEOUT = 15.0

UXPLAY_EXE = r"C:\msys64\ucrt64\bin\uxplay.exe"
AIRPLAY_NAME = "CaptureRig"
AIRPLAY_DIR = r"C:\CaptureRig\airplay"


def _receiver_chain(name):
    """Bounded chains for the receiver's row; which type iOS 27 uses is unverified."""
    if not name or any(ch in name for ch in '`"\\'):
        raise WDAError(f"Receiver name {name!r} must be plain text (no quotes or backslashes).")
    # WDA's class-chain parser chokes on [ ] inside a literal (ApowerMirror lists as
    # "Apowersoft[HOSTNAME]"), so a bracketed name matches on the text before the bracket.
    head = name.split("[")[0].strip()
    if head != name:
        if not head or "]" in head:
            raise WDAError(f"Receiver name {name!r} has no usable text before '['.")
        pred = f'label BEGINSWITH "{head}"'
    else:
        pred = f'label == "{name}"'
    return [f'**/XCUIElementType{t}[`{pred}`]' for t in ("Button", "Cell", "StaticText")]


def _tap_element(eid, what):
    pt = _on_screen_center(eid)
    if pt is None:
        raise WDAError(f"{what} is not on screen; not tapping.")
    tap(*pt)


def _find_any(chains, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for chain in chains:
            eid = client().find_first(chain)
            if eid is not None:
                return eid
    return None


def start_mirroring(receiver=AIRPLAY_NAME, confirm=None, timeout=_MIRROR_TIMEOUT):
    """Mirror the phone to an AirPlay receiver via Control Center.

    `confirm` is a callable returning True once the PC side sees video (the
    capture handle's `receiving`); the phone's own UI is not trusted for that.
    """
    _open_control_center(_MIRROR_CHAIN, _MIRROR_LABEL)
    try:
        _tap_element(client().find_first(_MIRROR_CHAIN), f"'{_MIRROR_LABEL}'")
        row = _find_any(_receiver_chain(receiver), timeout)
        if row is None:
            raise WDAError(
                f"Receiver {receiver!r} never appeared in the mirroring list "
                "(is it running, on the same network, and allowed through the firewall?)."
            )
        _tap_element(row, f"receiver {receiver!r}")
    finally:
        # The sheet sits over Control Center; the bottom tap is assumed to
        # dismiss both. UNVERIFIED on iOS 27: a sheet left up hides the mirroring
        # button, so this check can pass with the sheet still open.
        _cleanup(_close_control_center, _MIRROR_CHAIN)
    if confirm is not None:
        deadline = time.time() + timeout
        while not confirm():
            if time.time() > deadline:
                raise WDAError(f"Mirroring to {receiver!r} started on the phone but no video reached the PC.")
            time.sleep(0.25)


def stop_mirroring():
    """Stop AirPlay mirroring via Control Center's mirroring sheet."""
    _open_control_center(_MIRROR_CHAIN, _MIRROR_LABEL)
    try:
        _tap_element(client().find_first(_MIRROR_CHAIN), f"'{_MIRROR_LABEL}'")
        stop = _find_any([_STOP_MIRROR_CHAIN], 5.0)
        if stop is None:
            raise WDAError(f"No '{_STOP_MIRROR_LABEL}' button: is the phone mirroring?")
        _tap_element(stop, f"'{_STOP_MIRROR_LABEL}'")
    finally:
        _cleanup(_close_control_center, _MIRROR_CHAIN)


def _new_workdir():
    """A fresh directory per capture, never shared with a retry in the same second."""
    import os

    os.makedirs(AIRPLAY_DIR, exist_ok=True)
    base, n = os.path.join(AIRPLAY_DIR, time.strftime("%Y%m%d-%H%M%S")), 0
    while True:
        work = base if n == 0 else f"{base}-{n}"
        try:
            os.mkdir(work)
            return work
        except FileExistsError:
            n += 1


def _probe_video(path):
    """(seconds, width, height, fps) of a finished file, or raise."""
    import json
    import subprocess

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,avg_frame_rate:format=duration", "-of", "json", path],
        capture_output=True, text=True, timeout=60,
    )
    try:
        info = json.loads(out.stdout)
        st, dur = info["streams"][0], float(info["format"]["duration"])
        num, den = (st.get("avg_frame_rate") or "0/1").split("/")
        return dur, st["width"], st["height"], (float(num) / float(den) if float(den) else 0.0)
    except (KeyError, IndexError, ValueError, json.JSONDecodeError):
        raise WDAError(f"{path} is not a playable video: {out.stderr.strip()[:300]}")


def _window_region(title):
    """(output_idx, x, y, w, h) of a window in physical pixels, for ffmpeg ddagrab.

    gdigrab reached only 42 fps on a 1918x988 window (CPU copy, measured
    2026-10-29); ddagrab duplicates the desktop on the GPU but captures a whole
    monitor, so the window is located and cropped. DXGI output N is assumed to be
    \\\\.\\DISPLAY(N+1) (right on this PC: DISPLAY2 was output 1, checked on a
    frame); re-check the first frame if a monitor is added or reordered.
    The window must stay uncovered: ddagrab records what the screen shows.
    """
    import ctypes
    from ctypes import wintypes

    u, dwm = ctypes.windll.user32, ctypes.windll.dwmapi

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]

    # Typed signatures: an untyped HWND/HMONITOR is truncated to 32 bits.
    u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    u.FindWindowW.restype = wintypes.HWND
    u.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    u.MonitorFromWindow.restype = wintypes.HMONITOR
    u.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEXW)]
    u.GetMonitorInfoW.restype = wintypes.BOOL
    dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    dwm.DwmGetWindowAttribute.restype = ctypes.c_long
    try:
        u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2: physical pixels
    except (AttributeError, OSError):
        pass

    hwnd = u.FindWindowW(None, title)
    if not hwnd:
        raise WDAError(f"No window titled {title!r} (check the receiver is open).")
    rect = wintypes.RECT()
    if dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:  # extended frame bounds
        raise WDAError(f"Could not read the bounds of window {title!r}.")
    mi = MONITORINFOEXW()
    mi.cbSize = ctypes.sizeof(mi)
    if not u.GetMonitorInfoW(u.MonitorFromWindow(hwnd, 2), ctypes.byref(mi)):
        raise WDAError(f"Could not find the monitor showing {title!r}.")
    digits = "".join(ch for ch in mi.szDevice if ch.isdigit())
    if not digits:
        raise WDAError(f"Unrecognised monitor name {mi.szDevice!r}.")
    m = mi.rcMonitor
    if rect.left < m.left or rect.top < m.top or rect.right > m.right or rect.bottom > m.bottom:
        raise WDAError(f"Window {title!r} crosses a monitor edge; move it fully onto one screen.")
    w, h = (rect.right - rect.left) // 2 * 2, (rect.bottom - rect.top) // 2 * 2
    if w <= 0 or h <= 0:
        raise WDAError(f"Window {title!r} is minimised.")
    return int(digits) - 1, rect.left - m.left, rect.top - m.top, w, h


def _end_process(proc, graceful):
    """Stop a capture process: graceful first, then kill. Always reaps it."""
    import subprocess

    if proc.poll() is not None:
        return proc.returncode
    try:
        graceful()
        return proc.wait(timeout=20)
    except Exception:
        # The graceful stop failed or timed out: never leave a receiver running.
        proc.kill()
        return proc.wait(timeout=10)


def start_capture(backend="uxplay", window_title=None, size="1320x2868", fps=60, name=AIRPLAY_NAME):
    """Start the PC side of an AirPlay take; returns a handle for stop_capture().

    UxPlay runs in its own process GROUP on this console (no CREATE_NO_WINDOW):
    CTRL_BREAK only reaches a process sharing the caller's console, and that
    signal is what lets it finish the mp4. Whether UxPlay finalises on Ctrl-Break
    under Windows is UNVERIFIED; the raw dump is the fallback (see stop_capture).
    """
    import os
    import subprocess

    work = _new_workdir()
    log = open(os.path.join(work, f"{backend}.log"), "wb")
    proc = None
    try:
        if backend == "uxplay":
            if not os.path.exists(UXPLAY_EXE):
                raise WDAError(f"UxPlay is not built yet ({UXPLAY_EXE} missing); see the AirPlay checklist.")
            cmd = [UXPLAY_EXE, "-n", name, "-nh", "-h265", "-s", size, "-fps", str(fps),
                   # Relative names: UxPlay runs in `work`, and on Windows it
                   # drops the backslashes from an absolute -mp4 path.
                   "-mp4", "take", "-vdmp", "videodump"]
            proc = subprocess.Popen(cmd, cwd=work, stdout=log, stderr=subprocess.STDOUT,
                                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        elif backend == "window":
            if not window_title:
                raise WDAError("window capture needs window_title (the receiver window's exact title).")
            idx, x, y, w, h = _window_region(window_title)
            grab = (f"ddagrab=output_idx={idx}:framerate={fps}:draw_mouse=0:"
                    f"offset_x={x}:offset_y={y}:video_size={w}x{h}")
            cmd = ["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", grab, "-an",
                   "-c:v", "h264_nvenc", "-preset", "p7", "-cq", "16", os.path.join(work, "take.partial.mp4")]
            proc = subprocess.Popen(cmd, cwd=work, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
        else:
            raise WDAError(f"Unknown capture backend {backend!r}.")
        # Process-alive only. UxPlay's "ready to accept" log line is unknown
        # until it has run here once; receiving() is the real readiness signal.
        time.sleep(1.0)
        if proc.poll() is not None:
            raise WDAError(f"{backend} capture exited at once (code {proc.returncode}); see {work}.")
    except BaseException:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
        log.close()
        raise
    return {"backend": backend, "proc": proc, "work": work, "log": log, "name": name, "sizes": {}}


def receiving(handle):
    """True while the capture is alive AND its output grew since the last call.

    Growth, not size: bytes left from earlier (or from a receiver that has since
    died) must not read as a live stream. For the window backend this proves
    only that ffmpeg is recording the window, not that the phone is connected;
    check the first frame of that backend's takes.
    """
    import glob
    import os

    if handle["proc"].poll() is not None:
        return False
    w = handle["work"]
    watched = glob.glob(os.path.join(w, "videodump*")) + glob.glob(os.path.join(w, "take*.mp4"))
    if handle["backend"] == "window":
        # ffmpeg's mp4 grows only every ~10s on a still window (12.8s to first
        # growth, measured); its progress line in the log updates ~2x a second.
        watched.append(os.path.join(w, "window.log"))
    grew = False
    for f in watched:
        size = os.path.getsize(f)
        if size > handle["sizes"].get(f, 0):
            grew = grew or f in handle["sizes"]  # a first sighting only sets the baseline
        handle["sizes"][f] = size
    return grew


def _remux_dump(work):
    """Fallback when UxPlay's mp4 is missing or unplayable: remux the raw dump.

    AirPlay sends frames only when the screen changes, so the timing here is an
    ASSUMED 60 fps: still stretches come out shorter than they were. Usable for
    review, not as a delivery take.
    """
    import glob
    import os
    import subprocess

    dumps = sorted(glob.glob(os.path.join(work, "videodump*.h264")))
    if not dumps:
        return None
    out = os.path.join(work, "take.fromdump.mp4")
    # The dump is named .h264 whatever the codec; UxPlay logs the real one.
    try:
        with open(os.path.join(work, "uxplay.log"), encoding="utf-8", errors="replace") as f:
            fmt = "hevc" if "codec is h265" in f.read() else "h264"
    except OSError:
        fmt = "h264"
    subprocess.run(["ffmpeg", "-hide_banner", "-y", "-fflags", "+genpts", "-r", "60", "-f", fmt,
                    "-i", dumps[0], "-c", "copy", "-movflags", "+faststart", out],
                   capture_output=True, timeout=600)
    return out if os.path.exists(out) else None


def stop_capture(handle, dest=_TAKES_DIR):
    """Stop the capture, check the file plays, move it into dest; returns its path.

    Every mp4 the receiver wrote is kept (a reconnect can start a new segment);
    the longest one is returned. Raw dumps and logs stay in the work folder.
    """
    import glob
    import os
    import shutil
    import signal

    proc, w = handle["proc"], handle["work"]
    try:
        if handle["backend"] == "window":
            def graceful():
                proc.stdin.write(b"q")  # ffmpeg's own clean stop: finishes the mp4
                proc.stdin.flush()
        else:
            # UxPlay writes its mp4 only when the phone ends the session (Stop
            # Mirroring); Ctrl-Break kills it before the file is closed, leaving
            # 0 bytes (measured 2026-10-30). Give a just-stopped mirror up to 5s.
            deadline = time.time() + 5
            while time.time() < deadline:
                try:
                    with open(os.path.join(w, "uxplay.log"), encoding="utf-8", errors="replace") as f:
                        if "Stopped recording" in f.read():
                            break
                except OSError:
                    pass
                time.sleep(0.25)

            def graceful():
                proc.send_signal(signal.CTRL_BREAK_EVENT)
        code = _end_process(proc, graceful)
    finally:
        handle["log"].close()
    playable = []
    for f in glob.glob(os.path.join(w, "take*.mp4")):
        try:
            playable.append((_probe_video(f), f))
        except WDAError as e:
            print(f"[capture] skipping unplayable {f}: {e}")
    if not playable and handle["backend"] == "uxplay":
        rough = _remux_dump(w)
        if rough:
            print("[capture] mp4 missing or unfinished; remuxed the raw dump at an ASSUMED 60 fps (timing approximate).")
            playable.append((_probe_video(rough), rough))
    if not playable:
        raise WDAError(f"No playable video (capture exit code {code}); dump and log kept in {w}.")
    playable.sort(key=lambda p: p[0][0], reverse=True)
    os.makedirs(dest, exist_ok=True)
    finals = []
    for n, ((dur, width, height, fps), f) in enumerate(playable):
        final = os.path.join(dest, f"airplay-{os.path.basename(w)}{'' if n == 0 else f'-part{n + 1}'}.mp4")
        shutil.move(f, final)
        finals.append(final)
        print(f"[capture] {final}: {dur:.1f}s {width}x{height} @ {fps:.2f} fps")
    if len(finals) > 1:
        print(f"[capture] {len(finals)} segments: the stream restarted mid-take; check them all.")
    return finals[0]


def replay_take_airplay(path, receiver=AIRPLAY_NAME, backend="uxplay", window_title=None):
    """Replay a take while the phone mirrors to this PC; returns (mp4 path, take seconds).

    The take is validated in full before the receiver starts. Mirroring is
    stopped in cleanup whenever start_mirroring() was ENTERED, not only when it
    returned: a failure after the receiver was picked can leave the phone
    mirroring, and stop_mirroring() raising "not mirroring" is then harmless.
    """
    _require_app(_load_take(path))
    handle = start_capture(backend, window_title, name=receiver)
    mirror_attempted, secs, final = False, None, None
    try:
        mirror_attempted = True
        start_mirroring(receiver, confirm=lambda: receiving(handle))
        secs = replay_take(path, record=False)
    finally:
        try:
            if mirror_attempted:
                _cleanup(stop_mirroring)
        finally:
            # Nested so the receiver is always stopped, even when stopping the
            # mirror is what failed.
            final = _cleanup(stop_capture, handle)
    return final, secs


# Clean-island capture over USB with qtmux (C:\CaptureRig\qtmux).
#
# qtmux owns the phone through the libusb-win32 driver and, in one process,
# records Apple's QuickTime screen stream AND serves a usbmuxd socket that
# go-ios uses (USBMUXD_SOCKET_ADDRESS=tcp://127.0.0.1:27115), so touches keep
# working while it records. Unlike Screen Recording (red dot) and AirPlay (blue
# pill), the QuickTime stream draws no Dynamic Island at all and shows the 09:41
# capture status bar: 0 indicator pixels in 378 sampled frames on 2026-10-30.
#
# These helpers only talk to qtmux's loopback control API; they never start
# qtmux or touch drivers. Before using them: phone on libusb0
# (driver\set_iphone_driver.ps1 -Driver libusb0, UAC), qtmux.exe running, and
# `phone-harness up` started with USBMUXD_SOCKET_ADDRESS set. Stop qtmux only
# with POST /quit: a session cut off mid-stream wedges the phone's capture
# service until the iPhone is restarted.

QTMUX_CTL = "http://127.0.0.1:27116"
_QT_TIMEOUT = 10.0
_QT_STOP_TIMEOUT = 150.0  # stop waits for qtmux's ffmpeg remux (bounded at 2 min)


def _qtmux(path, method="GET", timeout=_QT_TIMEOUT):
    """JSON from qtmux's control API; its errors come back as WDAError."""
    import json
    import urllib.error
    import urllib.request

    req = urllib.request.Request(QTMUX_CTL + path, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error") or e.reason
        except ValueError:
            msg = e.reason
        raise WDAError(f"qtmux {path}: {msg}") from None
    except (urllib.error.URLError, OSError) as e:
        raise WDAError(
            f"qtmux is not answering at {QTMUX_CTL} ({e}). Start C:\\CaptureRig\\qtmux\\qtmux.exe "
            "with the phone on the libusb0 driver."
        ) from None


def qt_status():
    """qtmux's live counters: video_frames, idr_frames, width, height, recording, mux_connections."""
    return _qtmux("/status")


def start_capture_qt(name=None):
    """Start a clean-island recording in qtmux; returns the .flv path it writes.

    Refuses unless video is already flowing (frames and a resolution reported):
    a recording opened on a silent session would only fail at stop.
    """
    import urllib.parse

    st = qt_status()
    if not st.get("video_frames") or not st.get("width"):
        raise WDAError(f"qtmux has no video yet ({st}); is the phone unlocked and qtmux past its PING?")
    if st.get("recording"):
        raise WDAError(f"qtmux is already recording to {st['recording']}")
    q = "?name=" + urllib.parse.quote(name) if name else ""
    return _qtmux("/record/start" + q, method="POST")["flv"]


def stop_capture_qt():
    """Stop the qtmux recording; returns the mp4 path after checking it plays."""
    res = _qtmux("/record/stop", method="POST", timeout=_QT_STOP_TIMEOUT)
    mp4 = res.get("mp4")
    if not mp4:
        raise WDAError(f"qtmux wrote no mp4: {res}")
    secs, w, h, fps = _probe_video(mp4)
    print(f"[qtmux] {mp4}: {secs:.1f}s {w}x{h} ~{fps:.0f}fps, {res.get('frames')} frames")
    return mp4


def replay_take_qt(path, name=None):
    """Replay a take while qtmux records it; returns (mp4 path, take seconds).

    The take's app must be in front before recording starts; the recording is
    stopped in cleanup whenever it was started, without hiding the take's error.
    """
    _require_app(_load_take(path))
    start_capture_qt(name)
    secs = None
    try:
        time.sleep(0.5)  # recordings open on the next IDR frame (~0.6 s apart)
        secs = replay_take(path, record=False)
        time.sleep(0.5)
    finally:
        mp4 = _cleanup(stop_capture_qt)
    return mp4, secs
