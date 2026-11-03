import { api } from "./api.js";
import { $, esc, showError, showToast, stateKo, severityKo, issueStatusKo, verdictKo, kindKo, modeKo, historyTimelineHtml } from "./util.js";
import { initColResizer, restoreColResizer } from "./resizer.js";
import { mountManuscriptPanel } from "./manuscript.js";
import { getWork } from "./work.js";

const REVIEW_RESIZER = { resizerId: "review-resizer", varName: "--review-right-w",
  storageKey: "review-right-w", side: "right", min: 280, reserve: 420 };

// 화면 4 · 작가 검토 (읽기 전용 원고 + 일관성 분석 + 이슈 + 기억)

// 원고 보기는 공용 패널(읽기 전용)으로 제공한다.
const REVIEW_MS_ROLES = {
  versionSelect: "review.manuscript.version-select",
  diffCheckbox: "review.manuscript.diff-checkbox",
  body: "review.manuscript.body",
};

let job = null;
let msPanel = null;
let issues = [];
let messages = [];
let pending = null;
let aliases = [];
let analysis = null;
let entityOptions = [];
let busy = false;

function setBusy(v) {
  busy = v;
  for (const b of document.querySelectorAll("#s3 button")) b.disabled = v;
  if (!v) {
    updateActionState();
    updateMemoryButtons();
  }
}

function showSaveResultDialog(msg) {
  $("save-result-msg").textContent = msg;
  $("save-result-backdrop").style.display = "";
}

function selectReviewTab(which) {
  const extract = which !== "consistency";
  $("tabpane-extract").style.display = extract ? "" : "none";
  $("tabpane-consistency").style.display = extract ? "none" : "";
  $("tab-extract").classList.toggle("active", extract);
  $("tab-consistency").classList.toggle("active", !extract);
  $("tab-extract").setAttribute("aria-selected", String(extract));
  $("tab-consistency").setAttribute("aria-selected", String(!extract));
}

function committedDoomed() {
  const s = pending?.committed_summary;
  return (s?.changes ?? 0) + (s?.events ?? 0) + (s?.aliases ?? 0);
}

function hasMemoryData() {
  return !!(pending?.proposal_set || pending?.saved_set) || committedDoomed() > 0;
}

function updateMemoryButtons() {
  const btn = $("btn-entity-reset");
  if (!btn) return;
  btn.disabled = !hasMemoryData();
}

function openResetConfirm() {
  const s = pending?.committed_summary ?? {};
  const bits = [];
  if (s.changes) bits.push(`변경 ${s.changes}건`);
  if (s.events) bits.push(`사건 ${s.events}건`);
  if (s.aliases) bits.push(`별명 ${s.aliases}건`);
  const open = issues.filter((i) => i.status === "open").length;
  $("reset-confirm-msg").textContent = (bits.length
    ? `이번 화의 저장된 설정 기억(${bits.join("·")})과 추출 이력을 모두 삭제합니다.`
    : `임시 추출안을 삭제합니다.`)
    + (open ? ` 열린 이슈 ${open}건도 함께 삭제됩니다.` : "")
    + ` 되돌릴 수 없습니다.`;
  $("reset-confirm-backdrop").style.display = "";
}

function hasReviewData() {
  return issues.some((i) => i.status === "open") || !!analysis;
}

function updateConsistencyResetButton() {
  const btn = $("btn-consistency-reset");
  if (!btn) return;
  btn.disabled = !hasReviewData();
}

function openReviewResetConfirm() {
  const open = issues.filter((i) => i.status === "open").length;
  const bits = [];
  if (open) bits.push(`열린 이슈 ${open}건`);
  if (analysis) bits.push("분석 기록");
  $("consistency-reset-confirm-msg").textContent =
    `이번 화의 ${bits.join("·")}를 삭제합니다. 처리된 이슈는 유지됩니다. 되돌릴 수 없습니다.`;
  $("consistency-reset-confirm-backdrop").style.display = "";
}

async function reloadMemoryPanel() {
  pending = await api.entityProposals(currentEp(), currentWorkKey());
  await reloadAliases();
  await refreshAnalysis();
  entityOptions = (await api.entities("")).entities ?? [];
  renderPending();
  renderAliases();
  renderNotice();
}

async function doReset() {
  await api.resetEntities(currentEp(), currentWorkKey());
  await reloadMemoryPanel();
  await refreshIssues();
}

async function doReviewReset() {
  await api.consistencyReset(currentEp(), currentWorkKey());
  await refreshIssues();
  await refreshAnalysis();
}

async function doExtract() {
  $("btn-entity-extract").textContent = "추출 중…";
  try {
    await api.extractEntities(currentEp(), null, currentWorkKey());
  } finally {
    $("btn-entity-extract").textContent = "설정 추출 실행";
    await reloadMemoryPanel();
  }
}

function inReview() {
  return !!job && job.state === "IN_REVIEW";
}

