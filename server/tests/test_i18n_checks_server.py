"""Server i18n-B: locale-gated checks, prompts, caps, recovery (plan §6/§8/§9).

ko-KR paths must stay byte-identical to the legacy behavior; en-US paths
use structural matching, English directives/messages, and prompt_loader.
The en-US prompt *files* belong to the prompts agents, so these tests
emulate translated prompts via a monkeypatched loader instead of
touching server/app/prompts/.
"""
from __future__ import annotations

import json
import re

import httpx
import pytest
from fastapi import HTTPException
from pydantic import BaseModel

from app import checks, consistency, db as dbmod, drafting, extraction
from app import ideas as ideasmod
from app import memory, meta_client as metac
from app import prompt_loader, publishing, quality, verification
from app.routes import ideas as ideas_routes

_HANGUL = re.compile(r"[\uac00-\ud7a3]")


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.connect(tmp_path / "i18n_b.db")
    c.execute("INSERT INTO works(id, title, created_at) VALUES(1,'kr','t')")
    c.execute("INSERT INTO works(id, title, market, writing_locale,"
              " created_at) VALUES(2,'us','US','en-US','t')")
    c.execute("INSERT INTO story_designs(id, work_id, version,"
              " scope_total_episodes, concept_json, sections_json, status)"
              " VALUES(1,1,1,2,'{}','[]','approved')")
    c.execute("INSERT INTO story_designs(id, work_id, version,"
              " scope_total_episodes, concept_json, sections_json, status)"
              " VALUES(2,2,1,2,'{}','[]','approved')")
    c.commit()
    yield c
    c.close()


@pytest.fixture()
def en_prompts(monkeypatch):
    """Emulate translated en-US prompts; ko-KR uses the real loader."""
    real_load = prompt_loader.load

    def fake_load(feature, writing_locale, market, base_dir=None):
        if writing_locale == "en-US":
            return prompt_loader.PromptBundle(
                text=f"EN-{feature}", feature=feature,
                writing_locale=writing_locale, market=market,
                locale_path=f"en-US/{feature}.txt", overlay_path=None,
                legacy_fallback=False)
        return real_load(feature, writing_locale, market, base_dir=base_dir)

    monkeypatch.setattr(prompt_loader, "load", fake_load)
    return fake_load


class AnyMeta:
    """Tolerant fake: records every call, replays payloads in order."""

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def chat_json(self, **kw):
        self.calls.append(kw)
        return kw["schema"].model_validate(
            self.payloads.pop(0)).model_dump()


class LegacyMeta:
    """Seven-parameter fake mirroring the pre-i18n signature."""

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def chat_json(self, *, bundle, episode, system, user, schema, max_tokens,
                  work_id=None):
        self.calls.append({"bundle": bundle, "system": system, "user": user})
        return schema.model_validate(self.payloads.pop(0)).model_dump()


def _rev(conn, wid, ep, text):
    return drafting.save_revision(conn, wid, ep, text)["revision_id"]


def _commit(conn, wid, ep, text, items, kind_default="attribute"):
    """Commit items via the public proposal flow (mirrors eval tests)."""
    rev = _rev(conn, wid, ep, text)
    full = [{"entity_id": i.get("entity_id"),
             "candidate_name": i.get("candidate_name", ""),
             "kind": i.get("kind", kind_default),
             "operation": i.get("operation", "set"),
             "attribute_path": i.get("attribute_path", ""),
             "old_value": i.get("old_value", ""),
             "new_value": i.get("new_value", ""),
             "standard_value": i.get("standard_value", ""),
             "story_time": i.get("story_time", ""),
             "narrative_mode": i.get("narrative_mode", "narration"),
             "evidence_quote": i.get("evidence_quote", text)}
            for i in items]
    sid = memory.store_proposal(
        conn, episode_no=ep, revision_id=rev, items=full, work_id=wid,
        base_memory_version=memory.current_memory_version(conn, wid))
    memory.save_episode_memory(conn, episode_no=ep, revision_id=rev,
                               proposal_set_id=sid, work_id=wid)
    return sid


