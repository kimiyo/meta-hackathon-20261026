"""i18n prompt parity checks (plan section 6, stage 3).

File-level assertions only: no prompt_loader import, so this test stays
valid regardless of loader rewiring. Covers the full legacy basename set;
prompts-en-a (a-m) and prompts-en-b (n-z) each contribute their files and
the committed test goes green once both have landed (integration step).
"""
from __future__ import annotations

import re
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent.parent / "app" / "prompts"
EN_DIR = PROMPTS_DIR / "en-US"
US_MARKET_DIR = PROMPTS_DIR / "markets" / "US"

_HANGUL_RE = re.compile(r"[\uac00-\ud7a3\u1100-\u11ff\u3130-\u318f]")

# Market overlays the contract mandates (docs/i18n-contracts.md section 6:
# overlays are optional; none are currently mandated for any feature).
REQUIRED_US_OVERLAYS: frozenset[str] = frozenset()


def _legacy_basenames() -> list[str]:
    return sorted(p.stem for p in PROMPTS_DIR.glob("*.txt"))


def _hangul_lines(path: Path) -> list[tuple[int, str]]:
    bad = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if "# PRESERVE" in line:
            continue
        if _HANGUL_RE.search(line):
            bad.append((i, line))
    return bad


def test_every_legacy_basename_has_en_us_file():
    missing = [b for b in _legacy_basenames()
               if not (EN_DIR / f"{b}.txt").is_file()]
    assert not missing, f"missing en-US prompts: {missing}"
    assert _legacy_basenames(), "no legacy prompt files found"


def test_no_hangul_in_en_us_files():
    bad: dict[str, list[tuple[int, str]]] = {}
    for path in sorted(EN_DIR.glob("*.txt")):
        lines = _hangul_lines(path)
        if lines:
            bad[path.name] = lines
    assert not bad, f"Hangul in en-US prompts: {bad}"


def test_required_us_market_overlays_exist():
    missing = [b for b in sorted(REQUIRED_US_OVERLAYS)
               if not (US_MARKET_DIR / f"{b}.txt").is_file()]
    assert not missing, f"missing US overlays: {missing}"


def test_no_hangul_in_us_market_overlays():
    if not US_MARKET_DIR.is_dir():
        return
    bad: dict[str, list[tuple[int, str]]] = {}
    for path in sorted(US_MARKET_DIR.glob("*.txt")):
        lines = _hangul_lines(path)
        if lines:
            bad[path.name] = lines
    assert not bad, f"Hangul in US overlays: {bad}"