function updateActionState() {
  const ok = !busy && inReview();
  for (const id of ["btn-back-to-writing", "btn-request-publish",
                    "btn-consistency-analyze", "btn-consistency-send",
                    "btn-entity-extract", "btn-entity-save",
                    "btn-entity-chat"]) {
    const b = $(id);
    if (b) b.disabled = !ok;
  }
  for (const id of ["consistency-input", "entity-chat-input"]) {
    const input = $(id);
    if (input) input.disabled = !ok;
  }
  for (const b of document.querySelectorAll("#issue-list button, #memory-pending button, #alias-list button")) {
    if (b.hasAttribute("data-jump")) continue; // 근거 이동은 읽기 전용에서도 허용
    b.disabled = !ok;
  }
}

async function run(fn) {
  setBusy(true);
  showError("review-error", "");
  try {
    await fn();
  } catch (e) {
    showError("review-error", String(e));
  } finally {
    setBusy(false);
  }
}

function currentEp() {
  return Number($("review-ep").value);
}

function currentWorkKey() {
  const w = getWork();
  return w?.uid || w?.id || null;
}

function disposeMsPanel() {
  if (!msPanel) return;
  msPanel.dispose();
  msPanel = null;
}

function mountMsPanel(epNo, document) {
  disposeMsPanel();
  msPanel = mountManuscriptPanel($("review-ms-mount"), {
    episodeNo: epNo,
    initialDocument: document,
    readOnly: true,
    roles: REVIEW_MS_ROLES,
  });
}

// 근거 이동(§7): 사실·이슈 클릭 시 원고 버전과 구간으로 이동한다.
function jumpToEvidence(quote, revisionId) {
  if (!msPanel || !quote) return;
  const found = msPanel.jumpToQuote(quote, revisionId ?? null);
  if (!found) {
    showError("review-error", "현재 원고에서 해당 구간을 찾지 못했습니다(다른 회차 근거일 수 있습니다).");
  } else {
    showError("review-error", "");
  }
}

async function load(epNo) {
  const wk = currentWorkKey();
  if (!wk) {
    showError("review-error", "작품을 먼저 선택하세요.");
    return;
  }
  if (!Number.isInteger(epNo) || epNo < 1) {
    showError("review-error", "유효한 회차 번호를 입력하세요.");
    return;
  }
  const j = await api.episode(epNo, wk);
  const { document } = await api.manuscriptDoc(epNo, wk);
  const [iss, msgs, ents, prop, ana] = await Promise.all([
    api.consistencyIssues(epNo, null, wk),
    api.consistencyMessages(epNo, wk),
    api.entities(""),
    api.entityProposals(epNo, wk),
    api.latestAnalysis(epNo, wk).catch(() => ({ analysis: null })),
  ]);
  let aliasList = [];
  try {
    aliasList = (await api.aliases(wk)).candidates ?? [];
  } catch {
    aliasList = [];
  }
  job = j;
  mountMsPanel(epNo, document);
  issues = iss.issues ?? [];
  messages = msgs.messages ?? [];
  pending = prop;
  analysis = ana.analysis ?? null;
  aliases = aliasList;
  entityOptions = ents.entities ?? [];
  renderAll();
}

function renderNotice() {
  const box = $("review-notice");
  if (!job) {
    box.innerHTML = "";
    return;
  }
  const open = issues.filter((i) => i.status === "open").length;
  let preconditions = "";
  if (job.state === "IN_REVIEW") {
    const conds = [];
    const pset = pending?.proposal_set;
    const saved = pending?.saved_set;
    const revId = job.manuscript_revision_id;
    if (pset && pset.manuscript_revision_id === revId) {
      conds.push("미저장 변경 있음(저장 필요)");
    } else if (!saved || saved.manuscript_revision_id !== revId) {
      conds.push("추출 필요");
    }
    if (!analysis) {
      conds.push("일관성 분석 전");
    } else if (analysis.status === "partial") {
      conds.push("분석 부분 완료(AI 실패)");
    } else if (analysis.status === "failed") {
      conds.push("분석 실패");
    } else if (analysis.status === "skipped") {
      conds.push("분석 건너뜀(비교 대상 없음)");
    } else if (analysis.manuscript_revision_id !== revId) {
      conds.push("분석이 낡음(원고 변경됨)");
    }
    if (open > 0) conds.push(`열린 이슈 ${open}건`);
    preconditions = conds.length ? ` — 발간 전 확인: ${esc(conds.join(" · "))}` : " — 발간 가능";
    box.innerHTML = `<div class="notice ok"><b>EP ${esc(job.episode_no)} · ${stateKo(job.state)}</b> — 열린 이슈 ${open}건. 수정이 필요하면 집필중으로 돌아가세요. 문제없으면 발간요청을 누르세요.${preconditions}</div>`;
  } else if (job.state === "PUBLISHING") {
    box.innerHTML = `<div class="notice"><b>EP ${esc(job.episode_no)} · ${stateKo(job.state)}</b> — 발간된 회차입니다. 읽기 전용입니다.</div>`;
  } else if (job.needs_attention) {
    box.innerHTML = `<div class="notice err"><b>EP ${esc(job.episode_no)} · ${stateKo(job.state)}</b> — ${esc(job.last_error || "확인이 필요합니다.")}</div>`;
  } else {
    box.innerHTML = `<div class="notice warn"><b>EP ${esc(job.episode_no)} · ${stateKo(job.state)}</b> — 집필중인 회차입니다. 집필 화면에서 검토를 요청하세요.</div>`;
  }
  $("review-state").innerHTML = `상태: <b>${esc(stateKo(job.state))}</b>`;
}

