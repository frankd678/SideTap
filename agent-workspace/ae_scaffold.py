"""After Effects scaffold: the recording plan, the takes and the voice-overs
laid out as one master comp per cut, ready to hand to an editor.

Per cut it builds a master comp with the approved voice-over at 0, a marker
per script line (number, voice, featured text), a guide text layer per line
showing the featured text, and a precomp for every beat placed at its exact
start: app beats hold their take, scaled to fit and offset so the right
moment of the take plays; live action, graphics and takes not recorded yet
get a labelled colour placeholder.

This module only WRITES an ExtendScript function body (pure, testable). It is
run inside After Effects through the AE MCP tool ``ae_run_script`` (``code`` =
the generated text). The script refuses to work in a project that is saved or
has anything in it, unless ``--allow-existing`` is given, in which case it only
adds a new folder and never saves over the open project.

Usage::

    python agent-workspace/ae_scaffold.py --plan C:/CaptureRig/clients/<slug>/recordings.json \
        --clips C:/CaptureRig/clients/<slug>/takes --vo ios=<ios-vo.mp3> --vo promo=<promo-vo.mp3> \
        --aep C:/CaptureRig/clients/<slug>/<slug>-scaffold.aep --jsx C:/CaptureRig/clients/<slug>/scaffold.jsx
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HANDLE_SECONDS = 2.0  # must match recording_plan.HANDLE_SECONDS
CLIP_EXTS = (".mp4", ".mov", ".m4v")
DEFAULT_SIZES = {"ios": (886, 1920, 30), "promo": (1080, 1920, 30), "google": (1080, 1920, 30)}
COLORS = {"live": [0.80, 0.33, 0.28], "graphic": [0.27, 0.45, 0.80], "missing": [0.45, 0.45, 0.45]}


def _legacy_also(plan: dict, moment: dict) -> list[str]:
    """Beat ids for an older plan that lists reuse only as "promo L7"."""
    out = []
    for tag in moment.get("also", []):
        cut_key, _, num = tag.partition(" L")
        for cut in plan["cuts"]:
            if cut["key"] != cut_key:
                continue
            for ln in cut["lines"]:
                if str(ln["number"]) != num:
                    continue
                apps = [b for b in ln["beats"] if b["kind"] == "app"]
                words = set(moment["text"].lower().split())
                if apps:
                    best = max(apps, key=lambda b: len(words & set(b["text"].lower().split())))
                    out.append(best["id"])
    return out


def _beat_sources(plan: dict) -> dict[str, dict]:
    """beat id -> the take and where in it this beat's moment starts."""
    out = {}
    for rec in plan["recordings"]:
        offset = HANDLE_SECONDS
        for m in rec["moments"]:
            reused = m["also_beats"] if "also_beats" in m else _legacy_also(plan, m)
            for bid in [m["beat"], *reused]:
                out[bid] = {"take": rec["id"], "title": rec["title"], "offset": round(offset, 3)}
            offset += m["seconds"]
    return out


def find_clips(clips_dir: str | None, take_ids: list[str]) -> dict[str, str]:
    """Take id -> clip file (R2.mp4, R2.mov ...). Missing takes are left out."""
    found = {}
    if not clips_dir or not Path(clips_dir).is_dir():
        return found
    files = {p.stem.upper(): p for p in Path(clips_dir).iterdir() if p.suffix.lower() in CLIP_EXTS}
    for tid in take_ids:
        if tid.upper() in files:
            found[tid] = str(files[tid.upper()].resolve()).replace("\\", "/")
    return found


