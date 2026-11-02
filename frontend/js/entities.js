import { api } from "./api.js";
import { $, esc, showError } from "./util.js";
import { initColResizer, restoreColResizer } from "./resizer.js";

const ENTITIES_RESIZER = { resizerId: "entities-resizer", varName: "--entities-left-w",
  storageKey: "entities-left-w", side: "left", min: 260, reserve: 320 };

// 화면 5 · 설정 (구 React판에서 이식)
let list = [];
let sel = null;

async function search(query) {
  showError("entities-error", "");
  try {
    const r = await api.entities(query);
    list = r.entities ?? [];
    renderList();
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
      </div>`
    )
    .join("");
  box.querySelectorAll("[data-ent]").forEach((el) => {
    el.addEventListener("click", () => open(el.dataset.ent));
  });
}

function renderState(entId, asOf, state) {
  const box = $("ent-detail");
  const attrs = state?.attributes ?? {};
  const rows = Object.entries(attrs)
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd class="mono">${esc(typeof v === "string" ? v : JSON.stringify(v))}</dd>`)
    .join("");
  box.innerHTML = `
    <h3>${esc(entId)}
      <span class="hint">회차 지정 상태:
        <input type="number" id="asof" data-role="entities.detail.asof-input" value="${esc(asOf)}" min="1" style="width:70px;display:inline-block;padding:3px 8px">
        <button class="btn sm" id="btn-asof" data-role="entities.detail.asof-btn">조회</button>
      </span>
    </h3>
    <div class="notice warn small" style="margin-bottom:10px">조회는 <b>승인된 변경만</b> 사용합니다.</div>
    <dl class="kv">${rows || `<dt>속성</dt><dd class="mono">없음</dd>`}</dl>`;
  $("btn-asof").addEventListener("click", () => open(entId, Number($("asof").value)));
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
  box.innerHTML = history
    .map(
      (h) => `<div class="tl-item">
        <div class="ep">EP ${esc(h.episode_no)}${h.story_time ? ` · <span class="chip">${esc(h.story_time)}</span>` : ""} · ${esc(h.operation)} ${esc(h.attribute_path ?? "")}</div>
        <div class="what">${esc(h.old_value ?? "")} → ${esc(h.new_value ?? "")}</div>
        ${h.evidence_quote ? `<div class="q">"${esc(h.evidence_quote)}"</div>` : ""}
      </div>`
    )
    .join("");
}

async function open(id, asOf) {
  sel = id;
  const ep = asOf ?? Number($("asof")?.value ?? 1) ?? 1;
  renderList();
  showError("entities-error", "");
  try {
    const [state, hist] = await Promise.all([
      api.entityState(id, ep),
      api.entityHistory(id),
    ]);
    renderState(id, ep, state);
    renderHistory(hist.history ?? []);
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
  $("btn-ent-search").addEventListener("click", () => search($("ent-search").value.trim()));
  $("ent-search").addEventListener("keydown", (e) => {
    if (e.key === "Enter") search($("ent-search").value.trim());
  });
  search("");
}
