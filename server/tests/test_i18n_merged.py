"""Integration: merged locale dicts, prompt parity, works locale wiring.

Part A mirrors frontend/js/i18n.js loadLocale (base + manifest-ordered
_frag files) and asserts the effective dictionaries are complete and
consistent. Part B proves the works blurb/cover routes pass the work's
stored market/writing_locale into publishing.py.
"""
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import auth as authmod
from app import db as dbmod
from app.routes.deps import get_conn, get_current_author

ROOT = Path(__file__).resolve().parents[2]
LOCALES = ROOT / "frontend" / "locales"
FRAG = LOCALES / "_frag"
JS_DIR = ROOT / "frontend" / "js"
PROMPTS = ROOT / "server" / "app" / "prompts"
ERRORS_PY = ROOT / "server" / "app" / "errors.py"
PUBLISHING_PY = ROOT / "server" / "app" / "publishing.py"

PREFIXES = ("common", "nav", "error", "home", "model", "monitor", "manual",
            "market", "ideas", "design", "work", "manuscript", "ailog",
            "review", "entities", "publish")

T_CALL_RE = re.compile(r"""\bt\(\s*["']([^"']+)["']""")
BARE_RE = re.compile(r"""["']((?:%s)\.[A-Za-z0-9_.]+)["']""" % "|".join(PREFIXES))
ATTR_RE = re.compile(r"""data-i18n(?:-html|-placeholder|-title|-aria-label|-content)?="([^"]+)\"""")
PARAM_RE = re.compile(r"\{(\w+)\}")
KOREAN_RE = re.compile(r"[가-힣]")
ROLES_BLOCK_RE = re.compile(r"const REVIEW_MS_ROLES = \{.*?\};", re.S)
DATAROLE_RE = re.compile(r"""data-role="[^"]*\"""")
BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
CODE_RE = re.compile(r'^([A-Z][A-Z0-9_]+)\s*=\s*"([A-Z][A-Z0-9_]+)"', re.M)

# Stage-1 skeleton keys with no runtime reference yet (topbar uses
# its own locale keys). Pinned so new orphans fail loudly instead of
# accumulating silently. market.* left this set once the novel-create
# dialog rendered the market selector; common.loading/common.ok once
# static placeholders referenced them.
KNOWN_UNUSED = {"common.appName", "common.confirm", "common.error"}


def _load(p):
    return json.loads(p.read_text(encoding="utf-8"))


def _manifest():
    owners = _load(FRAG / "manifest.json")["fragments"]
    assert isinstance(owners, list) and owners
    return owners


def _merged(locale):
    """Effective dictionary exactly as i18n.js loadLocale builds it."""
    merged = dict(_load(LOCALES / f"{locale}.json"))
    for owner in _manifest():
        merged.update(_load(FRAG / f"{owner}.{locale}.json"))
    return merged


def _used_keys():
    """Translation keys referenced by the static frontend.

    t("literal") is always a translation key, even on querySelector
    lines (e.g. manuscript empty-preview labels). Bare "prefix.key"
    literals skip CSS-selector lines, data-role identifiers, and the
    REVIEW_MS_ROLES data-role map. error.* keys arrive dynamically
    via api.js error.${code}, covered from the server code catalog.
    """
    keys = set()
    for path in sorted(JS_DIR.glob("*.js")):
        src = BLOCK_COMMENT_RE.sub("", path.read_text(encoding="utf-8"))
        src = ROLES_BLOCK_RE.sub("", src)
        for line in src.splitlines():
            if line.strip().startswith("//"):
                continue
            keys.update(T_CALL_RE.findall(line))
            if "querySelector" in line or "getAttribute" in line:
                continue
            keys.update(BARE_RE.findall(DATAROLE_RE.sub("", line)))
    for name in ("index.html", "manual.html"):
        src = (ROOT / "frontend" / name).read_text(encoding="utf-8")
        keys.update(T_CALL_RE.findall(src))
        keys.update(ATTR_RE.findall(src))
    codes = set(CODE_RE.findall(ERRORS_PY.read_text(encoding="utf-8")))
    codes |= set(CODE_RE.findall(PUBLISHING_PY.read_text(encoding="utf-8")))
    assert codes, "no error codes found"
    for _const, code in codes:
        keys.add(f"error.{code}")
    return keys


def test_manifest_covers_all_fragments():
    owners = _manifest()
    assert len(owners) == len(set(owners)), "duplicate manifest owner"
    on_disk = {p.name.split(".")[0] for p in FRAG.glob("*.ko-KR.json")}
    on_disk |= {p.name.split(".")[0] for p in FRAG.glob("*.en-US.json")}
    assert set(owners) == on_disk, (
        f"manifest-only: {sorted(set(owners) - on_disk)} "
        f"disk-only: {sorted(on_disk - set(owners))}")
    for owner in owners:
        for locale in ("ko-KR", "en-US"):
            assert (FRAG / f"{owner}.{locale}.json").is_file(), owner


def test_no_duplicate_keys_across_base_and_fragments():
    for locale in ("ko-KR", "en-US"):
        seen = {}
        for label, mapping in (
                ("base", _load(LOCALES / f"{locale}.json")),
                *((o, _load(FRAG / f"{o}.{locale}.json")) for o in _manifest())):
            for key in mapping:
                assert key not in seen, (
                    f"{locale}: {key} in {seen[key]} and {label}")
                seen[key] = label