def scaffold_data(plan: dict, clips: dict[str, str], vo: dict[str, str], aep: str | None,
                  sizes: dict[str, tuple[int, int, float]] | None = None, allow_existing: bool = False) -> dict:
    sizes = {**DEFAULT_SIZES, **(sizes or {})}
    sources = _beat_sources(plan)
    name = plan.get("app") or plan.get("client") or "Client"
    cuts = []
    for cut in plan["cuts"]:
        w, h, fps = sizes.get(cut["key"], (1080, 1920, 30))
        end = max([ln["end"] for ln in cut["lines"]] + [cut.get("vo_seconds") or 0])
        lines, beats = [], []
        for ln in cut["lines"]:
            lines.append({"n": ln["number"], "start": ln["start"], "end": ln["end"],
                          "voice": ln["voice"], "featured": ln["featured"]})
            t = ln["start"]
            for i, b in enumerate(ln["beats"], 1):
                dur = max(b["seconds"], 1.0 / fps)
                src = sources.get(b["id"]) if b["kind"] == "app" else None
                take = src["take"] if src else None
                if b["kind"] == "app":
                    label = f"{take or 'NO TAKE'}: {b['text']}"
                    kind = "app" if take in clips else "missing"
                    pname = f"{cut['key']} L{ln['number']} b{i} {take or '-'} {src['title'] if src else ''}".strip()
                else:
                    kind = b["kind"]
                    word = "LIVE ACTION" if kind == "live" else "GRAPHIC"
                    label = f"{word}: {b['text']}"
                    pname = f"{cut['key']} L{ln['number']} b{i} {word.lower()}"
                beats.append({
                    "name": pname[:200], "start": round(t, 3), "dur": round(dur, 3), "kind": kind,
                    "take": take if kind == "app" else None, "offset": src["offset"] if src else 0,
                    "label": label, "color": COLORS.get(kind, COLORS["missing"]),
                })
                t += b["seconds"]
        cuts.append({"key": cut["key"], "name": f"{name} - {cut['key']} master", "w": w, "h": h, "fps": fps,
                     "duration": round(end + 1.0, 3), "vo": vo.get(cut["key"], "").replace("\\", "/"),
                     "lines": lines, "beats": beats})
    return {"folder": f"Scaffold - {name}", "allowExisting": allow_existing, "aep": (aep or "").replace("\\", "/"),
            "clips": clips, "cuts": cuts}


_JSX = r"""
var D = __DATA__;
var proj = app.project;
// Never build into someone's open work: an empty, unsaved project only,
// unless the producer explicitly allowed adding a folder to this one.
var startedEmpty = (proj.file === null && proj.numItems === 0);
if (!D.allowExisting && !startedEmpty) {
    return {ok: false, error: "The open project is saved or not empty. Open a new empty project, or rerun with --allow-existing to add a folder to this one (it will not be saved over)."};
}
function imp(path, folder) {
    if (!path) { return null; }
    var f = new File(path);
    if (!f.exists) { return null; }
    var it = proj.importFile(new ImportOptions(f));
    it.parentFolder = folder;
    return it;
}
function styleText(layer, size, color, center) {
    var p = layer.property("ADBE Text Properties").property("ADBE Text Document");
    var td = p.value;
    td.fontSize = size;
    td.fillColor = color;
    td.applyFill = true;
    if (center) { td.justification = ParagraphJustification.CENTER_JUSTIFY; }
    p.setValue(td);
}
var root = proj.items.addFolder(D.folder);
var footF = proj.items.addFolder("Footage"); footF.parentFolder = root;
var preF = proj.items.addFolder("Beats"); preF.parentFolder = root;
var clips = {}, missing = [], made = [];
for (var k in D.clips) { if (D.clips.hasOwnProperty(k)) {
    clips[k] = imp(D.clips[k], footF);
    if (!clips[k]) { missing.push(k + " (file not found)"); }
} }
for (var c = 0; c < D.cuts.length; c++) {
    var cut = D.cuts[c];
    var comp = proj.items.addComp(cut.name, cut.w, cut.h, 1, cut.duration, cut.fps);
    comp.parentFolder = root;
    for (var b = 0; b < cut.beats.length; b++) {
        var bt = cut.beats[b];
        var pc = proj.items.addComp(bt.name, cut.w, cut.h, 1, bt.dur, cut.fps);
        pc.parentFolder = preF;
        var item = bt.take ? clips[bt.take] : null;
        if (item) {
            var L = pc.layers.add(item);
            var s = Math.min(cut.w / item.width, cut.h / item.height) * 100;
            L.property("ADBE Transform Group").property("ADBE Scale").setValue([s, s]);
            L.startTime = -bt.offset;
        } else {
            pc.layers.addSolid(bt.color, "placeholder", cut.w, cut.h, 1, bt.dur);
            var t = pc.layers.addBoxText([cut.w * 0.86, cut.h * 0.5], bt.label);
            styleText(t, Math.round(cut.w / 18), [1, 1, 1], true);
            t.property("ADBE Transform Group").property("ADBE Position").setValue([cut.w / 2, cut.h / 2]);
        }
        var PL = comp.layers.add(pc);
        PL.startTime = bt.start;
        PL.label = bt.take ? 9 : (bt.kind === "live" ? 1 : (bt.kind === "graphic" ? 8 : 0));
    }
    var lines = comp.layers.addNull(cut.duration);
    lines.name = "LINES (markers)";
    lines.guideLayer = true;
    for (var n = 0; n < cut.lines.length; n++) {
        var ln = cut.lines[n];
        var mv = new MarkerValue("L" + ln.n + ": " + ln.voice + "\r" + "Text: " + ln.featured);
        mv.duration = ln.end - ln.start;
        lines.property("ADBE Marker").setValueAtTime(ln.start, mv);
        var g = comp.layers.addBoxText([cut.w * 0.86, cut.h * 0.2], ln.featured);
        g.name = "L" + ln.n + " featured text";
        g.guideLayer = true;
        styleText(g, Math.round(cut.w / 16), [1, 1, 1], true);
        g.property("ADBE Transform Group").property("ADBE Position").setValue([cut.w / 2, cut.h * 0.18]);
        g.inPoint = ln.start;
        g.outPoint = ln.end;
    }
    var vo = imp(cut.vo, footF);
    if (vo) {
        var V = comp.layers.add(vo);
        V.startTime = 0;
        V.name = "VO";
    } else if (cut.vo) {
        missing.push(cut.key + " voice-over (file not found)");
    }
    made.push(cut.name);
}
var saved = "", note = "";
if (D.aep) {
    var target = new File(D.aep);
    if (!startedEmpty || D.allowExisting) {
        note = "Not saved: this project was already open; save it yourself.";
    } else if (target.exists) {
        note = "Not saved: " + D.aep + " already exists; save under a new name.";
    } else {
        proj.save(target);
        saved = D.aep;
    }
}
return {ok: true, comps: made, missing: missing, saved: saved, note: note};
"""


