"""Shell i18n: shell fragments cover the shell-owned frontend files.

Scope (disjoint from other i18n owners): frontend/index.html,
frontend/manual.html, frontend/js/{app,router,home,model,monitor,util,
resizer,api}.js, and frontend/locales/_frag/shell.*.json.

Covers only shell-owned files so other scopes' fragments cannot break
these tests.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS_DIR = ROOT / "frontend" / "js"
OWNED_JS = ["app.js", "router.js", "home.js", "model.js", "monitor.js",
            "util.js", "resizer.js", "api.js"]
OWNED_HTML = ["index.html", "manual.html"]
KO = ROOT / "frontend" / "locales" / "_frag" / "shell.ko-KR.json"
EN = ROOT / "frontend" / "locales" / "_frag" / "shell.en-US.json"
BASE_KO = ROOT / "frontend" / "locales" / "ko-KR.json"
BASE_EN = ROOT / "frontend" / "locales" / "en-US.json"
ERRORS_PY = ROOT / "server" / "app" / "errors.py"
GLOSSARY = ROOT / "docs" / "i18n-glossary.md"
PREFIXES = ("common.", "nav.", "error.", "home.", "model.", "monitor.",
            "manual.")

PARAM_RE = re.compile(r"\{(\w+)\}")
T_CALL_RE = re.compile(r"""\bt\(\s*["']([^"']+)["']""")
BARE_RE = re.compile(r"""["']((?:common|nav|error|home|model|monitor|manual|market)\.[A-Za-z0-9_.]+)["']""")
OBJVAL_RE = re.compile(r"""^[ \t]*[A-Za-z0-9_]+:[ \t]*["']((?:common|nav|error|home|model|monitor|manual|market)\.[A-Za-z0-9_.]+)["']""")
ATTR_RE = re.compile(r"""data-i18n(?:-html|-placeholder|-title|-aria-label|-content)?="([^"]+)\"""")
KOREAN_RE = re.compile(r"[가-힣]")

# index.html blocks owned by other i18n scopes. Each block is covered by its
# own scope test (test_i18n_ideas.py covers the ideas section,
# test_i18n_static_html.py covers design/review/publish/entities and their
# dialogs), so the shell scan skips them to keep ownership disjoint.
# (start marker, end marker, include end marker in the cut)
NON_SHELL_BLOCKS = [
    ('<section class="screen" id="ideas"', "</section>", True),
    ('<div id="handoff-backdrop"', '<div id="ailog-backdrop"', False),
    # Screen sections with design./review./publish./entities. keys.
    ('<section class="screen" id="sdesign"', "</section>", True),
    ('<section class="screen" id="s3"', "</section>", True),
    ('<section class="screen" id="s5"', "</section>", True),
    ('<section class="screen" id="s4"', "</section>", True),
    # Design dialogs (rename-backdrop between them stays shell-owned).
    ('<div id="thread-search-backdrop"', '<div id="rename-backdrop"',
     False),
    ('<div id="ns-dialog-backdrop"', '<div id="reset-confirm-backdrop"',
     False),
    # Review dialogs (reset, consistency-reset, fix, save-result).
    ('<div id="reset-confirm-backdrop"', "<script", False),
]


def _strip_non_shell_blocks(src):
    for start_marker, end_marker, include_end in NON_SHELL_BLOCKS:
        start = src.find(start_marker)
        if start == -1:
            continue
        end = src.find(end_marker, start)
        if end == -1:
            continue
        cut = end + len(end_marker) if include_end else end
        src = src[:start] + src[cut:]
    return src


def _load(p):
    return json.loads(p.read_text(encoding="utf-8"))


def _used_keys():
    """Translation keys referenced by owned files.

    Covers t("k"), t(c ? "a" : "b") ternaries, "K": "prefix.key" maps,
    and data-i18n* attributes. querySelector/getAttribute data-role
    strings are CSS selectors, not translation keys.
    """
    keys = set()
    for name in OWNED_JS:
        src = (JS_DIR / name).read_text(encoding="utf-8")
        keys.update(T_CALL_RE.findall(src))
        for line in src.splitlines():
            if "querySelector" in line or "getAttribute" in line:
                continue
            if "t(" in line:
                keys.update(BARE_RE.findall(line))
                continue
            m = OBJVAL_RE.match(line)
            if m:
                keys.add(m.group(1))
    for name in OWNED_HTML:
        src = (ROOT / "frontend" / name).read_text(encoding="utf-8")
        if name == "index.html":
            src = _strip_non_shell_blocks(src)
        keys.update(ATTR_RE.findall(src))
        keys.update(T_CALL_RE.findall(src))
    return keys


def test_fragments_exist_and_share_key_set():
    ko, en = _load(KO), _load(EN)
    assert len(ko) > 0
    assert set(ko) == set(en), (
        f"only ko: {sorted(set(ko) - set(en))} "
        f"only en: {sorted(set(en) - set(ko))}"
    )


def test_fragment_keys_use_shell_prefixes_and_params_match():
    ko, en = _load(KO), _load(EN)
    base_ko, base_en = _load(BASE_KO), _load(BASE_EN)
    for k in ko:
        assert k.startswith(PREFIXES), k
        assert k not in base_ko, f"ko fragment collides with base: {k}"
        assert k not in base_en, f"en fragment collides with base: {k}"
        assert ko[k], k
        assert en[k], k
        assert set(PARAM_RE.findall(ko[k])) == set(PARAM_RE.findall(en[k])), k


def test_every_owned_key_exists_in_merged_dicts():
    ko = dict(_load(BASE_KO))
    ko.update(_load(KO))
    en = dict(_load(BASE_EN))
    en.update(_load(EN))
    for k in sorted(_used_keys()):
        assert k.startswith(PREFIXES + ("market.",)), k
        assert k in ko, f"missing in ko merged dict: {k}"
        assert k in en, f"missing in en merged dict: {k}"


def test_server_error_catalog_covered():
    ko, en = _load(KO), _load(EN)
    codes = set(re.findall(r'^([A-Z][A-Z0-9_]+)\s*=\s*"([A-Z][A-Z0-9_]+)"',
                           ERRORS_PY.read_text(encoding="utf-8"), re.M))
    assert codes, "no error codes found in errors.py"
    for const, code in sorted(codes):
        assert f"error.{code}" in ko, code
        assert f"error.{code}" in en, code


def test_error_values_match_glossary():
    ko, en = _load(KO), _load(EN)
    rows = re.findall(r"^\| `([A-Z][A-Z0-9_]+)` \| (.+?) \| (.+?) \|$",
                      GLOSSARY.read_text(encoding="utf-8"), re.M)
    assert len(rows) >= 6, "glossary error table not parsed"
    for code, ko_text, en_text in rows:
        assert ko[f"error.{code}"] == ko_text, code
        assert en[f"error.{code}"] == en_text, code


def test_no_korean_left_in_code_outside_comments():
    bad = []
    for name in OWNED_JS:
        for i, ln in enumerate(
                (JS_DIR / name).read_text(encoding="utf-8").splitlines(), 1):
            if ln.strip().startswith("//"):
                continue
            code = re.sub(r"/\*.*?\*/", "", ln.split("//")[0])
            if KOREAN_RE.search(code):
                bad.append(f"{name}:{i}:{ln.strip()}")
    assert bad == []
