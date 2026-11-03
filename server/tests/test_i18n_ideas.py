"""Ideas screen i18n: screen-ideas fragments cover frontend/js/ideas.js.

Covers only files owned by the screen-ideas scope so other screens'
fragments cannot break these tests.
"""
import json
import re
from html.parser import HTMLParser
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


INDEX = ROOT / "frontend" / "index.html"

# JS-owned texts: re-rendered by ideas.js renderAll on every locale change,
# covered by test_ideas_rerenders_on_locale_change instead of data-i18n.
JS_OWNED_IDS = {"struct-toggle", "btn-handoff"}

KOREAN_RE = re.compile(r"[가-힣]")
VOID_TAGS = {"input", "img", "br", "meta", "link", "hr", "source"}


def _ideas_html_blocks():
    src = INDEX.read_text(encoding="utf-8")
    start = src.find('<section class="screen" id="ideas"')
    assert start != -1
    end = src.find("</section>", start)
    assert end != -1
    section = src[start:end]
    dstart = src.find('<div id="handoff-backdrop"')
    assert dstart != -1
    dend = src.find('<div id="ailog-backdrop"', dstart)
    assert dend != -1
    return [section, src[dstart:dend]]


class _TextUse(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.bare_korean = []
        self.attr_violations = []
        self.used_keys = set()

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        for attr, i18n_attr in (
            ("title", "data-i18n-title"),
            ("placeholder", "data-i18n-placeholder"),
            ("aria-label", "data-i18n-aria-label"),
        ):
            if d.get(attr) and KOREAN_RE.search(d[attr]):
                if i18n_attr in d:
                    self.used_keys.add(d[i18n_attr])
                else:
                    self.attr_violations.append(
                        f"<{tag} {attr}={d[attr]!r}> missing {i18n_attr}"
                    )
        if "data-i18n" in d:
            self.used_keys.add(d["data-i18n"])
        if tag not in VOID_TAGS:
            self.stack.append((tag, d))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if not data.strip() or not KOREAN_RE.search(data):
            return
        if not self.stack:
            self.bare_korean.append(f"top-level text: {data.strip()[:40]}")
            return
        tag, d = self.stack[-1]
        if "data-i18n" in d or d.get("id") in JS_OWNED_IDS:
            return
        self.bare_korean.append(f"<{tag}>: {data.strip()[:60]}")


def _merged_dicts():
    merged_ko = json.loads(
        (ROOT / "frontend" / "locales" / "ko-KR.json").read_text(encoding="utf-8")
    )
    merged_en = json.loads(
        (ROOT / "frontend" / "locales" / "en-US.json").read_text(encoding="utf-8")
    )
    for frag in (ROOT / "frontend" / "locales" / "_frag").glob("*.ko-KR.json"):
        merged_ko.update(json.loads(frag.read_text(encoding="utf-8")))
    for frag in (ROOT / "frontend" / "locales" / "_frag").glob("*.en-US.json"):
        merged_en.update(json.loads(frag.read_text(encoding="utf-8")))
    return merged_ko, merged_en


def test_ideas_static_html_has_i18n_coverage():
    parser = _TextUse()
    for block in _ideas_html_blocks():
        parser.feed(block)
    assert parser.bare_korean == [], parser.bare_korean
    assert parser.attr_violations == [], parser.attr_violations
    merged_ko, merged_en = _merged_dicts()
    for k in sorted(parser.used_keys):
        assert k in merged_ko, f"data-i18n key missing in ko dict: {k}"
        assert k in merged_en, f"data-i18n key missing in en dict: {k}"


def test_ideas_rerenders_on_locale_change():
    src = JS.read_text(encoding="utf-8")
    idx = src.find('"locale-changed"')
    assert idx != -1, "ideas.js has no locale-changed listener"
    assert "renderAll(" in src[idx:idx + 800]


def test_synopsis_blur_save_suspended_during_locale_rerender():
    src = JS.read_text(encoding="utf-8")
    blur_idx = src.find('addEventListener("blur"')
    assert blur_idx != -1
    assert "suspendSynBlurSave" in src[blur_idx:blur_idx + 200]
    loc_idx = src.find('"locale-changed"')
    assert "suspendSynBlurSave" in src[loc_idx:loc_idx + 900]


def test_open_setting_editor_survives_locale_rerender():
    src = JS.read_text(encoding="utf-8")
    idx = src.find('"locale-changed"')
    block = src[idx:idx + 900]
    assert "setting-edit" in block and "skipSettings" in block
