// 작은 DOM·문자열 헬퍼. React와 달리 innerHTML 삽입 시 직접 이스케이프한다.
export function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function $(id) {
  return document.getElementById(id);
}

// 탭 상단 에러 영역에 notice 출력. 빈 문자열이면 지운다.
export function showError(boxId, message) {
  const box = $(boxId);
  if (!box) return;
  box.innerHTML = message
    ? `<div class="notice err">${esc(message)}</div>`
    : "";
}

