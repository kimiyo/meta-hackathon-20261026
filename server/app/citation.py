"""Citation check (§8): every evidence_quote / counter_evidence_quote /
start_quote is verified against the source text before storage or display.

Normalization: strip ends, collapse whitespace/newlines to one space, unify
quote characters. Exact substring match on the normalized text only — no fuzzy
matching in the MVP. On failure the item is marked deferred ("근거 미확인").
"""
from __future__ import annotations

import re

_QUOTE_MAP = str.maketrans({
    "“": '"', "”": '"', "„": '"', "‟": '"', "«": '"', "»": '"',
    "『": '"', "』": '"', "「": '"', "」": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
})

_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Normalize for comparison. Deterministic, no ML."""
    return _WS_RE.sub(" ", text.translate(_QUOTE_MAP)).strip()


def find_quote(source: str, quote: str) -> tuple[int | None, int | None]:
    """Return (start, end) offsets into the *original* source, or (None, None).

    Offsets map back to the original text by locating the normalized quote's
    first and last tokens in order. When the quote is empty/blank → (None, None).
    """
    norm_src = normalize(source)
    norm_q = normalize(quote)
    if not norm_q:
        return None, None
    idx = norm_src.find(norm_q)
    if idx == -1:
        return None, None
    # Map normalized offsets back to original offsets.
    orig_start = _norm_to_orig(source, norm_src, idx)
    orig_end = _norm_to_orig(source, norm_src, idx + len(norm_q), end=True)
    return orig_start, orig_end


def check_quote(source: str, quote: str) -> dict:
    """Convenience result: {"ok": bool, "start": int|None, "end": int|None}."""
    start, end = find_quote(source, quote)
    return {"ok": start is not None, "start": start, "end": end}


def _norm_to_orig(source: str, norm_src: str, norm_pos: int, end: bool = False) -> int:
    """Walk the normalization forward to recover an original offset.

    Rebuilds the normalized string char-by-char from the original while
    tracking positions; O(n), exact.
    """
    # Build mapping: normalized index -> original index.
    mapping: list[int] = []
    # Simulate normalize(): translate quotes, collapse whitespace runs, strip.
    translated = source.translate(_QUOTE_MAP)
    # Find leading whitespace length (stripped by normalize).
    stripped = translated.strip()
    lead_cut = len(translated) - len(translated.lstrip())
    out_len = 0
    i = 0
    n = len(translated)
    # Skip leading whitespace.
    while i < n and translated[i] in " \t\n\r\f\v":
        i += 1
    while i < n:
        ch = translated[i]
        if ch in " \t\n\r\f\v":
            # Collapse run to one space unless it is trailing whitespace.
            j = i
            while j < n and translated[j] in " \t\n\r\f\v":
                j += 1
            if j < n:
                mapping.append(i)
                out_len += 1
            i = j
        else:
            mapping.append(i)
            out_len += 1
            i += 1
    if not mapping:
        return 0
    if norm_pos <= 0:
        return mapping[0]
    if norm_pos >= len(mapping):
        # End of normalized text → end of last non-space char +1.
        last_orig = mapping[-1]
        # Advance past the char itself.
        return last_orig + 1
    if end:
        # End offset: original index of the char *after* the previous norm char.
        # mapping[norm_pos] is the original index of normalized char norm_pos,
        # which is exactly the exclusive end we want in most cases.
        return mapping[norm_pos]
    return mapping[norm_pos]
