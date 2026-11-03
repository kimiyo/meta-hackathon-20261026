"""Publishing: work meta, platform, covers, blurb, approved text."""
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


def test_patch_platform(pub_client):
    client, wid = pub_client
    r = client.get(f"/api/v1/works/{wid}")
    assert r.json()["platform"] == "문피아"
    assert r.json()["platforms"] == ["문피아", "카카오페이지", "네이버시리즈",
                                     "리디", "기타"]
    r = client.patch(f"/api/v1/works/{wid}", json={"platform": "리디"})
    assert r.status_code == 200, r.text
    assert r.json()["platform"] == "리디"
    r = client.patch(f"/api/v1/works/{wid}", json={"platform": "네이버웹툰"})
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


def test_publish_record_flow(pub_client):
    client, wid = pub_client
    # Record with work platform snapshot (default 문피아).
    r = client.put(f"/api/v1/episodes/1/publish-record?work={wid}",
                   json={"published_date": "2026-11-03", "note": "1화 등록"})
    assert r.status_code == 200, r.text
    assert r.json() == {"published_date": "2026-11-03",
                        "published_note": "1화 등록",
                        "published_platform": "문피아"}
    # Visible in the publishing list.
    did = client.get(f"/api/v1/works/{wid}").json()["latest_design"]["id"]
    r = client.get(f"/api/v1/designs/{did}/publishing")
    eps = {e["episode_no"]: e for e in r.json()["episodes"]}
    assert eps[1]["published_date"] == "2026-11-03"
    # Platform change is snapshotted per record.
    client.patch(f"/api/v1/works/{wid}", json={"platform": "리디"})
    r = client.put(f"/api/v1/episodes/1/publish-record?work={wid}",
                   json={"published_date": "2026-11-04", "note": ""})
    assert r.json()["published_platform"] == "리디"
    # Empty date clears the record.
    r = client.put(f"/api/v1/episodes/1/publish-record?work={wid}",
                   json={"published_date": "", "note": ""})
    assert r.json() == {"published_date": "", "published_note": "",
                        "published_platform": ""}
    # Unknown episode.
    r = client.put(f"/api/v1/episodes/9/publish-record?work={wid}",
                   json={"published_date": "2026-11-03", "note": ""})
    assert r.status_code == 404


def test_publish_record_validation(pub_client):
    client, wid = pub_client
    r = client.put(f"/api/v1/episodes/1/publish-record?work={wid}",
                   json={"published_date": "2026-13-99", "note": ""})
    assert r.status_code == 400
    r = client.put(f"/api/v1/episodes/1/publish-record?work={wid}",
                   json={"published_date": "2026-11-03", "note": "x" * 201})
    assert r.status_code == 400


def test_approved_text(pub_client):
    client, wid = pub_client
    r = client.get(f"/api/v1/episodes/1/approved-text?work={wid}")
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "1화 승인 원고 본문이다."
    assert r.json()["revision_id"] is not None
    # EP2 has no approved revision.
    r = client.get(f"/api/v1/episodes/2/approved-text?work={wid}")
    assert r.status_code == 409
    # Unknown episode.
    r = client.get(f"/api/v1/episodes/9/approved-text?work={wid}")
    assert r.status_code == 404


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
        # The API does not save; the frontend saves after showing it.
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