def _pend(conn, wid, ep, text, items):
    rev = _rev(conn, wid, ep, text)
    full = [{"entity_id": i.get("entity_id"),
             "candidate_name": i.get("candidate_name", ""),
             "kind": i.get("kind", "attribute"),
             "operation": i.get("operation", "set"),
             "attribute_path": i.get("attribute_path", ""),
             "old_value": i.get("old_value", ""),
             "new_value": i.get("new_value", ""),
             "standard_value": i.get("standard_value", ""),
             "story_time": i.get("story_time", ""),
             "narrative_mode": i.get("narrative_mode", "narration"),
             "evidence_quote": i.get("evidence_quote", text)}
            for i in items]
    return memory.store_proposal(
        conn, episode_no=ep, revision_id=rev, items=full, work_id=wid,
        base_memory_version=memory.current_memory_version(conn, wid))


# --- checks: Korean rules gated by writing_locale ---------------------------

def test_related_stem_heuristic_is_ko_only():
    assert checks._related("마력제한", "마력사용") is True
    assert checks._related("마력제한", "마력사용", "en-US") is False
    assert checks._related("ability.magic.cap", "ability.magic.use",
                           "en-US") is True
    assert checks._related("ability.magic.cap", "ability.magic.use") is True


def test_count_limits_ko_markers_gated(conn):
    memory.create_entity(conn, entity_id="ch_ko", work_id=1,
                         type_="character", name="마법사", first_episode=1)
    _commit(conn, 1, 1, "마법사는 하루에 한 번 쓴다", [{
        "entity_id": "ch_ko", "kind": "rule", "operation": "set",
        "attribute_path": "마력제한", "standard_value": 1}])
    sid = _pend(conn, 1, 2, "마법사는 두 번 썼다", [{
        "entity_id": "ch_ko", "kind": "event", "operation": "set",
        "attribute_path": "마력사용", "standard_value": 2,
        "story_time": "day1"}])
    ko = checks.check_count_limits(conn, 1, 2, sid)
    assert len(ko) == 1 and ko[0]["verdict"] == "conflict"
    assert "횟수 제한 위반" in ko[0]["detail"]
    en = checks.check_count_limits(conn, 1, 2, sid, "en-US")
    assert en == []  # Korean markers + stem matching do not apply


def test_count_limits_en_structural_markers(conn):
    memory.create_entity(conn, entity_id="ch_en", work_id=2,
                         type_="character", name="Mage", first_episode=1)
    _commit(conn, 2, 1, "The mage casts once a day", [{
        "entity_id": "ch_en", "kind": "rule", "operation": "set",
        "attribute_path": "spell.max_uses_per_day", "standard_value": 1}])
    sid = _pend(conn, 2, 2, "The mage casts twice", [{
        "entity_id": "ch_en", "kind": "event", "operation": "set",
        "attribute_path": "spell.max_uses_per_day", "standard_value": 2,
        "story_time": "day1"}])
    out = checks.check_count_limits(conn, 2, 2, sid, "en-US")
    assert len(out) == 1 and out[0]["verdict"] == "conflict"
    assert "Count-limit violation" in out[0]["detail"]
    assert not _HANGUL.search(out[0]["title"] + out[0]["detail"])


def test_death_vocab_is_locale_keyed(conn):
    memory.create_entity(conn, entity_id="ch_d", work_id=2,
                         type_="character", name="John", first_episode=1)
    _commit(conn, 2, 1, "John died", [{
        "entity_id": "ch_d", "kind": "state", "operation": "set",
        "attribute_path": "status", "standard_value": "dead",
        "new_value": "dead"}])
    sid = _pend(conn, 2, 2, "John walks", [{
        "entity_id": "ch_d", "kind": "event", "operation": "set",
        "attribute_path": "act", "evidence_quote": "John walks"}])
    en = checks.check_death(conn, 2, 2, sid, "en-US")
    assert len(en) == 1 and en[0]["verdict"] == "conflict"
    assert en[0]["title"] == "Post-death action"
    assert "John" in en[0]["detail"] and "EP1" in en[0]["detail"]
    assert not _HANGUL.search(en[0]["title"] + en[0]["detail"])
    assert checks.check_death(conn, 2, 2, sid) == []

    memory.create_entity(conn, entity_id="ch_k", work_id=1,
                         type_="character", name="서연", first_episode=1)
    _commit(conn, 1, 1, "서연은 죽었다", [{
        "entity_id": "ch_k", "kind": "state", "operation": "set",
        "attribute_path": "생사", "standard_value": "사망",
        "new_value": "사망"}])
    sid2 = _pend(conn, 1, 2, "서연은 걷는다", [{
        "entity_id": "ch_k", "kind": "event", "operation": "set",
        "attribute_path": "행동", "evidence_quote": "서연은 걷는다"}])
    ko = checks.check_death(conn, 1, 2, sid2)
    assert len(ko) == 1
    assert ko[0]["title"] == "사망 후 행동"
    assert "서연" in ko[0]["detail"]


