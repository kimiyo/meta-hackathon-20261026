import { api } from "./api.js";
import { $, esc, showError } from "./util.js";
import { getWork } from "./work.js";
import { t } from "./i18n.js";

// 상단 토큰 버튼 → 현재 소설의 AI 호출 로그 다이얼로그 (목록 + 상세).
// 함수로 감싼다: 렌더 시점의 활성 로케일로 번역해야 하므로 모듈 로드 시 평가 금지.
const ROLE = {
  planning: () => t("ailog.rolePlanning"), drafting: () => t("ailog.roleDrafting"),
  entity: () => t("ailog.roleEntity"), verify: () => t("ailog.roleVerify"),
  quality: () => t("ailog.roleQuality"), idea_chat: () => t("ailog.roleIdeaChat"),
  idea_extract: () => t("ailog.roleIdeaExtract"),
  design_chat: () => t("ailog.roleDesignChat"), design_expand: () => t("ailog.roleDesignExpand"),
  consistency: () => t("ailog.roleConsistency"),
  "consistency-chat": () => t("ailog.roleConsistencyChat"),
};

function roleLabel(bundle) {
  return ROLE[bundle]?.() ?? bundle;
}

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
  return t("ailog.duration", { seconds: (Number(ms || 0) / 1000).toFixed(1) });
}

function listRowHtml(c) {
  const dot = c.ok ? "step-done" : "step-run";
  const mark = c.ok ? t("ailog.success") : t("ailog.failed");
  return `<div class="ailog-item${c.id === selectedId ? " active" : ""}" data-log="${c.id}">
    <div><b>${esc(roleLabel(c.bundle))}</b> <span class="${dot} small">${mark}</span></div>
    <div class="small muted">${esc(fmtTime(c.ts))}</div>
    <div class="small muted">in ${esc(c.input_tokens)}/out ${esc(c.output_tokens)} · ${esc(fmtDur(c.latency_ms))}</div>
  </div>`;
}

// ---- 읽기 쉬운 렌더링 ----

// "설명 문장 + 꼬리 JSON" 형태를 분리한다. 스켈레톤 예시처럼 파싱에
// 실패하는 {...}는 건너뛰고, 문자열 끝까지 파싱되는 첫 JSON을 쓴다.
function splitPayload(text) {
  const t = text || "";
  for (let i = t.indexOf("{"); i !== -1; i = t.indexOf("{", i + 1)) {
    try {
      return { head: t.slice(0, i).trim(), obj: JSON.parse(t.slice(i)) };
    } catch {
      /* 다음 후보 */
    }
  }
  return { head: t, obj: null };
}

function highlightJson(value) {
  const json = JSON.stringify(value, null, 2);
  return json.replace(
    /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*")(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g,
    (m, str, _inner, colon, lit) => {
      if (str !== undefined) {
        return `<span class="${colon ? "jk" : "js"}">${esc(str)}</span>${colon ? esc(colon) : ""}`;
      }
      if (lit !== undefined) return `<span class="jl">${esc(m)}</span>`;
      return `<span class="jn">${esc(m)}</span>`;
    });
}

function paragraphs(text) {
  return String(text ?? "")
    .split("\n")
    .map((line) => `<p>${esc(line) || "&nbsp;"}</p>`)
    .join("");
}

function sec(title, inner) {
  if (!inner) return "";
  return `<div class="alog-sec"><div class="alog-sec-t">${esc(title)}</div>${inner}</div>`;
}

function strList(items) {
  const arr = (items ?? []).filter((x) => x !== null && x !== undefined && x !== "");
  if (!arr.length) return "";
  return `<ul class="alog-list">${arr.map((x) => `<li>${esc(typeof x === "string" ? x : JSON.stringify(x))}</li>`).join("")}</ul>`;
}

