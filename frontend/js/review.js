import { api } from "./api.js";
import { t } from "./i18n.js";
import { $, esc, showError, showInfo, showToast, stateKo, severityKo, issueStatusKo, verdictKo, kindKo, modeKo, historyTimelineHtml } from "./util.js";
import { initColResizer, restoreColResizer } from "./resizer.js";
import { mountManuscriptPanel } from "./manuscript.js";
import { getWork } from "./work.js";
import { go, novelPath } from "./router.js";

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
let comparable = null;
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
  if (s.changes) bits.push(t("review.reset.changes", { n: s.changes }));
  if (s.events) bits.push(t("review.reset.events", { n: s.events }));
  if (s.aliases) bits.push(t("review.reset.aliases", { n: s.aliases }));
  $("reset-confirm-msg").textContent = (bits.length
    ? t("review.reset.savedMemory", { bits: bits.join("·") })
    : t("review.reset.draftOnly"))
    + (issues.length ? " " + t("review.reset.issuesNote", { n: issues.length }) : "")
    + " " + t("review.reset.irreversible");
  $("reset-confirm-backdrop").style.display = "";
}

function hasReviewData() {
  return issues.length > 0 || !!analysis;
}

function updateConsistencyResetButton() {
  const btn = $("btn-consistency-reset");
  if (!btn) return;
  btn.disabled = !hasReviewData();
}

// 비교 근거(직전화·확정 기억)가 없으면 분석 실행을 미리 막고 안내한다.
// EP 1처럼 아직 대조할 것이 없을 때 skipped 왕복을 피하기 위함이다.
function updateAnalyzeGate() {
  const btn = $("btn-consistency-analyze");
  if (!btn) return;
  const noBasis = comparable === false;
  btn.disabled = busy || !inReview() || noBasis;
  btn.title = noBasis ? t("review.analyze.noBasis") : "";
}

function openReviewResetConfirm() {
  const bits = [];
  if (issues.length) bits.push(t("review.consistencyReset.issues", { n: issues.length }));
  if (analysis) bits.push(t("review.consistencyReset.analysis"));
  $("consistency-reset-confirm-msg").textContent =
    t("review.consistencyReset.message", { bits: bits.join("·") }) + " " + t("review.reset.irreversible");
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
  $("btn-entity-extract").textContent = t("review.memory.extracting");
  try {
    await api.extractEntities(currentEp(), null, currentWorkKey());
  } finally {
    $("btn-entity-extract").textContent = t("review.memory.extractRun");
    await reloadMemoryPanel();
  }
}

function inReview() {
  return !!job && job.state === "IN_REVIEW";
}

function updateActionState() {
  const ok = !busy && inReview();
  for (const id of ["btn-back-to-writing", "btn-request-publish",
                    "btn-consistency-send",
                    "btn-entity-extract", "btn-entity-save",
                    "btn-entity-chat"]) {
    const b = $(id);
    if (b) b.disabled = !ok;
  }
  updateAnalyzeGate();
  for (const id of ["consistency-input", "entity-chat-input"]) {
    const input = $(id);
    if (input) input.disabled = !ok;
  }
  for (const b of document.querySelectorAll("#issue-list button, #memory-pending button, #alias-list button")) {
    if (b.hasAttribute("data-jump")) continue; // 근거 이동은 읽기 전용에서도 허용
    b.disabled = !ok;
  }
  updateFixSelectedButton();
}

function selectedFixIds() {
  return [...document.querySelectorAll("[data-issue-sel]:checked")]
    .map((c) => Number(c.dataset.issueSel)).filter((n) => Number.isFinite(n));
}

function updateFixSelectedButton() {
  const btn = $("btn-fix-selected");
  if (!btn) return;
  const n = selectedFixIds().length;
  const label = t("review.fix.label");
  btn.textContent = busy ? t("review.fix.fixing") : (n ? label + t("review.fix.countSuffix", { n }) : label);
  btn.disabled = busy || !inReview() || n === 0;
}

function openFixConfirm() {
  const n = selectedFixIds().length;
  $("fix-confirm-msg").textContent = t("review.fix.confirm", { n });
  $("fix-confirm-backdrop").style.display = "";
}

