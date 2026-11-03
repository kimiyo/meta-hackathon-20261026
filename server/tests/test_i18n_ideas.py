"""Ideas screen i18n: screen-ideas fragments cover frontend/js/ideas.js.

Covers only files owned by the screen-ideas scope so other screens'
fragments cannot break these tests.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "frontend" / "js" / "ideas.js"
KO = ROOT / "frontend" / "locales" / "_frag" / "screen-ideas.ko-KR.json"
EN = ROOT / "frontend" / "locales" / "_frag" / "screen-ideas.en-US.json"

PARAM_RE = re.compile(r"\{(\w+)\}")
T_CALL_RE = re.compile(r"""\bt\(\s*["']([^"']+)["']""")
IDEAS_LITERAL_RE = re.compile(r'"(ideas\.[A-Za-z0-9_.]+)"')


def _load_fragments():
    ko = json.loads(KO.read_text(encoding="utf-8"))
    en = json.loads(EN.read_text(encoding="utf-8"))
    return ko, en


def _used_keys():
    src = JS.read_text(encoding="utf-8")
    return set(T_CALL_RE.findall(src)) | set(IDEAS_LITERAL_RE.findall(src))


def test_fragments_exist_and_share_key_set():
    ko, en = _load_fragments()
    assert len(ko) > 0
    assert set(ko) == set(en), (
        f"only ko: {sorted(set(ko) - set(en))} "
        f"only en: {sorted(set(en) - set(ko))}"
    )


def test_fragment_keys_use_ideas_prefix_and_params_match():
    ko, en = _load_fragments()
    for k in ko:
        assert k.startswith("ideas."), k
        assert ko[k], k
        assert en[k], k
        assert set(PARAM_RE.findall(ko[k])) == set(PARAM_RE.findall(en[k])), k


def test_every_js_key_exists_in_both_fragments():
    ko, en = _load_fragments()
    for k in sorted(_used_keys()):
        assert k.startswith("ideas."), k
        assert k in ko, f"missing in ko fragment: {k}"
        assert k in en, f"missing in en fragment: {k}"


def test_no_korean_left_in_code_outside_comments():
    src = JS.read_text(encoding="utf-8")
    bad = []
    for ln in src.splitlines():
        if ln.strip().startswith("//"):
            continue
        if re.search(r"[가-힣]", ln.split("//")[0]):
            bad.append(ln.strip())
    assert bad == []
