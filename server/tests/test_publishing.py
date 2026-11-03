"""Publishing: work meta, covers, per-episode editions + teasers."""
import base64

import pytest
from fastapi.testclient import TestClient

from app import auth as authmod
from app import db as dbmod
from app import publishing
from app.routes.deps import get_conn, get_current_author

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


class TeaserFake:
    def __init__(self, teaser="다음 화에서 비밀이 밝혀진다."):
        self.teaser = teaser

    def chat_json(self, **kw):
        return {"teaser": self.teaser}


@pytest.fixture()
def pub_client(tmp_path, monkeypatch):
    db_path = tmp_path / "pub.db"
    monkeypatch.setenv("STUDIO_DB", str(db_path))

    def _override():
        c = dbmod.connect(db_path)
        try:
            yield c
        finally:
            c.close()

    from app.main import app
    app.dependency_overrides[get_conn] = _override
    seed = dbmod.connect(db_path)
    _author = authmod.public_author(authmod.register(
        seed, login_id="pub", pen_name="발간작가", password="pw1234"))
    wid = seed.execute(
        "INSERT INTO works(author_id, title, logline, created_at, updated_at)"
        " VALUES(?,'연재작','', 't','t')", (_author["id"],)).lastrowid
    did = seed.execute(
        "INSERT INTO story_designs(work_id, version, scope_total_episodes,"
        " concept_json, sections_json) VALUES(?,1,2,'{}','{}')",
        (wid,)).lastrowid
    rev = seed.execute(
        "INSERT INTO manuscript_revisions(work_id, episode_no, version, text,"
        " approval_status, created_at) VALUES(?,1,1,?,'approved','t')",
        (wid, "1화 승인 원고 본문이다.")).lastrowid
    seed.execute(
        "INSERT INTO episode_jobs(work_id, episode_no, design_id, state,"
        " attempt, manuscript_revision_id, updated_at)"
        " VALUES(?,1,?,'PUBLISHING',0,?,'t')", (wid, did, rev))
    seed.execute(
        "INSERT INTO episode_jobs(work_id, episode_no, design_id, state,"
        " attempt, updated_at) VALUES(?,2,?,'WRITING',0,'t')", (wid, did))
    seed.execute(
        "INSERT INTO manuscript_docs(work_id, episode_no, draft_content,"
        " revision, updated_at) VALUES(?,?,?,0,?)",
        (wid, 2, "2화 초안 첫 문장. 이어지는 내용.", "t"))
    seed.commit()
    seed.close()
    app.dependency_overrides[get_current_author] = lambda: _author
    yield TestClient(app), wid
    app.dependency_overrides.clear()


def test_patch_publish_meta(pub_client):
    client, wid = pub_client
    r = client.patch(f"/api/v1/works/{wid}", json={
        "publish_title": "게재 제목", "publish_pen_name": "게재 필명",
        "publish_blurb": "소개글이다."})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["publish_title"] == "게재 제목"
    assert body["publish_pen_name"] == "게재 필명"
    assert body["publish_blurb"] == "소개글이다."
    r = client.get(f"/api/v1/works/{wid}")
    assert r.json()["author_pen_name"] == "발간작가"
    assert r.json()["cover_url"] == ""
    r = client.patch(f"/api/v1/works/{wid}", json={"publish_title": "x" * 81})
    assert r.status_code == 400


def test_cover_upload_serve_delete(pub_client):
    client, wid = pub_client
    b64 = base64.b64encode(PNG_1PX).decode()
    r = client.post(f"/api/v1/works/{wid}/cover",
                    json={"filename": "cover.png", "content_base64": b64})
    assert r.status_code == 200, r.text
    url = r.json()["cover_url"]
    assert url.startswith("/covers/")
    r = client.get(url)
    assert r.status_code == 200
    assert r.content == PNG_1PX
    r = client.get(f"/api/v1/works/{wid}")
    assert r.json()["cover_url"] == url
    # Invalid extension rejected.
    r = client.post(f"/api/v1/works/{wid}/cover",
                    json={"filename": "cover.gif", "content_base64": b64})
    assert r.status_code == 400
    # Delete removes row + file.
    r = client.delete(f"/api/v1/works/{wid}/cover")
    assert r.status_code == 200
    r = client.get(url)
    assert r.status_code == 404
    r = client.delete(f"/api/v1/works/{wid}/cover")
    assert r.status_code == 404


def test_cover_rejects_oversize(pub_client, monkeypatch):
    client, wid = pub_client
    monkeypatch.setattr(publishing, "COVER_MAX_BYTES", 10)
    b64 = base64.b64encode(PNG_1PX).decode()
    r = client.post(f"/api/v1/works/{wid}/cover",
                    json={"filename": "c.png", "content_base64": b64})
    assert r.status_code == 400


