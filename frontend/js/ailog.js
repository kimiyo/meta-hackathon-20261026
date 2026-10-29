import { api } from "./api.js";
import { $, esc, showError } from "./util.js";
import { getWork } from "./work.js";

// 상단 토큰 버튼 → 현재 소설의 AI 호출 로그 다이얼로그 (목록 + 상세).
const ROLE = {
  planning: "기획", drafting: "집필", entity: "추출", verify: "검증",
  quality: "품질", idea_chat: "아이디어 대화", idea_extract: "아이디어 정리",
  design_chat: "설계 파트너", design_expand: "설계 확장",
};

let calls = [];
let selectedId = null;

function pretty(text) {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text || "";
  }
}

function fmtTime(ts) {
  return String(ts || "").slice(0, 19).replace("T", " ");
}

function fmtDur(ms) {
  return `${(Number(ms || 0) / 1000).toFixed(1)}초`;
}

function listRowHtml(c) {
  const dot = c.ok ? "step-done" : "step-run";
  const mark = c.ok ? "성공" : "실패";
  return `<div class="ailog-item${c.id === selectedId ? " active" : ""}" data-log="${c.id}">
    <div><b>${esc(ROLE[c.bundle] ?? c.bundle)}</b> <span class="${dot} small">${mark}</span></div>
    <div class="small muted">${esc(fmtTime(c.ts))}</div>
    <div class="small muted">in ${esc(c.input_tokens)}/out ${esc(c.output_tokens)} · ${esc(fmtDur(c.latency_ms))}</div>
  </div>`;
}

function block(label, text) {
  if (!text) return "";
  return `<details class="ailog-block"><summary>${label} <span class="muted small">(${(text ?? "").length}자)</span></summary><pre>${esc(pretty(text))}</pre></details>`;
}

function detailHtml(c) {
  const role = ROLE[c.bundle] ?? c.bundle;
  const ep = c.episode_no ?? "-";
  const ok = c.ok
    ? `<span class="step-done">성공</span>`
    : `<span class="step-run">${esc(c.error || "실패")}</span>`;
  return `<div class="ailog-detail-head"><b>${esc(role)}</b> <span class="chip mono">${esc(c.bundle)}</span>
      <span class="muted small">${esc(fmtTime(c.ts))} · EP ${esc(ep)} · ${esc(fmtDur(c.latency_ms))} · in ${esc(c.input_tokens)}/out ${esc(c.output_tokens)}</span> ${ok}</div>
    ${block("입력 프롬프트 (system)", c.prompt_system)}
    ${block("입력 프롬프트 (user)", c.prompt_user)}
    ${block("출력 결과", c.output_text)}`;
}

function renderList() {
  const box = $("ailog-list");
  box.innerHTML = calls.map(listRowHtml).join("");
  box.querySelectorAll("[data-log]").forEach((el) => {
    el.addEventListener("click", () => {
      selectedId = Number(el.dataset.log);
      renderList();
      renderDetail();
    });
  });
}

function renderDetail() {
  const box = $("ailog-detail");
  const c = calls.find((x) => x.id === selectedId);
  box.innerHTML = c ? detailHtml(c) : `<span class="muted">왼쪽 목록에서 호출을 선택하세요.</span>`;
}

export async function openAiLog() {
  const w = getWork();
  $("ailog-error").innerHTML = "";
  calls = [];
  selectedId = null;
  if (!w) {
    $("ailog-sub").textContent = "";
    $("ailog-list").innerHTML = "";
    $("ailog-detail").innerHTML = `<span class="muted">소설을 선택하면 해당 소설의 AI 로그를 봅니다.</span>`;
  } else {
    $("ailog-sub").textContent = w.title;
    $("ailog-list").innerHTML = `<span class="muted small">불러오는 중…</span>`;
    $("ailog-detail").innerHTML = "";
    try {
      const out = await api.aiLog(w.uid || w.id, 50);
      calls = out.calls ?? [];
      if (!calls.length) {
        $("ailog-list").innerHTML = "";
        $("ailog-detail").innerHTML = `<span class="muted">기록이 없습니다. (이 기능 추가 전 호출은 소설별 기록이 없습니다)</span>`;
      } else {
        selectedId = calls[0].id;
        renderList();
        renderDetail();
      }
    } catch (e) {
      showError("ailog-error", String(e));
      $("ailog-list").innerHTML = "";
      $("ailog-detail").innerHTML = "";
    }
  }
  $("ailog-backdrop").style.display = "";
  document.body.style.overflow = "hidden";
}

function closeAiLog() {
  $("ailog-backdrop").style.display = "none";
  document.body.style.overflow = "";
}

export function initAiLog() {
  $("budget-chip").addEventListener("click", openAiLog);
  $("btn-ailog-close").addEventListener("click", closeAiLog);
  $("ailog-backdrop").addEventListener("click", (e) => {
    if (e.target === $("ailog-backdrop")) closeAiLog();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && $("ailog-backdrop").style.display !== "none") closeAiLog();
  });
}