def test_merged_locales_share_key_set_and_params():
    ko, en = _merged("ko-KR"), _merged("en-US")
    assert len(ko) > 0
    assert set(ko) == set(en), (
        f"only ko: {sorted(set(ko) - set(en))} "
        f"only en: {sorted(set(en) - set(ko))}")
    for key in ko:
        assert ko[key], f"empty ko value: {key}"
        assert en[key], f"empty en value: {key}"
        assert set(PARAM_RE.findall(ko[key])) == set(
            PARAM_RE.findall(en[key])), f"param mismatch: {key}"


def test_every_used_key_exists_in_both_merged_dicts():
    ko, en = _merged("ko-KR"), _merged("en-US")
    for key in sorted(_used_keys()):
        assert key in ko, f"missing in ko merged dict: {key}"
        assert key in en, f"missing in en merged dict: {key}"


def test_error_catalog_covered_in_merged_dicts():
    ko, en = _merged("ko-KR"), _merged("en-US")
    codes = set(CODE_RE.findall(ERRORS_PY.read_text(encoding="utf-8")))
    codes |= set(CODE_RE.findall(PUBLISHING_PY.read_text(encoding="utf-8")))
    assert ("BLURB_EMPTY", "BLURB_EMPTY") in codes
    for _const, code in sorted(codes):
        assert f"error.{code}" in ko, code
        assert f"error.{code}" in en, code


def test_no_korean_in_en_merged_dict():
    bad = {k: v for k, v in _merged("en-US").items()
           if KOREAN_RE.search(v)}
    assert bad == {}


def test_unused_keys_are_known_skeleton_keys():
    unused = set(_merged("ko-KR")) - _used_keys()
    assert unused == KNOWN_UNUSED, (
        f"new orphans: {sorted(unused - KNOWN_UNUSED)} "
        f"now used: {sorted(KNOWN_UNUSED - unused)}")


def test_every_legacy_basename_has_en_us_file():
    legacy = sorted(p.stem for p in PROMPTS.glob("*.txt"))
    assert legacy, "no legacy prompts found"
    for base in legacy:
        assert (PROMPTS / "en-US" / f"{base}.txt").is_file(), base


# --- Part B: works routes pass the work's stored locale ---

@pytest.fixture()
def int_client(tmp_path, monkeypatch):
    db_path = tmp_path / "int.db"
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
    author = authmod.public_author(authmod.register(
        seed, login_id="int", pen_name="통합작가", password="pw1234"))
    seed.commit()
    seed.close()
    app.dependency_overrides[get_current_author] = lambda: author
    yield TestClient(app)
    app.dependency_overrides.clear()


class CaptureMeta:
    def __init__(self, blurb):
        self.blurb = blurb
        self.seen = {}

    def chat_json(self, **kw):
        self.seen = kw
        return {"blurb": self.blurb}


def _make_work(client, **fields):
    body = {"title": "통합작", **fields}
    r = client.post("/api/v1/works", json=body)
    assert r.status_code == 200, r.text
    wid = r.json()["id"]
    r = client.patch(f"/api/v1/works/{wid}", json={"logline": "재료"})
    assert r.status_code == 200, r.text
    return wid


def test_generate_blurb_uses_work_locale_and_market(int_client):
    from app import prompt_loader
    from app.main import app
    from app.routes.works import get_meta

    client = int_client
    wid = _make_work(client, market="US", writing_locale="en-US")
    fake = CaptureMeta("word " * 150)
    app.dependency_overrides[get_meta] = lambda: fake
    try:
        r = client.post(f"/api/v1/works/{wid}/blurb")
    finally:
        del app.dependency_overrides[get_meta]
    assert r.status_code == 200, r.text
    # Only the en-US path word-trims to 100.
    assert len(r.json()["blurb"].split()) == 100
    assert fake.seen["writing_locale"] == "en-US"
    assert fake.seen["market"] == "US"
    assert fake.seen["system"] == prompt_loader.load(
        "blurb", "en-US", "US").text


def test_upload_cover_error_uses_work_locale(int_client):
    client = int_client
    us = _make_work(client, market="US", writing_locale="en-US")
    kr = _make_work(client)
    bad = {"filename": "x.gif", "content_base64": ""}
    r = client.post(f"/api/v1/works/{us}/cover", json=bad)
    assert r.status_code == 400
    assert r.json()["detail"] == "covers must be jpg/png"
    r = client.post(f"/api/v1/works/{kr}/cover", json=bad)
    assert r.status_code == 400
    assert "jpg/png" in r.json()["detail"]
    assert KOREAN_RE.search(r.json()["detail"])


def test_generate_blurb_missing_prompt_maps_501(int_client, monkeypatch):
    from app import prompt_loader

    client = int_client
    wid = _make_work(client, market="US", writing_locale="en-US")

    def _missing(*a, **k):
        raise FileNotFoundError("no such prompt")

    monkeypatch.setattr(prompt_loader, "load", _missing)
    r = client.post(f"/api/v1/works/{wid}/blurb")
    assert r.status_code == 501, r.text
