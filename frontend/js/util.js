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
  PLAN: "준비",
  DRAFT: "초안 작성 중",
  VERIFY: "검토 중",
  READY_FOR_REVIEW: "작가 검토 대기",
  AUTHOR_APPROVED: "확정 처리 중",
  COMMITTED: "확정됨",
  NEEDS_AUTHOR: "작가 확인 필요",
};

export function stateKo(state) {
  return STATE_KO[state] ?? String(state ?? "-");
}

// 검증 판정 코드 → 화면 표시 한글.
const VERDICT_KO = { ok: "일치", error: "충돌", change: "변화", deferred: "보류" };

export function verdictKo(v) {
  return VERDICT_KO[v] ?? String(v ?? "-");
}

