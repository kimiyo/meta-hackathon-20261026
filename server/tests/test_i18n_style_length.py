"""server-i18n-a: US preset catalog, length-unit policy, drafting prompt wiring."""
import json
import re
from pathlib import Path

import pytest

from app import auth as authmod
from app import budget as budgetmod
from app import custom_styles
from app import db as dbmod
from app import drafting, style_presets

_HANGUL = re.compile(r"[\uac00-\ud7a3]")


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.connect(tmp_path / "i18n_a.db")
    c.execute("INSERT INTO works(id, title, created_at) VALUES(1,'kr','t')")
    c.execute("INSERT INTO works(id, title, market, writing_locale,"
              " created_at) VALUES(2,'us','US','en-US','t')")
    for wid, did in ((1, 1), (2, 2)):
        c.execute("INSERT INTO story_designs(id, work_id, version,"
                  " scope_total_episodes, concept_json, sections_json,"
                  " status) VALUES(?,?,?,?,'{}','[]','approved')",
                  (did, wid, 1, 2))
        c.execute("INSERT INTO episode_outlines(design_id, episode_no,"
                  " title, goal, constraints_json)"
                  " VALUES(? ,1,'T','G','[]')", (did,))
    c.commit()
    yield c
    c.close()


class SysMeta:
    """FakeMeta that also captures system prompts and max_tokens."""

    def __init__(self, manuscripts):
        from tests.test_drafting import FakeMeta  # reuse validation behavior
        self._m = FakeMeta(manuscripts)
        self.systems = []
        self.max_tokens_seen = []

    def chat_json(self, *, bundle, episode, system, user, schema,
                  max_tokens, work_id=None):
        self.systems.append(system)
        self.max_tokens_seen.append(max_tokens)
        return self._m.chat_json(bundle=bundle, episode=episode,
                                 system=system, user=user, schema=schema,
                                 max_tokens=max_tokens, work_id=work_id)


# --- preset catalogs ---

def test_kr_catalog_keys_unchanged():
    assert set(style_presets.PRESETS) == {
        "fusion-fantasy", "romance", "mystery", "classic"}
    assert style_presets.DEFAULT_PRESET == "fusion-fantasy"
    for key, p in style_presets.PRESETS.items():
        assert p["id"] == key and p["market"] == "KR"
        assert isinstance(p["version"], int)
        for f in ("label", "description", "pov", "diction", "feedback"):
            assert p[f], key


def test_us_catalog_schema_and_english():
    assert set(style_presets.US_PRESETS) == {
        "fantasy", "romance", "mystery-thriller", "science-fiction",
        "literary-fiction"}
    for key, p in style_presets.US_PRESETS.items():
        assert p["id"] == key and p["market"] == "US"
        assert isinstance(p["version"], int)
        for f in ("label", "description", "pov", "diction", "feedback"):
            assert p[f], key
            assert not _HANGUL.search(p[f]), (key, f)
    labels = [style_presets.US_PRESETS[k]["label"]
              for k in ("fantasy", "romance", "mystery-thriller",
                        "science-fiction", "literary-fiction")]
    assert labels == ["Fantasy", "Romance", "Mystery / Thriller",
                      "Science Fiction", "Literary Fiction"]


def test_preset_list_markets():
    legacy = style_presets.preset_list()
    assert [p["key"] for p in legacy] == [
        "fusion-fantasy", "romance", "mystery", "classic"]
    us = style_presets.preset_list("US")
    assert [p["key"] for p in us] == [
        "fantasy", "romance", "mystery-thriller", "science-fiction",
        "literary-fiction"]
    assert all(p["market"] == "US" for p in us)
    kr = style_presets.preset_list("KR")
    assert [p["key"] for p in kr] == [p["key"] for p in legacy]
    with pytest.raises(ValueError):
        style_presets.preset_list("GB")


def test_resolve_preset_per_market():
    k, p = style_presets.resolve_preset("romance", "KR")
    assert k == "romance" and "로맨스" in p["label"]
    k, p = style_presets.resolve_preset("romance", "US")
    assert k == "romance" and p["label"] == "Romance"
    assert style_presets.resolve_preset(None, "KR")[0] == "fusion-fantasy"
    assert style_presets.resolve_preset(None, "US")[0] == "fantasy"
    assert style_presets.resolve_preset("nope", "US")[0] == "fantasy"
    assert style_presets.resolve_preset("nope")[0] == "fusion-fantasy"


