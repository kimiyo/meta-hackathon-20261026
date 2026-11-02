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

// 회차 상태 코드 → 화면 표시 한글. 코드 식별자(API·CSS 클래스)는 그대로 둔다.
const STATE_KO = {
  WRITING: "집필중",
  IN_REVIEW: "검토중",
  PUBLISHING: "발간중",
};

export function stateKo(state) {
  return STATE_KO[state] ?? String(state ?? "-");
}

// 일관성 이슈 심각도·상태 → 화면 표시 한글.
const SEVERITY_KO = { critical: "심각", major: "주요", minor: "경미" };

export function severityKo(s) {
  return SEVERITY_KO[s] ?? String(s ?? "-");
}

const ISSUE_STATUS_KO = { open: "열림", resolved: "해결됨", wontfix: "수정 안 함" };

export function issueStatusKo(s) {
  return ISSUE_STATUS_KO[s] ?? String(s ?? "-");
}

