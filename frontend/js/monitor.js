import { api } from "./api.js";
import { $, esc, showError, stateKo } from "./util.js";
import { getWork } from "./work.js";
import { go, novelPath } from "./router.js";
import { initColResizer, restoreColResizer } from "./resizer.js";

const WRITING_RESIZER = { resizerId: "writing-resizer", varName: "--writing-left-w",
  storageKey: "writing-left-w", side: "left", min: 260, reserve: 320 };

// 화면 3 · 집필: 회차 작업실 (선택한 1개 회차 중심)
const ORDER = ["PLAN", "DRAFT", "VERIFY", "READY_FOR_REVIEW", "AUTHOR_APPROVED", "COMMITTED"];
const SHORT = { PLAN: "P", DRAFT: "D", VERIFY: "V", READY_FOR_REVIEW: "R", AUTHOR_APPROVED: "A", COMMITTED: "C" };
const ONCLS = { PLAN: "on-p", DRAFT: "on-d", VERIFY: "on-v", READY_FOR_REVIEW: "on-r", AUTHOR_APPROVED: "on-a", COMMITTED: "on-c" };

let rows = [];
let designNodes = [];
let detail = null;
let selectedEp = null;
let busy = false;

function setBusy(v) {
  busy = v;
  for (const b of document.querySelectorAll("#s2 button")) b.disabled = v;
  if (!v) applyStartGate();
}

// 순차 집필: EP N의 첫 시작은 EP N-1이 확정(COMMITTED)된 뒤에만 가능.
// 확정된 앞 회차 원고가 다음 회차 검증의 근거가 된다. EP 1은 항상 가능.
function canStart(ep) {
  const prev = rows.find((r) => r.episode_no === ep - 1);
  if (!prev) return true;
  return prev.job?.state === "COMMITTED";
}

function gateHint(ep) {
  return `EP ${ep - 1} 확정 후 시작할 수 있습니다`;
}