def test_exclusive_location_predicate_per_locale(conn):
    memory.create_entity(conn, entity_id="ch_l", work_id=2,
                         type_="character", name="Ann", first_episode=1)
    sid = _pend(conn, 2, 2, "Ann is here and there", [
        {"entity_id": "ch_l", "kind": "state", "operation": "set",
         "attribute_path": "location", "standard_value": "tower",
         "story_time": "dawn"},
        {"entity_id": "ch_l", "kind": "state", "operation": "set",
         "attribute_path": "location", "standard_value": "harbor",
         "story_time": "dawn"}])
    out = checks.check_exclusive_location(conn, 2, 2, sid, "en-US")
    assert len(out) == 1
    assert out[0]["title"] == "Conflicting locations at the same point"
    assert "'tower'" in out[0]["detail"] and "'harbor'" in out[0]["detail"]
    # The Korean predicate does not match the EN check.
    sid2 = _pend(conn, 2, 2, "앤은 여기저기", [
        {"entity_id": "ch_l", "kind": "state", "operation": "set",
         "attribute_path": "위치", "standard_value": "탑",
         "story_time": "dawn"},
        {"entity_id": "ch_l", "kind": "state", "operation": "set",
         "attribute_path": "위치", "standard_value": "항구",
         "story_time": "dawn"}])
    assert checks.check_exclusive_location(conn, 2, 2, sid2, "en-US") == []


def test_state_clash_matches_both_locales(conn):
    memory.create_entity(conn, entity_id="ch_s", work_id=2,
                         type_="character", name="Bob", first_episode=1)
    _commit(conn, 2, 1, "Bob is a novice", [{
        "entity_id": "ch_s", "kind": "state", "operation": "set",
        "attribute_path": "rank", "standard_value": "novice",
        "story_time": "day1"}])
    sid = _pend(conn, 2, 2, "Bob is a master", [{
        "entity_id": "ch_s", "kind": "state", "operation": "set",
        "attribute_path": "rank", "standard_value": "master",
        "story_time": "day1"}])
    en = checks.check_state_clash(conn, 2, 2, sid, "en-US")
    ko = checks.check_state_clash(conn, 2, 2, sid)
    assert len(en) == len(ko) == 1
    assert en[0]["title"] == "Same-point state clash: rank"
    assert not _HANGUL.search(en[0]["detail"])
    assert ko[0]["title"] == "같은 시점의 상태 충돌: rank"


def test_run_reports_locale_and_en_limits(conn):
    out = checks.run_deterministic_checks(conn, 2, 2, None, "en-US")
    assert out["writing_locale"] == "en-US"
    assert len(out["notes"]) == 3
    assert all(r["status"] == "completed" for r in out["ran"])
    ko = checks.run_deterministic_checks(conn, 1, 2, None)
    assert ko["writing_locale"] == "ko-KR" and ko["notes"] == []


# --- publishing: loader wiring, EN word cap, coded errors -------------------

_CTX = {"title": "t", "logline": "l", "synopsis": "", "outlines": []}


def test_blurb_en_word_cap_and_directive(en_prompts):
    meta = AnyMeta([{"blurb": " ".join(f"w{i}" for i in range(150))}])
    out = publishing.make_blurb(meta, 2, _CTX, writing_locale="en-US",
                                market="US")
    assert out == " ".join(f"w{i}" for i in range(100))
    assert publishing.count_words(out) == 100
    sent = meta.calls[0]
    assert sent["system"] == "EN-blurb"
    assert sent["user"].startswith("Work info. Skeleton:")
    assert not _HANGUL.search(sent["user"])


