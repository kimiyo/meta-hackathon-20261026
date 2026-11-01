import { api } from "./api.js";
import { $, esc, showError } from "./util.js";
import { getWork } from "./work.js";
import { go, novelPath } from "./router.js";
import { initColResizer, restoreColResizer } from "./resizer.js";

const DESIGN_RESIZERS = [
  { resizerId: "design-resizer-left", varName: "--design-left-w",
    storageKey: "design-left-w", side: "left", min: 220, reserve: 560 },
  { resizerId: "design-resizer-right", varName: "--design-right-w",
    storageKey: "design-right-w", side: "right", min: 260, reserve: 540 },
];

// 화면 2 · 소설 설계. 구조 트리(좌) + 설계 편집(중) + 설계 파트너(우).
let design = null;
let nodes = [];
let treeNodeSettings = {}; // node_id -> own settings (트리 전체 보기용)
let collapsedIds = new Set(); // 접힌 노드, 리렌더 유지용
let selectedId = null;
let editorNode = null;
let partnerMsgs = [];
let partnerThreads = [];
let partnerThreadId = null;
let includedPartnerMessages = new Set();
let partnerMsgMode = "select";
let partnerAssistantId = null;
let partnerSending = false;
let busy = false;
let loadedUid = null;

const KIND_LABEL = { work: "작품", part: "Part", chapter: "Chapter", episode: "Episode", scene: "Scene" };

function treeMode() {
  return localStorage.getItem("design-tree-mode") || "flow";
}
function setTreeMode(m) {
  localStorage.setItem("design-tree-mode", m);
  renderTree();
}

function setPartnerCollapsed(collapsed) {
  localStorage.setItem("design-partner-collapsed", collapsed ? "1" : "0");
  document.querySelector(".grid-design")?.classList.toggle("partner-collapsed", collapsed);
}

async function whenFree(timeoutMs = 30000) {
  const t0 = Date.now();
  while (busy && Date.now() - t0 < timeoutMs) {
    await new Promise((r) => setTimeout(r, 25));
  }
}

function runWhenFree(fn, label) {
  (async () => {
    await whenFree();
    await run(fn, label);
  })();
}

async function run(fn, label) {
  if (busy) return;
  busy = true;
  showError("sdesign-error", "");
  try {
    await fn();
  } catch (e) {
    showError("sdesign-error", String(e));
  } finally {
    busy = false;
  }
}

function structure() {
  try {
    return JSON.parse(getWork()?.structure_json || "{}");
  } catch {
    return {};
  }
}

// ---- 진입/적재 ----

export async function enterDesign() {
  DESIGN_RESIZERS.forEach(restoreColResizer);
  const w = getWork();
  if (!w) return;
  if (loadedUid !== (w.uid || w.id)) {
    loadedUid = w.uid || w.id;
    selectedId = null;
    editorNode = null;
    partnerMsgs = [];
    partnerThreads = [];
    partnerThreadId = null;
    includedPartnerMessages = new Set();
    partnerMsgMode = "select";
    partnerAssistantId = null;
    $("proposal-list").innerHTML = "";
    $("partner-messages").innerHTML = "";
  }
  await run(async () => {
    const full = await api.work(w.uid || w.id);
    design = full.latest_design || null;
    renderDesignMeta();
    if (!design) {
      nodes = [];
      renderTree();
      renderEditor();
      return;
    }
    await reloadNodes();
    if (!selectedId || !nodes.some((n) => n.id === selectedId)) {
      selectedId = (nodes.find((n) => n.kind === "work") ?? nodes[0])?.id ?? null;
    }
    renderTree();
    await refreshEditor();
    await loadPartnerThreads();
  });
}

async function reloadNodes() {
  const q = $("tree-search").value.trim();
  const archived = $("show-archived").checked;
  nodes = (await api.nodes(design.id, q || null, archived)).nodes ?? [];
  collapsedIds = new Set([...collapsedIds].filter((id) => nodes.some((n) => n.id === id)));
  treeNodeSettings = {};
  if (design) {
    try {
      const all = (await api.designNodeSettings(design.id)).settings ?? [];
      for (const s of all) (treeNodeSettings[s.node_id] ??= []).push(s);
    } catch { /* 트리는 설정 없이도 표시 */ }
  }
}

function renderDesignMeta() {
  const box = $("design-meta");
  if (!design) {
    box.innerHTML = `<span class="muted">설계가 없습니다. 아이디어 화면에서 인계하세요.</span>`;
    return;
  }
  box.innerHTML = `<b>설계 v${esc(design.version)}</b> · ${esc(design.scope_total_episodes)}화`;
}

// ---- 트리 ----

function childrenOf(parentId) {
  return nodes.filter((n) => (n.parent_id ?? null) === parentId);
}

