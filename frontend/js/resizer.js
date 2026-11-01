// 화면 2~3열 사이의 드래그 분할선. 조절 대상 너비를 그리드의 CSS 변수로 들고
// 로컬 스토리지에 저장한다. 화면별 설정만 다르고 동작은 같다.
const GUTTER_HALF = 4; // .col-resizer 너비(8px)의 절반

export function clampResizerW(grid, w, min, reserve) {
  const max = Math.max(min, grid.clientWidth - reserve);
  return Math.round(Math.min(Math.max(w, min), max));
}

export function initColResizer(cfg) {
  const resizer = document.getElementById(cfg.resizerId);
  const grid = resizer?.parentElement;
  if (!resizer || !grid) return;
  restoreColResizer(cfg);
  resizer.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    try { resizer.setPointerCapture(e.pointerId); } catch { /* window 리스너로 폴백 */ }
    document.body.classList.add("col-resizing");
    const rect = grid.getBoundingClientRect();
    const move = (ev) => {
      const raw = cfg.side === "right"
        ? rect.right - ev.clientX - GUTTER_HALF
        : ev.clientX - rect.left - GUTTER_HALF;
      grid.style.setProperty(cfg.varName, `${clampResizerW(grid, raw, cfg.min, cfg.reserve)}px`);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      document.body.classList.remove("col-resizing");
      const v = parseInt(grid.style.getPropertyValue(cfg.varName), 10);
      if (Number.isFinite(v)) localStorage.setItem(cfg.storageKey, String(v));
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  });
}

export function restoreColResizer(cfg) {
  const resizer = document.getElementById(cfg.resizerId);
  const grid = resizer?.parentElement;
  if (!grid || !grid.clientWidth) return;
  const saved = Number(localStorage.getItem(cfg.storageKey));
  if (Number.isFinite(saved) && saved > 0) {
    grid.style.setProperty(cfg.varName, `${clampResizerW(grid, saved, cfg.min, cfg.reserve)}px`);
  }
}
