#!/usr/bin/env python3
"""Helpline gate: a changed emergency number must fail, item by item.

The failure this guards against: verify.py compared chapter-wide number
multisets, so swapping "call 120" for another number passed with a warning
whenever 120 also appeared as a statistic elsewhere in the chapter.
"""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

from forge.ops import check_helplines as hl

ROOT = Path(__file__).resolve().parents[3]

SRC = """# 6. 标题

导语。

### 1. 老人摔倒先打 120
- 收益：一项 120 人的试验
### 2. 心理危机打 12356
- 备注：无
"""


class Compare(unittest.TestCase):
    protected = ("110", "120", "12356")

    def test_identical_numbers_pass(self):
        tr = SRC.replace("老人摔倒先打", "Elderly fall: call")
        self.assertEqual(hl.compare(SRC, tr, self.protected), [])

    def test_swapped_helpline_fails_even_if_value_appears_elsewhere(self):
        tr = SRC.replace("先打 120", "call 115")
        probs = hl.compare(SRC, tr, self.protected)
        self.assertEqual([p["section"] for p in probs], ["1"])

    def test_localized_digits_do_not_hide_a_change(self):
        same = SRC.replace("12356", "۱۲۳۵۶")
        self.assertEqual(hl.compare(SRC, same, self.protected), [])
        changed = SRC.replace("12356", "۱۲۳۶۵")
        self.assertEqual([p["section"] for p in hl.compare(SRC, changed, self.protected)], ["2"])

    def test_translator_note_may_name_a_local_number(self):
        tr = SRC.replace(
            "导语。", "Intro.\n\n> Translator's note: 120 is China's number; in Iran call 115."
        )
        self.assertEqual(hl.compare(SRC, tr, self.protected), [])

    def test_baseline_only_excuses_the_exact_pinned_difference(self):
        tr = SRC.replace("先打 120", "call an ambulance")
        probs = hl.compare(SRC, tr, self.protected)
        base = {hl._key("ru", "06", p) for p in probs}
        self.assertEqual(hl.unpinned("ru", "06", probs, base), [])
        worse = hl.compare(SRC, tr.replace("12356", "12365"), self.protected)
        self.assertTrue(hl.unpinned("ru", "06", worse, base))


class RepoState(unittest.TestCase):
    def test_protected_list_is_not_empty(self):
        self.assertGreater(len(hl.load_protected()), 5)

    def test_book_has_no_unpinned_helpline_difference(self):
        r = subprocess.run(
            [sys.executable, "forge/ops/check_helplines.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        m = re.search(r"(\d+) chapter pairs checked", r.stdout)
        self.assertTrue(m and int(m.group(1)) > 0, r.stdout)


class VerifyUsesGate(unittest.TestCase):
    """verify.py must hard-fail a helpline swap that its number multiset misses."""

    def _clean_section_with_120(self):
        protected = hl.load_protected()
        for nn, src in hl.source_chapters().items():
            hits = sorted((hl.BOOK / "en").glob(f"{nn}-*.md"))
            if not hits:
                continue
            tr = hits[0].read_text(encoding="utf-8")
            s_parts = hl.split_sections(src.read_text(encoding="utf-8"))
            t_parts = hl.split_sections(tr)
            for key, body in t_parts.items():
                if key == "intro" or "call 120" not in body:
                    continue
                if hl.count_numbers(s_parts.get(key, ""), protected) != hl.count_numbers(
                    body, protected
                ):
                    continue
                if tr.count("120") < 2:
                    continue  # needs a second 120 so the old multiset check passes
                return nn, hits[0], tr, body
        self.skipTest("no en item with a clean 'call 120' and a second 120 in the chapter")
        return None

    def test_swap_fails_verify(self):
        import tempfile

        nn, _path, tr, body = self._clean_section_with_120()
        bad = tr.replace(body, body.replace("call 120", "call 115", 1), 1)
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(bad)
        r = subprocess.run(
            [
                sys.executable,
                "translate/steps/verify/verify.py",
                nn,
                "--lang",
                "en",
                "--file",
                f.name,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"},
        )
        Path(f.name).unlink()
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("helpline numbers changed", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