def test_set_style_preset_market_aware(conn):
    out = drafting.set_style_preset(conn, 2, "fantasy")
    assert out["preset"] == "fantasy"
    row = dict(conn.execute("SELECT * FROM style_policies WHERE work_id=2"
                            ).fetchone())
    assert "Third-person" in row["pov"]
    out = drafting.set_style_preset(conn, 2, "romance")
    row = dict(conn.execute("SELECT * FROM style_policies WHERE work_id=2"
                            ).fetchone())
    assert row["pov"] == style_presets.US_PRESETS["romance"]["pov"]
    assert out["length_unit"] == "words"
    with pytest.raises(ValueError, match="unknown style preset"):
        drafting.set_style_preset(conn, 1, "fantasy")  # US key on KR work
    with pytest.raises(ValueError, match="unknown style preset"):
        drafting.set_style_preset(conn, 2, "fusion-fantasy")
    with pytest.raises(ValueError, match="unknown style preset"):
        drafting.set_style_preset(conn, 2, "nope")


def test_get_style_market_defaults(conn):
    assert drafting.get_style(conn, 1)["style_preset"] == "fusion-fantasy"
    assert drafting.get_style(conn, 2)["style_preset"] == "fantasy"
    # A US key on a US work is not "legacy": voice must survive a re-read.
    drafting.set_style_preset(conn, 2, "mystery-thriller")
    assert drafting.get_style(conn, 2)["style_preset"] == "mystery-thriller"


def test_preset_snapshot_stable(conn):
    drafting.set_style_preset(conn, 2, "fantasy")
    before = drafting.get_style(conn, 2)["pov"]
    orig = style_presets.US_PRESETS["fantasy"]["pov"]
    style_presets.US_PRESETS["fantasy"]["pov"] = "CHANGED AFTER SNAPSHOT"
    try:
        assert drafting.get_style(conn, 2)["pov"] == before
    finally:
        style_presets.US_PRESETS["fantasy"]["pov"] = orig


# --- length units ---

@pytest.mark.parametrize("text,want", [
    ("", 0), ("   \n\t ", 0), (None, 0),
    ("hello", 1), ("hello world", 2),
    ("  a   b\nc\t\td  ", 4),
    ("The Last Bookshop", 3),
])
def test_count_words(text, want):
    assert drafting.count_words(text) == want


def test_length_unit_validation():
    assert drafting.validate_length_unit("words") == "words"
    assert drafting.validate_length_unit(" Characters ") == "characters"
    with pytest.raises(ValueError, match="length_unit"):
        drafting.validate_length_unit("pages")
    assert drafting.measure_length("a b c", "words") == 3
    assert drafting.measure_length("a b c", "characters") == 5


def test_effective_length_unit(conn):
    assert drafting.effective_length_unit(conn, 1) == "characters"
    assert drafting.effective_length_unit(conn, 2) == "words"
    assert drafting.effective_length_unit(conn, 2, "characters") == "characters"
    assert drafting.get_length_range(conn, 1)["length_unit"] == "characters"
    assert drafting.get_length_range(conn, 2)["length_unit"] == "words"
    with pytest.raises(ValueError, match="length_unit"):
        drafting.effective_length_unit(conn, 2, "pages")


def test_validate_manuscript_words():
    body = " ".join(f"w{i}" for i in range(100))
    assert drafting.validate_manuscript(body, [], "90-110", "words") == []
    bad = drafting.validate_manuscript(body, [], "90-110", "characters")
    assert any("length" in p for p in bad)
    short = " ".join(f"w{i}" for i in range(10))
    bad = drafting.validate_manuscript(short, [], "90-110", "words")
    assert any("length 10 outside" in p for p in bad)
    # Legacy 3-arg calls still measure characters.
    assert drafting.validate_manuscript("짧다", [], "3000-5000") != []


def test_structured_constraints():
    body = "the silver key opened the door " + "x" * 3000
    ok = drafting.validate_manuscript(
        body, [{"type": "required_phrase", "value": "the silver key"},
               {"type": "forbidden_phrase", "value": "it was all a dream"}],
        "3000-5000")
    assert ok == []
    bad = drafting.validate_manuscript(
        "x" * 3000, [{"type": "required_phrase", "value": "the silver key"}],
        "3000-5000")
    assert any("missing required phrase: the silver key" in p for p in bad)
    bad = drafting.validate_manuscript(
        "it was all a dream " + "x" * 3000,
        [{"type": "forbidden_phrase", "value": "it was all a dream"}],
        "3000-5000")
    assert any("forbidden phrase present" in p for p in bad)
    # Legacy strings and unknown shapes keep working / stay ignored.
    ok = drafting.validate_manuscript(
        "달빛 " + "가" * 3000,
        ["필수:달빛", {"type": "note", "value": "zzz"}, "plain", None, 42],
        "3000-5000")
    assert ok == []