function nodeRowHtml(n) {
  const m = treeMode();
  const sel = n.id === selectedId ? " selected" : "";
  const arch = n.archived ? " archived" : "";
  const kids = childrenOf(n.id).length;
  const toggle = kids ? `<span class="tw" data-toggle="${n.id}">▾</span>` : `<span class="tw empty">·</span>`;
  if (m === "one") {
    return `<div class="tnode${sel}${arch}" data-node="${n.id}">${toggle}<span class="nid mono" title="${esc(n.full_code ?? n.id)}">${esc(n.code ?? n.id)}</span><span class="t oneline"><b>${esc(n.title)}</b>${n.logline ? ` <span class="muted">${esc(n.logline)}</span>` : ""}</span></div>`;
  }
  if (m === "flow") {
    return `<div class="tnode${sel}${arch}" data-node="${n.id}">${toggle}<span class="nid mono" title="${esc(n.full_code ?? n.id)}">${esc(n.code ?? n.id)}</span><span class="t flowline"><b>${esc(n.title)}</b>${n.logline ? ` <span class="muted">${esc(n.logline)}</span>` : ""}</span></div>`;
  }
  const nss = treeNodeSettings[n.id] ?? [];
  const nsHtml = nss.length ? `<br>${nss.map((s) => `<span class="chip mono">${esc(s.code)}</span> <b>${esc(s.title)}</b>${s.summary ? ` <span class="small muted">${esc(s.summary)}</span>` : ""}`).join("<br>")}` : "";
  return `<div class="tnode${sel}${arch}" data-node="${n.id}">${toggle}<span class="nid mono" title="${esc(n.full_code ?? n.id)}">${esc(n.code ?? n.id)}</span>
    <span><b>[${esc(KIND_LABEL[n.kind] ?? n.kind)}] ${esc(n.title)}</b><br>
    <span class="small">${esc(n.logline)}</span>${nsHtml}</span></div>`;
}

function treeHtml(parentId) {
  const kids = childrenOf(parentId);
  if (!kids.length) return "";
  return `<ul class="tree">${kids.map((n) =>
    `<li>${nodeRowHtml(n)}${treeHtml(n.id)}</li>`).join("")}</ul>`;
}

function renderTree() {
  document.querySelectorAll("#tree-mode button").forEach((b) =>
    b.classList.toggle("active", b.dataset.mode === treeMode()));
  const box = $("tree-list");
  if (!design) {
    box.innerHTML = `<span class="muted small">설계 없음</span>`;
    return;
  }
  if (!nodes.length) {
    box.innerHTML = `<span class="muted small">노드가 없습니다.</span>
      <div class="btn-row"><button class="btn sm" id="btn-seed-root" data-role="design.tree.seed-root-btn">루트 노드 만들기</button>
      <button class="btn sm" id="btn-seed-outlines" data-role="design.tree.seed-outlines-btn">기존 개요에서 Episode 생성</button></div>`;
    $("btn-seed-root").addEventListener("click", seedRoot);
    $("btn-seed-outlines").addEventListener("click", seedFromOutlines);
    return;
  }
  box.innerHTML = treeHtml(null) || `<span class="muted small">표시할 노드 없음</span>`;
  box.querySelectorAll("[data-node]").forEach((el) => {
    el.addEventListener("click", (e) => {
      if (e.target.closest("[data-toggle]")) {
        const nid = el.dataset.node;
        if (collapsedIds.has(nid)) collapsedIds.delete(nid);
        else collapsedIds.add(nid);
        applyCollapsedState();
        return;
      }
      selectedId = el.dataset.node;
      renderTree();
      runWhenFree(async () => {
        await refreshEditor();
        await loadPartnerThreads();
      });
    });
  });
  applyCollapsedState();
  const kinds = $("node-add-kind");
  const st = structure();
  [...kinds.options].forEach((o) => {
    if (o.value === "part") o.disabled = st.use_part === false;
    if (o.value === "chapter") o.disabled = st.use_chapter === false;
  });
}

function setAllCollapsed(collapsed) {
  collapsedIds = new Set(collapsed ? nodes.map((n) => n.id) : []);
  applyCollapsedState();
}

function applyCollapsedState() {
  $("tree-list").querySelectorAll("[data-node]").forEach((el) => {
    const collapsed = collapsedIds.has(el.dataset.node);
    el.parentElement?.classList.toggle("collapsed", collapsed);
    const tw = el.querySelector("[data-toggle]");
    if (tw) tw.textContent = collapsed ? "▸" : "▾";
  });
}

async function seedRoot() {
  await run(async () => {
    const w = getWork();
    const root = await api.createNode(design.id, { kind: "work", title: w.title });
    selectedId = root.id;
    await reloadNodes();
    renderTree();
    await refreshEditor();
  });
}

async function seedFromOutlines() {
  await run(async () => {
    const w = getWork();
    let root = nodes.find((n) => n.kind === "work");
    if (!root) {
      root = await api.createNode(design.id, { kind: "work", title: w.title });
    }
    const d = await api.design(design.id);
    for (const o of d.outlines ?? []) {
      await api.createNode(design.id, {
        kind: "episode", parent_id: root.id, title: o.title, logline: o.goal,
        body: {
          episode_no: o.episode_no, goal: o.goal, events: o.events,
          char_changes: o.char_changes, foreshadowing: o.foreshadowing,
          links: o.links, constraints: o.constraints,
        },
      });
    }
    selectedId = root.id;
    await reloadNodes();
    renderTree();
    await refreshEditor();
  }, "생성 중…");
}

async function addNode() {
  const title = $("node-add-title").value.trim();
  if (!title) {
    showError("sdesign-error", "노드 제목을 입력하세요.");
    return;
  }
  await run(async () => {
    const parent = selectedId ?? nodes.find((n) => n.kind === "work")?.id ?? null;
    const created = await api.createNode(design.id, {
      kind: $("node-add-kind").value, parent_id: parent, title,
    });
    $("node-add-title").value = "";
    selectedId = created.id;
    await reloadNodes();
    renderTree();
    await refreshEditor();
  });
}

// ---- 중앙 편집 ----

async function refreshEditor() {
  if (!selectedId) {
    editorNode = null;
  } else {
    editorNode = await api.node(selectedId);
  }
  renderEditor();
}

async function loadEditor() {
  await run(refreshEditor);
}

