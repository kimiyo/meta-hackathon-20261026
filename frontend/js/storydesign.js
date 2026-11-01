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
    await loadEditor();
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
  const stLabel = design.status === "draft" ? "작성 중" : design.status;
  const st = design.status === "approved"
    ? `<span class="st-AUTHOR_APPROVED state-label">승인됨</span>`
    : `<span class="chip">${esc(stLabel)}</span>
       <button class="btn sm primary" id="btn-design-approve" data-role="design.meta.approve-btn">설계 승인</button>`;
  box.innerHTML = `<b>설계 v${esc(design.version)}</b> · ${esc(design.scope_total_episodes)}화 · ${st}`;
  $("btn-design-approve")?.addEventListener("click", () => run(async () => {
    await api.approveDesign(design.id);
    const full = await api.work(getWork().uid || getWork().id);
    design = full.latest_design;
    renderDesignMeta();
  }));
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
      loadEditor();
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
    await loadEditor();
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
    await loadEditor();
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
    await loadEditor();
  });
}

// ---- 중앙 편집 ----

async function loadEditor() {
  if (!selectedId) {
    editorNode = null;
    renderEditor();
    return;
  }
  await run(async () => {
    editorNode = await api.node(selectedId);
    renderEditor();
  });
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
      loadEditor();
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

function openDeleteDialog() {
  if (!editorNode || !editorNode.parent_id) return;
  deleteTargetId = editorNode.id;
  const st = subtreeStats(editorNode.id);
  let msg = `<b>${esc(editorNode.full_code ?? editorNode.id)} · ${esc(editorNode.title)}</b>`;
  if (st.nodes) msg += ` 외 하위 ${st.nodes}개 노드`;
  msg += `를 삭제합니다. 되돌릴 수 없습니다.`;
  if (st.eps) msg += `<br>연결된 집필 개요 ${st.eps}건도 함께 삭제됩니다(원고 기록은 유지).`;
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
    selectedId = null;
    editorNode = null;
    renderEditor();
    await reloadNodes();
    renderTree();
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

// ---- 설계 파트너 ----

function renderPartner() {
  const box = $("partner-messages");
  box.innerHTML = partnerMsgs.length ? partnerMsgs.map((m) => `
    <div class="msg ${m.role}"><div class="bubble">${esc(m.content).replace(/\n/g, "<br>")}</div></div>`).join("")
    : `<span class="muted small">선택한 노드에 대해 질문하세요. AI가 하위 줄거리를 함께 설계합니다.</span>`;
  box.scrollTop = box.scrollHeight;
}

function partnerRefs() {
  return $("partner-refs").value.split(",").map((s) => s.trim().replace(/^@/, "")).filter(Boolean);
}

async function partnerSend() {
  const input = $("partner-input");
  const content = input.value.trim();
  if (!content || busy || !selectedId) return;
  input.value = "";
  const refs = partnerRefs();
  $("partner-refs").value = "";
  partnerMsgs.push({ role: "user", content });
  renderPartner();
  await run(async () => {
    const out = await api.nodeChat(selectedId, content, refs);
    partnerMsgs.push({ role: "assistant", content: out.reply || "(빈 응답)" });
    renderPartner();
    renderProposals(out.proposed_children ?? [], out.proposed_patch ?? {});
  }, "전송 중… (취소 불가)");
}

async function expandFlow() {
  if (busy || !selectedId) return;
  const kind = $("expand-kind").value || null;
  await run(async () => {
    const out = await api.nodeExpand(selectedId, kind);
    renderProposals(out.children ?? [], {});
  }, "설계 중… (취소 불가)");
}

function renderProposals(children, patch) {
  const box = $("proposal-list");
  const hasPatch = patch && Object.keys(patch).length > 0;
  if (!children.length && !hasPatch) {
    box.innerHTML = `<span class="muted small">제안 없음</span>`;
    return;
  }
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
    const fields = {};
    if (patch.title !== undefined) fields.title = patch.title;
    if (patch.logline !== undefined) fields.logline = patch.logline;
    if (patch.body !== undefined) fields.body = patch.body;
    await api.patchNode(selectedId, fields);
    await reloadNodes();
    renderTree();
    await loadEditor();
    box.innerHTML = `<span class="muted small">적용됨</span>`;
  }));
  const applyChild = async (c) => {
    await api.createNode(design.id, {
      kind: c.kind, parent_id: selectedId, title: c.title || "무제",
      logline: c.logline || "", body: c.body || {},
    });
  };
  box.querySelectorAll("[data-apply-child]").forEach((b) => {
    b.addEventListener("click", () => run(async () => {
      await applyChild(children[Number(b.dataset.applyChild)]);
      await reloadNodes();
      renderTree();
      await loadEditor();
    }));
  });
  box.querySelector("[data-apply-all]")?.addEventListener("click", () => run(async () => {
    for (const c of children) await applyChild(c);
    await reloadNodes();
    renderTree();
    await loadEditor();
    box.innerHTML = `<span class="muted small">전체 적용됨</span>`;
  }, "적용 중…"));
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
  $("partner-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      partnerSend();
    }
  });
  $("btn-expand").addEventListener("click", expandFlow);
  initMobileTabs();
}
