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

// 화면 하단 토스트로 잠시 보여주고 사라진다.
export function showToast(message, ms = 2500) {
  let wrap = $("toast-wrap");
  if (!wrap) {
    wrap = document.createElement("div");
    wrap.id = "toast-wrap";
    wrap.className = "toast-wrap";
    document.body.appendChild(wrap);
  }
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = message;
  wrap.appendChild(el);
  requestAnimationFrame(() => el.classList.add("show"));
  setTimeout(() => {
    el.classList.remove("show");
    setTimeout(() => el.remove(), 300);
  }, ms);
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

const ISSUE_STATUS_KO = {
  open: "열림", fixed: "수정 완료", accepted: "의도적 수용", dismissed: "오탐 기각",
  resolved: "수정 완료", wontfix: "오탐 기각",
};

export function issueStatusKo(s) {
  return ISSUE_STATUS_KO[s] ?? String(s ?? "-");
}

// 분석 판정(verdict) → 화면 표시 한글. 작가 처리 상태와 분리된다.
const VERDICT_KO = { conflict: "모순 확정", explained: "설명됨", uncertain: "판단 보류" };

export function verdictKo(v) {
  return VERDICT_KO[v] ?? String(v ?? "-");
}

// 사실 종류(kind) → 화면 표시 한글.
const KIND_KO = {
  attribute: "속성", state: "상태", relation: "관계", rule: "규칙", event: "행위",
};

export function kindKo(k) {
  return KIND_KO[k] ?? String(k ?? "-");
}

// 확정 변경 이력 → 타임라인 HTML. 설정 화면과 작가 검토가 공유한다.
export function historyTimelineHtml(history) {
  if (!history.length) return `<span class="muted small">확정된 이력이 없습니다.</span>`;
  const fmtVal = (v) => {
    if (v === null || v === undefined || v === "") return "—";
    return typeof v === "string" ? v : JSON.stringify(v);
  };
  return history
    .map(
      (h) => `<div class="tl-item">
        <div class="ep">EP ${esc(h.episode_no)}${h.story_time ? ` · <span class="chip">${esc(h.story_time)}</span>` : ""} · <span class="chip">${esc(kindKo(h.kind))}</span> ${esc(h.operation)} ${esc(h.attribute_path ?? "")}</div>
        <div class="what">${esc(fmtVal(h.old_value))} → ${esc(fmtVal(h.new_value))}</div>
        ${h.evidence_quote ? `<div class="q">"${esc(h.evidence_quote)}"</div>` : ""}
      </div>`
    )
    .join("");
}

// 서술 방식(narrative_mode) → 화면 표시 한글.
const MODE_KO = {
  narration: "직접 서술", dialogue: "대사", rumor: "전언", conjecture: "추측",
  hypothesis: "가정", negation: "부정", dream: "꿈", recollection: "회상",
  lie: "거짓말",
};

export function modeKo(m) {
  return MODE_KO[m] ?? String(m ?? "-");
}