def test_blurb_empty_raises_coded_error(en_prompts):
    meta = AnyMeta([{"blurb": "   "}])
    with pytest.raises(HTTPException) as e:
        publishing.make_blurb(meta, 2, _CTX, writing_locale="en-US",
                              market="US")
    assert e.value.status_code == 400
    assert e.value.detail["code"] == publishing.BLURB_EMPTY
    assert e.value.detail["params"]["writing_locale"] == "en-US"


def test_blurb_ko_legacy_path_uses_loader_text():
    meta = AnyMeta([{"blurb": "가" * 600}])
    out = publishing.make_blurb(meta, 1, _CTX)
    assert len(out) == 500
    assert meta.calls[0]["system"] == publishing.BLURB_PROMPT
    assert meta.calls[0]["user"].startswith("작품 정보다. 스켈레톤:")


def test_count_words_contract_definition():
    assert publishing.count_words("") == 0
    assert publishing.count_words("   \n\t ") == 0
    assert publishing.count_words("  alpha  beta\ngamma ") == 3


def test_publish_record_messages_per_locale():
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        publishing.check_publish_record("2026-13-99", "", "en-US")
    with pytest.raises(ValueError, match="등록일은 YYYY-MM-DD"):
        publishing.check_publish_record("2026-13-99", "")
    with pytest.raises(ValueError, match="at most 200 characters"):
        publishing.check_publish_record("2026-11-03", "x" * 201, "en-US")
    with pytest.raises(ValueError, match="200자 이하"):
        publishing.check_publish_record("2026-11-03", "x" * 201)


def test_save_cover_messages_per_locale():
    with pytest.raises(ValueError, match="jpg/png"):
        publishing.save_cover(2, "c.gif", "", "en-US")
    with pytest.raises(ValueError, match="jpg/png만"):
        publishing.save_cover(1, "c.gif", "")


def test_blurb_empty_ko_also_coded():
    meta = AnyMeta([{"blurb": ""}])
    with pytest.raises(HTTPException) as e:
        publishing.make_blurb(meta, 1, _CTX)
    assert e.value.status_code == 400
    assert e.value.detail["code"] == publishing.BLURB_EMPTY


# --- meta_client: recovery routing, provenance, resolvers --------------------

class _Toy(BaseModel):
    answer: str


def _mock_client(handler, records, **kw):
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://x.test/v1")
    return metac.MetaClient(
        api_key="k", base_url="https://x.test/v1", model_id="m",
        timeout_s=5, max_retry=0, recorder=records.append,
        http_client=http, **kw)


