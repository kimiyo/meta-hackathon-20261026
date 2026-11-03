import { api } from "./api.js";
import { $, esc, showError, historyTimelineHtml } from "./util.js";
import { initColResizer, restoreColResizer } from "./resizer.js";
import { getWork } from "./work.js";

const ENTITIES_RESIZER = { resizerId: "entities-resizer", varName: "--entities-left-w",
  storageKey: "entities-left-w", side: "left", min: 260, reserve: 320 };

// 화면 5 · 설정 (구 React판에서 이식)
let list = [];
let sel = null;
let asOfEp = null;

function currentWorkId() {
  const w = getWork();
  return w?.id ?? null;
}

function clearDetail() {
  sel = null;
  renderList();
  $("ent-detail").innerHTML = `<h3>설정 상태</h3><span class="muted">설정을 선택하세요.</span>`;
  $("ent-history-card").style.display = "none";
  $("ent-history").innerHTML = "";
}

async function search(text) {
  showError("entities-error", "");
  try {
    const r = await api.entities(null, currentWorkId(), asOfEp, null, text || null);
    list = r.entities ?? [];
    renderList();
    if (sel && list.some((e) => e.entity_id === sel)) open(sel);
    else if (sel) clearDetail();
  } catch (e) {
    showError("entities-error", String(e));
  }
}

function renderList() {
  const box = $("ent-list");
  if (!list.length) {
    box.innerHTML = `<span class="muted small">설정 항목이 없습니다.</span>`;
    return;
  }
  box.innerHTML = list
    .map(
      (e) => `<div class="work-item${e.entity_id === sel ? " selected" : ""}" data-ent="${esc(e.entity_id)}">
        <div class="t">${esc(e.canonical_name)} <span class="chip mono">${esc(e.entity_id)}</span></div>
        <div class="meta">${esc(e.type)} · 첫 등장 EP ${esc(e.first_episode)} · ${esc(e.lifecycle_status)}</div>
        ${e.match_snippet ? `<div class="small muted">${esc(e.match_snippet)}</div>` : ""}
      </div>`
    )
    .join("");
  box.querySelectorAll("[data-ent]").forEach((el) => {
    el.addEventListener("click", () => open(el.dataset.ent));
  });
}

function renderState(entId, state) {
  const box = $("ent-detail");
  const attrs = state?.attributes ?? {};
  const asOf = state?.as_of_episode ?? "?";
  const rows = Object.entries(attrs)
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd class="mono">${esc(typeof v === "string" ? v : JSON.stringify(v))}</dd>`)
    .join("");
  box.innerHTML = `
    <h3>${esc(entId)}
      <span class="hint">${esc(asOf)}화 기준</span>
    </h3>
    <dl class="kv">${rows || `<dt>속성</dt><dd class="mono">없음</dd>`}</dl>`;
}

function renderHistory(history) {
  const card = $("ent-history-card");
  const box = $("ent-history");
  if (!history.length) {
    card.style.display = "none";
    box.innerHTML = "";
    return;
  }
  card.style.display = "";
  box.innerHTML = historyTimelineHtml(history);
}

function renderRelations(relations, selfId, asOf) {
  const card = $("ent-history-card");
  const vis = (asOf === null || asOf === undefined)
    ? relations
    : relations.filter((r) => r.valid_from_episode <= asOf &&
        (r.valid_to_episode === null || r.valid_to_episode === undefined || asOf < r.valid_to_episode));
  if (!vis.length) return;
  const box = $("ent-history");
  const relHtml = vis.map((r) => {
    const outgoing = r.subject_id === selfId;
    const other = outgoing ? r.object_id : r.subject_id;
    const span = `EP ${esc(r.valid_from_episode)}${r.valid_to_episode ? `~${esc(r.valid_to_episode - 1)}` : "~"}`;
    return `<div class="tl-item"><div class="ep">${outgoing ? "→" : "←"} ${esc(other)} · ${esc(r.predicate)} · ${span}</div></div>`;
  }).join("");
  box.innerHTML = `<div class="small" style="margin-bottom:6px"><b>관계</b></div>${relHtml}
    <div class="small" style="margin:6px 0"><b>속성 이력</b></div>` + box.innerHTML;
  card.style.display = "";
}

async function open(id) {
  sel = id;
  renderList();
  showError("entities-error", "");
  try {
    const [state, hist, rels] = await Promise.all([
      api.entityState(id, asOfEp),
      api.entityHistory(id, asOfEp),
      api.entityRelations(id),
    ]);
    renderState(id, state);
    renderHistory(hist.history ?? []);
    renderRelations(rels.relations ?? [], id, asOfEp);
  } catch (e) {
    showError("entities-error", String(e));
  }
}

export function initEntities() {
  initColResizer(ENTITIES_RESIZER);
  document.addEventListener("enter-screen", (e) => {
    if (e.detail?.section !== "s4") return;
    restoreColResizer(ENTITIES_RESIZER);
  });
  const doSearch = () => {
    const raw = $("ent-asof").value.trim();
    const n = raw === "" ? null : Number(raw);
    asOfEp = Number.isNaN(n) ? null : n;
    search($("ent-search").value.trim());
  };
  $("btn-ent-search").addEventListener("click", doSearch);
  $("btn-ent-asof").addEventListener("click", doSearch);
  for (const id of ["ent-asof", "ent-search"]) {
    $(id).addEventListener("keydown", (e) => {
      if (e.key === "Enter") doSearch();
    });
  }
  doSearch();
}
