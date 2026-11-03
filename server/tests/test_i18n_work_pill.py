"""Work pill must re-render on locale change.

Regression: switching to English left the topbar work pill showing Korean
'작품 없음' because nothing re-rendered it on locale-changed.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "frontend" / "js" / "app.js"
KO = ROOT / "frontend" / "locales" / "_frag" / "screen-writing.ko-KR.json"
EN = ROOT / "frontend" / "locales" / "_frag" / "screen-writing.en-US.json"


def test_locale_change_handler_refreshes_work_pill():
    src = APP.read_text(encoding="utf-8")
    idx = src.find('"locale-changed"')
    assert idx != -1, "locale-changed handler missing in app.js"
    assert "refreshWorkPill()" in src[idx:idx + 500]


def test_work_no_work_key_exists_in_both_locales():
    ko = json.loads(KO.read_text(encoding="utf-8"))
    en = json.loads(EN.read_text(encoding="utf-8"))
    assert ko["work.noWork"], "work.noWork missing in ko fragment"
    assert en["work.noWork"], "work.noWork missing in en fragment"