function applyStartGate() {
  document.querySelectorAll("#ep-rows tr[data-ep]").forEach((tr) => {
    const ep = Number(tr.dataset.ep);
    const btn = tr.querySelector("[data-advance-row]");
    const row = rows.find((r) => r.episode_no === ep);
    if (btn && !row?.job) {
      btn.disabled = !canStart(ep);
      btn.title = canStart(ep) ? "" : gateHint(ep);
    }
  });
  const start = document.querySelector("#ep-detail [data-start]");
  if (start && detail && !detail.hasJob) {
    start.disabled = !canStart(detail.ep);
    start.title = canStart(detail.ep) ? "" : gateHint(detail.ep);
  }
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
      (r) => `<tr class="clickable${r.episode_no === selectedEp ? " selected" : ""}" data-ep="${r.episode_no}">
        <td><b>EP ${esc(r.episode_no)}</b><br><span class="muted small">${esc(r.title)}</span></td>
        <td>${badges(r.job?.state)}</td>
        <td>${stateLabel(r.job?.state)}</td>
        <td style="white-space:nowrap">
          ${r.job ? `<button class="btn sm" data-detail data-role="writing.progress.row-detail-btn">상세</button> ` : ""}
          <button class="btn sm" data-advance-row data-role="writing.progress.row-advance-btn"${!r.job && !canStart(r.episode_no) ? " disabled" : ""}${!r.job && !canStart(r.episode_no) ? ` title="${gateHint(r.episode_no)}"` : ""}>전진</button>
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
    tr.addEventListener("click", () => openDetail(ep));
  });
  const done = rows.filter((r) => r.job?.state === "COMMITTED").length;
  const bar = $("monitor-progress");
  bar.style.display = "";
  bar.firstElementChild.style.width = `${rows.length ? Math.round((done / rows.length) * 100) : 0}%`;
}

function designEp(ep) {
  const node = designNodes.find(
    (n) => n.kind === "episode" && n.body?.episode_no === ep) ?? null;
  if (!node) return { node: null, scenes: [] };
  const scenes = designNodes.filter(
    (n) => n.parent_id === node.id && !n.archived);
  return { node, scenes };
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

function manuscriptHtml() {
  const d = detail;
  if (!d.versions.length) {
    return `<span class="muted small">원고가 없습니다. ${
      d.hasJob ? "전진을 계속하면 원고가 생성됩니다." : "집필을 시작하면 초안이 생성됩니다."}</span>`;
  }
  const opts = d.versions
    .map((r) => `<option value="${r.version}"${r.version === d.msVersion ? " selected" : ""}>v${r.version} (${esc(r.approval_status)})</option>`)
    .join("");
  const body = d.msText
    .split("\n")
    .map((p) => `<p>${esc(p) || "&nbsp;"}</p>`)
    .join("");
  return `
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
      <select data-ms-version data-role="writing.episode.version-select" style="width:auto">${opts}</select>
      <span class="small muted">${d.msText.length}자 · v${d.msVersion}</span>
    </div>
    <div class="manuscript" data-role="writing.episode.manuscript">${body}</div>`;
}

function renderDetail() {
  const box = $("ep-detail");
  if (!detail) {
    box.innerHTML = `<h3>회차 상세</h3><span class="muted">회차를 선택하면 상세 정보가 표시됩니다.</span>`;
    return;
  }
  const d = detail;
  const scenes = d.scenes.length
    ? `<ul class="ep-scenes">${d.scenes
        .map((s) => `<li><span class="chip mono">${esc(s.code ?? "")}</span> <b>${esc(s.title)}</b>${s.logline ? ` <span class="small muted">${esc(s.logline)}</span>` : ""}</li>`)
        .join("")}</ul>`
    : `<span class="muted small">장면 없음</span>`;
  box.innerHTML = `
    <h3>EP ${esc(d.ep)} · ${esc(d.title || "")} ${d.hasJob ? stateLabel(d.state) : `<span class="muted small">미시작</span>`}</h3>
    ${d.hasJob ? "" : `<div class="btn-row"><button class="btn primary" data-start data-role="writing.episode.start-btn"${canStart(d.ep) ? "" : " disabled"}${canStart(d.ep) ? "" : ` title="${gateHint(d.ep)}"`}>이 화 집필 시작</button>${canStart(d.ep) ? "" : `<span class="small muted">${gateHint(d.ep)}</span>`}</div>`}
    <h4>설계 줄거리${d.designNode ? ` <span class="chip mono">${esc(d.designNode.code ?? "")}</span>` : ""}</h4>
    <p class="small">${d.designNode ? esc(d.designNode.logline || "(줄거리 없음)") : `<span class="muted">연결된 설계 노드가 없습니다.</span>`}</p>
    <h4>장면 (${d.scenes.length})</h4>
    ${scenes}
    <h4>원고</h4>
    ${manuscriptHtml()}
    ${d.hasJob ? `
    <h4>수정 요청</h4>
    <div style="display:flex;gap:8px">
      <input type="text" data-change-input data-role="writing.episode.change-input" placeholder="수정 지시를 입력하세요" style="flex:1">
      <button class="btn sm" data-change-btn data-role="writing.episode.change-btn" style="white-space:nowrap">요청</button>
    </div>` : ""}
    <div class="btn-row" style="margin-top:10px">
      ${d.hasJob ? `<button class="btn primary" data-advance data-role="writing.episode.advance-btn">다음 단계로 전진</button>` : ""}
      ${d.hasJob ? `<button class="btn" data-review data-role="writing.episode.review-link">작가 검토로 이동 →</button>` : ""}
    </div>
    ${d.hasJob ? `<div class="small muted" style="margin-top:6px">※ 전진은 되돌릴 수 없습니다. 같은 상태에서 반복하면 재시도 횟수를 소모합니다.</div>` : ""}
    ${d.hasJob ? `<details style="margin-top:10px"><summary class="small muted">파이프라인 정보</summary>
      <dl class="kv">
        <dt>시도</dt><dd class="mono">${esc(d.attempt)}</dd>
        <dt>원고 rev</dt><dd class="mono">${esc(d.manuscript_revision_id ?? "-")}</dd>
        <dt>제안 set</dt><dd class="mono">${esc(d.proposal_set_id ?? "-")}</dd>
      </dl>
      ${lastErrorHtml()}
      ${verificationHtml()}
      ${qualityHtml()}
    </details>` : ""}`;
  box.querySelector("[data-start]")?.addEventListener("click", (e) =>
    advanceCurrent(e.currentTarget));
  box.querySelector("[data-advance]")?.addEventListener("click", (e) =>
    advanceCurrent(e.currentTarget));
  box.querySelector("[data-review]")?.addEventListener("click", goReview);
  box.querySelector("[data-ms-version]")?.addEventListener("change", (e) =>
    run(async () => {
      detail.msVersion = Number(e.target.value);
      detail.msText = (await api.manuscript(detail.ep, detail.msVersion)).text ?? "";
      renderDetail();
    }));
  const changeBtn = box.querySelector("[data-change-btn]");
  if (changeBtn) changeBtn.addEventListener("click", () =>
    run(async () => {
      const input = box.querySelector("[data-change-input]");
      const instruction = input.value.trim();
      if (!instruction) {
        showError("monitor-error", "수정 지시를 입력하세요.");
        return;
      }
      await api.requestChange(detail.ep, instruction);
      await load(currentDesignId());
      await fetchDetail(detail.ep);
    }));
}

function currentDesignId() {
  return $("monitor-design-id").value.trim();
}

async function load(did) {
  const d = await api.designEpisodes(did);
  rows = d.episodes ?? [];
  try {
    designNodes = (await api.nodes(did)).nodes ?? [];
  } catch {
    designNodes = [];
  }
  renderRows();
}

async function fetchDetail(ep) {
  selectedEp = Number(ep);
  renderRows();
  const row = rows.find((r) => r.episode_no === selectedEp);
  const hasJob = !!row?.job;
  const { node, scenes } = designEp(selectedEp);
  if (hasJob) {
    detail = { ep: selectedEp, hasJob: true, title: row.title,
      ...(await api.episode(selectedEp)), designNode: node, scenes };
    detail.versions = detail.revisions ?? [];
    if (detail.versions.length) {
      detail.msVersion = detail.versions[detail.versions.length - 1].version;
      detail.msText = (await api.manuscript(
        selectedEp, detail.msVersion)).text ?? "";
    } else {
      detail.msVersion = null;
      detail.msText = "";
    }
  } else {
    detail = { ep: selectedEp, hasJob: false, title: row?.title ?? "",
      designNode: node, scenes, versions: [], msVersion: null, msText: "" };
  }
  renderDetail();
}

function openDetail(ep) {
  return run(() => fetchDetail(ep));
}

async function advanceCurrent(btn) {
  await run(async () => {
    const label = btn.textContent;
    btn.textContent = "실행 중… (취소 불가)";
    try {
      await api.advance(detail.ep, detail.hasJob ? undefined : Number(currentDesignId()));
      await load(currentDesignId());
      await fetchDetail(detail.ep);
      await refreshBudgetChips();
    } finally {
      btn.textContent = label;
    }
  });
}

async function advanceRow(ep, hasJob, btn) {
  await run(async () => {
    const label = btn.textContent;
    btn.textContent = "실행 중… (취소 불가)";
    try {
      await api.advance(ep, hasJob ? undefined : Number(currentDesignId()));
      await load(currentDesignId());
      await fetchDetail(ep);
      await refreshBudgetChips();
    } finally {
      btn.textContent = label;
    }
  });
}

function goReview() {
  const w = getWork();
  if (!w || !detail) return;
  const ep = detail.ep;
  go(novelPath(w.uid || w.id, "review"));
  setTimeout(() => {
    document.dispatchEvent(new CustomEvent("review-episode", {
      detail: { ep },
    }));
  }, 50);
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
  initColResizer(WRITING_RESIZER);
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
      await fetchDetail(Number(ep));
      document.querySelector(`#ep-rows tr[data-ep="${Number(ep)}"]`)
        ?.scrollIntoView({ block: "nearest" });
    });
  });
  document.addEventListener("enter-screen", (e) => {
    if (e.detail?.section !== "s2") return;
    restoreColResizer(WRITING_RESIZER);
    const cur = getWork();
    if (!cur) return;
    run(async () => {
      const full = await api.work(cur.uid || cur.id);
      const did = full.latest_design?.id;
      if (did && String($("monitor-design-id").value).trim() !== String(did)) {
        $("monitor-design-id").value = did;
        await load(did);
      }
      if (selectedEp != null && rows.some((r) => r.episode_no === selectedEp)) {
        await fetchDetail(selectedEp);
      }
    });
  });
  refreshBudgetChips();
}