def test_edition_crud_and_platform(pub_client):
    client, wid = pub_client
    r = client.get(f"/api/v1/episodes/1/publish-edition?work={wid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["edition"] is None
    assert body["has_approved"] is True
    assert body["next_available"] is True
    assert body["default_platform"] == "문피아"
    # Upsert with explicit platform.
    r = client.put(f"/api/v1/episodes/1/publish-edition?work={wid}",
                   json={"platform": "리디", "text": "출판본 초안"})
    assert r.status_code == 200
    assert r.json()["edition"]["platform"] == "리디"
    # Bad platform rejected.
    r = client.put(f"/api/v1/episodes/1/publish-edition?work={wid}",
                   json={"platform": "네이버웹툰"})
    assert r.status_code == 400
    # Text-only update keeps platform.
    r = client.put(f"/api/v1/episodes/1/publish-edition?work={wid}",
                   json={"text": "수정한 출판본"})
    assert r.json()["edition"]["text"] == "수정한 출판본"
    assert r.json()["edition"]["platform"] == "리디"


def test_edition_from_approved(pub_client):
    client, wid = pub_client
    r = client.post(
        f"/api/v1/episodes/1/publish-edition/from-approved?work={wid}")
    assert r.status_code == 200, r.text
    ed = r.json()["edition"]
    assert ed["text"] == "1화 승인 원고 본문이다."
    assert ed["platform"] == "문피아"
    # EP2 has no approved revision.
    r = client.post(
        f"/api/v1/episodes/2/publish-edition/from-approved?work={wid}")
    assert r.status_code == 409


def test_edition_teaser_flow(pub_client):
    from app.main import app
    from app.routes.works import get_meta

    client, wid = pub_client
    app.dependency_overrides[get_meta] = lambda: TeaserFake()
    try:
        # Creates the edition from approved text when missing.
        r = client.post(
            f"/api/v1/episodes/1/publish-edition/teaser?work={wid}")
        assert r.status_code == 200, r.text
        assert r.json()["teaser"] == "다음 화에서 비밀이 밝혀진다."
        ed = r.json()["edition"]
        assert ed["text"].startswith("1화 승인 원고 본문이다.")
        assert publishing.TEASER_MARK in ed["text"]
        assert ed["teaser_episode"] == 2
        # Re-run replaces the teaser block instead of duplicating it.
        r = client.post(
            f"/api/v1/episodes/1/publish-edition/teaser?work={wid}")
        assert r.json()["edition"]["text"].count(
            publishing.TEASER_MARK) == 1
        # EP2 has no next episode: 409.
        r = client.post(
            f"/api/v1/episodes/2/publish-edition/teaser?work={wid}")
        assert r.status_code == 409
    finally:
        del app.dependency_overrides[get_meta]


class BlurbFake:
    def __init__(self, blurb="Hook. 주인공이 모험을 떠난다."):
        self.blurb = blurb

    def chat_json(self, **kw):
        return {"blurb": self.blurb}


def test_generate_blurb(pub_client):
    from app.main import app
    from app.routes.works import get_meta

    client, wid = pub_client
    app.dependency_overrides[get_meta] = lambda: BlurbFake()
    try:
        r = client.patch(f"/api/v1/works/{wid}",
                         json={"logline": "망한 서점을 물려받은 주인공의 이야기"})
        assert r.status_code == 200
        r = client.post(f"/api/v1/works/{wid}/blurb")
        assert r.status_code == 200, r.text
        assert r.json()["blurb"].startswith("Hook.")
        # Not auto-saved: the author reviews and saves explicitly.
        r = client.get(f"/api/v1/works/{wid}")
        assert r.json()["publish_blurb"] == ""
    finally:
        del app.dependency_overrides[get_meta]


def test_generate_blurb_no_material(pub_client):
    client, _wid = pub_client
    r = client.post("/api/v1/works", json={"title": "빈 작품"})
    wid = r.json()["id"]
    r = client.post(f"/api/v1/works/{wid}/blurb")
    assert r.status_code == 409


def test_blurb_long_output_trimmed():
    assert publishing.BLURB_AI_MAX_CHARS == 500
    fake = BlurbFake("가" * 600)
    out = publishing.make_blurb(fake, 1, {"title": "t", "logline": "l",
                                         "synopsis": "", "outlines": []})
    assert len(out) == 500


def test_teaser_long_output_trimmed():
    assert publishing.TEASER_MAX_CHARS == 100
    assert publishing.compose_with_teaser("본문", "예고").count(
        publishing.TEASER_MARK) == 1
    assert publishing.strip_teaser_block(
        publishing.compose_with_teaser("본문", "예고")).strip() == "본문"