const INHERIT_CAT = { character: "인물", world: "세계관", event: "사건", part: "Part 이야기", misc: "기타" };

function renderInherited(n) {
  const box = $("node-inherited");
  if (!box) return;
  const body = n?.body ?? {};
  if (n?.kind !== "work" || (!body.synopsis && !(body.settings ?? []).length)) {
    box.innerHTML = "";
    return;
  }
  const settings = (body.settings ?? []).map((s) =>
    `<li><span class="chip mono">${esc(s.code || "")}</span> [${esc(INHERIT_CAT[s.category] ?? s.category ?? "")}] <b>${esc(s.title || "")}</b>` +
    `${s.summary ? ` — ${esc(s.summary)}` : ""}` +
    `${s.detail ? `<br><span class="muted">${esc(s.detail)}</span>` : ""}</li>`).join("");
  box.innerHTML = `<b class="small">아이디어에서 인계됨</b>` +
    (body.synopsis
      ? `<div class="small" style="margin:4px 0"><b>전체 줄거리</b><br>${esc(body.synopsis).replace(/\n/g, "<br>")}</div>`
      : "") +
    (settings ? `<ul class="small ho-tree">${settings}</ul>` : "");
}

function rollupMode() {
  return localStorage.getItem("design-rollup-mode") === "flow" ? "flow" : "one";
}

function renderRollup(n) {
  const box = $("children-rollup");
  const kids = n?.children ?? [];
  if (!kids.length) {
    box.innerHTML = `<span class="muted small">하위 노드 없음. 오른쪽에서 Expand로 설계하세요.</span>`;
    return;
  }
  const rm = rollupMode();
  box.innerHTML = `<div class="rollup-head"><b class="small">하위 줄거리 (${kids.length})</b><span class="seg"><button data-rmode="one" data-role="design.editor.rollup-mode-one">한줄</button><button data-rmode="flow" data-role="design.editor.rollup-mode-flow">이어서</button></span></div>` + kids.map((k) => `
    <div class="rollup-row ${rm}" data-goto="${k.id}" title="클릭하여 이동"><span class="chip mono blue" title="${esc(k.full_code ?? k.id)}">${esc(k.code ?? k.id)}</span> <b>${esc(k.title)}</b> <span class="small muted">${esc(k.logline)}</span></div>`).join("");
  box.querySelectorAll("[data-rmode]").forEach((b) => {
    b.classList.toggle("active", b.dataset.rmode === rm);
    b.addEventListener("click", () => {
      localStorage.setItem("design-rollup-mode", b.dataset.rmode);
      renderRollup(editorNode);
    });
  });
  box.querySelectorAll("[data-goto]").forEach((el) => {
    el.addEventListener("click", () => {
      selectedId = el.dataset.goto;
      renderTree();
      runWhenFree(async () => {
        await refreshEditor();
        await loadPartnerThreads();
      });
    });
  });
}

function renderEditor() {
  const empty = !$("node-title");
  if (empty) return;
  if (!editorNode) {
    $("node-id-label").textContent = "-";
    $("btn-node-delete").disabled = true;
    $("node-title").value = "";
    $("node-logline").value = "";
    renderInherited(null);
    $("children-rollup").innerHTML = `<span class="muted small">노드를 선택하세요.</span>`;
    return;
  }
  const n = editorNode;
  $("node-id-label").innerHTML = `<span class="mono">${esc(n.full_code ?? n.id)}</span> · ${esc(KIND_LABEL[n.kind] ?? n.kind)}${n.archived ? " · 보관됨" : ""}`;
  if (document.activeElement !== $("node-title")) $("node-title").value = n.title ?? "";
  if (document.activeElement !== $("node-logline")) $("node-logline").value = n.logline ?? "";
  $("node-save-info").textContent = "";
  renderInherited(n);
  renderRollup(n);
  const canWrite = n.kind === "episode" && n.body && Number.isInteger(n.body.episode_no);
  $("btn-write-jump").style.display = canWrite ? "" : "none";
  $("btn-node-delete").disabled = !n.parent_id;
}

// ---- 노드 설정 다이얼로그 ----
const NS_CATEGORIES = ["character", "world", "event", "misc"];
const NS_CAT_LABEL = { character: "인물", world: "세계관", event: "사건", misc: "기타" };
let nsDialogNode = null; // { id, title }
let nsOwn = [];
let nsInherited = [];
let nsOverridden = [];
let nsForm = null; // null | { mode: "add" } | { mode: "edit", id } | { mode: "override", code, from }

function openNsDialog() {
  if (!editorNode) return;
  nsDialogNode = { id: editorNode.id, title: editorNode.title,
    fullCode: editorNode.full_code ?? editorNode.id };
  $("ns-dialog-node").textContent = `${nsDialogNode.fullCode} · ${nsDialogNode.title}`;
  nsForm = null;
  showError("ns-dialog-error", "");
  $("ns-dialog-backdrop").style.display = "";
  loadNsDialog();
}

function closeNsDialog() {
  $("ns-dialog-backdrop").style.display = "none";
  nsDialogNode = null;
  nsForm = null;
}

async function loadNsDialog() {
  if (!nsDialogNode) return;
  try {
    const eff = await api.nodeSettings(nsDialogNode.id);
    if (!nsDialogNode) return;
    nsOwn = eff.own ?? [];
    nsInherited = eff.inherited ?? [];
    nsOverridden = eff.overridden ?? [];
    nsForm = null;
    renderNsDialog();
  } catch (e) {
    showError("ns-dialog-error", String(e));
  }
}