def generate_jsx(data: dict) -> str:
    """The ExtendScript function body. Data goes in as a JSON literal, which is
    valid ES3 and escapes quotes, backslashes, newlines and U+2028/9."""
    literal = json.dumps(data, ensure_ascii=True)
    return _JSX.replace("__DATA__", literal).strip() + "\n"


def _size(s: str) -> tuple[str, tuple[int, int, float]]:
    m = re.fullmatch(r"(\w+)=(\d+)x(\d+)(?:@(\d+(?:\.\d+)?))?", s)
    if not m:
        raise SystemExit(f"--size wants cut=WxH[@fps], got {s!r}")
    return m.group(1), (int(m.group(2)), int(m.group(3)), float(m.group(4) or 30))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write the After Effects scaffold script for a recording plan.")
    ap.add_argument("--plan", required=True, help="recordings.json from recording_plan.py")
    ap.add_argument("--clips", help="folder with one clip per take: R1.mp4, R2.mov ...")
    ap.add_argument("--vo", action="append", default=[], help="cut=path.mp3")
    ap.add_argument("--aep", help="save the new project here")
    ap.add_argument("--jsx", required=True, help="where to write the script")
    ap.add_argument("--size", action="append", default=[], help="cut=WxH@fps, e.g. ios=886x1920@30")
    ap.add_argument("--allow-existing", "--allow-new-folder", dest="allow_existing", action="store_true",
                    help="add a folder to the open project instead of requiring an empty one (never saves it)")
    a = ap.parse_args(argv)
    plan = json.loads(Path(a.plan).read_text(encoding="utf-8"))
    vo = {}
    for item in a.vo:
        k, _, v = item.partition("=")
        vo[k] = v
    clips = find_clips(a.clips, [r["id"] for r in plan["recordings"]])
    data = scaffold_data(plan, clips, vo, a.aep, dict(_size(s) for s in a.size), a.allow_existing)
    Path(a.jsx).write_text(generate_jsx(data), encoding="utf-8")
    takes = [r["id"] for r in plan["recordings"]]
    print(f"{a.jsx}: {len(data['cuts'])} master comp(s); clips for {sorted(clips) or 'none'}; "
          f"placeholders for {[t for t in takes if t not in clips] or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
