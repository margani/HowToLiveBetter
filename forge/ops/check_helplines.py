#!/usr/bin/env python3
"""Gate: emergency and helpline numbers survive translation unchanged, item by item.

Why: verify.py compares chapter-wide multisets of numbers. A helpline that also
appears as a statistic elsewhere in the chapter (120 is both China's medical
emergency number and a common trial size) could be swapped for another number
and pass with only a warning. In a life-safety book a wrong emergency number is
the one error a reader cannot recover from, so these numbers are compared per
``### N.`` item and per chapter intro, and any difference fails.

The protected list lives in translate/protected-numbers.json. Changing it, or
changing a protected number in a chapter, needs the owner's verification.

Several locales localize on purpose (ru writes "call an ambulance" instead of
"call 120"). Those differences existed before this gate and are pinned, as
found, in translate/helplines-baseline.json for the owner to review. The gate
fails on any difference that is not pinned exactly, so a new or changed
difference cannot pass silently. --write-baseline re-pins the current state and
is an owner decision, never a way to make CI green.

Exit codes: 0 no unpinned difference, 1 an unpinned difference, 2 the check
could not run (no list, no chapters, or no pairs compared).

Usage: python3 forge/ops/check_helplines.py [--lang fa] [--chapter 06] [--json]
       python3 forge/ops/check_helplines.py --write-baseline
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOOK = ROOT / "book"
LIST_PATH = ROOT / "translate" / "protected-numbers.json"
BASELINE_PATH = ROOT / "translate" / "helplines-baseline.json"

ITEM_RE = re.compile(r"^###\s+(\d+)\.", re.MULTILINE)
# Translator's notes may name a local equivalent (e.g. "in Iran call 115").
# They are blockquotes that start with a localized "note" label; they are
# excluded from the comparison on the translation side only.
NOTE_LINE_RE = re.compile(r"^\s*>")


def load_protected(path: Path = LIST_PATH) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    nums = [str(n["number"]) for n in data["numbers"]]
    if not nums:
        raise ValueError(f"{path}: empty protected list")
    return nums


def ascii_digits(text: str) -> str:
    """Map any Unicode decimal digit (Persian, Arabic-Indic…) to ASCII so a
    localized digit script cannot hide a changed number."""
    return "".join(
        str(unicodedata.decimal(ch)) if ch.isdecimal() and not ch.isascii() else ch for ch in text
    )


def split_sections(text: str) -> dict[str, str]:
    """Return {"intro": ..., "1": ..., "2": ...} keyed by item number."""
    parts: dict[str, str] = {}
    matches = list(ITEM_RE.finditer(text))
    parts["intro"] = text[: matches[0].start()] if matches else text
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        parts[m.group(1)] = text[m.start() : end]
    return parts


def strip_notes(section: str) -> str:
    return "\n".join(line for line in section.splitlines() if not NOTE_LINE_RE.match(line))


def count_numbers(text: str, protected: list[str]) -> dict[str, int]:
    text = ascii_digits(text)
    out: dict[str, int] = {}
    for n in protected:
        # Not part of a longer number, a decimal, a DOI/ISBN fragment or a date.
        c = len(re.findall(rf"(?<![\d.,/:\-]){re.escape(n)}(?![\d]|[.,]\d)", text))
        if c:
            out[n] = c
    return out


def compare(src: str, tr: str, protected: list[str]) -> list[dict]:
    s_parts, t_parts = split_sections(src), split_sections(tr)
    problems = []
    for key in s_parts.keys() | t_parts.keys():
        s = count_numbers(s_parts.get(key, ""), protected)
        t = count_numbers(strip_notes(t_parts.get(key, "")), protected)
        if s != t:
            problems.append({"section": key, "source": s, "translation": t})
    return sorted(
        problems,
        key=lambda p: (p["section"] != "intro", int(p["section"]) if p["section"].isdigit() else 0),
    )


def _key(lang: str, chapter: str, p: dict) -> str:
    return json.dumps(
        [lang, chapter, p["section"], p["source"], p["translation"]],
        sort_keys=True,
        ensure_ascii=False,
    )


def load_baseline(path: Path = BASELINE_PATH) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {_key(e["lang"], e["chapter"], e) for e in data["pinned"]}


def unpinned(lang: str, chapter: str, problems: list[dict], baseline: set[str]) -> list[dict]:
    return [p for p in problems if _key(lang, chapter, p) not in baseline]


def source_chapters() -> dict[str, Path]:
    return {p.name[:2]: p for p in sorted(BOOK.glob("[0-9][0-9]-*.md"))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lang", action="append", help="locale dir under book/ (repeatable)")
    ap.add_argument("--chapter", action="append", help="two-digit chapter (repeatable)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--write-baseline",
        action="store_true",
        help="re-pin all current differences (owner decision)",
    )
    args = ap.parse_args(argv)

    try:
        protected = load_protected()
    except (OSError, ValueError, KeyError) as e:
        print(f"ERROR: cannot load protected numbers: {e}", file=sys.stderr)
        return 2
    sources = source_chapters()
    if args.chapter:
        sources = {k: v for k, v in sources.items() if k in args.chapter}
    langs = args.lang or sorted(p.name for p in BOOK.iterdir() if p.is_dir())
    if not sources or not langs:
        print("ERROR: no source chapters or locales to check", file=sys.stderr)
        return 2

    baseline = set() if args.write_baseline else load_baseline()
    pairs = 0
    pinned = 0
    failures = []
    for lang in langs:
        for nn, src in sources.items():
            hits = sorted((BOOK / lang).glob(f"{nn}-*.md"))
            if not hits:
                continue  # missing chapters are parity's job, not this gate's
            pairs += 1
            probs = compare(
                src.read_text(encoding="utf-8"), hits[0].read_text(encoding="utf-8"), protected
            )
            new = unpinned(lang, nn, probs, baseline)
            pinned += len(probs) - len(new)
            rel = str(hits[0].relative_to(ROOT))
            failures.extend({"lang": lang, "chapter": nn, "file": rel, **p} for p in new)

    if pairs == 0:
        print("ERROR: no source/translation pairs were compared", file=sys.stderr)
        return 2
    if args.write_baseline:
        if args.lang or args.chapter:
            print(
                "ERROR: --write-baseline pins the whole book; drop --lang/--chapter",
                file=sys.stderr,
            )
            return 2
        keep = ("lang", "chapter", "file", "section", "source", "translation")
        BASELINE_PATH.write_text(
            json.dumps(
                {
                    "_comment": "Helpline differences pinned as found; each needs the owner's review. See forge/ops/check_helplines.py.",
                    "pinned": [{k: f[k] for k in keep} for f in failures],
                },
                ensure_ascii=False,
                indent=1,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"pinned {len(failures)} differences in {BASELINE_PATH.relative_to(ROOT)}")
        return 0
    if args.json:
        print(
            json.dumps(
                {"pairs": pairs, "pinned": pinned, "failures": failures},
                ensure_ascii=False,
                indent=1,
            )
        )
    else:
        for f in failures:
            sec = "intro" if f["section"] == "intro" else f"item {f['section']}"
            print(f"FAIL {f['file']} {sec}: source {f['source']} != translation {f['translation']}")
        print(
            f"helplines: {pairs} chapter pairs checked, {pinned} pinned differences, {len(failures)} unpinned"
        )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
