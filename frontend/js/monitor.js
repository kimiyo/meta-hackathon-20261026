import { api } from "./api.js";
import { $, esc, showError, stateKo } from "./util.js";
import { getWork } from "./work.js";

// 화면 3 · 집필 (구 React판에서 이식)
const ORDER = ["PLAN", "DRAFT", "VERIFY", "READY_FOR_REVIEW", "AUTHOR_APPROVED", "COMMITTED"];
const SHORT = { PLAN: "P", DRAFT: "D", VERIFY: "V", READY_FOR_REVIEW: "R", AUTHOR_APPROVED: "A", COMMITTED: "C" };
const ONCLS = { PLAN: "on-p", DRAFT: "on-d", VERIFY: "on-v", READY_FOR_REVIEW: "on-r", AUTHOR_APPROVED: "on-a", COMMITTED: "on-c" };

let rows = [];
let detail = null;
let busy = false;

function setBusy(v) {
  busy = v;
  for (const b of document.querySelectorAll("#s2 button")) b.disabled = v;
}

async function run(fn) {
  setBusy(true);
  showError("monitor-error", "");
  try {
    await fn();
  } catch (e) {
    showError("monitor-error", String(e));
  } finally {
    setBusy(false);
  }
}

function badges(state) {
  if (!state) return `<span class="muted small">미시작</span>`;
  if (state === "NEEDS_AUTHOR") return `<span class="st-NEEDS_AUTHOR state-label">${stateKo(state)}</span>`;
  const idx = ORDER.indexOf(state);
  return ORDER.map((s, i) =>
    `<span class="badge${i <= idx ? " " + ONCLS[s] : ""}" title="${esc(stateKo(s))}">${SHORT[s]}</span>`
  ).join("");
}

function stateLabel(state) {
  if (!state) return "-";
  return `<span class="st-${esc(state)} state-label">${esc(stateKo(state))}</span>`;
}

function renderRows() {
  const tb = $("ep-rows");
  if (!rows.length) {
    tb.innerHTML = `<tr><td colspan="4"><span class="muted small">회차가 없습니다.</span></td></tr>`;
    return;
  }
  tb.innerHTML = rows
    .map(
      (r) => `<tr class="clickable" data-ep="${r.episode_no}">
        <td><b>EP ${esc(r.episode_no)}</b><br><span class="muted small">${esc(r.title)}</span></td>
        <td>${badges(r.job?.state)}</td>
        <td>${stateLabel(r.job?.state)}</td>
        <td style="white-space:nowrap">
          ${r.job ? `<button class="btn sm" data-detail>상세</button> ` : ""}
          <button class="btn sm" data-advance-row>전진</button>
        </td>
      </tr>`
    )
    .join("");
  tb.querySelectorAll("tr[data-ep]").forEach((tr) => {
    const ep = Number(tr.dataset.ep);
    const row = rows.find((r) => r.episode_no === ep);
    const detailBtn = tr.querySelector("[data-detail]");
    if (detailBtn) detailBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      openDetail(ep);
    });
    tr.querySelector("[data-advance-row]").addEventListener("click", (e) => {
      e.stopPropagation();
      advanceRow(ep, !!row?.job, e.currentTarget);
    });
    tr.addEventListener("click", () => {
      if (row?.job) openDetail(ep);
    });
  });
  const done = rows.filter((r) => r.job?.state === "COMMITTED").length;
  const bar = $("monitor-progress");
  bar.style.display = "";
  bar.firstElementChild.style.width = `${rows.length ? Math.round((done / rows.length) * 100) : 0}%`;
}

function lastErrorHtml() {
  if (!detail.last_error) return "";
  return `<div class="notice err" style="margin-top:10px">사유: ${esc(detail.last_error)}</div>`;
}

function verificationHtml() {
  if (!detail.verification) return "";
  return `<p class="small">검증: <span class="mono">${esc(JSON.stringify(detail.verification.counts))}</span></p>`;
}

function qualityHtml() {
  if (!detail.quality) return "";
  const scores = detail.quality.scores ?? detail.quality;
  return `<p class="small">품질: <span class="mono">${esc(JSON.stringify(scores))}</span></p>`;
}

