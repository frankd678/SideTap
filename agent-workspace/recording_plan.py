"""Recording planner: turn a client's approved brief into a list of recordings.

Reads a client delivery ``brief.md`` (framework, creative rules, and one
section per cut with numbered Line blocks), times every line against the
approved voice-over, and groups the app-footage beats into recordings: what
screen, what happens on it, how long the take must be, which rules apply, and
which cut and line it feeds. With a UI map from ``ui_map.py`` it also says how
to reach each screen and lists placeholder text and empty elements to fix
before recording.

Everything here is offline: no phone, no WDA. Outputs go to
``C:/CaptureRig/clients/<slug>/`` by default:

- ``recording-plan.md``  the producer's checklist (the product)
- ``placeholders.md``    what to fix in the app before recording
- ``recordings.json``    the same data, structured

Usage::

    python agent-workspace/recording_plan.py plan --brief <brief.md> \
        --vo ios=<ios-vo.mp3> --vo promo=<promo-vo.mp3> [--ui-map <ui-map.json>]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

PLAN_VERSION = 1
DEFAULT_OUT = Path("C:/CaptureRig/clients")
HANDLE_SECONDS = 2.0

# --------------------------------------------------------------------------
# Data


@dataclass
class Beat:
    """One sentence-sized piece of a line's Visuals, of a single kind."""

    id: str
    cut: str
    line: int
    kind: str  # "app" | "live" | "graphic"
    text: str
    screens: list[str] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)  # quoted on-screen text
    manual: list[str] = field(default_factory=list)  # needs real content/people
    notes: list[str] = field(default_factory=list)  # framing notes from the brief
    seconds: float = 0.0


@dataclass
class Line:
    cut: str
    number: int
    voice: str
    visuals: str
    featured: str
    beats: list[Beat] = field(default_factory=list)
    start: float = 0.0
    end: float = 0.0
    timing: str = "unknown"  # "silence" | "words" | "speaking-rate"

    @property
    def seconds(self) -> float:
        return round(self.end - self.start, 2)

    @property
    def kinds(self) -> list[str]:
        return sorted({b.kind for b in self.beats})


@dataclass
class Cut:
    key: str
    title: str
    slot_seconds: float | None
    lines: list[Line] = field(default_factory=list)
    vo_seconds: float | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class Brief:
    client: str
    app: str
    app_url: str
    cuts: list[Cut]
    framework: dict[str, str]
    rules: list[str]
    notes: str
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Parsing


def _cut_key(title: str) -> str:
    t = title.lower()
    if "app store" in t or "apple" in t or "ios" in t:
        return "ios"
    if "google" in t or "play" in t:
        return "google"
    if "promo" in t:
        return "promo"
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-") or "cut"


def parse_brief(text: str) -> Brief:
    """Parse a brief.md. Tolerant of extra sections; strict about Line blocks."""
    text = text.replace("\r\n", "\n").lstrip("\ufeff")
    head = {}
    for m in re.finditer(r"^(Client|App|Videos|Account):\s*(.+)$", text, re.M):
        head.setdefault(m.group(1).lower(), m.group(2).strip())
    app_field = head.get("app", "")
    url = re.search(r"https?://\S+", app_field)
    app_name = app_field.split(" — ")[0].split(" - ")[0].strip() if app_field else ""

    # Split into "## " sections, then "### " subsections.
    sections = re.split(r"^## +", text, flags=re.M)
    framework: dict[str, str] = {}
    rules: list[str] = []
    cuts: list[Cut] = []
    notes = ""
    for sec in sections[1:]:
        title, _, body = sec.partition("\n")
        title = title.strip()
        if title.lower().startswith("narrative conversion framework"):
            for sub in re.split(r"^### +", body, flags=re.M)[1:]:
                stitle, _, sbody = sub.partition("\n")
                stitle = stitle.strip()
                if stitle.lower() == "creative rules":
                    rules = [r.strip() for r in re.findall(r"^- +(.+)$", sbody, re.M)]
                else:
                    framework[stitle] = sbody.strip()
            continue
        slot = re.search(r"\((\d+)\s*s\)", title)
        if not re.search(r"^Line +\d+\s*$", body, re.M):
            continue
        cut = Cut(key=_cut_key(title), title=title, slot_seconds=float(slot.group(1)) if slot else None)
        nm = re.search(r"^Notes:\s*(.+)$", body, re.M)
        if nm:
            notes = nm.group(1).strip()
        blocks = re.split(r"^Line +(\d+)\s*$", body, flags=re.M)
        seen = set()
        for num, block in zip(blocks[1::2], blocks[2::2]):
            n = int(num)
            if n in seen:
                cut.warnings.append(f"Line {n} appears twice.")
            seen.add(n)
            fields = {}
            for key, label in (("voice", "Voice"), ("visuals", "Visuals"), ("featured", "Featured Text")):
                m = re.search(rf"^- *{label}:\s*(.*?)(?=^- *\w[\w ]*:|^\S|\Z)", block, re.M | re.S)
                fields[key] = " ".join(m.group(1).split()) if m else ""
                if not m:
                    cut.warnings.append(f"Line {n} has no {label}.")
            # Notes paragraph can trail the last Line block.
            fields["featured"] = re.split(r"\s+Notes:", fields["featured"])[0]
            cut.lines.append(Line(cut=cut.key, number=n, **fields))
        nums = [ln.number for ln in cut.lines]
        if nums != list(range(1, len(nums) + 1)):
            cut.warnings.append(f"Line numbers are not 1..{len(nums)}: {nums}.")
        narr = re.search(r"Narration, in order\s*\n(.*?)(?:\n\s*\n|\Z)", body, re.S)
        if narr:
            spoken = [re.sub(r"^\d+\.\s*", "", s).strip() for s in narr.group(1).strip().splitlines() if s.strip()]
            for ln, said in zip(cut.lines, spoken):
                if _norm(ln.voice) != _norm(said):
                    cut.warnings.append(f"Line {ln.number}: narration list and layout disagree.")
            if len(spoken) != len(cut.lines):
                cut.warnings.append(f"Narration lists {len(spoken)} lines, layout has {len(cut.lines)}.")
        cuts.append(cut)
    brief = Brief(
        client=head.get("client", ""),
        app=app_name,
        app_url=url.group(0) if url else "",
        cuts=cuts,
        framework=framework,
        rules=rules,
        notes=notes,
    )
    if not cuts:
        brief.warnings.append("No cut with Line blocks was found in the brief.")
    for cut in cuts:
        for ln in cut.lines:
            ln.beats = split_beats(cut.key, ln.number, ln.visuals)
    return brief


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


