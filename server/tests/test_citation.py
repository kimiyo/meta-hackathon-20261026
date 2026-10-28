"""Phase 1: citation check — normalization, exact match, offsets, failures."""
from app.citation import check_quote, normalize


def test_exact_match_with_offsets():
    src = "서연은 장부를 내려다보았다. 달빛이 기울었다."
    r = check_quote(src, "장부를 내려다보았다")
    assert r["ok"] is True
    assert src[r["start"]:r["end"]] == "장부를 내려다보았다"


def test_whitespace_newline_collapsed():
    src = "서연은   장부를\n\n내려다보았다."
    r = check_quote(src, "서연은 장부를 내려다보았다.")
    assert r["ok"] is True
    assert src[r["start"]:r["end"]].replace("\n", " ") != ""


def test_quote_chars_unified():
    src = "그녀는 “장부”를 펼쳤다."
    assert check_quote(src, '"장부"')["ok"] is True
    assert check_quote(src, "『장부』")["ok"] is True


def test_mismatch_returns_not_ok():
    src = "서연은 장부를 내려다보았다."
    r = check_quote(src, "서연은 검은 봉투를 품에 넣었다")
    assert r == {"ok": False, "start": None, "end": None}


def test_partial_word_is_still_substring():
    # MVP is plain substring: any contiguous run matches.
    src = "달빛서점의 주인"
    assert check_quote(src, "빛서점")["ok"] is True


def test_empty_quote_never_matches():
    assert check_quote("원문", "")["ok"] is False
    assert check_quote("원문", "   ")["ok"] is False


def test_normalize_deterministic():
    a = normalize("  “서연”은\n장부를   펼쳤다. ")
    b = normalize("“서연”은 장부를 펼쳤다.")
    assert a == b == '"서연"은 장부를 펼쳤다.'