function outlineHtml(o) {
  if (!o || typeof o !== "object") return "";
  const links = o.links && typeof o.links === "object" && !Array.isArray(o.links)
    ? Object.entries(o.links).filter(([, v]) => v !== null && v !== undefined && v !== "")
    : [];
  return `<div><b>EP ${esc(o.episode_no ?? "?")} · ${esc(o.title ?? "")}</b></div>
    ${o.goal ? `<p class="small">${esc(o.goal)}</p>` : ""}
    ${o.events?.length ? `<div class="small"><b>${t("ailog.events")}</b>${strList(o.events)}</div>` : ""}
    ${o.char_changes?.length ? `<div class="small"><b>${t("ailog.charChanges")}</b>${strList(o.char_changes)}</div>` : ""}
    ${o.foreshadowing?.length ? `<div class="small"><b>${t("ailog.foreshadowing")}</b>${strList(o.foreshadowing)}</div>` : ""}
    ${links.length ? `<div class="small"><b>${t("ailog.links")}</b><ul class="alog-list">${links.map(([k, v]) => `<li><span class="mono">${esc(k)}</span>: ${esc(String(v))}</li>`).join("")}</ul></div>` : ""}
    ${o.constraints?.length ? `<div class="small"><b>${t("ailog.constraints")}</b>${strList(o.constraints)}</div>` : ""}`;
}

