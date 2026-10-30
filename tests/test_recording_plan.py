"""Recording planner (agent-workspace/recording_plan.py): brief -> recordings.
Offline: a made-up brief, scripted VO timing, no ffmpeg, no phone."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "agent-workspace"))

import recording_plan as rp  # noqa: E402

BRIEF = (Path(__file__).parent / "fixtures" / "recording_planner" / "brief.md").read_text(encoding="utf-8")
# ios VO 12 s with a pause inside line 4 ("Plantpal." ... "Happy plants") as a decoy.
TIMING = {
    "ios": (12.0, [(2.4, 2.8), (5.6, 6.0), (9.4, 9.8), (10.4, 10.7)]),
    "promo": (16.0, []),  # no pauses detected: word-proportional
}


@pytest.fixture
def plan():
    return rp.build_plan(BRIEF, {}, timing=TIMING)


def test_parses_both_cuts_and_every_line(plan):
    cuts = {c["key"]: c for c in plan["cuts"]}
    assert set(cuts) == {"ios", "promo"}
    assert [ln["number"] for ln in cuts["ios"]["lines"]] == [1, 2, 3, 4]
    assert len(cuts["promo"]["lines"]) == 5
    assert cuts["ios"]["slot_seconds"] == 30
    assert cuts["ios"]["lines"][2]["voice"] == "Snap a leaf and an expert tells you what it needs."
    assert not cuts["ios"]["warnings"] and not cuts["promo"]["warnings"]
    assert plan["app"] == "Plantpal"
    assert len(plan["rules"]) == 4


def test_classifies_live_action_graphics_and_app(plan):
    ios = {ln["number"]: ln for ln in plan["cuts"][0]["lines"]}
    assert [b["kind"] for b in ios[1]["beats"]] == ["live"]
    assert [b["kind"] for b in ios[2]["beats"]] == ["graphic", "app"]  # logo hands off into the app
    assert [b["kind"] for b in ios[4]["beats"]] == ["graphic"]
    # "Follower counters framed out." is a note on the shot, not a moment.
    assert ios[2]["beats"][1]["notes"] == ["Follower counters framed out."]


def test_screen_names(plan):
    assert rp.screen_names("then a tap into a plant profile showing the photo") == ["plant profile"]
    assert rp.screen_names("The Book a Call screen with the call options") == ["Book a Call screen"]
    assert rp.screen_names("The same message thread continuing") == ["message thread"]


def test_pause_inside_a_line_is_not_a_line_break(plan):
    ios = plan["cuts"][0]["lines"]
    assert [ln["timing"] for ln in ios] == ["silence"] * 4
    # Three breaks chosen from four pauses; the 10.55 s pause is inside line 4.
    assert [round(ln["end"], 2) for ln in ios] == [2.6, 5.8, 9.6, 12.0]


def test_word_timing_fallback_and_speaking_rate():
    lines = rp.parse_brief(BRIEF).cuts[1].lines
    assert rp.time_lines(lines, 16.0, []) == "words"
    assert lines[-1].end == 16.0
    assert rp.time_lines(lines, None, []) == "speaking-rate"


def test_recordings_group_screens_across_cuts(plan):
    recs = plan["recordings"]
    titles = [r["title"] for r in recs]
    assert titles[0].startswith("Garden screen")
    assert any("chat thread" in t for t in titles)
    assert any("Book a Call" in t for t in titles)
    chat = next(r for r in recs if "chat thread" in r["title"])
    # The composer belongs to the thread, and the identical promo moment is recorded once.
    assert "ios L3" in chat["serves"] and "promo L3" in chat["serves"]
    assert len(chat["moments"]) == 2
    assert chat["moments"][0]["also"] == ["promo L3"]
    assert any("never sends" in p for p in chat["prep"])
    for r in recs:
        assert r["take_seconds"] == pytest.approx(r["content_seconds"] + 2 * rp.HANDLE_SECONDS, abs=0.11)


def test_every_app_beat_lands_in_a_recording(plan):
    used = set()
    for r in plan["recordings"]:
        for m in r["moments"]:
            used.add((m["cut"], m["line"]))
            used.update(tuple(a.split(" L")) for a in [])
            for a in m["also"]:
                cut, line = a.split(" L")
                used.add((cut, int(line)))
    for c in plan["cuts"]:
        for ln in c["lines"]:
            if any(b["kind"] == "app" for b in ln["beats"]):
                assert (c["key"], ln["number"]) in used


def test_rules_attach_to_the_right_takes(plan):
    rules = {r["id"]: r for r in plan["rules"]}
    book = next(r for r in plan["recordings"] if "Book a Call" in r["title"])
    garden = plan["recordings"][0]
    tier = next(i for i, r in rules.items() if "Pro-tier" in r["text"])
    ratings = next(i for i, r in rules.items() if "ratings" in r["text"])
    assert tier in book["rules"] and tier not in garden["rules"]
    assert ratings in garden["rules"]
    assert rules[next(i for i, r in rules.items() if "em dash" in r["text"])]["kind"] == "copy"


UI_MAP = {
    "stopped": None,
    "screens": [
        {"id": "S01", "title": "My jungle", "headers": ["My jungle"], "tabs": ["Garden", "Chat"],
         "texts": ["My jungle", "Fern", "Lorem ipsum dolor", "12 followers", "Garden", "Chat"],
         "route": ["Garden"], "screenshot": "S01.png",
         "elements": [
             {"type": "StaticText", "text": "Lorem ipsum dolor", "rect": {}},
             {"type": "StaticText", "text": "12 followers", "rect": {}},
             {"type": "Image", "text": "", "name": "person.crop.circle", "rect": {}},
             {"type": "Button", "text": "Add plant", "rect": {}},
         ]},
        {"id": "S02", "title": "Expert chat", "headers": ["Expert chat"], "tabs": ["Garden", "Chat"],
         "texts": ["Expert chat", "No messages yet"], "route": ["Chat", "Expert chat"], "screenshot": "S02.png",
         "elements": [
             {"type": "StaticText", "text": "No messages yet", "rect": {}},
             {"type": "TextField", "text": "Message", "value": "", "placeholder": "Message", "rect": {}},
             {"type": "StaticText", "text": "$4.99/mo", "rect": {}},
         ]},
    ],
}


def test_ui_map_matching_routes_and_placeholders():
    plan = rp.build_plan(BRIEF, {}, ui_map=UI_MAP, timing=TIMING)
    garden = plan["recordings"][0]
    assert garden["matched_screens"]['"My jungle"']["status"] == "matched"
    assert garden["route"] == ["Garden"]
    book = next(r for r in plan["recordings"] if "Book a Call" in r["title"])
    assert book["matched_screens"]["Book a Call screen"]["status"] == "unmatched"
    cats = {(f["screen"], f["category"]) for f in plan["placeholders"]}
    assert ("S01", "dummy text") in cats
    assert ("S01", "rating or count") in cats
    assert ("S01", "default avatar") in cats
    assert ("S02", "empty state") in cats
    assert ("S02", "empty field") in cats
    assert ("S02", "price") in cats
    # "Add plant" is a button, not an unfilled prompt.
    assert not any(f["text"] == "Add plant" for f in plan["placeholders"])
    lorem = next(f for f in plan["placeholders"] if f["category"] == "dummy text")
    # Both Garden takes (iOS and the promo's held shot) use that screen.
    assert garden["id"] in lorem["recordings"]
    promo_garden = next(r for r in plan["recordings"] if r["serves"] == ["promo L2"])
    assert promo_garden["same_screen_as"] == garden["id"]
    assert promo_garden["id"] in lorem["recordings"]
    md = rp.render_placeholders_md(plan)
    assert "Lorem ipsum" in md and "used by R1" in md


def test_markdown_and_files(tmp_path, plan):
    md = rp.render_plan_md(plan)
    assert "No UI map yet" in md
    assert "## Not recorded on the phone" in md
    assert "No recording starts until the client gives the go." in md
    written = rp.write_outputs(plan, tmp_path)
    assert sorted(p.name for p in written) == ["placeholders.md", "recording-plan.md", "recordings.json"]
    assert json.loads((tmp_path / "recordings.json").read_text(encoding="utf-8"))["version"] == rp.PLAN_VERSION
    assert not list(tmp_path.glob("*.tmp"))


def test_parse_silences():
    log = "silence_start: 2.9\nsilence_end: 3.3 | silence_duration: 0.4\nsilence_start: 7.0\nsilence_end: 7.4"
    assert rp.parse_silences(log) == [(2.9, 3.3), (7.0, 7.4)]


def test_missing_fields_are_reported():
    broken = BRIEF.replace("- Featured Text: Ask an expert\n", "", 1)
    cut = rp.parse_brief(broken).cuts[0]
    assert any("Line 3 has no Featured Text" in w for w in cut.warnings)


def test_identical_runs_share_one_take_and_variants_do_not(plan):
    chat = [r for r in plan["recordings"] if "chat thread" in r["title"]]
    assert len(chat) == 1  # iOS L3 and promo L3 are word for word the same
    assert chat[0]["serves"] == ["ios L3", "promo L3"]
    # The promo asks for a different photo and a different reply: its own take.
    head, promo = BRIEF.split("## Promo", 1)
    promo = promo.replace("A leaf photo is attached from the composer and sent, and a written reply from the named "
                          "expert arrives with one fix.",
                          "A photo of a whole shelf is attached from the composer and sent, and the expert asks a "
                          "question back.")
    other = rp.build_plan(head + "## Promo" + promo, {}, timing=TIMING)
    assert len([r for r in other["recordings"] if "chat thread" in r["title"]]) == 2


def test_ratings_rule_applies_to_every_take(plan):
    ratings = next(r["id"] for r in plan["rules"] if "ratings" in r["text"])
    assert all(ratings in r["rules"] for r in plan["recordings"])


def test_beat_time_follows_what_the_sentence_describes(plan):
    ios3 = plan["cuts"][0]["lines"][2]
    short, long_ = ios3["beats"]
    assert short["text"] == "The expert chat thread."
    assert long_["seconds"] > 2 * short["seconds"]


def test_placeholder_edge_cases():
    c = rp._classify_element
    assert c({"type": "StaticText", "text": "Coach bio", "value": "Lorem ipsum dolor"}) == ("dummy text", "high")
    assert c({"type": "StaticText", "text": "Name"}) is None
    assert c({"type": "StaticText", "text": "Bio"}) is None
    assert c({"type": "StaticText", "text": "0 dollars"}) is None
    assert c({"type": "StaticText", "text": "0 messages"}) == ("zero counter", "high")
    found = rp.find_placeholders({"screens": [{"id": "S1", "elements": [
        {"type": "StaticText", "text": "Ratings"}, {"type": "StaticText", "text": "4.8"},
    ]}]})
    assert [(f["text"], f["category"]) for f in found] == [("4.8", "rating or count")]


def test_equal_screens_are_ambiguous():
    twins = {"screens": [
        {"id": "S1", "headers": ["My jungle"], "texts": ["My jungle"], "route": ["Garden"]},
        {"id": "S2", "headers": ["My jungle"], "texts": ["My jungle"], "route": ["Chat", "x"]},
    ]}
    plan = rp.build_plan(BRIEF, {}, ui_map=twins, timing=TIMING)
    m = plan["recordings"][0]["matched_screens"]['"My jungle"']
    assert m["status"] == "ambiguous" and m["candidates"] == ["S1", "S2"]


REAL = __import__("os").environ.get("CAPTURERIG_REAL_BRIEF")


@pytest.mark.skipif(not REAL, reason="set CAPTURERIG_REAL_BRIEF to a real client brief.md (kept out of this public repo)")
def test_real_brief_every_app_line_has_a_take():
    plan = rp.build_plan(Path(REAL).read_text(encoding="utf-8"), {})
    covered = set()
    for r in plan["recordings"]:
        for m in r["moments"]:
            covered.add(f"{m['cut']} L{m['line']}")
            covered.update(m["also"])
    for c in plan["cuts"]:
        assert c["lines"] and not [w for w in c["warnings"] if "speaking-rate" not in w]
        for ln in c["lines"]:
            if any(b["kind"] == "app" for b in ln["beats"]):
                assert f"{c['key']} L{ln['number']}" in covered


def test_photo_variants_never_merge():
    a = rp.Beat("a", "ios", 1, "app", "A red jacket photo is attached from the composer and sent.", manual=["sent"])
    b = rp.Beat("b", "promo", 1, "app", "A blue jacket photo is attached from the composer and sent.", manual=["sent"])
    assert not rp._twin_runs([a], [b])
    c = rp.Beat("c", "promo", 1, "app", "A red jacket photo is attached from the composer and sent.", manual=["sent"])
    assert rp._twin_runs([a], [c])


def test_ambiguous_match_has_no_route_and_is_flagged():
    twins = {"screens": [
        {"id": "S1", "headers": ["My jungle"], "texts": ["My jungle"], "route": ["Garden"]},
        {"id": "S2", "headers": ["My jungle"], "texts": ["My jungle"], "route": ["Chat", "x"]},
    ]}
    plan = rp.build_plan(BRIEF, {}, ui_map=twins, timing=TIMING)
    md = rp.render_plan_md(plan)
    assert "More than one screen fits" in md and "S1 or S2" in md


def test_accounts_group_takes_and_hold_no_credentials(tmp_path):
    base = rp.build_plan(BRIEF, {}, timing=TIMING)
    chat = next(r["id"] for r in base["recordings"] if "chat thread" in r["title"])
    accounts = [{"label": "Member view", "screens": ["Garden screen"]},
                {"label": "Expert view", "takes": [chat]}]
    plan = rp.build_plan(BRIEF, {}, timing=TIMING, accounts=accounts)
    by = {r["id"]: r["account"] for r in plan["recordings"]}
    assert by[chat] == "Expert view"
    assert by[plan["recordings"][0]["id"]] == "Member view"
    md = rp.render_plan_md(plan)
    assert "**Sign-in 1: Member view.**" in md and "**Sign-in 2: Expert view.**" in md
    assert md.index("Sign-in 1") < md.index("Sign-in 2")
    # A take nothing claims goes to the first account, with a warning.
    assert any("no account" in w for w in plan["warnings"])
    bad = tmp_path / "accounts.json"
    bad.write_text('{"accounts": [{"label": "x", "password": "hunter2"}]}', encoding="utf-8")
    with pytest.raises(ValueError, match="credentials"):
        rp.load_accounts(str(bad))
    with pytest.raises(ValueError, match="two accounts"):
        rp.build_plan(BRIEF, {}, timing=TIMING, accounts=[{"label": "a", "takes": [chat]}, {"label": "b", "takes": [chat]}])


def test_reused_moments_name_their_beats(plan):
    chat = next(r for r in plan["recordings"] if "chat thread" in r["title"])
    assert chat["moments"][0]["also_beats"] == ["promo-L3-b1"]


def test_account_labels_must_be_plain_and_unique(tmp_path):
    f = tmp_path / "accounts.json"
    for body, msg in (('{"accounts": [{"label": {"password": "x"}}]}', "plain text"),
                      ('{"accounts": [{"label": "a"}, {"label": "a"}]}', "twice"),
                      ('{"accounts": [{"label": "a", "takes": "R1"}]}', "list of text")):
        f.write_text(body, encoding="utf-8")
        with pytest.raises(ValueError, match=msg):
            rp.load_accounts(str(f))


def test_back_to_back_only_within_one_account():
    base = rp.build_plan(BRIEF, {}, timing=TIMING)
    garden = [r["id"] for r in base["recordings"] if r["title"].startswith("Garden")]
    assert len(garden) == 2
    plan = rp.build_plan(BRIEF, {}, timing=TIMING, accounts=[{"label": "A", "takes": [garden[0]]},
                                                               {"label": "B", "takes": [garden[1]]}])
    second = next(r for r in plan["recordings"] if r["id"] == garden[1])
    assert second["same_screen_as"] is None


def test_every_sign_in_checkpoint_has_its_command(plan):
    md = rp.render_plan_md(plan)
    assert "**Sign-in 1: the recording account.**" in md
    assert '--account "default" --wait-for-login 15' in md
