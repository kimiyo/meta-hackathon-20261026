"""Citation check (§8): every evidence_quote / counter_evidence_quote /
start_quote is verified against the source text before storage or display.

Normalization: NFKC (unifies half-width forms and decomposed Hangul Jamo),
quote unification, strip ends, collapse whitespace/newlines to one space.
Exact substring match on the normalized text only — no fuzzy matching.
Offsets map back to the ORIGINAL source exactly. On failure the item is
marked deferred ("근거 미확인").
"""
from __future__ import annotations

import re
import unicodedata

_QUOTE_MAP = str.maketrans({
    "“": '"', "”": '"', "„": '"', "‟": '"', "«": '"', "»": '"',
    "『": '"', "』": '"', "「": '"', "」": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
})

_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Normalize for comparison. Deterministic, no ML."""
    nfkc = unicodedata.normalize("NFKC", text.translate(_QUOTE_MAP))
    return _WS_RE.sub(" ", nfkc).strip()


def _nfkc_with_map(text: str) -> tuple[str, list[int]]:
    """NFKC text plus per-char map (nfkc index -> original index)."""
    out: list[str] = []
    mp: list[int] = []
    for i, ch in enumerate(text):
        for nc in unicodedata.normalize("NFKC", ch):
            out.append(nc)
            mp.append(i)
    return "".join(out), mp


def find_quote(source: str, quote: str) -> tuple[int | None, int | None]:
    """Return (start, end) offsets into the *original* source, or (None, None).

    The normalized quote must appear verbatim in the normalized source.
    When the quote is empty/blank → (None, None).
    """
    prepped, smap = _nfkc_with_map(source.translate(_QUOTE_MAP))
    norm_src = _WS_RE.sub(" ", prepped).strip()
    norm_q = normalize(quote)
    if not norm_q or not norm_src:
        return None, None
    idx = norm_src.find(norm_q)
    if idx == -1:
        return None, None
    ns = _walk_to_prepped(prepped, idx)
    ne = _walk_to_prepped(prepped, idx + len(norm_q), end=True)
    if ns >= len(smap):
        return None, None
    start = smap[ns]
    end = smap[ne - 1] + 1 if ne > 0 else 0
    return start, end


def check_quote(source: str, quote: str) -> dict:
    """Convenience result: {"ok": bool, "start": int|None, "end": int|None}."""
    start, end = find_quote(source, quote)
    return {"ok": start is not None, "start": start, "end": end}


def _walk_to_prepped(prepped: str, norm_pos: int, end: bool = False) -> int:
    """Map a normalized-text offset back to a prepped-text offset.

    Rebuilds the normalized string char-by-char from the prepped text
    while tracking positions; O(n), exact.
    """
    mapping: list[int] = []
    n = len(prepped)
    i = 0
    while i < n and prepped[i] in " \t\n\r\f\v":
        i += 1
    while i < n:
        ch = prepped[i]
        if ch in " \t\n\r\f\v":
            j = i
            while j < n and prepped[j] in " \t\n\r\f\v":
                j += 1
            if j < n:
                mapping.append(i)
            i = j
        else:
            mapping.append(i)
            i += 1
    if not mapping:
        return 0
    if norm_pos <= 0:
        return mapping[0]
    if norm_pos >= len(mapping):
        return mapping[-1] + 1
    return mapping[norm_pos]
