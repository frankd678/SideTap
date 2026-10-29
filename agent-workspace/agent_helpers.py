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


def _open_control_center():
    s = screen_info()
    w, h = s["width"], s["height"]
    swipe(w * 0.92, 1, w * 0.92, h * 0.3, seconds=0.3)
    wait_stable()


def _close_control_center():
    s = screen_info()
    tap(s["width"] / 2, s["height"] * 0.93)
    wait_stable()


def _record_button():
    """The Control Center record Button node, or None if Control Center lacks it."""
    stack = [ui_tree()]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("type") == "Button" and node.get("label") == _RECORD_LABEL:
                return node
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return None


def _toggle_recording(want_on):
    _open_control_center()
    button = _record_button()
    if button is None:
        _close_control_center()
        raise WDAError(f"'{_RECORD_LABEL}' is not in Control Center; add it under Settings > Control Center.")
    if (button.get("value") == "1") == want_on:
        _close_control_center()
        raise WDAError(f"Screen recording is already {'on' if want_on else 'off'}.")
    r = button["rect"]
    tap(r["x"] + r["width"] / 2, r["y"] + r["height"] / 2)
    if want_on:
        time.sleep(_COUNTDOWN_SECONDS)
    after = _record_button()
    _close_control_center()
    if after is None or (after.get("value") == "1") != want_on:
        raise WDAError(f"Screen recording did not turn {'on' if want_on else 'off'}.")


def start_recording():
    """Start iOS screen recording from Control Center; returns once it is recording."""
    _toggle_recording(True)


def stop_recording():
    """Stop iOS screen recording from Control Center; iOS saves the clip to Photos."""
    _toggle_recording(False)


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