function renderMessages() {
  const box = $("consistency-messages");
  if (!messages.length) {
    box.innerHTML = "";
    box.style.display = "none";
    return;
  }
  box.style.display = "";
  box.innerHTML = messages.map((m) =>
    `<div class="msg ${m.role === "user" ? "user" : "assistant"}"><div class="bubble">${esc(m.content)}</div></div>`
  ).join("");
  box.scrollTop = box.scrollHeight;
}

function renderAnalysis() {
  const box = $("analysis-status");
  if (!analysis) {
    box.innerHTML = `<span class="muted small">분석 전입니다. 전체 분석 실행을 눌러주세요.</span>`;
    return;
  }
  const counts = (() => { try { return JSON.parse(analysis.counts_json || "{}"); } catch { return {}; } })();
  const cov = (() => { try { return JSON.parse(analysis.coverage_json || "{}"); } catch { return {}; } })();
  const statusKo = { completed: "분석 완료", partial: "부분 완료", failed: "분석 실패", skipped: "건너뜀" }[analysis.status] ?? analysis.status;
  const CHECK_KO = { check_count_limits: "횟수 제한", check_death: "사망 후 행동", check_exclusive_location: "배타적 위치", check_state_clash: "상태 충돌" };
  const ruleChecks = cov.rule_checks ?? [];
  const failedChecks = ruleChecks.filter((r) => r.status !== "completed");
  const ruleSummary = !ruleChecks.length ? ""
    : failedChecks.length ? ` · 확정검사 ${ruleChecks.length}건 중 ${failedChecks.length}건 실패(${failedChecks.map((r) => esc(CHECK_KO[r.check] ?? r.check)).join(", ")})`
    : ` · 확정검사 ${ruleChecks.length}건 완료`;
  const prev = cov.prev_manuscript ? `EP ${esc(cov.prev_manuscript.episode_no)} 원문${cov.prev_manuscript.approved ? "(발간본)" : "(최신본)"} + ` : "";
  box.innerHTML = `<span class="chip">${esc(statusKo)}</span> ` +
    `<span class="small muted">${prev ? `비교 범위: ${prev}확정 기억` : "비교 범위: 확정 기억"}` +
    (cov.entity_groups ? ` · 설정 묶음 ${esc(cov.entity_groups)}개` : "") +
    ` · 규칙 ${esc(counts.rule ?? 0)}건 · AI ${esc(counts.ai ?? 0)}건 · 저장 ${esc(counts.saved ?? issues.length)}건` +
    (counts.accepted_explained ? ` (설명됨 ${esc(counts.accepted_explained)}건 수용)` : "") +
    ` · 제외 ${esc(counts.dropped ?? 0)}건` +
    ruleSummary +
    (cov.reason ? ` · ${esc(cov.reason)}` : "") +
    (analysis.error ? ` · ${esc(analysis.error.slice(0, 120))}` : "") + `</span>`;
}