async function doFixSelected() {
  const ids = selectedFixIds();
  if (!ids.length) return;
  const btn = $("btn-fix-selected");
  if (btn) btn.textContent = t("review.fix.fixing");
  try {
    const out = await api.manuscriptFix(currentEp(), ids, currentWorkKey());
    showToast(t("review.fix.doneToast", { n: out.fixed_issue_ids?.length ?? ids.length }));
    const w = getWork();
    const ep = currentEp();
    go(novelPath(w.uid || w.id, "monitor"));
    setTimeout(() => document.dispatchEvent(
      new CustomEvent("writing-episode", { detail: { ep } })), 50);
  } finally {
    updateFixSelectedButton();
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
    showError("review-error", t("review.evidenceNotFound"));
  } else {
    showError("review-error", "");
  }
}

async function load(epNo) {
  const wk = currentWorkKey();
  if (!wk) {
    showError("review-error", t("review.needWork"));
    return;
  }
  if (!Number.isInteger(epNo) || epNo < 1) {
    showError("review-error", t("review.needEp"));
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
  comparable = ana.comparable ?? null;
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
      conds.push(t("review.notice.unsavedChanges"));
    } else if (!saved || saved.manuscript_revision_id !== revId) {
      conds.push(t("review.notice.needExtract"));
    }
    if (!analysis) {
      conds.push(t("review.notice.beforeAnalysis"));
    } else if (analysis.status === "partial") {
      conds.push(t("review.notice.partial"));
    } else if (analysis.status === "failed") {
      conds.push(t("review.notice.failed"));
    } else if (analysis.status === "skipped") {
      conds.push(t("review.notice.skipped"));
    } else if (analysis.manuscript_revision_id !== revId) {
      conds.push(t("review.notice.stale"));
    }
    if (open > 0) conds.push(t("review.notice.openIssues", { n: open }));
    preconditions = conds.length
      ? esc(t("review.notice.precheck", { conds: conds.join(" · ") }))
      : t("review.notice.publishable");
    box.innerHTML = `<div class="notice ok"><b>EP ${esc(job.episode_no)} · ${stateKo(job.state)}</b> — ${esc(t("review.notice.inReviewBody", { open }))}${preconditions}</div>`;
  } else if (job.state === "PUBLISHING") {
    box.innerHTML = `<div class="notice"><b>EP ${esc(job.episode_no)} · ${stateKo(job.state)}</b> — ${t("review.notice.publishedBody")}</div>`;
  } else if (job.needs_attention) {
    box.innerHTML = `<div class="notice err"><b>EP ${esc(job.episode_no)} · ${stateKo(job.state)}</b> — ${esc(job.last_error || t("review.notice.attentionFallback"))}</div>`;
  } else {
    box.innerHTML = `<div class="notice warn"><b>EP ${esc(job.episode_no)} · ${stateKo(job.state)}</b> — ${t("review.notice.writingBody")}</div>`;
  }
  $("review-state").innerHTML = t("review.notice.stateLabel", { state: esc(stateKo(job.state)) });
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
    box.innerHTML = `<span class="muted small">${t("review.analysis.beforeAnalysis")}`
      + (comparable === false ? ` · ${t("review.analyze.noBasis")}` : "") + `</span>`;
    return;
  }
  const counts = (() => { try { return JSON.parse(analysis.counts_json || "{}"); } catch { return {}; } })();
  const cov = (() => { try { return JSON.parse(analysis.coverage_json || "{}"); } catch { return {}; } })();
  const statusKo = {
    completed: t("review.analysis.status.completed"),
    partial: t("review.analysis.status.partial"),
    failed: t("review.analysis.status.failed"),
    skipped: t("review.analysis.status.skipped"),
  }[analysis.status] ?? analysis.status;
  const CHECK_KO = {
    check_count_limits: t("review.analysis.check.countLimits"),
    check_death: t("review.analysis.check.death"),
    check_exclusive_location: t("review.analysis.check.exclusiveLocation"),
    check_state_clash: t("review.analysis.check.stateClash"),
  };
  const ruleChecks = cov.rule_checks ?? [];
  const failedChecks = ruleChecks.filter((r) => r.status !== "completed");
  const ruleSummary = !ruleChecks.length ? ""
    : failedChecks.length ? " · " + t("review.analysis.ruleFailed", {
        total: ruleChecks.length,
        failed: failedChecks.length,
        names: failedChecks.map((r) => esc(CHECK_KO[r.check] ?? r.check)).join(", "),
      })
    : " · " + t("review.analysis.ruleOk", { total: ruleChecks.length });
  const prev = cov.prev_manuscript ? t("review.analysis.prevSource", {
    ep: esc(cov.prev_manuscript.episode_no),
    edition: cov.prev_manuscript.approved
      ? t("review.analysis.editionApproved")
      : t("review.analysis.editionLatest"),
  }) + " " : "";
  box.innerHTML = `<span class="chip">${esc(statusKo)}</span> ` +
    `<span class="small muted">${prev ? t("review.analysis.scopeWithPrev", { prev }) : t("review.analysis.scopeBase")}` +
    (cov.entity_groups ? " · " + t("review.analysis.groups", { n: esc(cov.entity_groups) }) : "") +
    " · " + t("review.analysis.counts", {
      rule: esc(counts.rule ?? 0), ai: esc(counts.ai ?? 0), saved: esc(counts.saved ?? issues.length),
    }) +
    (counts.accepted_explained ? " " + t("review.analysis.accepted", { n: esc(counts.accepted_explained) }) : "") +
    " · " + t("review.analysis.dropped", { n: esc(counts.dropped ?? 0) }) +
    ruleSummary +
    (cov.reason ? ` · ${esc(cov.reason)}` : "") +
    (analysis.error ? ` · ${esc(analysis.error.slice(0, 120))}` : "") + `</span>`;
}