# --------------------------------------------------------------------------
# Classifying visuals into beats

# Phrases that hand one beat to the next inside a sentence.
_HANDOFF = re.compile(
    r",?\s+(?:and\s+)?(?:then\s+)?(?:hands off into|transitions into|then a cut to|then cuts? to)\s+",
    re.I,
)
_NO_APP = re.compile(r"\bno (?:app ui|device|recognisable interface|recognizable interface)\b|\bstill no app ui\b", re.I)
_GRAPHIC = re.compile(
    r"\b(logo|brand field|tagline|typographic|disclosure line|overlay|end card|app store action button|lockup)\b", re.I
)
_LIVE = re.compile(
    r"\b(a man|a woman|the woman|the man|her |his |she |he |mirror moments?|closet|doorway|on her way out|"
    r"live[- ]action|trying on|stock footage)\b",
    re.I,
)
_APP = re.compile(
    r"\b(screen|thread|composer|profile|tap(?:s|ped)?|scroll(?:s|ing)?|button|header|row|cards?|list|grid|"
    r"feed|tab|app footage|send(?: button)?|reply|message)\b",
    re.I,
)
_SCREEN = re.compile(
    r"\b((?:[\w']+\s+){0,4})(screen|thread|profile|list|page|feed|sheet|tab|composer)\b"
)
_QUOTED = re.compile(r"[\"“]([^\"”]{3,60})[\"”]")
_MANUAL = re.compile(
    r"\b(attached|attach|sent|send|reply (?:arrives|from)|written reply|answering|replying|question|"
    r"selected|send request|request)\b",
    re.I,
)
_SKIP_WORDS = {"the", "a", "an", "same", "its", "their", "his", "her", "into", "of", "to", "on", "in", "with",
               "and", "then", "first", "plain", "dark", "brief", "app", "showing", "scrolling", "through", "back",
               "tap", "taps", "tapped", "open", "opens", "cut", "from", "onto", "at", "is", "are", "held", "as"}


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text.strip())
    out = []
    for p in parts:
        out.extend(s.strip(" ,") for s in _HANDOFF.split(p) if s.strip(" ,"))
    return out


def classify_sentence(s: str) -> str:
    if _NO_APP.search(s):
        # "No app UI" sentences are live action unless they are only a graphic.
        return "graphic" if _GRAPHIC.search(s) and not _LIVE.search(s) else "live"
    app = bool(_APP.search(s))
    if _GRAPHIC.search(s) and not re.search(r"\b(screen|thread|profile|list)\b", s, re.I):
        return "graphic"
    if app:
        return "app"
    if _LIVE.search(s):
        return "live"
    return "graphic"


def screen_names(s: str) -> list[str]:
    """Screen names a sentence asks for, e.g. 'Discover screen', 'coach profile'."""
    names = []
    for m in _SCREEN.finditer(s):
        before = m.group(1).split()
        # Walk left from the noun and stop at the first article or verb, so
        # "then a tap into a coach profile" gives "coach profile". A small word
        # between two capitalised ones belongs to a name ("Request a Service").
        words: list[str] = []
        i = len(before) - 1
        while i >= 0 and len(words) < 3:
            w = before[i]
            low = re.sub(r"'s$", "", w.lower())
            if low in _SKIP_WORDS:
                if low in ("a", "of", "the") and words and words[0][:1].isupper() and i > 0 and before[i - 1][:1].isupper() \
                        and before[i - 1].lower() not in _SKIP_WORDS:
                    words.insert(0, w)
                    i -= 1
                    continue
                break
            words.insert(0, re.sub(r"'s$", "", w))
            i -= 1
        noun = m.group(2)
        if noun.lower() in ("tab", "page") and not words:
            continue
        names.append(" ".join(words + [noun]))
    out = []
    for n in names:
        if n.lower() not in [o.lower() for o in out]:
            out.append(n)
    return out


def split_beats(cut: str, line: int, visuals: str) -> list[Beat]:
    beats = []
    for i, s in enumerate(_sentences(visuals), 1):
        kind = classify_sentence(s)
        b = Beat(id=f"{cut}-L{line}-b{i}", cut=cut, line=line, kind=kind, text=s)
        if kind == "app":
            b.screens = screen_names(s)
            b.headers = _QUOTED.findall(s)
            b.manual = sorted({m.group(1).lower() for m in _MANUAL.finditer(s)})
        beats.append(b)
    if not beats:
        beats.append(Beat(id=f"{cut}-L{line}-b1", cut=cut, line=line, kind="graphic", text="(no visuals given)"))
    # A sentence that only qualifies the shot before it ("Rating counters framed
    # out.", "The Gold tier named in the same frame") is a note on that beat,
    # not a moment of its own: it takes no time.
    merged: list[Beat] = []
    for b in beats:
        prev = merged[-1] if merged else None
        if prev and prev.kind == "app" and not b.screens and _FRAMING.search(b.text):
            prev.notes.append(b.text)
            continue
        if prev and prev.kind != "app" and _NO_APP.fullmatch(b.text.strip(" .")) is None                 and _NO_APP.search(b.text) and len(b.text.split()) <= 8:
            prev.notes.append(b.text)
            continue
        if prev and b.kind == "app" and not b.screens:
            b.screens = list(prev.screens)
        merged.append(b)
    for i, b in enumerate(merged, 1):
        b.id = f"{cut}-L{line}-b{i}"
    return merged


_FRAMING = re.compile(r"\bframed out\b|\bin the same frame\b|\bin view\b|\bcounters?\b|\bkept out of frame\b", re.I)


# --------------------------------------------------------------------------
# Timing