def _ok(content):
    return {"choices": [{"message": {"content": content},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


def test_recovery_prompt_routing_and_skeleton():
    ko = metac._recovery_prompt(_Toy)
    assert "JSON만 다시 출력하라" in ko
    assert '{"answer": ""}' in ko
    en = metac._recovery_prompt(_Toy, "en-US")
    assert "Output only correct JSON" in en
    assert '{"answer": ""}' in en
    assert not _HANGUL.search(en)


def test_chat_json_records_i18n_provenance():
    recs = []
    seen = []

    def h(request):
        seen.append(json.loads(request.content.decode())["messages"])
        if len(seen) == 1:
            return httpx.Response(200, json=_ok('{"wrong": 1}'))
        return httpx.Response(200, json=_ok('{"answer": "fixed"}'))

    c = _mock_client(h, recs)
    out = c.chat_json(bundle="verify", episode=2, system="s", user="u",
                      schema=_Toy, max_tokens=100, work_id=2,
                      writing_locale="en-US", market="US",
                      prompt_feature="verify", prompt_version="",
                      preset_id="fantasy", preset_version="")
    assert out == {"answer": "fixed"}
    assert len(seen) == 2  # first call + one EN recovery retry
    assert "Output only correct JSON" in seen[1][-1]["content"]
    r = recs[0]
    assert (r["market"], r["writing_locale"], r["prompt_feature"],
            r["prompt_version"], r["preset_id"],
            r["preset_version"]) == ("US", "en-US", "verify", "",
                                     "fantasy", "")
    assert "[recovery retry]" in r["prompt_user"]
    assert not _HANGUL.search(r["prompt_user"])


def test_chat_json_legacy_defaults_recorded():
    recs = []

    def h(request):
        return httpx.Response(200, json=_ok('{"answer": "yes"}'))

    c = _mock_client(h, recs)
    c.chat_json(bundle="verify", episode=None, system="s", user="u",
                schema=_Toy, max_tokens=100)
    r = recs[0]
    assert (r["market"], r["writing_locale"]) == ("KR", "ko-KR")
    assert r["prompt_feature"] == ""


def test_chat_with_provenance_routes_by_signature():
    legacy = LegacyMeta([{"answer": "a"}])
    metac.chat_with_provenance(
        legacy, bundle="b", episode=None, system="s", user="u",
        schema=_Toy, max_tokens=10, writing_locale="en-US", market="US",
        prompt_feature="verify")
    assert legacy.calls == [{"bundle": "b", "system": "s", "user": "u"}]
    full = AnyMeta([{"answer": "a"}])
    metac.chat_with_provenance(
        full, bundle="b", episode=None, system="s", user="u",
        schema=_Toy, max_tokens=10, writing_locale="en-US", market="US",
        prompt_feature="verify", preset_id="p")
    sent = full.calls[0]
    assert (sent["writing_locale"], sent["market"], sent["prompt_feature"],
            sent["preset_id"]) == ("en-US", "US", "verify", "p")


def test_work_locale_and_preset_ref(conn):
    assert metac.work_locale(conn, 1) == ("KR", "ko-KR")
    assert metac.work_locale(conn, 2) == ("US", "en-US")
    assert metac.work_locale(conn, 999) == ("KR", "ko-KR")
    assert metac.work_locale(None, None) == ("KR", "ko-KR")
    assert metac.preset_ref(conn, 1) == ("", "")
    conn.execute("INSERT INTO style_policies(work_id, pov, updated_at)"
                 " VALUES(2,'x','t')")
    conn.execute("UPDATE style_policies SET style_preset='us-fantasy'"
                 " WHERE work_id=2")
    assert metac.preset_ref(conn, 2) == ("us-fantasy", "")
    assert metac.preset_ref(conn, 999) == ("", "")


def test_feature_prompt_resolves_work_locale(conn, monkeypatch):
    seen = {}

    class _B:
        text = "T"

    def fake_load(feature, writing_locale, market, base_dir=None):
        seen.update(feature=feature, locale=writing_locale, market=market)
        return _B()

    monkeypatch.setattr(prompt_loader, "load", fake_load)
    b, loc, mkt = metac.feature_prompt(conn, 2, "verify")
    assert (b.text, loc, mkt) == ("T", "en-US", "US")
    assert seen == {"feature": "verify", "locale": "en-US", "market": "US"}
    b, loc, mkt = metac.feature_prompt(conn, 2, "verify", "ko-KR", "KR")
    assert (loc, mkt) == ("ko-KR", "KR")  # explicit args win


# --- verify/quality/extraction wiring ----------------------------------------

def _pending_set(conn, wid, ep, text, items):
    rev = _rev(conn, wid, ep, text)
    sid = memory.store_proposal(
        conn, episode_no=ep, revision_id=rev,
        items=[{"entity_id": i.get("entity_id"),
                "candidate_name": i.get("candidate_name", ""),
                "kind": i.get("kind", "attribute"),
                "operation": i.get("operation", "set"),
                "attribute_path": i.get("attribute_path", ""),
                "new_value": i.get("new_value", ""),
                "standard_value": i.get("standard_value", ""),
                "story_time": i.get("story_time", ""),
                "evidence_quote": i.get("evidence_quote", text)}
               for i in items],
        work_id=wid,
        base_memory_version=memory.current_memory_version(conn, wid))
    return rev, sid


def test_verify_proposals_en_prompt_and_directive(conn, en_prompts):
    rev, sid = _pending_set(conn, 2, 1, "John is a mage", [{
        "candidate_name": "John", "attribute_path": "job",
        "new_value": "mage"}])
    item_id = memory.get_proposals(conn, 1, 2)[0]["item_id"]
    meta = AnyMeta([{"verdicts": [{"item_id": item_id, "verdict": "ok",
                                   "reason": "t",
                                   "counter_evidence_quote": ""}]}])
    out = verification.verify_proposals(
        meta, conn, episode_no=1, revision_id=rev, proposal_set_id=sid,
        manuscript="John is a mage", work_id=2)
    assert out["counts"] == {"ok": 1}
    sent = meta.calls[0]
    assert sent["system"] == "EN-verify"
    assert sent["user"].split("\n", 1)[0].startswith(
        "Judge the following change proposals.")
    assert sent["prompt_feature"] == "verify"
    assert sent["writing_locale"] == "en-US" and sent["market"] == "US"


def test_verify_proposals_ko_unchanged(conn):
    rev, sid = _pending_set(conn, 1, 1, "서연은 기사다", [{
        "candidate_name": "서연", "attribute_path": "직업",
        "new_value": "기사"}])
    item_id = memory.get_proposals(conn, 1, 1)[0]["item_id"]
    meta = AnyMeta([{"verdicts": [{"item_id": item_id, "verdict": "ok",
                                   "reason": "t",
                                   "counter_evidence_quote": ""}]}])
    verification.verify_proposals(
        meta, conn, episode_no=1, revision_id=rev, proposal_set_id=sid,
        manuscript="서연은 기사다", work_id=1)
    assert meta.calls[0]["system"] == verification.PROMPT
    assert meta.calls[0]["user"].split("\n", 1)[0].startswith(
        "다음 변경안을 판정하라.")


def test_build_fix_instruction_per_locale(conn):
    rev, sid = _pending_set(conn, 1, 1, "서연은 걷는다", [{
        "candidate_name": "서연", "attribute_path": "생사",
        "evidence_quote": "서연은 걷는다"}])
    item_id = memory.get_proposals(conn, 1, 1)[0]["item_id"]
    memory.set_item_validation(conn, item_id, "error", "r", "")
    ko = verification.build_fix_instruction(conn, 1, sid)
    assert "고쳐 써라" in ko and "문제 문장" in ko
    en = verification.build_fix_instruction(conn, 1, sid, "en-US")
    assert en.startswith("Rewrite the manuscript")
    assert "Problem sentence" in en
    # Template lines are English; user data (names/quotes) is preserved.
    assert not _HANGUL.search(en.split("\n", 1)[0])
    assert "서연은 걷는다" in en


def test_run_quality_en_directive_and_label(conn, en_prompts):
    meta = AnyMeta([{"scores": {"immersion": 0.5, "tension": 0.5,
                                "coherence": 0.9},
                     "issues": [],
                     "proofread_edits": [{"start_quote": "world",
                                          "replacement": "there",
                                          "reason": "r"}]}])
    out = quality.run_quality(
        meta, conn, episode_no=1, revision_id=None,
        manuscript="Hello world", outline={}, work_id=2)
    assert out["gate_pass"] is True and out["edits_applied"] == 1
    assert out["manuscript"] == "Hello there"
    row = conn.execute("SELECT label FROM manuscript_revisions"
                       " WHERE revision_id=?",
                       (out["new_revision"]["revision_id"],)).fetchone()
    assert row["label"] == "AI proofread"
    sent = meta.calls[0]
    assert sent["system"] == "EN-quality"
    assert sent["user"].startswith("Evaluate and proofread")


def test_build_quality_fix_per_locale():
    ko = quality.build_quality_fix(["x"], {"coherence": 0.1})
    assert "품질 게이트 미달" in ko
    en = quality.build_quality_fix(["x"], {"coherence": 0.1}, "en-US")
    assert en.startswith("Quality gate missed")
    assert not _HANGUL.search(en)


def test_extract_changes_en_directive(conn, en_prompts):
    rev = _rev(conn, 2, 1, "John is a mage")
    meta = AnyMeta([{"items": [], "mentions": [], "events": []}])
    out = extraction.extract_changes(
        meta, conn, episode_no=1, revision_id=rev,
        manuscript="John is a mage", work_id=2)
    assert out["empty"] is True
    sent = meta.calls[0]
    assert sent["system"] == "EN-entity"
    assert sent["user"].startswith("Extract change items")
    assert sent["prompt_version"] == extraction.PROMPT_VERSION
    assert '"entity_ref"' in sent["user"]  # JSON keys preserved


def test_extract_changes_ko_unchanged(conn):
    rev = _rev(conn, 1, 1, "본문")
    meta = AnyMeta([{"items": [], "mentions": [], "events": []}])
    extraction.extract_changes(
        meta, conn, episode_no=1, revision_id=rev, manuscript="본문",
        work_id=1)
    assert meta.calls[0]["system"] == extraction.PROMPT
    assert meta.calls[0]["user"].startswith("다음 입력에서 변경 항목을 추출하라.")


# --- consistency: locale threading, messages ----------------------------------

def test_homonym_candidates_per_locale(conn):
    memory.create_entity(conn, entity_id="ch_h", work_id=2,
                         type_="character", name="John", first_episode=1)
    pend = [{"entity_id": None, "candidate_name": "John",
             "evidence_quote": "q"}]
    en = consistency.homonym_candidates(conn, 2, pend, "en-US")
    assert en[0]["title"] == "Possible homonym: John"
    assert not _HANGUL.search(en[0]["title"] + en[0]["detail"])
    ko = consistency.homonym_candidates(conn, 2, pend)
    assert ko[0]["title"] == "동명이인 의심: John"


def test_analyze_skip_reason_per_locale(conn, en_prompts):
    rev = _rev(conn, 2, 1, "John walks")
    meta = AnyMeta([{"issues": []}])
    out = consistency.analyze_episode(meta, conn, episode_no=1,
                                      revision_id=rev, work_id=2)
    assert out["skipped"] is True
    assert out["analysis"]["status"] == "skipped"
    coverage = json.loads(out["analysis"]["coverage_json"])
    assert coverage["reason"] == "no prior memory"
    assert meta.calls == []


def test_analyze_en_prompt_and_directive(conn, en_prompts):
    _rev(conn, 2, 1, "John died yesterday")
    rev2 = _rev(conn, 2, 2, "John walks today")
    meta = AnyMeta([{"issues": []}])
    out = consistency.analyze_episode(meta, conn, episode_no=2,
                                      revision_id=rev2, work_id=2)
    assert out["analysis"]["status"] == "completed"
    sent = meta.calls[0]
    assert sent["system"] == "EN-consistency"
    assert sent["user"].startswith("Compare the current manuscript")
    assert sent["prompt_version"] == consistency.PROMPT_VERSION
    assert "John died yesterday" in sent["user"]  # prev episode contrasted


def test_chat_en_prompt_and_directive(conn, en_prompts):
    rev = _rev(conn, 2, 1, "Some text here")
    meta = AnyMeta([{"reply": "All good", "issues": []}])
    out = consistency.chat(meta, conn, episode_no=1, revision_id=rev,
                           message="Any issues?", work_id=2)
    assert out["reply"] == "All good"
    sent = meta.calls[0]
    assert sent["system"] == "EN-consistency"
    assert sent["user"].startswith("Answer the author's question")


def _job(conn, wid, ep, state, rev_id=None):
    conn.execute("INSERT INTO episode_jobs(work_id, episode_no, design_id,"
                 " state, attempt, manuscript_revision_id, updated_at)"
                 " VALUES(?,?,?,?,?,?,?)",
                 (wid, ep, wid, state, 0, rev_id, "t"))
    conn.commit()


def test_fix_guards_en_messages(conn, en_prompts):
    meta = AnyMeta([{"manuscript": "x"}])
    _job(conn, 2, 1, "WRITING")
    with pytest.raises(ValueError, match="while in review"):
        consistency.fix_manuscript_issues(meta, conn, episode_no=1,
                                          work_id=2, issue_ids=[1])
    rev = _rev(conn, 2, 2, "Some text here for the fix flow")
    _job(conn, 2, 2, "IN_REVIEW", rev)
    with pytest.raises(ValueError, match="no issues selected"):
        consistency.fix_manuscript_issues(meta, conn, episode_no=2,
                                          work_id=2, issue_ids=[])


def test_fix_rejects_stale_revision_en(conn, en_prompts):
    meta = AnyMeta([{"manuscript": "x"}])
    rev = _rev(conn, 2, 1, "Current text here")
    _job(conn, 2, 1, "IN_REVIEW", rev)
    [issue] = consistency.add_issues(conn, 1, [{
        "title": "stale", "manuscript_quote": "Current text here"}], 2,
        revision_id=rev + 100, manuscript="Current text here")["issues"]
    with pytest.raises(ValueError, match="re-analysis is required"):
        consistency.fix_manuscript_issues(meta, conn, episode_no=1,
                                          work_id=2,
                                          issue_ids=[issue["issue_id"]])


# --- ideas routes: EN context builders ----------------------------------------

def test_clip_helpers_per_locale():
    text, cut = ideas_routes._clip("가" * 7000, 6000)
    assert cut is True and text.endswith("…(이하 1000자 생략)")
    text, cut = ideas_routes._clip("x" * 7000, 6000, "en-US")
    assert cut is True and text.endswith("…(1000 chars omitted)")
    lines = ideas_routes._clip_lines(["a", "b", "c"], 2)
    assert lines[-1] == "…(2개 생략)"
    lines = ideas_routes._clip_lines(["a", "b", "c"], 2, "en-US")
    assert lines[-1] == "…(2 omitted)"


def test_proposal_digest_en():
    m = {"proposals": {"settings": [{"category": "world", "title": "R",
                                     "summary": "s", "detail": "d"}]}}
    out = ideas_routes._proposal_digest(
        m, [], {"content": ""}, {}, "en-US")
    assert out == "\n  (This reply proposed: Settings [world]R(pending))"
    ko = ideas_routes._proposal_digest(m, [], {"content": ""}, {})
    assert ko == "\n  (이 답변의 제안: 설정 [world]R(미반영))"


def test_normalize_proposals_untitled_per_locale():
    got = ideas_routes._normalize_setting_proposals(
        [{"category": "c", "title": "", "summary": ""}], [], "en-US")
    assert got[0]["title"] == "Untitled"
    got = ideas_routes._normalize_setting_proposals(
        [{"category": "c", "title": "", "summary": ""}], [])
    assert got[0]["title"] == "무제"


def test_handoff_user_text_en():
    w = {"title": "T"}
    struct = {"use_part": True, "use_chapter": False,
              "total_episodes": 10, "parts": []}
    out = ideas_routes._handoff_user_text(
        w, struct, {"content": ""}, [], "en-US")
    assert out.startswith("Work: T\nWork structure: Part / Episode,"
                          " 10 episodes total")
    assert "(Parts undecided)" in out and "(no settings)" in out
    assert not _HANGUL.search(out)


def test_chat_context_en_has_no_hangul(conn):
    t = ideasmod.create_thread(conn, 2, "talk")
    um = ideasmod.add_message(conn, t["id"], "user", "What next?")
    ideasmod.add_message(conn, t["id"], "assistant", "An idea",
                         proposals={"settings": []})
    w = {"id": 2, "title": "US work", "structure_json": "{}"}
    ctx, info = ideas_routes._chat_context(
        conn, w, t["id"], user_msg=um, include_synopsis=False,
        include_structure=True, writing_locale="en-US")
    assert info == {"synopsis_safe": True}
    for needle in ("Work: US work", "[Included work structure]:",
                   "Total 25 episodes", "[Full synopsis excluded]",
                   "Settings:", "Author: What next?",
                   "Recent conversation:"):
        assert needle in ctx, needle
    assert not _HANGUL.search(ctx)


def test_chat_context_ko_pins_legacy_labels(conn):
    t = ideasmod.create_thread(conn, 1, "대화")
    um = ideasmod.add_message(conn, t["id"], "user", "다음은?")
    w = {"id": 1, "title": "KR 작품", "structure_json": "{}"}
    ctx, _info = ideas_routes._chat_context(
        conn, w, t["id"], user_msg=um, include_synopsis=False)
    for needle in ("작품: KR 작품", "[전체 줄거리 미포함]",
                   "설정정보:", "작가: 다음은?", "최근 대화:"):
        assert needle in ctx, needle
