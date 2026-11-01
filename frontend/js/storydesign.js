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
  const kids = childrenOf(n.id).length;
  const toggle = kids ? `<span class="tw" data-toggle="${n.id}">▾</span>` : `<span class="tw empty">·</span>`;
  if (m === "one") {
    return `<div class="tnode${sel}" data-node="${n.id}">${toggle}<span class="nid mono">${esc(n.id)}</span> <span class="t concise">${esc(n.title)}</span></div>`;
  }
  if (m === "flow") {
    return `<div class="tnode${sel}" data-node="${n.id}">${toggle}<span class="nid mono">${esc(n.id)}</span>
      <span><b>${esc(n.title)}</b><br><span class="small muted">${esc(n.logline)}</span></span></div>`;
  }
  return `<div class="tnode${sel}" data-node="${n.id}">${toggle}<span class="nid mono">${esc(n.id)}</span>
    <span><b>[${esc(KIND_LABEL[n.kind] ?? n.kind)}] ${esc(n.title)}</b><br>
    <span class="small">${esc(n.logline)}</span><br>
    <span class="small muted mono">${esc(JSON.stringify(n.body ?? {}).slice(0, 120))}</span></span></div>`;
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
        const li = el.parentElement;
        li.classList.toggle("collapsed");
        e.target.textContent = li.classList.contains("collapsed") ? "▸" : "▾";
        return;
      }
      selectedId = el.dataset.node;
      renderTree();
      loadEditor();
    });
  });
  const kinds = $("node-add-kind");
  const st = structure();
  [...kinds.options].forEach((o) => {
    if (o.value === "part") o.disabled = st.use_part === false;
    if (o.value === "chapter") o.disabled = st.use_chapter === false;
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

function renderEditor() {
  const empty = !$("node-title");
  if (empty) return;
  if (!editorNode) {
    $("node-id-label").textContent = "-";
    $("node-title").value = "";
    $("node-logline").value = "";
    $("node-body").value = "";
    renderInherited(null);
    $("children-rollup").innerHTML = `<span class="muted small">노드를 선택하세요.</span>`;
    return;
  }
  const n = editorNode;
  $("node-id-label").innerHTML = `<span class="mono">${esc(n.id)}</span> · ${esc(KIND_LABEL[n.kind] ?? n.kind)}${n.archived ? " · 보관됨" : ""}`;
  if (document.activeElement !== $("node-title")) $("node-title").value = n.title ?? "";
  if (document.activeElement !== $("node-logline")) $("node-logline").value = n.logline ?? "";
  if (document.activeElement !== $("node-body")) {
    $("node-body").value = JSON.stringify(n.body ?? {}, null, 2);
  }
  $("node-save-info").textContent = "";
  renderInherited(n);
  const kids = n.children ?? [];
  $("children-rollup").innerHTML = kids.length
    ? `<b class="small">하위 줄거리 (${kids.length})</b>` + kids.map((k) => `
      <div class="rollup" data-goto="${k.id}"><span class="mono">${esc(k.id)}</span> <b>${esc(k.title)}</b><br>
      <span class="small muted">${esc(k.logline)}</span></div>`).join("")
    : `<span class="muted small">하위 노드 없음. 오른쪽에서 Expand로 설계하세요.</span>`;
  $("children-rollup").querySelectorAll("[data-goto]").forEach((el) => {
    el.addEventListener("click", () => {
      selectedId = el.dataset.goto;
      renderTree();
      loadEditor();
    });
  });
  const canWrite = n.kind === "episode" && n.body && Number.isInteger(n.body.episode_no);
  $("btn-write-jump").style.display = canWrite ? "" : "none";
  $("btn-node-archive").textContent = n.archived ? "보관 해제" : "보관";
}

async function saveEditor() {
  if (!editorNode) return;
  let body;
  try {
    body = JSON.parse($("node-body").value || "{}");
  } catch {
    showError("sdesign-error", "상세(JSON) 파싱 실패. JSON 형식으로 입력하세요.");
    return;
  }
  if (typeof body !== "object" || Array.isArray(body)) {
    showError("sdesign-error", "상세는 JSON 객체여야 합니다.");
    return;
  }
  await run(async () => {
    editorNode = await api.patchNode(editorNode.id, {
      title: $("node-title").value,
      logline: $("node-logline").value,
      body,
    }).then(() => api.node(editorNode.id));
    $("node-save-info").textContent = "저장됨";
    await reloadNodes();
    renderTree();
    renderEditor();
  });
}

async function toggleArchive() {
  if (!editorNode) return;
  await run(async () => {
    editorNode = await api.archiveNode(editorNode.id, !editorNode.archived)
      .then(() => api.node(editorNode.id));
    await reloadNodes();
    renderTree();
    renderEditor();
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
  setPartnerCollapsed(localStorage.getItem("design-partner-collapsed") === "1");
  $("btn-partner-collapse").addEventListener("click", () => setPartnerCollapsed(true));
  $("btn-partner-expand").addEventListener("click", () => setPartnerCollapsed(false));
  document.querySelectorAll("#tree-mode button").forEach((b) =>
    b.addEventListener("click", () => setTreeMode(b.dataset.mode)));
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
  $("btn-node-save").addEventListener("click", saveEditor);
  $("btn-node-archive").addEventListener("click", toggleArchive);
  $("btn-write-jump").addEventListener("click", writeJump);
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