def test_retry_feedback_units():
    detail = "length 10 outside ±30% of 90-110 (63-143)"
    en = drafting._retry_feedback(detail, "words")
    assert "[Length correction]" in en and "10 words" in en
    assert "자" not in en
    ko = drafting._retry_feedback(detail, "characters")
    assert "[분량 교정]" in ko and "10자" in ko
    assert "[Validation correction]" in drafting._retry_feedback(
        "missing x", "words")
    assert "검증 교정" in drafting._retry_feedback("missing x")


def test_output_budget_units():
    assert drafting.draft_output_budget(7000) == 13650
    assert drafting.draft_output_budget(10000) == 19500
    assert drafting.draft_output_budget(100) == 8000
    n, d = budgetmod.EN_BUDGET_NUM, budgetmod.EN_BUDGET_DEN
    assert drafting.draft_output_budget(10000, length_unit="words") == \
        max(8000, (10000 * n + d - 1) // d)
    assert drafting.draft_output_budget(100, length_unit="words") == 8000
    assert budgetmod.en_draft_output_budget(10000) == \
        drafting.draft_output_budget(10000, length_unit="words")


# --- prompt wiring ---

def _prompt_tree(tmp_path):
    base = tmp_path / "prompts"
    (base / "ko-KR").mkdir(parents=True)
    (base / "en-US").mkdir(parents=True)
    (base / "ko-KR" / "drafting.txt").write_text("KOREAN", encoding="utf-8")
    (base / "en-US" / "drafting.txt").write_text("ENGLISH", encoding="utf-8")
    return base


def test_resolve_drafting_prompt_locale(tmp_path, conn):
    base = _prompt_tree(tmp_path)
    assert drafting.resolve_drafting_prompt(
        conn, 1, base_dir=base).text == "KOREAN"
    assert drafting.resolve_drafting_prompt(
        conn, 2, base_dir=base).text == "ENGLISH"


def test_resolve_drafting_prompt_no_fallback(tmp_path, conn):
    base = tmp_path / "prompts_ko_only"
    (base / "ko-KR").mkdir(parents=True)
    (base / "ko-KR" / "drafting.txt").write_text("KOREAN", encoding="utf-8")
    with pytest.raises(FileNotFoundError) as e:
        drafting.resolve_drafting_prompt(conn, 2, base_dir=base)
    assert "drafting" in str(e.value) and "en-US" in str(e.value)


def test_draft_uses_resolved_prompt(conn):
    flat = (Path(drafting.__file__).parent / "prompts" / "drafting.txt"
            ).read_text(encoding="utf-8")
    meta = SysMeta(["가" * 5000])
    out = drafting.draft_episode(meta, conn, design_id=1, work_id=1,
                                 episode_no=1)
    assert out["length"] == 5000 and out["length_unit"] == "characters"
    assert meta.systems and meta.systems[0] == flat
    assert "다음 입력으로 원고를 써라" in meta._m.calls[0]


def test_draft_words_end_to_end(conn):
    drafting.set_length_target(conn, 2, 90, 110)
    body = " ".join(f"w{i}" for i in range(100))
    meta = SysMeta([body])
    # EN prompt tree is owned by the prompts agent; seed one via base_dir
    # is impossible through draft_episode, so this path currently raises
    # until prompts/en-US/drafting.txt lands (contracts: no KR fallback).
    try:
        out = drafting.draft_episode(meta, conn, design_id=2, work_id=2,
                                     episode_no=1)
    except FileNotFoundError as e:
        assert "en-US" in str(e)
        pytest.skip("prompts/en-US/drafting.txt not landed yet")
    assert out["length"] == 100 and out["length_unit"] == "words"


# --- custom styles passthrough ---

def test_apply_custom_passes_unit_through(conn, tmp_path):
    author = authmod.public_author(authmod.register(
        conn, login_id="stylefan", pen_name="p", password="pw1234"))
    row = custom_styles.create_custom(conn, author["id"], "mine", pov="v")
    drafting.set_length_target(conn, 2, 90, 110)
    out = custom_styles.apply_custom(conn, 2, author["id"], row["id"])
    assert (out["length_min"], out["length_max"],
            out["length_unit"]) == (90, 110, "words")
    out = custom_styles.apply_custom(conn, 1, author["id"], row["id"])
    assert out["length_unit"] == "characters"


def test_fragments_parse():
    from app import drafting as _d  # module path anchor
    frag = Path(_d.__file__).resolve().parents[2] / "frontend" / "locales"
    ko = json.loads((frag / "_frag" / "server-a.ko-KR.json"
                     ).read_text(encoding="utf-8"))
    en = json.loads((frag / "_frag" / "server-a.en-US.json"
                     ).read_text(encoding="utf-8"))
    assert isinstance(ko, dict) and isinstance(en, dict)
    assert set(ko) == set(en)
