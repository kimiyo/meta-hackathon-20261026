// 작은 DOM·문자열 헬퍼. React와 달리 innerHTML 삽입 시 직접 이스케이프한다.
import { t } from "./i18n.js";

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

// 에러가 아닌 정보성 안내는 파란 배너로 보여준다. 빈 문자열이면 지운다.
export function showInfo(boxId, message) {
  const box = $(boxId);
  if (!box) return;
  box.innerHTML = message
    ? `<div class="notice info">${esc(message)}</div>`
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

// 코드 식별자(API·CSS 클래스·DB 값) → 현재 화면 언어 표시문. 식별자는 그대로 둔다.
// 함수명은 기존 호출부 호환을 위해 유지한다.
const STATE_KEYS = {
  WRITING: "common.state.WRITING",
  IN_REVIEW: "common.state.IN_REVIEW",
  PUBLISHING: "common.state.PUBLISHING",
};

export function stateKo(state) {
  const key = STATE_KEYS[state];
  return key ? t(key) : String(state ?? "-");
}

// 일관성 이슈 심각도·상태 → 현재 화면 언어 표시문.
const SEVERITY_KEYS = {
  critical: "common.severity.critical",
  major: "common.severity.major",
  minor: "common.severity.minor",
};

export function severityKo(s) {
  const key = SEVERITY_KEYS[s];
  return key ? t(key) : String(s ?? "-");
}

const ISSUE_STATUS_KEYS = {
  open: "common.issueStatus.open",
  fixed: "common.issueStatus.fixed",
  accepted: "common.issueStatus.accepted",
  dismissed: "common.issueStatus.dismissed",
  resolved: "common.issueStatus.resolved",
  wontfix: "common.issueStatus.wontfix",
};

export function issueStatusKo(s) {
  const key = ISSUE_STATUS_KEYS[s];
  return key ? t(key) : String(s ?? "-");
}

// 분석 판정(verdict) → 현재 화면 언어 표시문. 작가 처리 상태와 분리된다.
const VERDICT_KEYS = {
  conflict: "common.verdict.conflict",
  explained: "common.verdict.explained",
  uncertain: "common.verdict.uncertain",
};

export function verdictKo(v) {
  const key = VERDICT_KEYS[v];
  return key ? t(key) : String(v ?? "-");
}

// 사실 종류(kind) → 현재 화면 언어 표시문.
const KIND_KEYS = {
  attribute: "common.kind.attribute",
  state: "common.kind.state",
  relation: "common.kind.relation",
  rule: "common.kind.rule",
  event: "common.kind.event",
};

export function kindKo(k) {
  const key = KIND_KEYS[k];
  return key ? t(key) : String(k ?? "-");
}

// 확정 변경 이력 → 타임라인 HTML. 설정 화면과 작가 검토가 공유한다.
export function historyTimelineHtml(history) {
  if (!history.length) return `<span class="muted small">${esc(t("common.historyEmpty"))}</span>`;
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

// 서술 방식(narrative_mode) → 현재 화면 언어 표시문.
const MODE_KEYS = {
  narration: "common.mode.narration",
  dialogue: "common.mode.dialogue",
  rumor: "common.mode.rumor",
  conjecture: "common.mode.conjecture",
  hypothesis: "common.mode.hypothesis",
  negation: "common.mode.negation",
  dream: "common.mode.dream",
  recollection: "common.mode.recollection",
  lie: "common.mode.lie",
};

export function modeKo(m) {
  const key = MODE_KEYS[m];
  return key ? t(key) : String(m ?? "-");
}