function nsOwnRowHtml(s) {
  const shadow = nsOverridden.includes(s.code)
    ? ` <span class="chip" title="상위 노드의 같은 코드 설정을 덮어씁니다">상위 덮어씀</span>` : "";
  return `<div class="setting" data-ns="${s.id}">
    <div class="setting-head"><span class="chip mono">${esc(s.code)}</span><span class="chip">${esc(NS_CAT_LABEL[s.category] ?? s.category)}</span><b>${esc(s.title)}</b>${shadow}</div>
    ${s.summary ? `<div class="small">${esc(s.summary).replace(/\n/g, "<br>")}</div>` : ""}
    ${s.detail ? `<div class="small muted">${esc(s.detail).replace(/\n/g, "<br>")}</div>` : ""}
    <div class="btn-row"><button class="btn sm" data-ns-edit data-role="design.editor.ns-edit-btn">편집</button><button class="btn sm" data-ns-del data-role="design.editor.ns-delete-btn">삭제</button></div>
  </div>`;
}

function nsInheritedRowHtml(s) {
  return `<div class="setting" data-ns-src="${s.id}">
    <div class="setting-head"><span class="chip mono" title="${esc(s.source_kind)} ${esc(s.source_title)}">${esc(s.source_node)}·${esc(s.code)}</span><span class="chip">${esc(NS_CAT_LABEL[s.category] ?? s.category)}</span><b>${esc(s.title)}</b></div>
    ${s.summary ? `<div class="small">${esc(s.summary).replace(/\n/g, "<br>")}</div>` : ""}
    ${s.detail ? `<div class="small muted">${esc(s.detail).replace(/\n/g, "<br>")}</div>` : ""}
    <div class="btn-row"><button class="btn sm" data-ns-override="${s.id}" data-role="design.editor.ns-override-btn">이 노드에서 덮어쓰기</button></div>
  </div>`;
}

function nsFormHtml() {
  const f = nsForm;
  const src = f.mode === "edit"
    ? nsOwn.find((x) => x.id === f.id) ?? {}
    : f.mode === "override"
      ? nsInherited.find((x) => x.id === f.from) ?? {}
      : {};
  const codeNote = f.mode === "add"
    ? `<div class="small muted">코드는 저장 시 자동 부여(S번호)</div>`
    : `<div class="small">코드: <span class="chip mono">${esc(f.mode === "edit" ? (src.code ?? "") : f.code)}</span>${f.mode === "override" ? " (상위 설정을 덮어씁니다)" : ""}</div>`;
  return `<label class="f">분류</label>
    <select data-f="category" data-role="design.editor.ns-form-category">${NS_CATEGORIES.map((c) =>
      `<option value="${c}"${src.category === c ? " selected" : ""}>${NS_CAT_LABEL[c]}</option>`).join("")}</select>
    ${codeNote}
    <label class="f">제목</label><input type="text" data-f="title" data-role="design.editor.ns-form-title" value="${esc(src.title ?? "")}">
    <label class="f">요약</label><textarea data-f="summary" data-role="design.editor.ns-form-summary">${esc(src.summary ?? "")}</textarea>
    <label class="f">상세</label><textarea data-f="detail" data-role="design.editor.ns-form-detail">${esc(src.detail ?? "")}</textarea>
    <div class="btn-row"><button class="btn sm primary" data-ns-save data-role="design.editor.ns-form-save">저장</button>
    <button class="btn sm" data-ns-cancel data-role="design.editor.ns-form-cancel">취소</button></div>`;
}

function renderNsDialog() {
  const ownBox = $("ns-dialog-own");
  const inhBox = $("ns-dialog-inherited");
  const formBox = $("ns-dialog-form");
  if (!ownBox || !inhBox || !formBox) return;
  ownBox.innerHTML = nsOwn.length ? nsOwn.map(nsOwnRowHtml).join("")
    : `<span class="muted small">이 노드의 설정 없음.</span>`;
  inhBox.innerHTML = nsInherited.length ? nsInherited.map(nsInheritedRowHtml).join("")
    : `<span class="muted small">물려받은 설정 없음.</span>`;
  if (!nsForm) {
    formBox.style.display = "none";
    formBox.innerHTML = "";
  } else {
    formBox.style.display = "";
    formBox.innerHTML = nsFormHtml();
    formBox.querySelector("[data-ns-save]").addEventListener("click", saveNsForm);
    formBox.querySelector("[data-ns-cancel]").addEventListener("click", () => {
      nsForm = null;
      renderNsDialog();
    });
  }
  ownBox.querySelectorAll("[data-ns-edit]").forEach((b) => {
    b.addEventListener("click", () => {
      nsForm = { mode: "edit", id: Number(b.closest("[data-ns]").dataset.ns) };
      renderNsDialog();
    });
  });
  ownBox.querySelectorAll("[data-ns-del]").forEach((b) => {
    b.addEventListener("click", () => deleteNsFlow(Number(b.closest("[data-ns]").dataset.ns)));
  });
  inhBox.querySelectorAll("[data-ns-override]").forEach((b) => {
    b.addEventListener("click", () => {
      const src = nsInherited.find((x) => x.id === Number(b.dataset.nsOverride));
      if (!src) return;
      nsForm = { mode: "override", code: src.code, from: src.id };
      renderNsDialog();
    });
  });
}

