"""After Effects scaffold (agent-workspace/ae_scaffold.py): the generated
ExtendScript, checked offline. No After Effects needed."""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "agent-workspace"))

import ae_scaffold as ae  # noqa: E402
import recording_plan as rp  # noqa: E402

BRIEF = (Path(__file__).parent / "fixtures" / "recording_planner" / "brief.md").read_text(encoding="utf-8")
TIMING = {"ios": (12.0, [(2.4, 2.8), (5.6, 6.0), (9.4, 9.8), (10.4, 10.7)]), "promo": (16.0, [])}


def _plan():
    return rp.build_plan(BRIEF, {}, timing=TIMING)


def _data(plan, clips=None):
    return ae.scaffold_data(plan, clips or {}, {"ios": r"C:\vo\ios.mp3"}, "C:/out/x.aep")


def test_handles_match_the_planner():
    assert ae.HANDLE_SECONDS == rp.HANDLE_SECONDS


def test_every_beat_is_placed_at_line_start_plus_earlier_beats():
    plan = _plan()
    data = _data(plan)
    ios = data["cuts"][0]
    assert (ios["w"], ios["h"], ios["fps"]) == (886, 1920, 30)
    assert data["cuts"][1]["w"] == 1080
    line3 = next(ln for ln in plan["cuts"][0]["lines"] if ln["number"] == 3)
    b1, b2 = [b for b in ios["beats"] if b["name"].startswith("ios L3 ")]
    assert b1["start"] == line3["start"]
    assert abs(b2["start"] - (line3["start"] + line3["beats"][0]["seconds"])) < 1e-6
    assert ios["vo"] == "C:/vo/ios.mp3"
    assert ios["duration"] >= ios["lines"][-1]["end"]


def test_app_beats_point_into_their_take():
    plan = _plan()
    chat = next(r for r in plan["recordings"] if "chat thread" in r["title"])
    data = _data(plan, {chat["id"]: "C:/takes/R.mp4"})
    ios_b = [b for b in data["cuts"][0]["beats"] if b["take"] == chat["id"]]
    promo_b = [b for b in data["cuts"][1]["beats"] if b["take"] == chat["id"]]
    # First moment at the handle, second after it; the promo reuses the same footage.
    assert [b["offset"] for b in ios_b] == [2.0, round(2.0 + chat["moments"][0]["seconds"], 3)]
    assert [b["offset"] for b in promo_b] == [b["offset"] for b in ios_b]
    assert all(b["kind"] == "app" for b in ios_b)


def test_missing_takes_live_action_and_graphics_get_placeholders():
    data = _data(_plan())
    kinds = {b["kind"] for b in data["cuts"][0]["beats"]}
    assert kinds == {"live", "graphic", "missing"}
    live = next(b for b in data["cuts"][0]["beats"] if b["kind"] == "live")
    assert live["label"].startswith("LIVE ACTION: ") and live["take"] is None


def test_script_guards_before_it_changes_anything_and_escapes_text():
    plan = _plan()
    plan["cuts"][0]["lines"][0]["featured"] = 'Say "hi"\nnew line \\ and ' + chr(0x2028) + " sep"
    js = ae.generate_jsx(_data(plan))
    guard = js.index("if (!D.allowExisting && !startedEmpty)")
    assert js.index("var startedEmpty = (proj.file === null && proj.numItems === 0);") < guard
    assert guard < js.index("proj.items.addFolder")
    assert guard < js.index("proj.importFile")
    data_line = js.splitlines()[0]
    # The raw line separator would end the JS line; it must arrive escaped.
    assert chr(0x2028) not in data_line and r"\u2028" in data_line
    assert json.loads(data_line[len("var D = "):-1])["cuts"][0]["lines"][0]["featured"].startswith('Say "hi"\n')
    # ES3 only.
    assert not re.search(r"\b(let|const)\b|=>|JSON\.", js)
    assert js.rstrip().endswith("return {ok: true, comps: made, missing: missing, saved: saved, note: note};")


def test_saves_only_a_project_it_started_empty_and_never_over_a_file():
    js = ae.generate_jsx(_data(_plan()))
    save = js.index("proj.save(target)")
    assert js.index("if (!startedEmpty || D.allowExisting)") < save
    assert js.index("} else if (target.exists) {") < save
    assert js.count("proj.save(") == 1


def test_legacy_plan_without_beat_ids_still_finds_reused_footage():
    plan = _plan()
    chat = next(r for r in plan["recordings"] if "chat thread" in r["title"])
    for r in plan["recordings"]:
        for m in r["moments"]:
            m.pop("also_beats", None)
    data = _data(plan, {chat["id"]: "C:/takes/R.mp4"})
    promo = [b for b in data["cuts"][1]["beats"] if b["take"] == chat["id"]]
    assert promo and promo[0]["offset"] == 2.0


def test_find_clips(tmp_path):
    (tmp_path / "r2.MOV").write_bytes(b"x")
    (tmp_path / "R3.mp4").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("x")
    found = ae.find_clips(str(tmp_path), ["R1", "R2", "R3"])
    assert sorted(found) == ["R2", "R3"]
    assert ae.find_clips(str(tmp_path / "nope"), ["R1"]) == {}
