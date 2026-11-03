"""Locale/market-aware AI prompt loading (plan §6.1).

Resolution rules (see docs/i18n-contracts.md):
1. Required: prompts/<writing_locale>/<feature>.txt
2. Optional overlay: prompts/markets/<market>/<feature>.txt
3. Merge: overlay text is appended after the locale text, separated by
   two newlines. The locale file owns language; the overlay owns
   market-specific creative guidance.
4. Legacy flat prompts/<feature>.txt is used ONLY when writing_locale is
   ko-KR and the ko-KR file is missing (transition path for files not
   yet moved into per-locale directories).
5. A missing required locale file raises FileNotFoundError naming
   feature + locale. Never silently substitute another language.

Call sites are NOT rewired here; wave-2+ agents adopt load() per the
AI-call inventory in plan §6.2.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"

_FEATURE_RE = re.compile(r"[A-Za-z0-9_-]+")
_OVERLAY_SEP = "\n\n"

LEGACY_LOCALE = "ko-KR"


@dataclass(frozen=True)
class PromptBundle:
    """Resolved prompt text plus provenance for llm_calls logging."""

    text: str
    feature: str
    writing_locale: str
    market: str
    locale_path: str  # path actually read (legacy path when fell back)
    overlay_path: str | None  # None when no market overlay exists
    legacy_fallback: bool = False


def check_feature(feature: str) -> str:
    """Reject path traversal / extension tricks in feature names."""
    if not feature or not _FEATURE_RE.fullmatch(feature):
        raise ValueError(f"invalid prompt feature: {feature!r}")
    return feature


def resolve_paths(feature: str, writing_locale: str, market: str,
                  base_dir: Path | None = None) -> tuple[Path, Path, Path]:
    """Return (locale_path, overlay_path, legacy_path) for a feature."""
    check_feature(feature)
    base = Path(base_dir) if base_dir is not None else PROMPTS_DIR
    return (
        base / writing_locale / f"{feature}.txt",
        base / "markets" / market / f"{feature}.txt",
        base / f"{feature}.txt",
    )


def load(feature: str, writing_locale: str, market: str,
         base_dir: Path | None = None) -> PromptBundle:
    """Load and merge prompt text for one AI feature.

    Raises FileNotFoundError naming feature + locale when the required
    locale file (and the ko-KR-only legacy fallback) is absent.
    """
    locale_path, overlay_path, legacy_path = resolve_paths(
        feature, writing_locale, market, base_dir)
    legacy_used = False
    if locale_path.is_file():
        text = locale_path.read_text(encoding="utf-8")
        used = locale_path
    elif writing_locale == LEGACY_LOCALE and legacy_path.is_file():
        text = legacy_path.read_text(encoding="utf-8")
        used = legacy_path
        legacy_used = True
    else:
        raise FileNotFoundError(
            f"missing prompt for feature={feature!r}"
            f" writing_locale={writing_locale!r}"
            f" (looked for {locale_path})")
    overlay_used: str | None = None
    if overlay_path.is_file():
        overlay = overlay_path.read_text(encoding="utf-8")
        if overlay.strip():
            text = text.rstrip("\n") + _OVERLAY_SEP + overlay.lstrip("\n")
        overlay_used = str(overlay_path)
    return PromptBundle(
        text=text, feature=feature, writing_locale=writing_locale,
        market=market, locale_path=str(used), overlay_path=overlay_used,
        legacy_fallback=legacy_used)


def available_features(writing_locale: str,
                       base_dir: Path | None = None) -> list[str]:
    """Feature names having a locale file (plus legacy files for ko-KR)."""
    base = Path(base_dir) if base_dir is not None else PROMPTS_DIR
    names = {p.stem for p in (base / writing_locale).glob("*.txt")}
    if writing_locale == LEGACY_LOCALE:
        names |= {p.stem for p in base.glob("*.txt")
                  if _FEATURE_RE.fullmatch(p.stem)}
    return sorted(names)