function styleHtml(s) {
  if (!s || typeof s !== "object") return "";
  const rows = [
    [t("ailog.stylePreset"), s.style_preset || "—"],
    [t("ailog.stylePov"), s.pov || "—"],
    [t("ailog.styleDiction"), s.diction || "—"],
    [t("ailog.styleLength"), s.length_target || "—"],
    [t("ailog.styleFeedback"), s.feedback || "—"],
  ];
  return `<dl class="kv">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd style="white-space:pre-wrap">${esc(String(v))}</dd>`).join("")}</dl>`;
}

function entityStatesHtml(states) {
  if (!Array.isArray(states) || !states.length) return `<span class="muted small">${t("ailog.none")}</span>`;
  return `<ul class="alog-list">${states
    .map((e) => `<li><b>${esc(e?.name ?? e?.entity_id ?? "?")}</b>${e?.summary ? ` — ${esc(e.summary)}` : ""}${e?.state ? `<br><span class="muted">${esc(typeof e.state === "string" ? e.state : JSON.stringify(e.state))}</span>` : ""}</li>`)
    .join("")}</ul>`;
}

function lookupResultsHtml(items) {
  if (!Array.isArray(items) || !items.length) return `<span class="muted small">${t("ailog.none")}</span>`;
  return `<ul class="alog-list">${items
    .map((r) => `<li><span class="chip mono">${esc(r?.kind ?? "?")}</span> ${esc(r?.summary ?? r?.text ?? JSON.stringify(r))}</li>`)
    .join("")}</ul>`;
}

function rawToggle(text) {
  return `<details class="alog-raw"><summary class="small muted">${t("ailog.showRaw")}</summary><pre>${esc(text || "")}</pre></details>`;
}

function userHtml(text) {
  const { head, obj } = splitPayload(text);
  if (!obj || typeof obj !== "object") {
    return `<pre>${esc(text || "")}</pre>`;
  }
  const seen = new Set();
  const take = (key) => {
    seen.add(key);
    return obj[key];
  };
  let html = head ? `<p class="small">${esc(head)}</p>` : "";
  if (obj.episode_outline !== undefined) html += sec(t("ailog.episodeOutline"), outlineHtml(take("episode_outline")));
  if (obj.entity_states !== undefined) html += sec(t("ailog.entityStates"), entityStatesHtml(take("entity_states")));
  if (obj.style_policy !== undefined) html += sec(t("ailog.stylePolicy"), styleHtml(take("style_policy")));
  if (obj.manuscript_draft !== undefined) {
    const draft = take("manuscript_draft");
    html += sec(t("ailog.prevManuscript"), draft
      ? `<div class="manuscript alog-scroll">${paragraphs(draft)}</div>`
      : `<span class="muted small">${t("ailog.noneNew")}</span>`);
  }
  if (obj.author_instruction !== undefined) {
    const ins = take("author_instruction");
    html += sec(t("ailog.authorInstruction"), ins ? `<div class="alog-callout">${esc(ins)}</div>` : `<span class="muted small">${t("ailog.none")}</span>`);
  }
  if (obj.lookup_results !== undefined) html += sec(t("ailog.lookupResults"), lookupResultsHtml(take("lookup_results")));
  if (obj.schema !== undefined) {
    take("schema");
    html += `<details class="alog-raw"><summary class="small muted">${t("ailog.schema")}</summary><pre>${esc(typeof obj.schema === "string" ? obj.schema : JSON.stringify(obj.schema, null, 2))}</pre></details>`;
  }
  for (const [k, v] of Object.entries(obj)) {
    if (!seen.has(k)) html += sec(k, `<pre class="alog-json">${highlightJson(v)}</pre>`);
  }
  return `${html}${rawToggle(text)}`;
}

function outputHtml(text) {
  let obj = null;
  try {
    obj = JSON.parse(text || "");
  } catch {
    return `<pre>${esc(text || "")}</pre>`;
  }
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
    return `<pre class="alog-json">${highlightJson(obj)}</pre>${rawToggle(text)}`;
  }
  let html = "";
  const rest = { ...obj };
  if (typeof rest.manuscript === "string") {
    html += sec(t("ailog.manuscript"), `<div class="manuscript alog-scroll">${paragraphs(rest.manuscript)}</div>`);
    delete rest.manuscript;
  }
  if (rest.lookup_requests !== undefined) {
    const reqs = rest.lookup_requests;
    delete rest.lookup_requests;
    html += sec(t("ailog.lookupRequests"), Array.isArray(reqs) && reqs.length
      ? `<ul class="alog-list">${reqs.map((r) => `<li><span class="chip mono">${esc(r?.kind ?? "?")}</span> ${esc(r?.query ?? r?.entity_id ?? JSON.stringify(r))}</li>`).join("")}</ul>`
      : `<span class="muted small">${t("ailog.none")}</span>`);
  }
  if (Object.keys(rest).length) html += `<pre class="alog-json">${highlightJson(rest)}</pre>`;
  return `${html}${rawToggle(text)}`;
}

function block(label, text, bodyHtml) {
  if (!text) return "";
  return `<details class="ailog-block" open><summary>${label} <span class="muted small">${t("ailog.charCount", { count: (text ?? "").length })}</span></summary>${bodyHtml}</details>`;
}

function detailHtml(c) {
  const role = roleLabel(c.bundle);
  const ep = c.episode_no ?? "-";
  const ok = c.ok
    ? `<span class="step-done">${t("ailog.success")}</span>`
    : `<span class="step-run">${esc(c.error || t("ailog.failed"))}</span>`;
  return `<div class="ailog-detail-head"><b>${esc(role)}</b> <span class="chip mono">${esc(c.bundle)}</span>
      <span class="muted small">${esc(fmtTime(c.ts))} · EP ${esc(ep)} · ${esc(fmtDur(c.latency_ms))} · in ${esc(c.input_tokens)}/out ${esc(c.output_tokens)}</span> ${ok}</div>
    ${c.prompt_system ? `<details class="ailog-block" open><summary>${t("ailog.promptSystem")} <span class="muted small">${t("ailog.charCount", { count: c.prompt_system.length })}</span></summary><pre>${esc(c.prompt_system)}</pre></details>` : ""}
    ${block(t("ailog.promptUser"), c.prompt_user, userHtml(c.prompt_user))}
    ${block(t("ailog.outputResult"), c.output_text, outputHtml(c.output_text))}`;
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
  box.innerHTML = c ? detailHtml(c) : `<span class="muted">${t("ailog.selectCall")}</span>`;
}

export async function openAiLog() {
  const w = getWork();
  $("ailog-error").innerHTML = "";
  calls = [];
  selectedId = null;
  if (!w) {
    $("ailog-sub").textContent = "";
    $("ailog-list").innerHTML = "";
    $("ailog-detail").innerHTML = `<span class="muted">${t("ailog.noWork")}</span>`;
  } else {
    $("ailog-sub").textContent = w.title;
    $("ailog-list").innerHTML = `<span class="muted small">${t("ailog.loading")}</span>`;
    $("ailog-detail").innerHTML = "";
    try {
      const out = await api.aiLog(w.uid || w.id, 50);
      calls = out.calls ?? [];
      if (!calls.length) {
        $("ailog-list").innerHTML = "";
        $("ailog-detail").innerHTML = `<span class="muted">${t("ailog.noRecords")}</span>`;
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