def vo_duration(path: str) -> float | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=60,
        )
        return float(out.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def vo_silences(path: str, noise: str = "-35dB", min_pause: float = 0.25) -> list[tuple[float, float]]:
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af", f"silencedetect=noise={noise}:d={min_pause}",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_silences(out.stderr)


def parse_silences(log: str) -> list[tuple[float, float]]:
    starts = [float(x) for x in re.findall(r"silence_start:\s*([\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([\d.]+)", log)]
    return list(zip(starts, ends))


def _words(s: str) -> int:
    return max(1, len(re.findall(r"[\w']+", s)))


# Speaking "units" for estimating where a break falls: a word is 1, and the
# pause a reader leaves at punctuation is worth a bit more than a word.
_UNIT_STOP, _UNIT_COMMA, _UNIT_LINE = 1.5, 0.7, 1.5
# Cost of leaving a detected pause unexplained (seconds squared): small enough
# that a pause far from any break is ignored, big enough that a pause at a
# full stop inside a line is used by that full stop rather than left over.
_SKIP_PAUSE = 0.15


def _breaks(lines: list[Line]) -> tuple[list[tuple[float, bool]], float]:
    """Every place a reader may pause, in speaking units: (position, is_line_break)."""
    pos, out = 0.0, []
    for i, ln in enumerate(lines):
        toks = ln.voice.split()
        for k, tok in enumerate(toks):
            pos += 1
            if k == len(toks) - 1:
                break  # the line's own end is the line break below
            if tok.endswith((".", "!", "?")):
                out.append((pos + _UNIT_STOP / 2, False))
                pos += _UNIT_STOP
            elif tok.endswith((",", ";", ":")):
                out.append((pos + _UNIT_COMMA / 2, False))
                pos += _UNIT_COMMA
        if i < len(lines) - 1:
            out.append((pos + _UNIT_LINE / 2, True))
            pos += _UNIT_LINE
    return out, pos


def time_lines(lines: list[Line], total: float | None, silences: list[tuple[float, float]]) -> str:
    """Give every line a start and end; returns the method used.

    Punctuation gives each line break (and each pause inside a line) an
    expected time. The detected pauses are then matched to those breaks in
    order (dynamic programming): line breaks must take a pause, a full stop or
    comma inside a line may, so "Brand. Your tagline here" keeps its
    inner pause instead of stealing the line break's.
    """
    if not lines:
        return "none"
    method = "words"
    breaks, units = _breaks(lines)
    if not total:
        total = sum(_words(ln.voice) for ln in lines) / 2.6  # ~156 wpm
        method = "speaking-rate"
    expected = [(total * p / units, must) for p, must in breaks]
    bounds = [t for t, must in expected if must]
    mids = [(a + b) / 2 for a, b in silences if 0 < (a + b) / 2 < total]
    if bounds and method == "words" and len(mids) >= len(bounds):
        chosen = _match_pauses(expected, mids, total)
        if chosen is not None:
            bounds, method = chosen, "silence"
    edges = [0.0] + bounds + [total]
    for ln, a, b in zip(lines, edges, edges[1:]):
        ln.start, ln.end, ln.timing = round(a, 2), round(b, 2), method
    return method


def _match_pauses(expected: list[tuple[float, bool]], mids: list[float], total: float) -> list[float] | None:
    """Ordered matching of breaks to pauses; returns the line-break times."""
    n, m = len(expected), len(mids)
    INF = float("inf")
    cost = [[INF] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple[int, int, int] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            c = cost[i][j]
            if c == INF:
                continue
            if j < m and c + _SKIP_PAUSE < cost[i][j + 1]:  # pause j left unexplained
                cost[i][j + 1], back[i][j + 1] = c + _SKIP_PAUSE, (i, j, -1)
            if i < n and not expected[i][1] and c < cost[i + 1][j]:  # optional break, no pause
                cost[i + 1][j], back[i + 1][j] = c, (i, j, -1)
            if i < n and j < m:  # break i takes pause j
                d = c + (mids[j] - expected[i][0]) ** 2
                if d < cost[i + 1][j + 1]:
                    cost[i + 1][j + 1], back[i + 1][j + 1] = d, (i, j, j)
    if cost[n][m] == INF:
        return None
    taken: dict[int, float] = {}
    i, j = n, m
    while (i, j) != (0, 0):
        pi, pj, used = back[i][j]
        if used >= 0:
            taken[pi] = mids[used]
        i, j = pi, pj
    lines_at = [taken[k] for k, (_, must) in enumerate(expected) if must]
    # Reject a fit where a line break lands far from where the words put it.
    drift = max(abs(taken[k] - t) for k, (t, must) in enumerate(expected) if must)
    if drift > total / (len(lines_at) + 1) * 0.6:
        return None
    return lines_at


def time_beats(line: Line) -> None:
    """Split a line's time across its beats, by how much each one describes
    (a bare "The coach message thread." is a short establishing shot; attaching
    a photo, sending it and getting a reply is most of the line). Graphics get
    a short share. These are estimates for the edit, not measurements."""
    weights = [(0.5 if b.kind == "graphic" else 1.0) * max(3, _words(b.text)) ** 0.7 for b in line.beats]
    tot = sum(weights) or 1
    for b, w in zip(line.beats, weights):
        b.seconds = round(line.seconds * w / tot, 2)


# --------------------------------------------------------------------------
# Rules


@dataclass
class Rule:
    id: str
    text: str
    kind: str  # "frame" (check on screen) | "copy" (edit/VO) | "casting" | "producer"
    applies_to: list[str]  # keywords that make it relevant to a recording; [] = every recording


_RULE_TOPICS = [
    (r"rating|star count|review|subscriber|leaderboard|ranking|counter", "frame", []),
    (r"price", "frame", []),
    (r"messag", "frame", ["thread", "message", "composer"]),
    (r"competitor|rival", "frame", []),
    (r"both audiences|men and for women|coaches for men", "frame", ["discover", "row"]),
    (r"proper product names|appear as written", "copy", []),
    (r"em dash", "copy", []),
    (r"claim ceiling|vetted|certified", "copy", []),
    (r"trial", "copy", []),
    (r"18\+|under-18|adult", "casting", []),
]


def _tier_topics(text: str) -> list[str]:
    """'Wardrobe audits, personal shopping and video consultations are
    Gold-tier' -> ['tier', 'gold', 'wardrobe audits', 'personal shopping', ...]."""
    m = re.search(r"(\w+)[- ]tier", text, re.I)
    topics = ["tier"] + ([m.group(1).lower()] if m else [])
    subject = re.split(r"\b(?:are|is)\b", text, maxsplit=1)[0]
    for part in re.split(r",|\band\b", subject):
        part = _norm(part)
        if part:
            topics.append(part)
    return topics


def interpret_rules(rules: list[str]) -> list[Rule]:
    out = []
    for i, text in enumerate(rules, 1):
        kind, topics = "producer", []
        for pat, k, kw in _RULE_TOPICS:
            if re.search(pat, text, re.I):
                kind, topics = k, kw
                break
        if re.search(r"[- ]tier\b", text, re.I):
            kind, topics = "frame", _tier_topics(text)
        out.append(Rule(id=f"rule-{i}", text=text, kind=kind, applies_to=topics))
    return out


# --------------------------------------------------------------------------
# Recordings


@dataclass
class Moment:
    beat: str
    cut: str
    line: int
    text: str
    seconds: float
    manual: list[str]
    notes: list[str] = field(default_factory=list)
    also: list[str] = field(default_factory=list)  # other cut/lines reusing this moment
    also_beats: list[str] = field(default_factory=list)  # their beat ids


@dataclass
class Recording:
    id: str
    title: str
    screens: list[str]
    headers: list[str]
    moments: list[Moment]
    content_seconds: float
    take_seconds: float
    serves: list[str]  # "ios L3", ...
    rules: list[str]  # rule ids
    prep: list[str]
    route: list[str] = field(default_factory=list)  # from the UI map
    matched_screens: dict[str, dict] = field(default_factory=dict)
    same_screen_as: str | None = None  # an earlier take on the same screen
    account: str = "default"  # the signed-in profile this take needs


def _family(screen: str) -> str:
    s = screen.lower()
    # The composer is part of the thread; a profile reached from Discover is
    # handled by the beat's first screen, not here.
    for key, fam in (("thread", "thread"), ("composer", "thread"), ("profile", "profile"), ("discover", "discover"),
                     ("closet", "closet"), ("service", "service"), ("request", "service"), ("settings", "settings"),
                     ("search", "search"), ("feed", "feed")):
        if key in s:
            return fam
    return _norm(screen)


def _similar(a: str, b: str) -> float:
    wa, wb = set(_norm(a).split()), set(_norm(b).split())
    return len(wa & wb) / max(1, len(wa | wb))


def _runs(brief: Brief) -> list[list[Beat]]:
    """Consecutive app beats on the same screen family, per cut.

    Each run is one take: the screen stays up and the action continues. A
    different screen, or live action / a graphic in between, starts a new run.
    """
    runs: list[list[Beat]] = []
    for cut in brief.cuts:
        cur: list[Beat] = []
        fam = None
        for ln in cut.lines:
            for b in ln.beats:
                f = _family(b.screens[0]) if (b.kind == "app" and b.screens) else ("unnamed" if b.kind == "app" else None)
                if f is None or f != fam:
                    if cur:
                        runs.append(cur)
                    cur, fam = ([b], f) if f else ([], None)
                else:
                    cur.append(b)
        if cur:
            runs.append(cur)
    return runs


def _twin_runs(a: list[Beat], b: list[Beat]) -> bool:
    if a[0].cut == b[0].cut or len(a) != len(b):
        return False
    # Moments that need set-up (a photo, a reply) must match closely; a plain
    # look at a screen may differ in wording ("held rather than tapped through").
    # A moment that needs set-up merges only when worded identically ("red
    # jacket photo" vs "blue jacket photo" are two takes).
    return all(x.manual == y.manual and (_norm(x.text) == _norm(y.text) if x.manual else _similar(x.text, y.text) >= 0.6)
               for x, y in zip(a, b))


def build_recordings(brief: Brief, rules: list[Rule]) -> list[Recording]:
    """One take per run of lines on the same screen, in script order.

    A beat that starts on screen X and taps through to Y belongs to X's take
    (Discover -> coach profile is one take). A run that another cut asks for
    word for word is recorded once, at the longer length; anything that needs
    a different photo, reply or question stays its own take.
    """
    runs = _runs(brief)
    merged: list[list[list[Beat]]] = []  # each entry: the runs sharing one take
    for run in runs:
        home = next((m for m in merged if _twin_runs(m[0], run)), None)
        if home:
            home.append(run)
        else:
            merged.append([run])
    recs = []
    fam_first: dict[str, str] = {}
    for n, group in enumerate(merged, 1):
        main = group[0]
        beats = [b for run in group for b in run]
        screens, headers = [], []
        for b in beats:
            for sname in b.screens:
                low = sname.lower()
                # "message thread" is the same screen as "coach message thread".
                if any(low in x.lower() or x.lower() in low for x in screens):
                    continue
                screens.append(sname)
            for h in b.headers:
                if h not in headers:
                    headers.append(h)
        moments = []
        for i, b in enumerate(main):
            mo = Moment(b.id, b.cut, b.line, b.text, b.seconds, b.manual, list(b.notes))
            for other in group[1:]:
                twin = other[i]
                mo.seconds = max(mo.seconds, twin.seconds)
                mo.also.append(f"{twin.cut} L{twin.line}")
                mo.also_beats.append(twin.id)
                mo.notes += [x for x in twin.notes if x not in mo.notes]
            moments.append(mo)
        content = round(sum(m.seconds for m in moments), 1)
        serves = []
        for b in beats:
            tag = f"{b.cut} L{b.line}"
            if tag not in serves:
                serves.append(tag)
        blob = " ".join([*screens, *(b.text for b in beats), *(x for b in beats for x in b.notes)]).lower()
        rids = [r.id for r in rules if r.kind == "frame" and (not r.applies_to or any(_mentions(blob, k) for k in r.applies_to))]
        fam = _family(main[0].screens[0]) if main[0].screens else "unnamed"
        rid = f"R{n}"
        same_as = fam_first.setdefault(fam, rid)
        title = " → ".join(screens[:3]) if screens else "App footage (screen not named in the brief)"
        recs.append(Recording(
            id=rid, title=title, screens=screens, headers=headers, moments=moments,
            content_seconds=content, take_seconds=round(content + 2 * HANDLE_SECONDS, 1),
            serves=serves, rules=rids, prep=_prep_for(beats),
            same_screen_as=None if same_as == rid else same_as,
        ))
    return recs


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def _mentions(blob: str, phrase: str) -> bool:
    have = {_stem(w) for w in _norm(blob).split()}
    return all(_stem(w) in have for w in _norm(phrase).split())


def _prep_for(beats: list[Beat]) -> list[str]:
    prep = []
    text = " ".join(b.text for b in beats).lower()
    if re.search(r"reply|answer", text):
        prep.append("A real coach reply must already be in the thread (or arranged), matching what the line describes. "
                    "The rig never sends messages.")
    if re.search(r"photo .*attached|attach|mirror photo|photo of an outfit", text):
        prep.append("Have the outfit / mirror photo ready in the phone's Photos so it can be attached on camera.")
    if re.search(r"send request|service options|selected", text):
        prep.append("Sending a real service request is a manual step: confirm with the client before tapping Send.")
    if re.search(r"men and for women|men and women", text):
        prep.append("Make sure coaches for men and for women are both in view.")
    if re.search(r"scroll(?:ing)? back|earlier exchanges", text):
        prep.append("The thread needs several earlier exchanges to scroll back through.")
    if re.search(r"retailer|outbound link", text):
        prep.append("The coach's list needs item cards with retailer names and link icons.")
    return prep


# --------------------------------------------------------------------------
# Matching against a UI map (from ui_map.py)


def match_screens(recs: list[Recording], ui_map: dict) -> None:
    screens = ui_map.get("screens", [])
    for rec in recs:
        for name in rec.screens + [f'"{h}"' for h in rec.headers]:
            scored = sorted(((_screen_score(name, sc), sc) for sc in screens), key=lambda x: -x[0])
            best_score, best = scored[0] if scored else (0.0, None)
            ties = [sc["id"] for sc_score, sc in scored if sc_score == best_score and best_score >= 0.5]
            if best is None or best_score < 0.5:
                rec.matched_screens[name] = {"status": "unmatched"}
            else:
                status = "matched" if best_score >= 0.8 else "possible"
                if len(ties) > 1:
                    status = "ambiguous"
                rec.matched_screens[name] = {
                    "status": status, "candidates": ties,
                    "screen": best["id"], "score": round(best_score, 2),
                    "route": best.get("route", []), "screenshot": best.get("screenshot"),
                }
        first = next((v for v in rec.matched_screens.values() if v["status"] in ("matched", "possible")), None)
        if first:
            rec.route = first["route"]


def _screen_score(name: str, screen: dict) -> float:
    texts = [t.lower() for t in screen.get("texts", [])]
    header = [t.lower() for t in screen.get("headers", [])]
    if name.startswith('"'):
        want = name.strip('"').lower()
        if any(want in h for h in header):
            return 1.0
        return 0.6 if any(want in t for t in texts) else 0.0
    words = [w for w in _norm(name).split() if w not in ("screen", "list", "page")]
    if not words:
        return 0.0
    hit_head = sum(1 for w in words if any(w in h for h in header)) / len(words)
    hit_any = sum(1 for w in words if any(w in t for t in texts)) / len(words)
    # A word only found on the tab bar does not prove the screen.
    tabs = [t.lower() for t in screen.get("tabs", [])]
    only_tabs = all(any(w in t for t in tabs) and not any(w in h for h in header) for w in words)
    score = max(hit_head, 0.7 * hit_any)
    return score * (0.5 if only_tabs and hit_head < 1 else 1.0)


# --------------------------------------------------------------------------
# Placeholder findings (input: ui-map screens; output: findings tied to recordings)

_PLACEHOLDER_TEXT = [
    (r"lorem ipsum|dolor sit amet", "dummy text"),
    (r"\b(john|jane) (doe|smith)\b", "dummy name"),
    (r"^(test|testing|test user|asdf|qwerty|xxx+)$", "test text"),
    (r"^(n/?a|null|undefined|nan|none|tbd|todo|untitled|placeholder|lorem)$", "placeholder value"),
    (r"example\.(com|org)|@example\b", "example address"),
    (r"^@?username$|^your name$|^full name$", "unfilled field label"),
    (r"^(enter|add|type|write|tell us)\b.{0,40}$", "unfilled prompt"),
    (r"\b(no (messages|items|results|posts|photos|coaches|reviews) yet|nothing here|empty|no data)\b", "empty state"),
]
_AVATAR = re.compile(r"person\.crop\.circle|person\.circle|person\.fill|avatar[-_ ]?placeholder|default[-_ ]?avatar|"
                     r"profile[-_ ]?placeholder|no[-_ ]?image|placeholder", re.I)
_RATING = re.compile(r"(\b\d(\.\d)?\s*(★|stars?|/\s*5)\b)|(\b\d[\d,.]*k?\s*(reviews?|ratings?|subscribers?|followers?|clients?)\b)", re.I)
_PRICE = re.compile(r"[$€£]\s?\d|\b\d+[.,]\d{2}\s?(usd|eur|gbp)\b|/\s?(mo|month|yr|year)\b", re.I)
_ZERO = re.compile(r"\b0\s+(subscribers?|followers?|reviews?|ratings?|clients?|items?|messages?|likes?|"
                   r"posts?|photos?|coaches?|sessions?)\b", re.I)
_BARE_SCORE = re.compile(r"^\s*[1-5][.,]\d\s*$")
_SCORE_LABEL = re.compile(r"\b(ratings?|reviews?|stars?|score)\b", re.I)

_FIX = {
    "dummy text": "Replace with real copy (I can draft it in the brand's tone).",
    "dummy name": "Use a believable real-looking name on the account used for recording.",
    "test text": "Replace the test entry with real-looking content.",
    "placeholder value": "Fill this field on the recording account.",
    "example address": "Swap for a real-looking address or hide it from frame.",
    "unfilled field label": "Fill the field so the label is replaced by content.",
    "unfilled prompt": "Fill the field (I can draft the text) so the prompt does not show.",
    "empty state": "Add content so the screen is not empty, or avoid this screen in the take.",
    "default avatar": "Upload a profile photo on the recording account.",
    "empty field": "Fill the field before recording.",
    "rating or count": "Creative rules forbid ratings and counts on screen: frame it out or scroll past.",
    "price": "Creative rules forbid prices on screen: frame it out.",
    "zero counter": "Record around counters showing zero (creative rule), or populate them.",
}


def find_placeholders(ui_map: dict) -> list[dict]:
    """Findings per crawled screen. Each element is the flat dict ui_map saves."""
    out = []
    for sc in ui_map.get("screens", []):
        has_score_label = any(_SCORE_LABEL.search(e.get("text") or "") for e in sc.get("elements", []))
        for el in sc.get("elements", []):
            cat = _classify_element(el)
            # "4.8" on its own, next to a "Ratings" label elsewhere on the screen.
            if not cat and has_score_label and _BARE_SCORE.match(el.get("text") or el.get("value") or ""):
                cat = ("rating or count", "medium")
            if not cat:
                continue
            category, confidence = cat
            out.append({
                "screen": sc["id"], "screen_title": sc.get("title", sc["id"]),
                "screenshot": sc.get("screenshot"), "type": el.get("type", ""),
                "text": el.get("text", ""), "rect": el.get("rect"),
                "category": category, "confidence": confidence,
                "suggested_fix": _FIX.get(category, ""), "recordings": [],
            })
    return out


def _classify_element(el: dict) -> tuple[str, str] | None:
    t = (el.get("text") or "").strip()
    typ = el.get("type", "").replace("XCUIElementType", "")
    name = el.get("name") or ""
    if typ == "Image" and (_AVATAR.search(name) or _AVATAR.search(t)):
        return "default avatar", "high"
    if typ in ("TextField", "SecureTextField", "TextView", "SearchField"):
        value, placeholder = el.get("value") or "", el.get("placeholder") or ""
        if not value or (placeholder and value == placeholder):
            # Search boxes are meant to be empty.
            return None if typ == "SearchField" else ("empty field", "medium")
    # The label names the element; the value is what it shows ("Coach bio" /
    # "Lorem ipsum"). Both are checked.
    value = (el.get("value") or "").strip()
    for s in [t] + ([value] if value and value != t else []):
        if not s:
            continue
        for pat, category in _PLACEHOLDER_TEXT:
            if re.search(pat, s, re.I):
                # "Add" and "Enter" are fine on a button; they are a placeholder on a field or label.
                if category == "unfilled prompt" and typ in ("Button", "Cell"):
                    continue
                return category, "high" if category in ("dummy text", "dummy name", "placeholder value") else "medium"
        if _ZERO.search(s):
            return "zero counter", "high"
        if _RATING.search(s):
            return "rating or count", "high"
        if _PRICE.search(s):
            return "price", "medium"
    return None


def link_findings(findings: list[dict], recs: list[Recording]) -> None:
    for f in findings:
        f["recordings"] = [r.id for r in recs if any(m.get("screen") == f["screen"] for m in r.matched_screens.values())]


# --------------------------------------------------------------------------
# Output


# --------------------------------------------------------------------------
# Accounts: which signed-in profile each take needs (labels only, never
# credentials; the producer signs in by hand)

_SECRET_KEYS = re.compile(r"pass|pwd|secret|token|otp|pin|code|credential|email|user(name)?$|login", re.I)


def load_accounts(path: str | None) -> list[dict]:
    if not path:
        return []
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    accounts = data.get("accounts") if isinstance(data, dict) else data
    if not isinstance(accounts, list) or not accounts:
        raise ValueError("accounts.json needs a non-empty 'accounts' list")
    seen = set()
    for a in accounts:
        if not isinstance(a, dict) or not isinstance(a.get("label"), str) or not a["label"].strip():
            raise ValueError("every account needs a 'label' that is plain text")
        bad = [k for k in a if k not in ("label", "takes", "screens", "notes") or _SECRET_KEYS.search(k)]
        if bad:
            raise ValueError(f"accounts.json must not hold credentials or unknown keys: {bad}")
        for key in ("takes", "screens"):
            if not isinstance(a.get(key, []), list) or not all(isinstance(x, str) for x in a.get(key, [])):
                raise ValueError(f"account '{a['label']}': '{key}' must be a list of text")
        if not isinstance(a.get("notes", ""), str):
            raise ValueError(f"account '{a['label']}': 'notes' must be text")
        if a["label"] in seen:
            raise ValueError(f"account '{a['label']}' is listed twice")
        seen.add(a["label"])
    return accounts


def assign_accounts(recs: list[Recording], accounts: list[dict]) -> list[str]:
    """Set rec.account for every take; returns warnings. Take ids beat screen names."""
    warnings = []
    if not accounts:
        for r in recs:
            r.account = "default"
        return warnings
    ids = {r.id for r in recs}
    by_take: dict[str, str] = {}
    for a in accounts:
        for t in a.get("takes", []):
            if t not in ids:
                raise ValueError(f"account '{a['label']}' lists unknown take {t}")
            if t in by_take and by_take[t] != a["label"]:
                raise ValueError(f"take {t} is listed under two accounts")
            by_take[t] = a["label"]
    for r in recs:
        if r.id in by_take:
            r.account = by_take[r.id]
            continue
        hits = [a["label"] for a in accounts
                if any(_mentions(" ".join(r.screens + [r.title]), sname) for sname in a.get("screens", []))]
        if len(set(hits)) > 1:
            raise ValueError(f"take {r.id} ({r.title}) matches screens of several accounts: {sorted(set(hits))}; "
                             "list it under 'takes' of one account")
        if hits:
            r.account = hits[0]
        else:
            r.account = accounts[0]["label"]
            warnings.append(f"{r.id} ({r.title}) has no account in accounts.json; using '{accounts[0]['label']}'.")
    return warnings


def build_plan(brief_text: str, vo: dict[str, str], ui_map: dict | None = None,
               timing: dict[str, tuple[float | None, list]] | None = None,
               accounts: list[dict] | None = None) -> dict:
    """The whole plan as plain data. `timing` overrides ffmpeg (used by tests)."""
    brief = parse_brief(brief_text)
    for cut in brief.cuts:
        if timing and cut.key in timing:
            total, sil = timing[cut.key]
        elif cut.key in vo:
            total, sil = vo_duration(vo[cut.key]), vo_silences(vo[cut.key])
        else:
            total, sil = None, []
        cut.vo_seconds = total
        method = time_lines(cut.lines, total, sil)
        if method == "speaking-rate":
            cut.warnings.append("No voice-over measured: timings are a speaking-rate estimate.")
        for ln in cut.lines:
            time_beats(ln)
    rules = interpret_rules(brief.rules)
    recs = build_recordings(brief, rules)
    brief.warnings += assign_accounts(recs, accounts or [])
    # "Record back to back" only pairs takes signed in to the same account.
    first: dict[tuple[str, str], str] = {}
    for r in recs:
        fam = _family(r.screens[0]) if r.screens else "unnamed"
        home = first.setdefault((fam, r.account), r.id)
        r.same_screen_as = None if home == r.id else home
    findings: list[dict] = []
    if ui_map:
        match_screens(recs, ui_map)
        findings = find_placeholders(ui_map)
        link_findings(findings, recs)
    return {
        "version": PLAN_VERSION,
        "client": brief.client, "app": brief.app, "app_url": brief.app_url,
        "notes": brief.notes, "warnings": brief.warnings,
        "cuts": [asdict(c) for c in brief.cuts],
        "rules": [asdict(r) for r in rules],
        "recordings": [asdict(r) for r in recs],
        "placeholders": findings,
        "ui_map": {"screens": len(ui_map.get("screens", [])), "stopped": ui_map.get("stopped")} if ui_map else None,
        "framework": brief.framework,
        "accounts": [a["label"] for a in accounts] if accounts else ["default"],
    }


def _fmt_s(x: float | None) -> str:
    return "?" if x is None else f"{x:.1f} s"


def _md(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def render_plan_md(plan: dict) -> str:
    L = []
    L.append(f"# Recording plan: {plan['app'] or plan['client']}")
    L.append("")
    if plan["notes"]:
        L.append(f"> **From the brief:** {plan['notes']}")
        L.append("")
    if plan["ui_map"] is None:
        L.append("> **No UI map yet.** Screens below come from the script only. Run `ui_map.py crawl` "
                 "with the app open to find how to reach each screen and what needs fixing.")
        L.append("")
    elif plan["ui_map"].get("stopped"):
        L.append(f"> UI map coverage: {plan['ui_map']['screens']} screens; crawl stopped: {plan['ui_map']['stopped']}")
        L.append("")
    for w in plan["warnings"]:
        L.append(f"- ⚠ {w}")

    L.append("## Cuts at a glance")
    L.append("")
    L.append("| Cut | Slot | Voice-over | Spare | App lines | Timing |")
    L.append("|---|---|---|---|---|---|")
    for c in plan["cuts"]:
        app_lines = [str(ln["number"]) for ln in c["lines"] if any(b["kind"] == "app" for b in ln["beats"])]
        spare = (c["slot_seconds"] - c["vo_seconds"]) if c["slot_seconds"] and c["vo_seconds"] else None
        method = c["lines"][0]["timing"] if c["lines"] else "?"
        L.append(f"| {_md(c['title'])} | {_fmt_s(c['slot_seconds'])} | {_fmt_s(c['vo_seconds'])} | "
                 f"{_fmt_s(spare)} | {', '.join(app_lines) or 'none'} | {method} |")
    L.append("")

    L.append("## Recordings")
    L.append("")
    L.append(f"Every take has {HANDLE_SECONDS:.0f} s of handle before and after. Lengths are the longest any cut needs. "
             "Line times come from the pauses in the voice-over and can be off by about a second.")
    L.append("")
    for r in plan["recordings"]:
        L.append(f"### {r['id']}. {r['title']}")
        L.append("")
        L.append(f"- **Take length:** about {r['take_seconds']:.0f} s ({r['content_seconds']:.1f} s used + handles)")
        L.append(f"- **Used in:** {', '.join(r['serves'])}")
        if r.get("same_screen_as"):
            L.append(f"- **Same screen as {r['same_screen_as']}:** record them back to back.")
        if r["headers"]:
            L.append(f"- **Must show:** " + ", ".join(f'"{h}"' for h in r["headers"]))
        if r["route"]:
            L.append(f"- **How to get there:** open the app → " + " → ".join(r["route"]))
        elif plan["ui_map"] is not None:
            L.append("- **How to get there:** not found in the UI map yet")
        L.append("- **What happens, in order:**")
        for m in r["moments"]:
            tags = ", ".join([f"{m['cut']} L{m['line']}", *m["also"]])
            L.append(f"  1. ({tags}, ~{m['seconds']:.1f} s) {m['text']}")
            for n in m["notes"]:
                L.append(f"     - On screen: {n}")
        if r["prep"]:
            L.append("- **Prepare first:**")
            for p in r["prep"]:
                L.append(f"  - {p}")
        if r["rules"]:
            L.append("- **Rules for this take:**")
            for rid in r["rules"]:
                rule = next(x for x in plan["rules"] if x["id"] == rid)
                L.append(f"  - {rule['text']}")
        ambiguous = [f"{k} ({' or '.join(v['candidates'])})" for k, v in r["matched_screens"].items()
                     if v["status"] == "ambiguous"]
        if ambiguous:
            L.append(f"- ⚠ **More than one screen fits:** {', '.join(ambiguous)}. Check the screenshots and pick one.")
        unmatched = [k for k, v in r["matched_screens"].items() if v["status"] == "unmatched"]
        if unmatched:
            L.append(f"- ⚠ **Not found in the app yet:** {', '.join(unmatched)}")
        issues = [f for f in plan["placeholders"] if r["id"] in f["recordings"]]
        if issues:
            L.append(f"- ⚠ **{len(issues)} thing(s) to fix on these screens** (see placeholders.md)")
        L.append("")

    L += _shooting_order(plan)
    L.append("## Not recorded on the phone")
    L.append("")
    L.append("| Cut | Line | Kind | What's needed |")
    L.append("|---|---|---|---|")
    for c in plan["cuts"]:
        for ln in c["lines"]:
            for b in ln["beats"]:
                if b["kind"] != "app":
                    kind = "Live action / stock" if b["kind"] == "live" else "Graphic"
                    L.append(f"| {c['key']} | {ln['number']} | {kind} | {_md(b['text'])} |")
    L.append("")

    L.append("## Line timing")
    L.append("")
    for c in plan["cuts"]:
        L.append(f"**{c['title']}**")
        L.append("")
        L.append("| Line | From | To | Length | Voice |")
        L.append("|---|---|---|---|---|")
        for ln in c["lines"]:
            L.append(f"| {ln['number']} | {ln['start']:.1f} | {ln['end']:.1f} | {ln['end'] - ln['start']:.1f} s | "
                     f"{_md(ln['voice'])} |")
        for w in c["warnings"]:
            L.append(f"\n- ⚠ {w}")
        L.append("")

    other = [r for r in plan["rules"] if r["kind"] != "frame"]
    if other:
        L.append("## Rules for the edit (not the recording)")
        L.append("")
        for r in other:
            L.append(f"- {r['text']}")
        L.append("")
    return "\n".join(L)


def _shooting_order(plan: dict) -> list[str]:
    """Takes grouped by account, so each account is signed into once."""
    L = ["## Shooting order", ""]
    order = plan["accounts"]
    for n, label in enumerate(order, 1):
        takes = [r for r in plan["recordings"] if r["account"] == label]
        if not takes:
            continue
        shown = "the recording account" if label == "default" else label
        L.append(f"**Sign-in {n}: {shown}.** Open the app and sign in on the phone yourself. The rig never types "
                 "credentials: it waits at the login screen and pings you. To map this account's screens:")
        L.append("")
        L.append(f"`python agent-workspace/ui_map.py crawl --bundle-id <app bundle id> --slug <client> "
                 f"--account \"{label}\" --wait-for-login 15`")
        L.append("")
        L.append("Then record:")
        L.append("")
        for r in takes:
            L.append(f"- [ ] {r['id']}. {r['title']} (~{r['take_seconds']:.0f} s)")
        L.append("")
    return L


def render_placeholders_md(plan: dict) -> str:
    L = [f"# Fix before recording: {plan['app'] or plan['client']}", ""]
    if plan["ui_map"] is None:
        L += ["No UI map yet: run `ui_map.py crawl` with the app open, then plan again.", ""]
        return "\n".join(L)
    found = plan["placeholders"]
    if not found:
        L += ["Nothing found on the crawled screens.", ""]
        return "\n".join(L)
    L.append(f"{len(found)} item(s) on {len({f['screen'] for f in found})} screen(s). "
             "Items on screens a recording uses come first.")
    L.append("")
    found = sorted(found, key=lambda f: (not f["recordings"], f["screen"]))
    by_screen: dict[str, list[dict]] = {}
    for f in found:
        by_screen.setdefault(f["screen"], []).append(f)
    for sid, items in by_screen.items():
        first = items[0]
        used = ", ".join(first["recordings"]) or "no recording"
        L.append(f"## {first['screen_title']} (used by {used})")
        L.append("")
        if first.get("screenshot"):
            L.append(f"Screenshot: `{first['screenshot']}`")
            L.append("")
        L.append("| What | Shows | Fix | Confidence |")
        L.append("|---|---|---|---|")
        for f in items:
            shown = f["text"] or f"({f['type'] or 'element'} with no text)"
            L.append(f"| {f['category']} | {_md(shown[:80])} | {_md(f['suggested_fix'])} | {f['confidence']} |")
        L.append("")
    return "\n".join(L)


def write_outputs(plan: dict, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "recordings.json": json.dumps(plan, indent=2, ensure_ascii=False),
        "recording-plan.md": render_plan_md(plan),
        "placeholders.md": render_placeholders_md(plan),
    }
    written = []
    for name, body in files.items():
        p = out_dir / name
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, p)
        written.append(p)
    return written


def _slug(s: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    if not slug:
        raise SystemExit("Client slug is empty.")
    return slug


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="Build the recording plan from a brief")
    p.add_argument("--brief", required=True)
    p.add_argument("--vo", action="append", default=[], help="cut=path.mp3, e.g. ios=ios-vo.mp3")
    p.add_argument("--ui-map", help="ui-map.json from ui_map.py crawl")
    p.add_argument("--slug", help="client folder name (default: the brief's folder)")
    p.add_argument("--out", help="output folder (default C:/CaptureRig/clients/<slug>)")
    p.add_argument("--accounts", help="accounts.json: account labels and the takes/screens each one serves "
                                      "(default: <client folder>/accounts.json if it exists)")
    a = ap.parse_args(argv)
    vo = {}
    for item in a.vo:
        key, _, path = item.partition("=")
        if not path:
            raise SystemExit(f"--vo needs cut=path, got {item!r}")
        vo[key.strip()] = path
    brief_text = Path(a.brief).read_text(encoding="utf-8")
    ui_map = json.loads(Path(a.ui_map).read_text(encoding="utf-8")) if a.ui_map else None
    out_guess = Path(a.out) if a.out else DEFAULT_OUT / _slug(a.slug or Path(a.brief).resolve().parent.name)
    acc_path = a.accounts or (str(out_guess / "accounts.json") if (out_guess / "accounts.json").exists() else None)
    plan = build_plan(brief_text, vo, ui_map, accounts=load_accounts(acc_path))
    slug = _slug(a.slug or Path(a.brief).resolve().parent.name)
    out = Path(a.out) if a.out else DEFAULT_OUT / slug
    for f in write_outputs(plan, out):
        print(f)
    n = len(plan["recordings"])
    print(f"{n} recording(s); {len(plan['placeholders'])} item(s) to fix.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
