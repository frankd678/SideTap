"""qtmux capture helpers in agent-workspace/agent_helpers.py, against a fake
qtmux control API. No phone, no qtmux, no driver needed."""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from phone_harness import run  # noqa: E402
from phone_harness.wda_client import WDAError  # noqa: E402


class FakeQtmux:
    """Answers /status, /record/start and /record/stop like qtmux does."""

    def __init__(self, status):
        self.status = status
        self.calls = []
        self.start_error = None
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                fake.calls.append(("GET", self.path))
                self._reply(200, fake.status)

            def do_POST(self):
                fake.calls.append(("POST", self.path))
                if self.path.startswith("/record/start"):
                    if fake.start_error:
                        self._reply(409, {"error": fake.start_error})
                    else:
                        self._reply(200, {"flv": r"C:\CaptureRig\takes\x.flv"})
                elif self.path == "/record/stop":
                    self._reply(200, {"flv": r"C:\CaptureRig\takes\x.flv", "frames": 600,
                                      "mp4": r"C:\CaptureRig\takes\x.mp4"})

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


LIVE = {"video_frames": 719, "idr_frames": 24, "width": 1320, "height": 2868, "recording": ""}


@pytest.fixture
def ns():
    return run._agent_namespace()


@pytest.fixture
def qtmux(ns):
    fake = FakeQtmux(dict(LIVE))
    ns["QTMUX_CTL"] = fake.url
    ns["_probe_video"] = lambda path: (10.0, 1320, 2868, 60.0)
    yield fake
    fake.close()


def test_start_and_stop_return_the_files(ns, qtmux):
    assert ns["start_capture_qt"]("take 1").endswith("x.flv")
    assert ("POST", "/record/start?name=take%201") in qtmux.calls
    assert ns["stop_capture_qt"]().endswith("x.mp4")


def test_start_refuses_without_video(ns, qtmux):
    qtmux.status = {"video_frames": 0, "width": 0, "recording": ""}
    with pytest.raises(WDAError, match="no video yet"):
        ns["start_capture_qt"]()
    assert not any(c[1].startswith("/record/start") for c in qtmux.calls)


def test_start_refuses_while_recording(ns, qtmux):
    qtmux.status = dict(LIVE, recording=r"C:\CaptureRig\takes\busy.flv")
    with pytest.raises(WDAError, match="already recording"):
        ns["start_capture_qt"]()


def test_qtmux_error_text_is_surfaced(ns, qtmux):
    qtmux.start_error = "already recording to elsewhere.flv"
    with pytest.raises(WDAError, match="elsewhere.flv"):
        ns["start_capture_qt"]()


def test_qtmux_down_names_the_fix(ns):
    ns["QTMUX_CTL"] = "http://127.0.0.1:9"  # discard port: nothing listens
    with pytest.raises(WDAError, match="qtmux.exe"):
        ns["qt_status"]()


def test_replay_checks_the_app_before_recording(ns, qtmux):
    def wrong_app(take):
        raise WDAError("Take expects Settings in front")

    ns["_load_take"] = lambda path: {"steps": []}
    ns["_require_app"] = wrong_app
    with pytest.raises(WDAError, match="Settings"):
        ns["replay_take_qt"]("take.json")
    assert not any(c[0] == "POST" for c in qtmux.calls)


def test_replay_stops_recording_and_keeps_the_takes_error(ns, qtmux, monkeypatch):
    def broken_replay(path, record=False):
        assert record is False  # never stacks iOS Screen Recording on top
        raise WDAError("tap missed")

    ns["_load_take"] = lambda path: {"steps": []}
    ns["_require_app"] = lambda take: None
    ns["replay_take"] = broken_replay
    monkeypatch.setattr(ns["time"], "sleep", lambda s: None)
    with pytest.raises(WDAError, match="tap missed"):
        ns["replay_take_qt"]("take.json")
    assert ("POST", "/record/stop") in qtmux.calls


def test_replay_returns_mp4_and_seconds(ns, qtmux, monkeypatch):
    ns["_load_take"] = lambda path: {"steps": []}
    ns["_require_app"] = lambda take: None
    ns["replay_take"] = lambda path, record=False: 12.5
    monkeypatch.setattr(ns["time"], "sleep", lambda s: None)
    mp4, secs = ns["replay_take_qt"]("take.json")
    assert mp4.endswith("x.mp4") and secs == 12.5