function renderIssues() {
  const box = $("issue-list");
  const open = issues.filter((i) => i.status === "open").length;
  $("issue-count").textContent = t("review.issues.count", { open, total: issues.length });
  updateConsistencyResetButton();
  updateAnalyzeGate();
  renderAnalysis();
  if (!issues.length) {
    box.innerHTML = `<span class="muted small">${t("review.issues.empty")}</span>`;
    return;
  }
  box.innerHTML = issues.map((it) => {
    let refs = [];
    try { refs = JSON.parse(it.fact_refs_json || "[]"); } catch { refs = []; }
    return `
    <div class="issue-card" data-issue="${it.issue_id}" data-status="${esc(it.status)}">
      <div class="issue-head">
        ${it.status === "open" ? `<input type="checkbox" data-issue-sel="${it.issue_id}" title="${t("review.issues.selectTitle")}"${inReview() ? "" : " disabled"}>` : ""}
        <span class="sev-${esc(it.severity)}">${esc(severityKo(it.severity))}</span>
        <span class="title">${esc(it.title || t("review.issues.untitled"))}</span>
        <span class="issue-status">${esc(issueStatusKo(it.status))}</span>
      </div>
      <div class="small muted">${t("review.issues.verdict")} <b>${esc(verdictKo(it.verdict))}</b>${refs.length ? " · " + t("review.issues.relatedFacts", { refs: refs.map((r) => esc(r)).join(", #") }) : ""}</div>
      ${it.detail ? `<div class="issue-detail">${esc(it.detail)}</div>` : ""}
      ${it.manuscript_quote ? `<div class="quote-box"><span class="src">${t("review.issues.msQuote")}</span>${esc(it.manuscript_quote)} <button class="btn sm ghost" data-jump data-role="review.issues.jump-btn" data-quote="${esc(it.manuscript_quote.slice(0, 200))}" data-rev="${it.manuscript_revision_id ?? ""}">${t("review.issues.jumpBtn")}</button></div>` : ""}
      ${it.evidence_quote ? `<div class="quote-box"><span class="src">${t("review.issues.evidence")}${it.evidence_episode ? " · " + t("review.issues.evidenceEp", { ep: esc(it.evidence_episode) }) : ""}${it.entity_id ? ` · ${esc(it.entity_id)}` : ""}</span>${esc(it.evidence_quote)}</div>` : ""}
      <div class="btn-row">
        ${it.status === "open"
          ? `<button class="btn sm" data-fixed>${t("review.issues.fixDone")}</button>
             <button class="btn sm ghost" data-accepted>${t("review.issues.accept")}</button>
             <button class="btn sm ghost" data-dismissed>${t("review.issues.dismiss")}</button>`
          : `<button class="btn sm ghost" data-reopen>${t("review.issues.reopen")}</button>`}
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
  box.querySelectorAll("[data-issue-sel]").forEach((c) => {
    c.addEventListener("change", updateFixSelectedButton);
  });
  updateFixSelectedButton();
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
  if (!items.length) return `<li><span class="muted small">${t("review.memory.noChanges")}</span></li>`;
  return items.map((it) => `
    <li><div class="pev-head"><span>
      <span class="chip">${esc(kindKo(it.kind))}</span>
      <b>${esc(it.entity_id || it.candidate_name || t("review.memory.unspecified"))}</b>
      ${it.attribute_path ? `<span class="small muted">${esc(it.attribute_path)}</span>` : ""}
      ${it.story_time ? `<span class="chip">${esc(it.story_time)}</span>` : ""}
      ${(it.narrative_mode && it.narrative_mode !== "narration") ? `<span class="small muted">${esc(modeKo(it.narrative_mode))}</span>` : ""}
      ${it.validation === "deferred" ? `<span class="small muted">${t("review.memory.deferred")}</span>` : ""}
      ${it.validation === "stale" ? `<span class="small muted">${t("review.memory.stale")}</span>` : ""}</span>
      <span><button class="btn sm ghost pev-del" data-jump data-role="review.memory.item-jump-btn" data-quote="${esc((it.evidence_quote || "").slice(0, 200))}" title="${t("review.memory.viewEvidenceTitle")}">${t("review.memory.jumpBtn")}</button>${readonly ? "" : `
      <button class="btn sm ghost pev-del" data-del-item="${it.item_id}" data-role="review.memory.item-del-btn" title="${t("review.memory.delItemTitle")}">${TRASH_SVG}</button>`}</span></div>
      <span class="small">${esc(displayValue(it.new_value).slice(0, 120))}</span>
      <br><span class="small muted">${t("review.memory.quoteLabel")} ${esc((it.evidence_quote || "").slice(0, 120))}</span></li>`
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
    const del = readonly ? "" : ` <button class="btn sm ghost pev-del" data-del-part="${idx}:${pidx}" data-role="review.memory.part-remove-btn" title="${t("review.memory.delPartTitle")}">×</button>`;
    return `<span class="chip">${esc(label)}</span>${del}`;
  }).join(" ") + `</div>`;
}

function proposalEventsHtml(events, readonly = false) {
  if (!events.length) return "";
  return `<div class="small" style="margin-top:6px"><b>${t("review.memory.eventsTitle", { n: events.length })}</b></div>
    <ul class="ep-scenes">${events.map((ev, idx) => `
      <li><div class="pev-head"><span>${ev.story_time ? `<span class="chip">${esc(ev.story_time)}</span> ` : ""}<span class="small">${esc(ev.summary || "")}</span>
      ${(ev.narrative_mode && ev.narrative_mode !== "narration") ? ` <span class="small muted">${esc(modeKo(ev.narrative_mode))}</span>` : ""}
      ${ev.validation === "deferred" ? ` <span class="small muted">${t("review.memory.deferred")}</span>` : ""}</span>
      <span>${ev.evidence_quote ? `<button class="btn sm ghost pev-del" data-jump data-role="review.memory.event-jump-btn" data-quote="${esc((ev.evidence_quote || "").slice(0, 200))}" title="${t("review.memory.viewEvidenceTitle")}">${t("review.memory.jumpBtn")}</button>` : ""}${readonly ? "" : `
      <button class="btn sm ghost pev-del" data-del-event="${idx}" data-role="review.memory.event-del-btn" title="${t("review.memory.delEventTitle")}">${TRASH_SVG}</button>`}</span></div>${eventPartsHtml(ev, idx, readonly)}</li>`
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
    return `<div class="small muted">${t("review.memory.sameAsSaved")}</div>`;
  }
  return `<div class="small" style="margin-top:6px"><b>${t("review.memory.diffTitle")}</b></div>
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
  return opt?.canonical_name || g.entity_id || t("review.memory.unspecified");
}

function groupedItemsHtml(items, readonly = false) {
  return groupItemsByEntity(items).map((g) => {
    const idChip = g.entity_id
      ? `<span class="chip mono">${esc(g.entity_id)}</span>`
      : `<span class="chip">${t("review.memory.newChip")}</span>`;
    const histBtn = g.entity_id
      ? `<button class="btn sm ghost" data-hist="${esc(g.entity_id)}" data-role="review.memory.history-btn">${t("review.memory.showHistory")}</button>`
      : "";
    return `<div style="margin-top:8px">
      <div class="pev-head"><span><b>${esc(entityGroupLabel(g))}</b> ${idChip} <span class="small muted">${t("review.memory.thisEpCount", { n: g.items.length })}</span></span>
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
    return `<span class="chip">${t("review.memory.savedBadge", { version: esc(saved.version ?? "?"), mem: esc(memVer) })}</span>`;
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
    html += `<div class="notice err small" style="margin-top:6px">${t("review.memory.extractFailed", { error: esc((latest.error || "").slice(0, 200)) })}</div>`;
  }
  if (!pset) {
    const savedSet = pending?.saved_set;
    const savedItems = pending?.saved_items ?? [];
    const savedEvents = pending?.saved_events ?? [];
    if (savedSet && (savedItems.length || savedEvents.length)) {
      html += `<div class="small muted" style="margin-top:6px">${t("review.memory.savedReadonly")}</div>` +
        groupedItemsHtml(savedItems, true) +
        proposalEventsHtml(savedEvents, true);
    } else {
      html += `<div class="small muted" style="margin-top:6px">${t("review.memory.noDraft")}</div>`;
    }
  } else {
    const badge = pending.fresh
      ? `<span class="chip">${t("review.memory.pendingBadge", { version: esc(pset.version ?? "?"), base: esc(pset.base_memory_version ?? "?") })}</span>`
      : `<span class="chip warn">${t("review.memory.staleBadge")}</span>`;
    const items = pending.items ?? [];
    const events = pending.events ?? [];
    const savedItems = pending.saved_items ?? [];
    html += `<div style="margin-top:6px">${badge} <span class="small muted">${t("review.memory.currentMem", { mem: esc(memVer) })}</span></div>
      ${groupedItemsHtml(items)}
      ${proposalEventsHtml(events)}
      ${diffHtml(items, savedItems)}`;
    if (!items.length && !events.length) {
      html += `<div class="small muted">${t("review.memory.emptySuccess")}</div>`;
    } else if (!items.length && events.length) {
      html += `<div class="small muted">${t("review.memory.emptyItems")}</div>`;
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
        btn.textContent = t("review.memory.showHistory");
        return;
      }
      btn.textContent = t("review.memory.loading");
      try {
        const hist = await api.entityHistory(id);
        histBox.dataset.open = "1";
        histBox.innerHTML = `<div class="timeline" style="margin-top:6px">${historyTimelineHtml(hist.history ?? [])}</div>`;
        btn.textContent = t("review.memory.hideHistory");
      } catch (e) {
        btn.textContent = t("review.memory.showHistory");
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
    ? `<div class="small muted" style="margin-bottom:6px">${t("review.memory.autoNote", { names: autoNames.map(esc).join("·") })}</div>`
    : "";
  if (!vis.length) {
    box.innerHTML = autoNote + `<span class="muted small">${t("review.memory.noAliases")}</span>`;
    return;
  }
  box.innerHTML = autoNote + `<ul class="ep-scenes">${vis.map((a) => `
    <li><div class="pev-head"><span><b>${esc(a.alias)}</b>
      <span class="small muted">${esc(a.basis || "")}</span></span></div>
      ${a.quote ? `<span class="small muted">${t("review.memory.quoteLabel")} ${esc(a.quote.slice(0, 100))}</span><br>` : ""}
      <span class="small">${t("review.memory.whichEntity")} </span>
      <select data-alias-link="${a.alias_id}" data-role="review.memory.alias-link-select" style="max-width:60%">
        <option value="">${t("review.memory.selectEntity")}</option>
        ${entityOptions.map((e) => `<option value="${esc(e.entity_id)}">${esc(e.canonical_name)} (${esc(e.entity_id)})</option>`).join("")}
      </select>
      <div class="btn-row">
        <button class="btn sm" data-alias-ok="${a.alias_id}" data-role="review.memory.alias-ok-btn"${entityOptions.length ? "" : ` disabled title="${t("review.memory.noEntityTitle")}"`}>${t("review.memory.linkBtn")}</button>
      </div></li>`
  ).join("")}</ul>`;
  box.querySelectorAll("[data-alias-ok]").forEach((btn) => {
    btn.addEventListener("click", () => run(async () => {
      const id = Number(btn.dataset.aliasOk);
      const sel = box.querySelector(`[data-alias-link="${id}"]`);
      const entityId = sel?.value || null;
      if (!entityId) {
        showError("review-error", t("review.memory.needEntity"));
        return;
      }
      // 빈 basis: 서버가 작품 언어의 기본 사유(작가 확정/author confirmed)를 넣는다.
      await api.resolveAlias(id, { entity_id: entityId, basis: "" });
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
    const ana = await api.latestAnalysis(currentEp(), currentWorkKey());
    analysis = ana.analysis ?? null;
    comparable = ana.comparable ?? null;
  } catch {
    analysis = null;
    comparable = null;
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
      const ep = currentEp();
      await api.backToWriting(ep, "", currentWorkKey());
      const w = getWork();
      go(novelPath(w.uid || w.id, "monitor"));
      setTimeout(() => document.dispatchEvent(
        new CustomEvent("writing-episode", { detail: { ep } })), 50);
    })
  );
  $("btn-request-publish").addEventListener("click", () =>
    run(async () => {
      const open = issues.filter((i) => i.status === "open").length;
      if (open > 0) {
        showError("review-error", t("review.publish.blocked", { n: open }));
        return;
      }
      const r = await api.requestPublish(currentEp(), currentWorkKey());
      showToast(t("review.publish.doneToast", { ep: r.episode_no }));
      await load(currentEp());
    })
  );
  $("btn-consistency-analyze").addEventListener("click", () =>
    run(async () => {
      const btn = $("btn-consistency-analyze");
      btn.textContent = t("review.analyze.running");
      try {
        const out = await api.consistencyAnalyze(currentEp(), currentWorkKey());
        messages = (await api.consistencyMessages(currentEp(), currentWorkKey())).messages ?? [];
        await refreshIssues();
        await refreshAnalysis();
        renderMessages();
        if (out.partial) {
          showError("review-error", t("review.analyze.partial", { error: out.error ?? "" }));
        } else if (out.skipped) {
          showInfo("review-error", t("review.analyze.skipped"));
        }
      } finally {
        btn.textContent = t("review.analyze.runFull");
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
  $("btn-fix-selected").addEventListener("click", () => {
    if (!selectedFixIds().length) return;
    openFixConfirm();
  });
  $("btn-fix-cancel").addEventListener("click", () => {
    $("fix-confirm-backdrop").style.display = "none";
  });
  $("btn-fix-confirm").addEventListener("click", () => {
    $("fix-confirm-backdrop").style.display = "none";
    run(doFixSelected);
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
        ? t("review.save.already")
        : t("review.save.done", { n: out.commit.applied })
          + (linked ? " · " + t("review.save.aliases", { n: linked }) : "")
          + " " + t("review.save.memVersion", { mem: out.commit.memory_version ?? "?" }));
    })
  );
  const sendEntityChat = () => run(async () => {
    const input = $("entity-chat-input");
    const text = input.value.trim();
    if (!text) {
      showError("review-error", t("review.chat.needText"));
      return;
    }
    input.value = "";
    const btn = $("btn-entity-chat");
    btn.textContent = t("review.chat.revising");
    try {
      await api.extractEntities(currentEp(), text, currentWorkKey());
      pending = await api.entityProposals(currentEp(), currentWorkKey());
      await reloadAliases();
      renderPending();
      renderAliases();
      renderNotice();
    } finally {
      btn.textContent = t("review.chat.send");
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