async function saveNsForm() {
  const box = $("ns-dialog-form");
  if (!nsForm || !nsDialogNode) return;
  const data = {
    category: box.querySelector('[data-f="category"]').value,
    title: box.querySelector('[data-f="title"]').value,
    summary: box.querySelector('[data-f="summary"]').value,
    detail: box.querySelector('[data-f="detail"]').value,
  };
  await run(async () => {
    if (nsForm.mode === "edit") {
      await api.patchNodeSetting(nsForm.id, data);
    } else {
      await api.createNodeSetting(nsDialogNode.id,
        nsForm.mode === "override" ? { ...data, code: nsForm.code } : data);
    }
    await reloadNodes();
    renderTree();
    await loadNsDialog();
  });
}

async function deleteNsFlow(id) {
  await run(async () => {
    await api.deleteNodeSetting(id);
    await reloadNodes();
    renderTree();
    await loadNsDialog();
  });
}

// 제목·줄거리는 입력 후 자동 저장한다(스냅샷을 잡아 노드 전환과 섞이지 않게).
let editorSaveTimer = null;

function scheduleEditorSave() {
  if (!editorNode) return;
  const snap = { id: editorNode.id, title: $("node-title").value, logline: $("node-logline").value };
  $("node-save-info").textContent = "입력 중…";
  clearTimeout(editorSaveTimer);
  editorSaveTimer = setTimeout(() => flushEditorSave(snap), 700);
}

async function flushEditorSave(snap) {
  if (!snap) return;
  if (busy) {
    editorSaveTimer = setTimeout(() => flushEditorSave(snap), 700);
    return;
  }
  $("node-save-info").textContent = "저장 중…";
  try {
    await api.patchNode(snap.id, { title: snap.title, logline: snap.logline });
    const fresh = await api.node(snap.id);
    if (editorNode?.id === snap.id) editorNode = fresh;
    await reloadNodes();
    renderTree();
    if (editorNode?.id === snap.id) {
      if (document.activeElement !== $("node-title")) $("node-title").value = fresh.title ?? "";
      if (document.activeElement !== $("node-logline")) $("node-logline").value = fresh.logline ?? "";
      $("node-save-info").textContent = "저장됨";
    }
  } catch (e) {
    if (editorNode?.id === snap.id) $("node-save-info").textContent = "";
    showError("sdesign-error", String(e));
  }
}

function subtreeStats(id) {
  let count = 0;
  let eps = 0;
  const stack = [...childrenOf(id)];
  while (stack.length) {
    const c = stack.pop();
    count++;
    if (c.kind === "episode") eps++;
    stack.push(...childrenOf(c.id));
  }
  return { nodes: count, eps };
}

let deleteTargetId = null;

function subtreeIds(id) {
  const out = [id];
  for (const k of childrenOf(id)) out.push(...subtreeIds(k.id));
  return out;
}

async function openDeleteDialog() {
  if (!editorNode || !editorNode.parent_id) return;
  deleteTargetId = editorNode.id;
  const st = subtreeStats(editorNode.id);
  let tcount = 0;
  try {
    const ids = new Set(subtreeIds(editorNode.id));
    const all = (await api.designThreads(design.id)).threads ?? [];
    tcount = all.filter((th) => ids.has(th.node_id)).length;
  } catch { /* 개수 없이도 삭제 가능 */ }
  let msg = `<b>${esc(editorNode.full_code ?? editorNode.id)} · ${esc(editorNode.title)}</b>`;
  if (st.nodes) msg += ` 외 하위 ${st.nodes}개 노드`;
  msg += `를 삭제합니다. 되돌릴 수 없습니다.`;
  if (st.eps) msg += `<br>연결된 집필 개요 ${st.eps}건도 함께 삭제됩니다(원고 기록은 유지).`;
  if (tcount) msg += `<br>연결된 대화 ${tcount}건도 함께 삭제됩니다.`;
  $("delete-dialog-msg").innerHTML = msg;
  showError("delete-dialog-error", "");
  $("delete-dialog-backdrop").style.display = "";
}

function closeDeleteDialog() {
  $("delete-dialog-backdrop").style.display = "none";
  deleteTargetId = null;
}

async function confirmDeleteNode() {
  if (!deleteTargetId) return;
  const id = deleteTargetId;
  await run(async () => {
    await api.deleteNode(id);
    closeDeleteDialog();
    await reloadNodes();
    renderTree();
    selectedId = (nodes.find((n) => n.kind === "work") ?? nodes[0])?.id ?? null;
    await refreshEditor();
    await loadPartnerThreads();
  });
}

function writeJump() {
  if (!editorNode?.body?.episode_no) return;
  const w = getWork();
  go(novelPath(w.uid || w.id, "monitor"));
  setTimeout(() => {
    document.dispatchEvent(new CustomEvent("write-episode", {
      detail: { designId: design.id, ep: editorNode.body.episode_no },
    }));
  }, 50);
}

// ---- 전체 대화 검색 ----

function openThreadSearch() {
  $("thread-search-input").value = "";
  $("thread-search-list").innerHTML = `<span class="muted small">불러오는 중…</span>`;
  showError("thread-search-error", "");
  $("thread-search-backdrop").style.display = "";
  $("thread-search-input").focus();
  runThreadSearch();
}

let threadSearchTimer = null;
function queueThreadSearch(immediate = false) {
  clearTimeout(threadSearchTimer);
  if (immediate) {
    runThreadSearch();
    return;
  }
  threadSearchTimer = setTimeout(runThreadSearch, 250);
}

function closeThreadSearch() {
  $("thread-search-backdrop").style.display = "none";
}