function renderIssues() {
  const box = $("issue-list");
  const open = issues.filter((i) => i.status === "open").length;
  $("issue-count").textContent = `열린 이슈 ${open}건 / 전체 ${issues.length}건`;
  updateConsistencyResetButton();
  renderAnalysis();
  if (!issues.length) {
    box.innerHTML = `<span class="muted small">등록된 이슈가 없습니다.</span>`;
    return;
  }
  box.innerHTML = issues.map((it) => {
    let refs = [];
    try { refs = JSON.parse(it.fact_refs_json || "[]"); } catch { refs = []; }
    return `
    <div class="issue-card" data-issue="${it.issue_id}" data-status="${esc(it.status)}">
      <div class="issue-head">
        <span class="sev-${esc(it.severity)}">${esc(severityKo(it.severity))}</span>
        <span class="title">${esc(it.title || "(제목 없음)")}</span>
        <span class="issue-status">${esc(issueStatusKo(it.status))}</span>
      </div>
      <div class="small muted">판정: <b>${esc(verdictKo(it.verdict))}</b>${refs.length ? ` · 관련 사실 #${refs.map((r) => esc(r)).join(", #")}` : ""}</div>
      ${it.detail ? `<div class="issue-detail">${esc(it.detail)}</div>` : ""}
      ${it.manuscript_quote ? `<div class="quote-box"><span class="src">원고 인용</span>${esc(it.manuscript_quote)} <button class="btn sm ghost" data-jump data-role="review.issues.jump-btn" data-quote="${esc(it.manuscript_quote.slice(0, 200))}" data-rev="${it.manuscript_revision_id ?? ""}">원고 이동</button></div>` : ""}
      ${it.evidence_quote ? `<div class="quote-box"><span class="src">근거${it.evidence_episode ? ` · EP ${esc(it.evidence_episode)}` : ""}${it.entity_id ? ` · ${esc(it.entity_id)}` : ""}</span>${esc(it.evidence_quote)}</div>` : ""}
      <div class="btn-row">
        ${it.status === "open"
          ? `<button class="btn sm" data-fixed>수정 완료</button>
             <button class="btn sm ghost" data-accepted>의도적 수용</button>
             <button class="btn sm ghost" data-dismissed>오탐 기각</button>`
          : `<button class="btn sm ghost" data-reopen>다시 열기</button>`}
      </div>
    </div>`;
  }).join("");
  box.querySelectorAll("[data-issue]").forEach((card) => {
    const id = Number(card.dataset.issue);
    const set = (status) => run(async () => {
      await api.setIssueStatus(id, status);
      issues = (await api.consistencyIssues(currentEp(), null, currentWorkKey())).issues ?? [];
      renderIssues();
      renderNotice();
    });
    card.querySelector("[data-fixed]")?.addEventListener("click", () => set("fixed"));
    card.querySelector("[data-accepted]")?.addEventListener("click", () => set("accepted"));
    card.querySelector("[data-dismissed]")?.addEventListener("click", () => set("dismissed"));
    card.querySelector("[data-reopen]")?.addEventListener("click", () => set("open"));
  });
  box.querySelectorAll("[data-jump]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const rev = btn.dataset.rev ? Number(btn.dataset.rev) : null;
      jumpToEvidence(btn.dataset.quote, rev);
    });
  });
}

function displayValue(v) {
  if (v == null || v === "") return "";
  try {
    const p = JSON.parse(v);
    return typeof p === "string" ? p : JSON.stringify(p);
  } catch {
    return String(v);
  }
}

const TRASH_SVG = `<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M2 4h12M6 4V2.5h4V4M4 4l.7 10h6.6L12 4M6.5 7v4M9.5 7v4"/></svg>`;

function proposalItemsHtml(items, readonly = false) {
  if (!items.length) return `<li><span class="muted small">변경 항목 없음</span></li>`;
  return items.map((it) => `
    <li><div class="pev-head"><span>
      <span class="chip">${esc(kindKo(it.kind))}</span>
      <b>${esc(it.entity_id || it.candidate_name || "(미지정)")}</b>
      ${it.attribute_path ? `<span class="small muted">${esc(it.attribute_path)}</span>` : ""}
      ${it.story_time ? `<span class="chip">${esc(it.story_time)}</span>` : ""}
      ${(it.narrative_mode && it.narrative_mode !== "narration") ? `<span class="small muted">${esc(modeKo(it.narrative_mode))}</span>` : ""}
      ${it.validation === "deferred" ? `<span class="small muted">(근거 미확인·제외)</span>` : ""}
      ${it.validation === "stale" ? `<span class="small muted">(낡음·재추출 필요)</span>` : ""}</span>
      <span><button class="btn sm ghost pev-del" data-jump data-role="review.memory.item-jump-btn" data-quote="${esc((it.evidence_quote || "").slice(0, 200))}" title="원고에서 근거 보기">이동</button>${readonly ? "" : `
      <button class="btn sm ghost pev-del" data-del-item="${it.item_id}" data-role="review.memory.item-del-btn" title="이 항목 삭제">${TRASH_SVG}</button>`}</span></div>
      <span class="small">${esc(displayValue(it.new_value).slice(0, 120))}</span>
      <br><span class="small muted">인용: ${esc((it.evidence_quote || "").slice(0, 120))}</span></li>`
  ).join("");
}

function partName(p) {
  if (p.candidate_name) return p.candidate_name;
  const opt = p.entity_id
    ? entityOptions.find((e) => e.entity_id === p.entity_id) : null;
  return opt?.canonical_name || p.entity_id || "?";
}

function eventPartsHtml(ev, idx, readonly = false) {
  const parts = ev.participants ?? [];
  if (!parts.length) return "";
  return `<div class="small" style="margin-top:2px">` + parts.map((p, pidx) => {
    const label = p.role ? `${p.role}: ${partName(p)}` : partName(p);
    const del = readonly ? "" : ` <button class="btn sm ghost pev-del" data-del-part="${idx}:${pidx}" data-role="review.memory.part-remove-btn" title="이 참여자 제외">×</button>`;
    return `<span class="chip">${esc(label)}</span>${del}`;
  }).join(" ") + `</div>`;
}

function proposalEventsHtml(events, readonly = false) {
  if (!events.length) return "";
  return `<div class="small" style="margin-top:6px"><b>사건 (${events.length})</b></div>
    <ul class="ep-scenes">${events.map((ev, idx) => `
      <li><div class="pev-head"><span>${ev.story_time ? `<span class="chip">${esc(ev.story_time)}</span> ` : ""}<span class="small">${esc(ev.summary || "")}</span>
      ${(ev.narrative_mode && ev.narrative_mode !== "narration") ? ` <span class="small muted">${esc(modeKo(ev.narrative_mode))}</span>` : ""}
      ${ev.validation === "deferred" ? ` <span class="small muted">(근거 미확인·제외)</span>` : ""}</span>
      <span>${ev.evidence_quote ? `<button class="btn sm ghost pev-del" data-jump data-role="review.memory.event-jump-btn" data-quote="${esc((ev.evidence_quote || "").slice(0, 200))}" title="원고에서 근거 보기">이동</button>` : ""}${readonly ? "" : `
      <button class="btn sm ghost pev-del" data-del-event="${idx}" data-role="review.memory.event-del-btn" title="이 사건 삭제">${TRASH_SVG}</button>`}</span></div>${eventPartsHtml(ev, idx, readonly)}</li>`
    ).join("")}</ul>`;
}

function diffHtml(items, savedItems) {
  if (!savedItems.length) return "";
  const key = (it) => `${it.entity_id || it.candidate_name}|${it.attribute_path}|${displayValue(it.new_value)}`;
  const savedKeys = new Set(savedItems.map(key));
  const curKeys = new Set(items.map(key));
  const added = items.filter((it) => !savedKeys.has(key(it)));
  const removed = savedItems.filter((it) => !curKeys.has(key(it)));
  if (!added.length && !removed.length) {
    return `<div class="small muted">저장본과 임시안이 같습니다.</div>`;
  }
  return `<div class="small" style="margin-top:6px"><b>저장본 대비 차이</b></div>
    <ul class="ep-scenes">` +
    added.map((it) => `<li><span class="small">+ ${esc(it.entity_id || it.candidate_name || "")} ${esc(it.attribute_path || "")} ${esc(displayValue(it.new_value).slice(0, 60))}</span></li>`).join("") +
    removed.map((it) => `<li><span class="small muted">− ${esc(it.entity_id || it.candidate_name || "")} ${esc(it.attribute_path || "")} ${esc(displayValue(it.new_value).slice(0, 60))}</span></li>`).join("") +
    `</ul>`;
}

function groupItemsByEntity(items) {
  const groups = new Map();
  for (const it of items) {
    const key = it.entity_id || `new:${it.candidate_name || ""}`;
    if (!groups.has(key)) {
      groups.set(key, { entity_id: it.entity_id || null,
        candidate_name: it.candidate_name || "", items: [] });
    }
    groups.get(key).items.push(it);
  }
  return [...groups.values()];
}

function entityGroupLabel(g) {
  if (g.candidate_name) return g.candidate_name;
  const opt = g.entity_id
    ? entityOptions.find((e) => e.entity_id === g.entity_id) : null;
  return opt?.canonical_name || g.entity_id || "(미지정)";
}

function groupedItemsHtml(items, readonly = false) {
  return groupItemsByEntity(items).map((g) => {
    const idChip = g.entity_id
      ? `<span class="chip mono">${esc(g.entity_id)}</span>`
      : `<span class="chip">신규</span>`;
    const histBtn = g.entity_id
      ? `<button class="btn sm ghost" data-hist="${esc(g.entity_id)}" data-role="review.memory.history-btn">기존 이력 보기</button>`
      : "";
    return `<div style="margin-top:8px">
      <div class="pev-head"><span><b>${esc(entityGroupLabel(g))}</b> ${idChip} <span class="small muted">이번 회차 ${g.items.length}건</span></span>
      <span>${histBtn}</span></div>
      <ul class="ep-scenes">${proposalItemsHtml(g.items, readonly)}</ul>
      ${g.entity_id ? `<div data-hist-box="${esc(g.entity_id)}"></div>` : ""}
    </div>`;
  }).join("");
}

function basisBadgeHtml() {
  const pset = pending?.proposal_set;
  const saved = pending?.saved_set;
  const memVer = pending?.memory_version ?? "?";
  if (saved) {
    return `<span class="chip">v${esc(saved.version ?? "?")} 저장됨 · 기억 v${esc(memVer)}에 반영됨</span>`;
  }
  return "";
}

function renderPending() {
  const box = $("memory-pending");
  const pset = pending?.proposal_set;
  const memVer = pending?.memory_version ?? "?";
  let html = basisBadgeHtml();
  const latest = pending?.latest_run;
  if (latest && latest.status === "failed") {
    html += `<div class="notice err small" style="margin-top:6px">최근 추출 실패: ${esc((latest.error || "").slice(0, 200))} — 기존 임시안을 유지합니다.</div>`;
  }
  if (!pset) {
    const savedSet = pending?.saved_set;
    const savedItems = pending?.saved_items ?? [];
    const savedEvents = pending?.saved_events ?? [];
    if (savedSet && (savedItems.length || savedEvents.length)) {
      html += `<div class="small muted" style="margin-top:6px">저장된 설정 (읽기 전용 · 다시 추출하려면 설정 추출 실행을 누르세요)</div>` +
        groupedItemsHtml(savedItems, true) +
        proposalEventsHtml(savedEvents, true);
    } else {
      html += `<div class="small muted" style="margin-top:6px">임시 변경안이 없습니다. 설정 추출 실행을 눌러주세요.</div>`;
    }
  } else {
    const badge = pending.fresh
      ? `<span class="chip">원고 v${esc(pset.version ?? "?")} · 기준 기억 v${esc(pset.base_memory_version ?? "?")} · 저장 대기</span>`
      : `<span class="chip warn">낡음 · 원고가 바뀌어 재추출 필요</span>`;
    const items = pending.items ?? [];
    const events = pending.events ?? [];
    const savedItems = pending.saved_items ?? [];
    html += `<div style="margin-top:6px">${badge} <span class="small muted">현재 기억 v${esc(memVer)}</span></div>
      ${groupedItemsHtml(items)}
      ${proposalEventsHtml(events)}
      ${diffHtml(items, savedItems)}`;
    if (!items.length && !events.length) {
      html += `<div class="small muted">성공한 빈 추출입니다. 저장하면 빈 기억으로 확정됩니다.</div>`;
    } else if (!items.length && events.length) {
      html += `<div class="small muted">인물·장소 항목이 비어 있습니다. 아래 수정 요청으로 보완하거나 다시 추출하세요.</div>`;
    }
  }
  box.innerHTML = html;
  box.querySelectorAll("[data-del-item]").forEach((btn) => {
    btn.addEventListener("click", () => run(async () => {
      await api.deleteEntityItem(Number(btn.dataset.delItem));
      pending = await api.entityProposals(currentEp(), currentWorkKey());
      renderPending();
      renderAliases();
      renderNotice();
    }));
  });
  box.querySelectorAll("[data-del-event]").forEach((btn) => {
    btn.addEventListener("click", () => run(async () => {
      await api.deleteEntityEvent(currentEp(), Number(btn.dataset.delEvent), currentWorkKey());
      pending = await api.entityProposals(currentEp(), currentWorkKey());
      renderPending();
      renderNotice();
    }));
  });
  box.querySelectorAll("[data-del-part]").forEach((btn) => {
    btn.addEventListener("click", () => run(async () => {
      const [evIdx, pIdx] = btn.dataset.delPart.split(":").map(Number);
      await api.deleteEntityParticipant(currentEp(), evIdx, pIdx, currentWorkKey());
      pending = await api.entityProposals(currentEp(), currentWorkKey());
      renderPending();
      renderNotice();
    }));
  });
  box.querySelectorAll("[data-jump]").forEach((btn) => {
    btn.addEventListener("click", () => {
      jumpToEvidence(btn.dataset.quote, pending?.proposal_set?.manuscript_revision_id ?? pending?.saved_set?.manuscript_revision_id ?? null);
    });
  });
  box.querySelectorAll("[data-hist]").forEach((btn) => {
    btn.addEventListener("click", () => run(async () => {
      const id = btn.dataset.hist;
      const histBox = box.querySelector(`[data-hist-box="${id}"]`);
      if (!histBox) return;
      if (histBox.dataset.open === "1") {
        histBox.dataset.open = "";
        histBox.innerHTML = "";
        btn.textContent = "기존 이력 보기";
        return;
      }
      btn.textContent = "불러오는 중…";
      try {
        const hist = await api.entityHistory(id);
        histBox.dataset.open = "1";
        histBox.innerHTML = `<div class="timeline" style="margin-top:6px">${historyTimelineHtml(hist.history ?? [])}</div>`;
        btn.textContent = "기존 이력 닫기";
      } catch (e) {
        btn.textContent = "기존 이력 보기";
        throw e;
      }
    }));
  });
  if (!inReview()) {
    for (const b of box.querySelectorAll("button")) {
      if (b.hasAttribute("data-jump") || b.hasAttribute("data-hist")) continue;
      b.disabled = true;
    }
  }
  updateMemoryButtons();
}

function aliasDisplay() {
  // 후보 표시 규칙: 이 이름의 사실(항목)이 있고 저장 시 귀결이 모호할
  // 때만 묻는다. 사실이 없거나 귀결이 확실하면 숨긴다.
  //   숨김: 사실 없음 / 기존 0건+전부 새 설정 / 기존 1건+전부 연결형
  //   표시: 연결형인데 기존 없음(별명 의심) / 새 설정인데 기존 있음(분리 의심)
  //         / 동명이인(2건 이상) / 혼합
  const items = pending?.proposal_set
    ? (pending.items ?? [])
    : (pending?.saved_items ?? []);
  const committable = items.filter((it) =>
    ["pending", "ok", "change"].includes(it.validation));
  const counts = {};
  for (const e of entityOptions) {
    const nm = e.canonical_name || "";
    counts[nm] = (counts[nm] || 0) + 1;
  }
  const hidden = new Set();
  const autoNames = [];
  for (const a of aliases) {
    const facts = committable.filter((it) =>
      (it.candidate_name || "") === a.alias);
    const pre = counts[a.alias] || 0;
    const hide = !facts.length ||
      (pre === 0 && facts.every((it) => it.explicit_new)) ||
      (pre === 1 && facts.every((it) => !it.explicit_new));
    if (hide) {
      hidden.add(a.alias_id);
      if (facts.length && !autoNames.includes(a.alias)) {
        autoNames.push(a.alias);
      }
    }
  }
  return { hidden, autoNames };
}

function renderAliases() {
  const box = $("alias-list");
  const { hidden, autoNames } = aliasDisplay();
  const vis = aliases.filter((a) => !hidden.has(a.alias_id));
  const autoNote = autoNames.length
    ? `<div class="small muted" style="margin-bottom:6px">${autoNames.map(esc).join("·")} — 저장 시 자동 처리됩니다.</div>`
    : "";
  if (!vis.length) {
    box.innerHTML = autoNote + `<span class="muted small">대기 중인 후보가 없습니다.</span>`;
    return;
  }
  box.innerHTML = autoNote + `<ul class="ep-scenes">${vis.map((a) => `
    <li><div class="pev-head"><span><b>${esc(a.alias)}</b>
      <span class="small muted">${esc(a.basis || "")}</span></span></div>
      ${a.quote ? `<span class="small muted">인용: ${esc(a.quote.slice(0, 100))}</span><br>` : ""}
      <span class="small">어느 설정에 연결할까요? </span>
      <select data-alias-link="${a.alias_id}" data-role="review.memory.alias-link-select" style="max-width:60%">
        <option value="">연결할 설정 선택</option>
        ${entityOptions.map((e) => `<option value="${esc(e.entity_id)}">${esc(e.canonical_name)} (${esc(e.entity_id)})</option>`).join("")}
      </select>
      <div class="btn-row">
        <button class="btn sm" data-alias-ok="${a.alias_id}" data-role="review.memory.alias-ok-btn"${entityOptions.length ? "" : " disabled title=\"연결할 설정이 없습니다\""}>연결</button>
      </div></li>`
  ).join("")}</ul>`;
  box.querySelectorAll("[data-alias-ok]").forEach((btn) => {
    btn.addEventListener("click", () => run(async () => {
      const id = Number(btn.dataset.aliasOk);
      const sel = box.querySelector(`[data-alias-link="${id}"]`);
      const entityId = sel?.value || null;
      if (!entityId) {
        showError("review-error", "연결할 설정을 선택하세요.");
        return;
      }
      await api.resolveAlias(id, { entity_id: entityId, basis: "작가 연결 확정" });
      await reloadAliases();
      entityOptions = (await api.entities("")).entities ?? [];
      renderAliases();
    }));
  });
  if (!inReview()) {
    for (const b of box.querySelectorAll("button")) b.disabled = true;
  }
}

async function reloadAliases() {
  const wk = currentWorkKey();
  if (wk) {
    try {
      aliases = (await api.aliases(wk)).candidates ?? [];
    } catch {
      aliases = [];
    }
  } else {
    aliases = [];
  }
}

function renderAll() {
  renderNotice();
  renderMessages();
  renderIssues();
  renderPending();
  renderAliases();
}

async function refreshIssues() {
  issues = (await api.consistencyIssues(currentEp(), null, currentWorkKey())).issues ?? [];
  renderIssues();
  renderNotice();
}

async function refreshAnalysis() {
  try {
    analysis = (await api.latestAnalysis(currentEp(), currentWorkKey())).analysis ?? null;
  } catch {
    analysis = null;
  }
  renderIssues();
  renderNotice();
}

export function initReview() {
  initColResizer(REVIEW_RESIZER);
  document.addEventListener("enter-screen", (e) => {
    if (e.detail?.section !== "s3") return;
    restoreColResizer(REVIEW_RESIZER);
    if (currentWorkKey()) run(() => load(currentEp()));
  });
  $("tab-extract").addEventListener("click", () => selectReviewTab("extract"));
  $("tab-consistency").addEventListener("click", () => selectReviewTab("consistency"));
  $("btn-save-result-ok").addEventListener("click", () => {
    $("save-result-backdrop").style.display = "none";
  });
  $("btn-review-load").addEventListener("click", () => run(() => load(currentEp())));
  document.addEventListener("review-episode", (e) => {
    const ep = e.detail?.ep;
    if (!ep) return;
    $("review-ep").value = ep;
    run(() => load(Number(ep)));
  });
  $("btn-back-to-writing").addEventListener("click", () =>
    run(async () => {
      const reason = window.prompt("집필중으로 돌아가는 이유를 입력하세요 (집필 화면에 표시됩니다).", "") ?? "";
      await api.backToWriting(currentEp(), reason, currentWorkKey());
      await load(currentEp());
    })
  );
  $("btn-request-publish").addEventListener("click", () =>
    run(async () => {
      const open = issues.filter((i) => i.status === "open").length;
      if (open > 0) {
        showError("review-error", `열린 이슈 ${open}건이 남아 있어 발간요청을 할 수 없습니다.`);
        return;
      }
      const r = await api.requestPublish(currentEp(), currentWorkKey());
      showToast(`발간요청 완료: EP ${r.episode_no}`);
      await load(currentEp());
    })
  );
  $("btn-consistency-analyze").addEventListener("click", () =>
    run(async () => {
      const btn = $("btn-consistency-analyze");
      btn.textContent = "분석 중…";
      try {
        const out = await api.consistencyAnalyze(currentEp(), currentWorkKey());
        messages = (await api.consistencyMessages(currentEp(), currentWorkKey())).messages ?? [];
        await refreshIssues();
        await refreshAnalysis();
        renderMessages();
        if (out.partial) {
          showError("review-error", `AI 분석 실패로 부분 완료되었습니다: ${out.error ?? ""} (확정 검사 결과는 저장됨)`);
        } else if (out.skipped) {
          showError("review-error", "비교할 이전 기억이 없어 분석을 건너뛰었습니다.");
        }
      } finally {
        btn.textContent = "전체 분석 실행";
      }
    })
  );
  const sendChat = () => run(async () => {
    const input = $("consistency-input");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    await api.consistencyChat(currentEp(), text, currentWorkKey());
    messages = (await api.consistencyMessages(currentEp(), currentWorkKey())).messages ?? [];
    renderMessages();
    await refreshIssues();
  });
  $("btn-consistency-send").addEventListener("click", sendChat);
  $("consistency-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendChat();
    }
  });
  $("btn-entity-extract").addEventListener("click", () => run(doExtract));
  $("btn-entity-reset").addEventListener("click", () => {
    if (!hasMemoryData()) return;
    openResetConfirm();
  });
  $("btn-reset-cancel").addEventListener("click", () => {
    $("reset-confirm-backdrop").style.display = "none";
  });
  $("btn-reset-confirm").addEventListener("click", () => {
    $("reset-confirm-backdrop").style.display = "none";
    run(doReset);
  });
  $("btn-consistency-reset").addEventListener("click", () => {
    if (!hasReviewData()) return;
    openReviewResetConfirm();
  });
  $("btn-consistency-reset-cancel").addEventListener("click", () => {
    $("consistency-reset-confirm-backdrop").style.display = "none";
  });
  $("btn-consistency-reset-confirm").addEventListener("click", () => {
    $("consistency-reset-confirm-backdrop").style.display = "none";
    run(doReviewReset);
  });
  $("btn-entity-save").addEventListener("click", () =>
    run(async () => {
      const out = await api.saveEntities(currentEp(), currentWorkKey());
      pending = await api.entityProposals(currentEp(), currentWorkKey());
      renderPending();
      renderNotice();
      entityOptions = (await api.entities("")).entities ?? [];
      await reloadAliases();
      renderAliases();
      const linked = out.commit?.aliases_linked ?? 0;
      showSaveResultDialog(out.already
        ? "이미 저장된 기억입니다."
        : `설정을 저장했습니다: ${out.commit.applied}건 반영`
          + (linked ? ` · 별명 ${linked}건 자동 정리` : "")
          + ` (기억 v${out.commit.memory_version ?? "?"})`);
    })
  );
  const sendEntityChat = () => run(async () => {
    const input = $("entity-chat-input");
    const text = input.value.trim();
    if (!text) {
      showError("review-error", "수정 요청을 입력하세요.");
      return;
    }
    input.value = "";
    const btn = $("btn-entity-chat");
    btn.textContent = "수정 중…";
    try {
      await api.extractEntities(currentEp(), text, currentWorkKey());
      pending = await api.entityProposals(currentEp(), currentWorkKey());
      await reloadAliases();
      renderPending();
      renderAliases();
      renderNotice();
    } finally {
      btn.textContent = "요청";
    }
  });
  $("btn-entity-chat").addEventListener("click", sendEntityChat);
  $("entity-chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendEntityChat();
    }
  });
  run(() => load(1)).catch(() => {});
}