function renderDetail() {
  const box = $("ep-detail");
  if (!detail) {
    box.innerHTML = `<h3>회차 상세</h3><span class="muted">회차를 선택하면 상세 정보가 표시됩니다.</span>`;
    return;
  }
  const revs = (detail.revisions ?? [])
    .map((r) => `v${r.version}(${r.approval_status}, ${r.length}자)`)
    .join(", ");
  box.innerHTML = `
    <h3>EP ${esc(detail.ep)} 상세 <span class="hint">revision · proposal set</span></h3>
    <dl class="kv">
      <dt>상태</dt><dd>${stateLabel(detail.state)}</dd>
      <dt>시도</dt><dd class="mono">${esc(detail.attempt)}</dd>
      <dt>원고 rev</dt><dd class="mono">${esc(detail.manuscript_revision_id ?? "-")}</dd>
      <dt>제안 set</dt><dd class="mono">${esc(detail.proposal_set_id ?? "-")}</dd>
      <dt>원고 버전</dt><dd class="mono">${esc(revs || "없음")}</dd>
    </dl>
    ${lastErrorHtml()}
    ${verificationHtml()}
    ${qualityHtml()}
    <div class="btn-row">
      <button class="btn primary" data-advance>다음 단계로 전진</button>
    </div>
    <div class="small muted" style="margin-top:6px">※ 전진은 되돌릴 수 없습니다. 같은 상태에서 반복하면 재시도 횟수를 소모합니다.</div>`;
  box.querySelector("[data-advance]").addEventListener("click", () =>
    run(async () => {
      const btn = box.querySelector("[data-advance]");
      btn.textContent = "실행 중… (취소 불가)";
      try {
        await api.advance(detail.ep, detail.hasJob ? undefined : currentDesignId());
        await load(currentDesignId());
        detail = { ep: detail.ep, ...(await api.episode(detail.ep)) };
        renderDetail();
        await refreshCalls();
        await refreshBudgetChips();
      } finally {
        btn.textContent = "다음 단계로 전진";
      }
    })
  );
}

function currentDesignId() {
  return $("monitor-design-id").value.trim();
}

async function load(did) {
  const d = await api.designEpisodes(did);
  rows = d.episodes ?? [];
  renderRows();
}

async function advanceRow(ep, hasJob, btn) {
  await run(async () => {
    const label = btn.textContent;
    btn.textContent = "실행 중… (취소 불가)";
    try {
      await api.advance(ep, hasJob ? undefined : Number(currentDesignId()));
      await load(currentDesignId());
      await refreshCalls();
      await refreshBudgetChips();
    } finally {
      btn.textContent = label;
    }
  });
}

async function openDetail(ep) {
  await run(async () => {
    const row = rows.find((r) => r.episode_no === ep);
    detail = { ep, hasJob: !!row?.job, ...(await api.episode(ep)) };
    renderDetail();
    await refreshCalls();
    await refreshBudgetChips();
  });
}

async function refreshCalls() {
  const tb = $("calls-rows");
  try {
    const { calls } = await api.llmCalls();
    if (!calls.length) {
      tb.innerHTML = `<tr><td colspan="6"><span class="muted small">기록 없음</span></td></tr>`;
      return;
    }
    tb.innerHTML = calls
      .map(
        (c) => `<tr>
          <td class="mono small">${esc((c.ts ?? "").slice(11, 19))}</td>
          <td><span class="chip">${esc(c.bundle)}</span></td>
          <td>${c.episode_no ?? "-"}</td>
          <td class="mono small">${esc(c.input_tokens)}/${esc(c.output_tokens)}</td>
          <td class="mono small">${esc(c.latency_ms)}ms</td>
          <td>${c.ok ? `<span class="step-done">성공</span>` : `<span class="step-run">${esc(c.error || "실패")}</span>`}</td>
        </tr>`
      )
      .join("");
  } catch {
    tb.innerHTML = `<tr><td colspan="6"><span class="muted small">호출 기록 조회 실패</span></td></tr>`;
  }
}

export async function refreshBudgetChips() {
  try {
    const b = await api.opsBudget();
    const text = `오늘 토큰 ${b.total_tokens} / ${b.budget} (잔여 ${b.remaining})`;
    const chip = $("budget-chip");
    if (chip) chip.textContent = text;
    const line = $("monitor-budget");
    if (line) line.textContent = text;
  } catch {
    /* 예산 조회 실패는 치명적이지 않음 */
  }
}

export function initMonitor() {
  $("btn-monitor-load").addEventListener("click", () =>
    run(async () => {
      const did = currentDesignId();
      if (did) await load(did);
    })
  );
  // 첫 작품의 최신 설계로 자동 초기화
  api
    .works()
    .then(async (w) => {
      if (!w.works.length) return;
      const cur = getWork();
      const target = cur ? cur.uid || cur.id : w.works[0].uid || w.works[0].id;
      const full = await api.work(target);
      if (full.latest_design) {
        $("monitor-design-id").value = full.latest_design.id;
        await load(full.latest_design.id);
      }
    })
    .catch(() => {});
  document.addEventListener("write-episode", (e) => {
    const { designId, ep } = e.detail ?? {};
    if (!designId) return;
    run(async () => {
      $("monitor-design-id").value = designId;
      await load(designId);
      const row = rows.find((r) => r.episode_no === ep);
      if (row?.job) {
        detail = { ep, hasJob: true, ...(await api.episode(ep)) };
        renderDetail();
      }
      await refreshCalls();
    });
  });
  document.addEventListener("enter-screen", (e) => {
    if (e.detail?.section !== "s2") return;
    const cur = getWork();
    if (!cur) return;
    run(async () => {
      const full = await api.work(cur.uid || cur.id);
      const did = full.latest_design?.id;
      if (did && String($("monitor-design-id").value).trim() !== String(did)) {
        $("monitor-design-id").value = did;
        await load(did);
      }
    });
  });
  refreshCalls();
  refreshBudgetChips();
}