async function runThreadSearch() {
  const q = $("thread-search-input").value.trim();
  const box = $("thread-search-list");
  showError("thread-search-error", "");
  try {
    const out = (await api.designThreads(design.id, null, q || null)).threads ?? [];
    if ($("thread-search-input").value.trim() !== q) return;
    if (!out.length) {
      box.innerHTML = `<span class="muted small">${q ? "결과 없음" : "대화 없음"}</span>`;
      return;
    }
    box.innerHTML = out.map((th) => `
      <button class="thread-hit" data-jump-thread="${th.id}" data-jump-node="${esc(th.node_id)}">
        <span class="chip mono">${esc(th.node_code ?? "")}</span>
        <b>${esc(th.title || "(무제)")}</b>
        <span class="muted small">${esc((th.updated_at || "").slice(0, 16).replace("T", " "))}</span>
        ${th.snippet ? `<span class="small">${esc(th.snippet)}</span>` : ""}
      </button>`).join("");
    box.querySelectorAll("[data-jump-thread]").forEach((b) => {
      b.addEventListener("click", () => {
        jumpToThread(b.dataset.jumpNode, Number(b.dataset.jumpThread));
      });
    });
  } catch (err) {
    showError("thread-search-error", String(err));
  }
}

async function jumpToThread(nodeId, threadId) {
  closeThreadSearch();
  await whenFree();
  await run(async () => {
    if (!nodes.some((n) => n.id === nodeId)) {
      $("tree-search").value = "";
      await reloadNodes();
    }
    if (!nodes.some((n) => n.id === nodeId)) {
      throw new Error("노드를 찾을 수 없습니다. 삭제되었을 수 있습니다.");
    }
    const chain = [];
    let cur = nodes.find((n) => n.id === nodeId);
    while (cur) {
      chain.unshift(cur.id);
      cur = nodes.find((n) => n.id === (cur.parent_id ?? null)) ?? null;
    }
    chain.slice(0, -1).forEach((id) => collapsedIds.delete(id));
    selectedId = nodeId;
    renderTree();
    document.querySelector(`.tnode[data-node="${nodeId}"]`)
      ?.scrollIntoView({ block: "nearest" });
    await refreshEditor();
    await loadPartnerThreads();
    if (partnerThreadId !== threadId) await openPartnerThread(threadId);
    renderThreadSelect();
  });
}

// ---- 설계 파트너 ----

function renderPartner() {
  const box = $("partner-messages");
  box.innerHTML = partnerMsgs.length ? partnerMsgs.map((m) => `
    <div class="msg ${m.role}"><label class="inc"><input type="checkbox" data-pinc-msg="${m.id}"${includedPartnerMessages.has(m.id) ? " checked" : ""}></label><div class="bubble">${esc(m.content).replace(/\n/g, "<br>")}</div></div>`).join("")
    : `<span class="muted small">선택한 노드에 대해 질문하세요. AI가 하위 줄거리를 함께 설계합니다.</span>`;
  box.querySelectorAll("[data-pinc-msg]").forEach((c) => {
    c.addEventListener("change", () => {
      const id = Number(c.dataset.pincMsg);
      if (!Number.isFinite(id)) return;
      if (c.checked) includedPartnerMessages.add(id);
      else includedPartnerMessages.delete(id);
      partnerMsgMode = "select";
      updatePartnerSeg();
    });
  });
  if (partnerSending) {
    box.insertAdjacentHTML("beforeend",
      `<div class="msg assistant"><div class="typing" data-role="design.partner.typing" aria-label="AI 답변 생성 중"><span></span><span></span><span></span></div></div>`);
  }
  box.scrollTop = box.scrollHeight;
}

function updatePartnerSeg() {
  document.querySelectorAll("#partner-msg-mode button").forEach((b) =>
    b.classList.toggle("active", b.dataset.pmmode === partnerMsgMode));
}

function resetPartnerInclusion() {
  partnerMsgMode = "select";
  includedPartnerMessages = new Set(partnerMsgs.slice(-2).map((m) => m.id));
  updatePartnerSeg();
  renderPartner();
}

async function loadPartnerThreads() {
  partnerThreads = [];
  partnerThreadId = null;
  partnerMsgs = [];
  partnerAssistantId = null;
  if (!design || !selectedId) {
    renderThreadSelect();
    resetPartnerInclusion();
    renderProposals([], {});
    return;
  }
  partnerThreads = (await api.designThreads(design.id, selectedId)).threads ?? [];
  renderThreadSelect();
  if (partnerThreads.length) await openPartnerThread(partnerThreads[0].id);
  else {
    resetPartnerInclusion();
    renderProposals([], {});
  }
}

function renderThreadSelect() {
  const sel = $("partner-thread");
  if (!partnerThreads.length) {
    sel.innerHTML = `<option value="">대화 없음 — ＋로 시작</option>`;
    return;
  }
  sel.innerHTML = partnerThreads.map((th) =>
    `<option value="${th.id}"${th.id === partnerThreadId ? " selected" : ""}>${esc(th.title || "(무제)")}</option>`).join("");
}

async function openPartnerThread(tid) {
  partnerThreadId = tid;
  partnerMsgs = (await api.designThreadMessages(tid)).messages ?? [];
  resetPartnerInclusion();
  const last = [...partnerMsgs].reverse().find((m) =>
    m.role === "assistant" && ((m.proposals?.children?.length) ||
      (m.proposals?.patch && Object.keys(m.proposals.patch).length)));
  if (last) {
    partnerAssistantId = last.id;
    renderProposals(last.proposals.children ?? [], last.proposals.patch ?? {},
      last.applied ?? {}, last.id);
  } else {
    partnerAssistantId = null;
    renderProposals([], {});
  }
}

