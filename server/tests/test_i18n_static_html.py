"""Static HTML i18n coverage for the whole index.html page.

Mirrors test_i18n_ideas.py::_TextUse, but scans the full page: every Korean
text node and every Korean title/placeholder/aria-label attribute must be
wired to a data-i18n* key present in both merged dicts — except JS-owned
elements, whose text is always rendered through t() by their screen module
(verified by test_js_owned_ids_are_rendered_by_js).
"""
import json
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "frontend" / "index.html"
JS_DIR = ROOT / "frontend" / "js"

KOREAN_RE = re.compile(r"[가-힣]")
VOID_TAGS = {"input", "img", "br", "meta", "link", "hr", "source"}

# id -> JS module that always renders its text through t().
JS_OWNED_IDS = {
    # ideas screen (same precedent as test_i18n_ideas.py).
    "struct-toggle": "ideas.js",
    "btn-handoff": "ideas.js",
    # home auth gate (renderGate runs at boot and on locale change).
    "auth-title": "home.js",
    "auth-desc": "home.js",
    "btn-auth-submit": "home.js",
    "btn-auth-switch": "home.js",
    # topbar work pill (refreshWorkPill runs at boot and on locale change).
    "work-pill": "work.js",
    # topbar model chip (refreshTopbar runs at boot and on locale change).
    "model-info": "app.js",
    # review fix button (updateFixSelectedButton appends a live count).
    "btn-fix-selected": "review.js",
    # publish detail title (renderDetail shows the selected episode).
    "pub-detail-title": "publish.js",
}


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
        # Language selectors keep native names by design ("한국어").
        if tag == "option" and any(
                t == "select" and a.get("id") == "locale-select"
                for t, a in self.stack):
            return
        # A data-i18n on any open ancestor covers nested static text.
        if any("data-i18n" in ancestor for _, ancestor in self.stack):
            return
        # So does a JS-owned ancestor: its whole subtree is rendered by JS.
        if any(a.get("id") in JS_OWNED_IDS for _, a in self.stack):
            return
        self.bare_korean.append(
            f"<{tag} id={d.get('id')!r}>: {data.strip()[:60]}")

    def handle_comment(self, data):
        pass


def _parse():
    parser = _TextUse()
    parser.feed(INDEX.read_text(encoding="utf-8"))
    return parser


def _merged_dicts():
    merged_ko = json.loads(
        (ROOT / "frontend" / "locales" / "ko-KR.json").read_text(
            encoding="utf-8"))
    merged_en = json.loads(
        (ROOT / "frontend" / "locales" / "en-US.json").read_text(
            encoding="utf-8"))
    for frag in (ROOT / "frontend" / "locales" / "_frag").glob("*.ko-KR.json"):
        merged_ko.update(json.loads(frag.read_text(encoding="utf-8")))
    for frag in (ROOT / "frontend" / "locales" / "_frag").glob("*.en-US.json"):
        merged_en.update(json.loads(frag.read_text(encoding="utf-8")))
    return merged_ko, merged_en


def test_static_html_has_i18n_coverage():
    parser = _parse()
    assert parser.bare_korean == [], parser.bare_korean
    assert parser.attr_violations == [], parser.attr_violations
    merged_ko, merged_en = _merged_dicts()
    for k in sorted(parser.used_keys):
        assert k in merged_ko, f"data-i18n key missing in ko dict: {k}"
        assert k in merged_en, f"data-i18n key missing in en dict: {k}"


def test_js_owned_ids_are_rendered_by_js():
    """Each whitelisted id is set through t() by its owning module."""
    src = INDEX.read_text(encoding="utf-8")
    for el_id, module in JS_OWNED_IDS.items():
        assert f'id="{el_id}"' in src, f"#{el_id} missing from index.html"
        js = (JS_DIR / module).read_text(encoding="utf-8")
        assert f'$("{el_id}")' in js or f"'{el_id}'" in js, (
            f"{module} never touches #{el_id}")
        assert "t(" in js, f"{module} does not translate"


def test_js_owned_ids_stay_small():
    """The whitelist is an exception list, not a second coverage system."""
    assert len(JS_OWNED_IDS) <= 12, sorted(JS_OWNED_IDS)