async function ensurePartnerThread() {
  if (partnerThreadId) return partnerThreadId;
  const th = await api.createDesignThread(design.id, selectedId);
  partnerThreads.unshift(th);
  partnerThreadId = th.id;
  renderThreadSelect();
  return th.id;
}

function partnerRefs() {
  return $("partner-refs").value.split(",").map((s) => s.trim().replace(/^@/, "")).filter(Boolean);
}

async function partnerSend() {
  const input = $("partner-input");
  const content = input.value.trim();
  if (!content || busy || partnerSending || !selectedId) return;
  input.value = "";
  const refs = partnerRefs();
  $("partner-refs").value = "";
  const sentNode = selectedId;
  const sentTid = partnerThreadId;
  const pending = { id: `pending-${Date.now()}`, role: "user", content };
  partnerSending = true;
  partnerMsgs.push(pending);
  renderPartner();
  const send = $("btn-partner-send");
  send.textContent = "처리중...";
  send.disabled = true;
  await run(async () => {
    const tid = await ensurePartnerThread();
    const out = await api.postDesignMessage(tid, content, refs,
      [...includedPartnerMessages]);
    if (sentNode !== selectedId || (sentTid && tid !== sentTid)) return;
    partnerSending = false;
    const i = partnerMsgs.findIndex((m) => m.id === pending.id);
    if (i >= 0) partnerMsgs.splice(i, 1, out.user, out.assistant);
    else partnerMsgs.push(out.user, out.assistant);
    partnerAssistantId = out.assistant.id;
    resetPartnerInclusion();
    renderProposals(out.assistant.proposals.children ?? [],
      out.assistant.proposals.patch ?? {}, out.assistant.applied ?? {},
      out.assistant.id);
    partnerThreads = (await api.designThreads(design.id, selectedId)).threads ?? [];
    renderThreadSelect();
  }, "전송 중… (취소 불가)");
  send.textContent = "전송";
  send.disabled = false;
  if (partnerSending && sentNode === selectedId) {
    // AI 호출 실패: 사용자 메시지는 서버에 저장되어 있으므로 동기화
    partnerSending = false;
    try {
      partnerMsgs = partnerThreadId
        ? (await api.designThreadMessages(partnerThreadId)).messages ?? []
        : [];
    } catch {
      partnerMsgs = partnerMsgs.filter((m) => m.id !== pending.id);
    }
    resetPartnerInclusion();
  } else if (partnerSending) {
    partnerSending = false;
    renderPartner();
  }
}

async function expandFlow() {
  if (busy || !selectedId) return;
  const kind = $("expand-kind").value || null;
  await run(async () => {
    const out = await api.nodeExpand(selectedId, kind);
    renderProposals(out.children ?? [], {});
  }, "설계 중… (취소 불가)");
}

function renderProposals(children, patch, applied = {}, assistantId = null) {
  const box = $("proposal-list");
  const hasPatch = patch && Object.keys(patch).length > 0;
  if (!children.length && !hasPatch) {
    box.innerHTML = `<span class="muted small">제안 없음</span>`;
    return;
  }
  let patchApplied = Boolean(applied?.patch);
  const childApplied = new Set(applied?.children ?? []);
  const applyPatch = async () => {
    const fields = {};
    if (patch.title !== undefined) fields.title = patch.title;
    if (patch.logline !== undefined) fields.logline = patch.logline;
    if (typeof patch.body === "string") {
      if (fields.logline === undefined) fields.logline = patch.body;
    } else if (patch.body !== undefined && patch.body !== null
               && typeof patch.body === "object") {
      fields.body = patch.body;
    }
    await api.patchNode(selectedId, fields);
    if (assistantId) await api.markDesignApplied(assistantId, { patch: true });
    patchApplied = true;
  };
  const applyChild = async (c) => {
    await api.createNode(design.id, {
      kind: c.kind, parent_id: selectedId, title: c.title || "무제",
      logline: c.logline || "", body: (c.body && typeof c.body === "object") ? c.body : {},
    });
  };
  const refreshAfterApply = async () => {
    await reloadNodes();
    renderTree();
    await refreshEditor();
  };
  const markApplied = (btn) => {
    if (btn) { btn.disabled = true; btn.textContent = "✓ 반영됨"; }
  };
  box.innerHTML = `
    ${hasPatch ? `<div class="proposal">
      <b class="small">선택 노드 수정안</b>
      <div class="small">${esc(patch.title ?? "")}</div>
      <div class="small muted">${esc(patch.logline ?? "")}</div>
      <div class="btn-row"><button class="btn sm primary" data-apply-patch data-role="design.partner.apply-patch-btn">이 노드에 적용</button></div>
    </div>` : ""}
    ${children.length ? `<b class="small">하위 설계안 (${children.length})</b>
    <div class="btn-row"><button class="btn sm" data-apply-all data-role="design.partner.apply-all-btn">전체 적용</button></div>` : ""}
    ${children.map((c, i) => `
      <div class="proposal">
        <b class="small">[${esc(KIND_LABEL[c.kind] ?? c.kind)}] ${esc(c.title)}</b>
        <div class="small muted">${esc(c.logline)}</div>
        <div class="btn-row"><button class="btn sm" data-apply-child="${i}" data-role="design.partner.apply-child-btn">하위로 추가</button></div>
      </div>`).join("")}`;
  box.querySelector("[data-apply-patch]")?.addEventListener("click", () => run(async () => {
    if (patchApplied) return;
    await applyPatch();
    await refreshAfterApply();
    markApplied(box.querySelector("[data-apply-patch]"));
  }));
  box.querySelectorAll("[data-apply-child]").forEach((b) => {
    b.addEventListener("click", () => run(async () => {
      const i = Number(b.dataset.applyChild);
      if (childApplied.has(i)) return;
      await applyChild(children[i]);
      if (assistantId) await api.markDesignApplied(assistantId, { child: i });
      childApplied.add(i);
      await refreshAfterApply();
      markApplied(b);
    }));
  });
  box.querySelector("[data-apply-all]")?.addEventListener("click", () => run(async () => {
    if (hasPatch && !patchApplied) await applyPatch();
    for (let i = 0; i < children.length; i++) {
      if (childApplied.has(i)) continue;
      await applyChild(children[i]);
      childApplied.add(i);
    }
    await refreshAfterApply();
    markApplied(box.querySelector("[data-apply-patch]"));
    box.querySelectorAll("[data-apply-child]").forEach(markApplied);
    const allBtn = box.querySelector("[data-apply-all]");
    if (allBtn) { allBtn.disabled = true; allBtn.textContent = "✓ 전체 반영됨"; }
  }, "적용 중…"));
  if (patchApplied) markApplied(box.querySelector("[data-apply-patch]"));
  box.querySelectorAll("[data-apply-child]").forEach((b) => {
    if (childApplied.has(Number(b.dataset.applyChild))) markApplied(b);
  });
  if ((patchApplied || !hasPatch) &&
      children.every((_, i) => childApplied.has(i)) && children.length) {
    const allBtn = box.querySelector("[data-apply-all]");
    if (allBtn) { allBtn.disabled = true; allBtn.textContent = "✓ 전체 반영됨"; }
  }
}

// ---- 모바일 pane 탭 ----

function initMobileTabs() {
  document.querySelectorAll("#sdesign-mtabs button").forEach((b) => {
    b.addEventListener("click", () => {
      document.querySelectorAll("#sdesign-mtabs button").forEach((x) =>
        x.classList.toggle("active", x === b));
      ["tree", "editor", "partner"].forEach((p) => {
        $("pane-" + p).classList.toggle("mpane-active", p === b.dataset.mpane);
      });
    });
  });
}

export function initDesign() {
  DESIGN_RESIZERS.forEach(initColResizer);
  $("btn-ns-add").addEventListener("click", openNsDialog);
  $("btn-ns-dialog-close").addEventListener("click", closeNsDialog);
  $("btn-ns-dialog-add").addEventListener("click", () => {
    nsForm = { mode: "add" };
    renderNsDialog();
  });
  setPartnerCollapsed(localStorage.getItem("design-partner-collapsed") === "1");
  $("btn-partner-collapse").addEventListener("click", () => setPartnerCollapsed(true));
  $("btn-partner-expand").addEventListener("click", () => setPartnerCollapsed(false));
  document.querySelectorAll("#tree-mode button[data-mode]").forEach((b) =>
    b.addEventListener("click", () => setTreeMode(b.dataset.mode)));
  $("btn-tree-expand-all").addEventListener("click", () => setAllCollapsed(false));
  $("btn-tree-collapse-all").addEventListener("click", () => setAllCollapsed(true));
  $("btn-tree-search").addEventListener("click", () => run(async () => {
    await reloadNodes();
    renderTree();
  }));
  $("tree-search").addEventListener("keydown", (e) => {
    if (e.key === "Enter") $("btn-tree-search").click();
  });
  $("show-archived").addEventListener("change", () => run(async () => {
    await reloadNodes();
    renderTree();
  }));
  $("btn-node-add").addEventListener("click", addNode);
  $("node-title").addEventListener("input", scheduleEditorSave);
  $("node-logline").addEventListener("input", scheduleEditorSave);
  $("btn-write-jump").addEventListener("click", writeJump);
  $("btn-node-delete").addEventListener("click", openDeleteDialog);
  $("btn-delete-cancel").addEventListener("click", closeDeleteDialog);
  $("btn-delete-confirm").addEventListener("click", confirmDeleteNode);
  $("btn-partner-send").addEventListener("click", partnerSend);
  $("partner-thread").addEventListener("change", (e) => {
    const tid = Number(e.target.value);
    if (tid) runWhenFree(async () => { await openPartnerThread(tid); });
  });
  $("btn-partner-new").addEventListener("click", () => runWhenFree(async () => {
    if (!design || !selectedId) return;
    const th = await api.createDesignThread(design.id, selectedId);
    partnerThreads.unshift(th);
    renderThreadSelect();
    await openPartnerThread(th.id);
    $("partner-input").focus();
  }));
  $("btn-partner-search").addEventListener("click", openThreadSearch);
  $("btn-thread-search-close").addEventListener("click", closeThreadSearch);
  $("thread-search-backdrop").addEventListener("click", (e) => {
    if (e.target === $("thread-search-backdrop")) closeThreadSearch();
  });
  $("thread-search-input").addEventListener("input", () => queueThreadSearch());
  $("thread-search-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") queueThreadSearch(true);
  });
  document.querySelectorAll("#partner-msg-mode button").forEach((b) => {
    b.addEventListener("click", () => {
      partnerMsgMode = b.dataset.pmmode;
      if (partnerMsgMode === "all") {
        includedPartnerMessages = new Set(partnerMsgs.map((m) => m.id));
      } else if (partnerMsgMode === "none") {
        includedPartnerMessages = new Set();
      } else {
        includedPartnerMessages = new Set(partnerMsgs.slice(-2).map((m) => m.id));
      }
      updatePartnerSeg();
      renderPartner();
    });
  });
  $("partner-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      partnerSend();
    }
  });
  $("btn-expand").addEventListener("click", expandFlow);
  initMobileTabs();
}
