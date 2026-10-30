import { api } from "./api.js";
import { $, esc, showError } from "./util.js";
import { getWork } from "./work.js";

// 화면 · 아이디어/구조. 채팅(좌) + 전체 줄거리(우상) + 설정정보(우하).
// 줄거리는 직접 inline 편집 + AI 제안 반영, 설정은 AI 답변의 제안 버튼으로 반영된다.
let threads = [];
let threadId = null;
let messages = [];
let synopsis = { content: "", version: 0 };
let synEditing = false;
let synDraft = "";
let synSaving = false;
let settings = [];
let busy = false;
let loadedUid = null;
let pendingHandoffPlan = null;
let includedMessages = new Set();
let includedSettings = new Set();
let expandedSettings = new Set();
let msgMode = "select";
let sending = false;
let structure = { use_part: false, use_chapter: false, total_episodes: 25, parts: [] };
let expandedParts = new Set();

const EPISODE_LIMIT = 300;

const CATEGORIES = ["character", "world", "event", "part", "misc"];
const CAT_LABEL = {
  character: "인물", world: "세계관", event: "사건", part: "Part 이야기", misc: "기타",
};

function mode() {
  return localStorage.getItem("ideas-mode") || "one";
}
function setMode(m) {
  localStorage.setItem("ideas-mode", m);
  renderSettings();
}

function partMode() {
  const m = localStorage.getItem("part-mode");
  return m === "flow" || m === "full" ? m : "one";
}
function setPartMode(m) {
  localStorage.setItem("part-mode", m);
  renderParts();
}

function locked() {
  return !!(getWork() && getWork().ideas_locked);
}

async function run(fn) {
  if (busy) return;
  busy = true;
  showError("ideas-error", "");
  try {
    await fn();
  } catch (e) {
    showError("ideas-error", String(e));
  } finally {
    busy = false;
  }
}

// ---- 진입/적재 ----

export async function enterIdeas() {
  const w = getWork();
  if (!w) return;
  if (loadedUid !== (w.uid || w.id)) {
    loadedUid = w.uid || w.id;
    threadId = null;
    synEditing = false;
    synDraft = "";
    synSaving = false;
    expandedParts = new Set();
  }
  await run(async () => {
    const t = await api.threads(w.uid || w.id);
    threads = t.threads ?? [];
    if (!threads.length && !locked()) {
      const nt = await api.createThread(w.uid || w.id, "대화 1");
      threads = [nt];
    }
    if (!threads.length) {
      threadId = null;
      messages = [];
    } else {
      if (!threadId || !threads.some((x) => x.id === threadId)) {
        threadId = threads[threads.length - 1].id;
      }
      messages = (await api.messages(threadId)).messages ?? [];
    }
    synopsis = await api.synopsis(w.uid || w.id);
    settings = (await api.settings(w.uid || w.id)).settings ?? [];
    structure = await api.getStructure(w.uid || w.id);
    resetInclusionDefaults();
    renderAll();
  });
}

function updateMsgModeSeg() {
  document.querySelectorAll("#msg-mode button").forEach((b) =>
    b.classList.toggle("active", b.dataset.mmode === msgMode));
}

// 포함 기본값: 줄거리·구조 체크, 설정 전부 언체크, 대화는 마지막 2개(직전 질문+답변) 체크.
// 화면 진입과 AI 응답 반환 시점에 리셋한다. 이후에는 자유롭게 수정.
function resetInclusionDefaults() {
  $("syn-include").checked = true;
  $("struct-include").checked = true;
  includedSettings = new Set();
  const ids = messages.map((m) => m.id);
  includedMessages = new Set(ids.slice(-2));
  msgMode = "select";
  updateMsgModeSeg();
}

function renderAll() {
  renderThreads();
  renderChat();
  renderSynopsis();
  renderStructure();
  renderSettings();
  renderHandoff();
  const banner = $("ideas-locked");
  if (banner) banner.style.display = locked() ? "" : "none";
  document.querySelectorAll("#ideas input, #ideas textarea, #ideas select, #ideas button:not([data-collapse])")
    .forEach((el) => {
      if (el.closest("#ideas-locked")) return;
      if (el.id === "thread-select" || el.id === "struct-toggle") return;
      if (locked()) el.disabled = true;
      else if (!busy) el.disabled = false;
    });
}

// ---- 스레드/채팅 ----

function renderThreads() {
  const sel = $("thread-select");
  sel.innerHTML = threads.map((t) =>
    `<option value="${t.id}"${t.id === threadId ? " selected" : ""}>${esc(t.title || `대화 ${t.id}`)}</option>`).join("");
}

function normCategory(c) {
  return CATEGORIES.includes(c) ? c : "misc";
}

function propTitle(p) {
  const t = (p.title || "").trim();
  if (t) return t;
  const head = (p.summary || "").trim().split("\n")[0].slice(0, 30).trim();
  return head || "무제";
}

function findSetting(p) {
  const title = propTitle(p);
  return settings.find((s) => s.category === normCategory(p.category) && s.title === title);
}

function sameContent(s, p) {
  return (s.summary || "") === (p.summary || "") && (s.detail || "") === (p.detail || "");
}

function tipAttr(text) {
  const t = ((text || "").trim() || "(내용 없음)").slice(0, 300);
  return ` data-tip="${esc(t).replace(/\n/g, "&#10;")}"`;
}

function isSynApplied(proposal) {
  return !!proposal && (synopsis.content || "") === proposal;
}

function proposalsHtml(m) {
  const props = m.proposals ?? {};
  const sps = props.settings ?? [];
  const syn = props.synopsis_proposal || "";
  const st = (props.structure && typeof props.structure === "object") ? props.structure : null;
  const hasStruct = st && ["use_part", "use_chapter", "total_episodes", "parts"]
    .some((k) => st[k] !== null && st[k] !== undefined);
  if (!sps.length && !syn && !hasStruct) return "";
  const settingBtns = sps.map((p, i) => {
    const title = propTitle(p);
    const cat = CAT_LABEL[normCategory(p.category)];
    const tip = tipAttr(`${p.summary || ""}\n${p.detail || ""}`);
    const s = findSetting(p);
    if (!s) {
      return `<button class="btn sm proposal-btn" data-msg="${m.id}" data-sp="${i}"${tip}>＋ [${esc(cat)}] ${esc(title)}</button>`;
    }
    if (sameContent(s, p)) {
      return `<button class="btn sm proposal-btn" data-msg="${m.id}" data-sp="${i}" disabled>✓ 반영됨</button>`;
    }
    return `<button class="btn sm proposal-btn" data-msg="${m.id}" data-sp="${i}"${tip}>'${esc(title)}' 수정 반영</button>`;
  }).join("");
  const synBtn = syn
    ? `<button class="btn sm proposal-btn syn" data-msg="${m.id}" data-syn="1"${isSynApplied(syn) ? " disabled" : ""}${isSynApplied(syn) ? "" : tipAttr(syn)}>${isSynApplied(syn) ? "✓ 줄거리 반영됨" : "줄거리 변경 반영"}</button>`
    : "";
  const structBtn = hasStruct
    ? `<button class="btn sm proposal-btn" data-msg="${m.id}" data-struct="1"${isStructApplied(st) ? " disabled" : ""}${isStructApplied(st) ? "" : structTip(st)}>${isStructApplied(st) ? "✓ 구조 반영됨" : `구조에 반영 (${structBits(st).join("·")})`}</button>`
    : "";
  const pendingSettings = sps.some((p) => {
    const s = findSetting(p);
    return !s || !sameContent(s, p);
  });
  const pendingSyn = !!syn && !isSynApplied(syn);
  const pendingStruct = !!hasStruct && !isStructApplied(st);
  const allDone = !pendingSettings && !pendingSyn && !pendingStruct;
  const allBtn = `<button class="btn sm proposal-btn" data-msg="${m.id}" data-apply-all title="설정·줄거리·구조 제안을 한꺼번에 적용합니다 (AI 호출 없음)"${allDone ? " disabled" : ""}>${allDone ? "✓ 모두 반영됨" : "모두 반영"}</button>`;
  return `<div class="proposals"><div class="proposals-head"><span class="small muted"><b>바로 반영</b></span>${allBtn}</div>
    <div class="btn-row" style="margin-top:6px">${settingBtns}${synBtn}${structBtn}</div></div>`;
}

let tipEl = null;

function ensureTip() {
  if (!tipEl) {
    tipEl = document.createElement("div");
    tipEl.id = "proposal-tip";
    tipEl.style.display = "none";
    document.body.appendChild(tipEl);
  }
  return tipEl;
}

function moveTip(e) {
  const tip = ensureTip();
  const w = 280;
  const h = tip.offsetHeight || 120;
  let x = e.clientX + 14;
  let y = e.clientY + 16;
  if (x + w > window.innerWidth - 8) x = window.innerWidth - w - 8;
  if (y + h > window.innerHeight - 8) y = e.clientY - h - 12;
  tip.style.left = `${Math.max(8, x)}px`;
  tip.style.top = `${Math.max(8, y)}px`;
}

function hideTip() {
  if (tipEl) tipEl.style.display = "none";
}

function renderChat() {
  hideTip();
  const box = $("chat-messages");
  if (!messages.length) {
    box.innerHTML = `<span class="muted small">AI에게 소재·줄거리·설정을 물어보세요. 예: “달빛 서점을 배경으로 한 추리물을 구상 중이야.”</span>`;
  } else {
    box.innerHTML = messages.map((m) => {
      const chk = `<input type="checkbox" class="inc" data-inc-msg="${m.id}"${includedMessages.has(m.id) ? " checked" : ""} title="대화를 메시지에 포함">`;
      if (m.role !== "assistant") {
        return `<div class="msg user">${chk}<div class="bubble">${esc(m.content).replace(/\n/g, "<br>")}</div></div>`;
      }
      return `<div class="msg assistant">${chk}<div class="msg-main"><div class="bubble">${esc(m.content).replace(/\n/g, "<br>")}</div>${proposalsHtml(m)}</div></div>`;
    }).join("");
  }
  if (sending) {
    box.insertAdjacentHTML("beforeend",
      `<div class="msg assistant"><div class="typing" aria-label="AI 답변 생성 중"><span></span><span></span><span></span></div></div>`);
  }
  box.scrollTop = box.scrollHeight;
  box.querySelectorAll("[data-inc-msg]").forEach((c) => {
    c.addEventListener("change", () => {
      const id = Number(c.dataset.incMsg);
      if (!Number.isFinite(id)) return;
      if (c.checked) includedMessages.add(id);
      else includedMessages.delete(id);
      msgMode = "select";
      updateMsgModeSeg();
    });
  });
  box.querySelectorAll("[data-sp]").forEach((b) => {
    b.addEventListener("click", () => applySettingProposal(Number(b.dataset.msg), Number(b.dataset.sp)));
  });
  box.querySelectorAll("[data-syn]").forEach((b) => {
    b.addEventListener("click", () => applySynProposal(Number(b.dataset.msg)));
  });
  box.querySelectorAll("[data-struct]").forEach((b) => {
    b.addEventListener("click", () => applyStructProposal(Number(b.dataset.msg)));
  });
  box.querySelectorAll("[data-apply-all]").forEach((b) => {
    b.addEventListener("click", () => applyAllProposals(Number(b.dataset.msg)));
  });
  const last = [...messages].reverse().find((m) => m.role === "assistant");
  const choices = (last && last.id === messages[messages.length - 1]?.id)
    ? (last.choices ?? []) : [];
  $("choice-row").innerHTML = choices.length
    ? `<div class="small muted" style="width:100%"><b>대화 이어가기</b> — 누르면 AI에게 전송됩니다</div>` + choices.map((c, i) =>
      `<button class="btn sm" data-choice="${i}">${esc(c)}</button>`).join("")
    : "";
  $("choice-row").querySelectorAll("[data-choice]").forEach((b) => {
    b.addEventListener("click", () => {
      $("chat-input").value = choices[Number(b.dataset.choice)];
      sendChat();
    });
  });
}

function parseMentions(text) {
  const out = [];
  const re = /@([^\s@,]+)/g;
  let m;
  while ((m = re.exec(text))) {
    const tok = m[1].replace(/[.,!?;:)\]}>"']+$/, "");
    if (tok) out.push(tok);
  }
  return [...new Set(out)];
}

async function sendChat() {
  const input = $("chat-input");
  const content = input.value.trim();
  if (!content || busy || sending || !threadId) return;
  input.value = "";
  const sentTid = threadId;
  const pending = { id: `pending-${Date.now()}`, role: "user", content };
  sending = true;
  messages.push(pending);
  renderChat();
  const send = $("btn-chat-send");
  const orig = send.textContent;
  send.textContent = "처리 중…";
  await run(async () => {
    const out = await api.postMessage(sentTid, content, parseMentions(content), null, {
      include_synopsis: $("syn-include").checked,
      include_settings: [...includedSettings],
      include_messages: messages.filter((m) => includedMessages.has(m.id)).map((m) => m.id),
      include_structure: $("struct-include").checked,
    });
    if (sentTid !== threadId) return;
    sending = false;
    const i = messages.findIndex((m) => m.id === pending.id);
    if (i >= 0) messages.splice(i, 1, out.user, out.assistant);
    else messages.push(out.user, out.assistant);
    resetInclusionDefaults();
    renderChat();
    renderSettings();
  });
  send.textContent = orig;
  if (sending && sentTid === threadId) {
    // AI 호출 실패: 사용자 메시지는 서버에 저장되어 있으므로 동기화
    sending = false;
    try {
      messages = (await api.messages(threadId)).messages ?? [];
    } catch {
      messages = messages.filter((m) => m.id !== pending.id);
    }
    resetInclusionDefaults();
    renderChat();
  } else if (sending) {
    sending = false;
  }
}

async function applySettingProposal(msgId, idx) {
  const m = messages.find((x) => x.id === msgId);
  const p = m?.proposals?.settings?.[idx];
  if (!p) return;
  const s = findSetting(p);
  if (s && sameContent(s, p)) return;
  await run(async () => {
    const key = getWork().uid || getWork().id;
    if (s) {
      await api.patchSetting(s.id, { summary: p.summary || "", detail: p.detail || "" });
    } else {
      await api.createSetting(key, {
        category: normCategory(p.category),
        title: propTitle(p),
        summary: p.summary || "",
        detail: p.detail || "",
        status: "applied",
      });
    }
    settings = (await api.settings(key)).settings ?? [];
    renderSettings();
    renderChat();
  });
}

async function applySynProposal(msgId) {
  const m = messages.find((x) => x.id === msgId);
  const proposal = m?.proposals?.synopsis_proposal || "";
  if (!proposal || isSynApplied(proposal)) return;
  synEditing = false;
  await run(async () => {
    synopsis = await api.putSynopsis(getWork().uid || getWork().id, proposal);
    renderSynopsis();
    renderChat();
  });
}

// ---- 전체 줄거리 (inline 편집: 클릭→편집, 포커스 이동→자동 저장) ----

function renderSynopsis() {
  $("synopsis-meta").textContent = `v${synopsis.version} · ${synopsis.updated_at ? String(synopsis.updated_at).slice(0, 16).replace("T", " ") : "-"}`;
  const hint = $("synopsis-edit-hint");
  if (hint) hint.style.display = locked() ? "none" : "";
  const view = $("synopsis-view");
  if (synEditing && !locked()) {
    view.classList.add("editing");
    view.classList.remove("editable");
    view.removeAttribute("title");
    view.innerHTML = "";
    const ta = document.createElement("textarea");
    ta.id = "synopsis-edit";
    ta.value = synDraft;
    ta.placeholder = "전체 줄거리를 입력하세요.";
    const autoresize = () => {
      ta.style.height = "auto";
      ta.style.height = `${ta.scrollHeight}px`;
    };
    ta.addEventListener("input", () => {
      synDraft = ta.value;
      autoresize();
    });
    ta.addEventListener("blur", () => {
      saveSynopsisEdit();
    });
    ta.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Escape") {
        synEditing = false;
        renderSynopsis();
      }
    });
    view.appendChild(ta);
    const sub = document.createElement("div");
    sub.className = "small muted synopsis-hint";
    sub.textContent = synSaving ? "저장 중…" : "편집 후 다른 곳을 클릭하면 자동 저장됩니다 (Esc: 취소)";
    view.appendChild(sub);
    autoresize();
    if (document.activeElement !== ta) {
      ta.focus();
      ta.selectionStart = ta.selectionEnd = ta.value.length;
    }
    return;
  }
  view.classList.remove("editing");
  const canEdit = !locked();
  view.classList.toggle("editable", canEdit);
  if (canEdit) view.title = "클릭하여 편집";
  else view.removeAttribute("title");
  view.innerHTML = synopsis.content
    ? esc(synopsis.content).replace(/\n/g, "<br>")
    : `<span class="muted">아직 줄거리가 없습니다. 클릭하여 직접 쓰거나, AI와 대화한 뒤 제안 버튼을 눌러 반영하세요.</span>`;
}

function enterSynopsisEdit() {
  if (synEditing || synSaving || locked()) return;
  synEditing = true;
  synDraft = synopsis.content || "";
  renderSynopsis();
}

// blur 시 자동 저장. AI 대화 중에도 동작해야 하므로 busy와 무관한 독립 저장이다.
async function saveSynopsisEdit() {
  if (!synEditing || synSaving) return;
  const next = synDraft;
  if (next === (synopsis.content || "")) {
    synEditing = false;
    renderSynopsis();
    return;
  }
  const key = getWork().uid || getWork().id;
  synSaving = true;
  const sub = document.querySelector("#synopsis-view .synopsis-hint");
  if (sub) sub.textContent = "저장 중…";
  try {
    showError("ideas-error", "");
    for (;;) {
      const target = synDraft;
      if (target === (synopsis.content || "")) break;
      const saved = await api.putSynopsis(key, target);
      if ((getWork()?.uid || getWork()?.id) !== key) return;
      synopsis = saved;
      if (synDraft === (saved.content || "")) break;
      // 저장 중에 이어서 입력된 내용이 있으면 한 번 더 저장한다.
    }
    synEditing = false;
    renderSynopsis();
    renderChat();
  } catch (e) {
    showError("ideas-error", String(e));
    renderSynopsis();
  } finally {
    synSaving = false;
  }
}

// ---- 작품 구조 ----

const ST_LEVELS = {
  part: { label: "Part 사용", use_part: true, use_chapter: true },
  chapter: { label: "Chapter만 사용", use_part: false, use_chapter: true },
  none: { label: "구조 미사용", use_part: false, use_chapter: false },
};

function levelOf(s) {
  if (s.use_part) return "part";
  if (s.use_chapter) return "chapter";
  return "none";
}

function renderStructure() {
  renderStructureControls();
  renderParts();
}

function renderStructureControls() {
  setControlValues();
  $("struct-info").textContent = "";
  updateStructSummary();
  applyStructCollapsed();
}

// 자동저장 완료 후에도 입력 포커스가 날아가지 않도록 바뀐 값만 반영한다.
function setControlValues() {
  const lv = levelOf(structure);
  if ($("st-level").value !== lv) $("st-level").value = lv;
  const tv = String(structure.total_episodes ?? 25);
  if ($("st-total").value !== tv) $("st-total").value = tv;
}

function updateStructSummary() {
  const lv = ST_LEVELS[levelOf(structure)];
  const n = (structure.parts ?? []).length;
  $("struct-summary").textContent =
    `${lv.label}, ${structure.total_episodes ?? 25}화${n ? ` · Part ${n}개` : ""}`;
}

function isStructCollapsed() {
  return localStorage.getItem("struct-collapsed") === "1";
}

function applyStructCollapsed() {
  const collapsed = isStructCollapsed();
  $("struct-body").style.display = collapsed ? "none" : "";
  $("struct-summary").style.display = collapsed ? "" : "none";
  $("struct-toggle").textContent = `${collapsed ? "▸" : "▾"} 작품 구조`;
  const cp = $("struct-collapsed-parts");
  if (collapsed) {
    cp.style.display = "";
    cp.innerHTML = (structure.parts ?? []).map((p, i) =>
      `<div class="cpart"><span class="pnum">${i + 1}</span> <b>${esc(p.title || "무제")}</b> - ${esc(p.summary || "")}</div>`).join("");
  } else {
    cp.style.display = "none";
    cp.innerHTML = "";
  }
}

function partRowHtml(p, i) {
  const m = partMode();
  const expanded = m !== "full" && expandedParts.has(i);
  const title = (p.title || "").trim() || "무제";
  if (m === "one" && !expanded) {
    return `<div class="part one-line" data-part="${i}"><span class="pnum">${i + 1}</span><span class="pline"><b>${esc(title)}</b> ${esc(p.summary || "")}</span></div>`;
  }
  if (m === "flow" && !expanded) {
    return `<div class="part" data-part="${i}">
      <div class="part-head"><span class="pnum">${i + 1}</span><b>${esc(title)}</b></div>
      ${p.summary ? `<div class="small">${esc(p.summary).replace(/\n/g, "<br>")}</div>` : ""}
    </div>`;
  }
  return `<div class="part editing" data-part="${i}">
    <div class="part-title-row"><span class="pnum">${i + 1}</span><input type="text" data-f="title" value="${esc(p.title || "")}" placeholder="Part 제목"></div>
    <textarea data-f="summary" rows="3" placeholder="개략 요약">${esc(p.summary || "")}</textarea>
    <div class="btn-row"><button class="btn sm" data-del>삭제</button>${expanded ? `<button class="btn sm" data-collapse>접기</button>` : ""}</div>
  </div>`;
}

function renderParts() {
  document.querySelectorAll("#part-mode button").forEach((b) =>
    b.classList.toggle("active", b.dataset.pmode === partMode()));
  const box = $("part-list");
  box.dataset.mode = partMode();
  const parts = structure.parts ?? [];
  box.innerHTML = parts.map(partRowHtml).join("") || `<span class="muted small">Part 없음</span>`;
  box.querySelectorAll("[data-part]").forEach((row) => {
    const i = Number(row.dataset.part);
    row.querySelector("[data-del]")?.addEventListener("click", () => deletePart(i));
    row.querySelector("[data-collapse]")?.addEventListener("click", () => {
      expandedParts.delete(i);
      renderParts();
    });
    if (partMode() !== "full" && !expandedParts.has(i)) {
      row.classList.add("expandable");
      row.addEventListener("click", (e) => {
        if (e.target.closest("input, button, textarea, select, a")) return;
        expandedParts.add(i);
        renderParts();
      });
    } else {
      row.classList.add("expanded");
    }
  });
  if (locked()) {
    box.querySelectorAll("input, textarea, button:not([data-collapse])").forEach((el) => {
      el.disabled = true;
    });
  }
}

function deletePart(i) {
  structure.parts = collectParts();
  structure.parts.splice(i, 1);
  const next = new Set();
  expandedParts.forEach((x) => {
    if (x < i) next.add(x);
    else if (x > i) next.add(x - 1);
  });
  expandedParts = next;
  renderParts();
  scheduleStructureSave();
}

// 저장 모델(structure.parts)이 기준이다. 화면에 편집 칸이 있으면 그 값을 쓰고,
// 보기 전용(한줄·이어서) 행은 저장 모델 값을 그대로 둔다. 입력 이벤트마다
// 저장 모델에 실시간 반영하므로 모드 전환·자동저장 시점에 내용이 지워지지 않는다.
function collectParts() {
  const out = (structure.parts ?? []).map((p) => ({
    title: p.title ?? "",
    summary: p.summary ?? "",
  }));
  $("part-list").querySelectorAll("[data-part]").forEach((row) => {
    const i = Number(row.dataset.part);
    if (!Number.isFinite(i) || i < 0 || i >= out.length) return;
    const t = row.querySelector('[data-f="title"]');
    const s = row.querySelector('[data-f="summary"]');
    if (t) out[i].title = t.value;
    if (s) out[i].summary = s.value;
  });
  return out;
}

function collectStructure() {
  const lv = ST_LEVELS[$("st-level").value] ?? ST_LEVELS.part;
  return {
    use_part: lv.use_part,
    use_chapter: lv.use_chapter,
    total_episodes: Number($("st-total").value),
    parts: collectParts(),
  };
}

function totalError(total, where) {
  if (!Number.isInteger(total) || total < 1) {
    return `총 화수는 1~${EPISODE_LIMIT} 사이의 정수여야 합니다 (${where}: ${total}).`;
  }
  if (total > EPISODE_LIMIT) {
    return `총 화수 상한(${EPISODE_LIMIT}화)을 초과했습니다 (${where}: ${total}화).`;
  }
  return "";
}

let structSaveTimer = null;
let structSaveSeq = 0;
const STRUCT_SAVE_DELAY = 700;

function scheduleStructureSave() {
  if (locked()) return;
  $("struct-info").textContent = "입력 중…";
  clearTimeout(structSaveTimer);
  structSaveTimer = setTimeout(flushStructureSave, STRUCT_SAVE_DELAY);
}

async function flushStructureSave() {
  clearTimeout(structSaveTimer);
  structSaveTimer = null;
  if (locked()) return;
  if (busy) {
    structSaveTimer = setTimeout(flushStructureSave, 600);
    return;
  }
  const body = collectStructure();
  const err = totalError(body.total_episodes, "입력값");
  if (err) {
    $("struct-info").textContent = `${err} ${EPISODE_LIMIT}화 이하로 입력하세요.`;
    return;
  }
  const seq = ++structSaveSeq;
  $("struct-info").textContent = "저장 중…";
  await run(async () => {
    const auto = normalizeStructure(body);
    const saved = await api.putStructure(getWork().uid || getWork().id, body);
    if (seq !== structSaveSeq) return;
    structure = saved;
    setControlValues();
    updateStructSummary();
    applyStructCollapsed();
    $("struct-info").textContent = auto ? "저장됨 (Part 사용을 함께 켰습니다)" : "저장됨";
  });
}

function mergeStructure(prop) {
  const out = JSON.parse(JSON.stringify(structure));
  for (const k of ["use_part", "use_chapter", "total_episodes"]) {
    if (prop[k] !== null && prop[k] !== undefined) out[k] = prop[k];
  }
  if (prop.parts !== null && prop.parts !== undefined) {
    out.parts = prop.parts.map((p) => ({ title: (p.title || "").slice(0, 200),
                                          summary: (p.summary || "").slice(0, 2000) }));
  }
  return out;
}

// 정합성: Part 목록이 있으면 Part 사용이 켜져 있어야 한다.
function normalizeStructure(obj) {
  if ((obj.parts ?? []).length && !obj.use_part) {
    obj.use_part = true;
    return true;
  }
  return false;
}

function isStructApplied(prop) {
  const m = mergeStructure(prop);
  normalizeStructure(m);
  return JSON.stringify(m) === JSON.stringify(structure);
}

function structBits(prop) {
  const bits = [];
  if (prop.use_part !== null && prop.use_part !== undefined) {
    bits.push(`Part${prop.use_part ? "사용" : "미사용"}`);
  }
  if (prop.use_chapter !== null && prop.use_chapter !== undefined) {
    bits.push(`Chapter${prop.use_chapter ? "사용" : "미사용"}`);
  }
  if (prop.total_episodes !== null && prop.total_episodes !== undefined) {
    bits.push(`${prop.total_episodes}화`);
  }
  if (prop.parts !== null && prop.parts !== undefined) {
    bits.push(`P${prop.parts.length}개`);
  }
  return bits;
}

function structTip(prop) {
  const bits = [];
  if (prop.use_part !== null && prop.use_part !== undefined) {
    bits.push(`Part ${prop.use_part ? "사용" : "미사용"}`);
  }
  if (prop.use_chapter !== null && prop.use_chapter !== undefined) {
    bits.push(`Chapter ${prop.use_chapter ? "사용" : "미사용"}`);
  }
  if (prop.total_episodes !== null && prop.total_episodes !== undefined) {
    bits.push(`총 ${prop.total_episodes}화`);
  }
  if (prop.parts !== null && prop.parts !== undefined) {
    bits.push(`Part ${prop.parts.length}개`);
    prop.parts.forEach((p) => bits.push(`- ${p.title || "무제"}: ${p.summary || ""}`));
  }
  return tipAttr(bits.join("\n"));
}

async function applyStructProposal(msgId) {
  const m = messages.find((x) => x.id === msgId);
  const prop = m?.proposals?.structure;
  if (!prop || isStructApplied(prop)) return;
  await run(async () => {
    const merged = mergeStructure(prop);
    const auto = normalizeStructure(merged);
    const err = totalError(merged.total_episodes, "AI 제안");
    if (err) {
      showError("ideas-error", `${err} AI에게 ${EPISODE_LIMIT}화 이하로 다시 제안해 달라고 물어보세요.`);
      return;
    }
    structure = await api.putStructure(getWork().uid || getWork().id, merged);
    expandedParts = new Set();
    renderStructure();
    renderChat();
    if (auto) $("struct-info").textContent = "반영됨 (Part 사용을 함께 켰습니다)";
  });
}

async function applyAllProposals(msgId) {
  const m = messages.find((x) => x.id === msgId);
  const n = m?.proposals?.settings?.length ?? 0;
  for (let i = 0; i < n; i++) {
    await applySettingProposal(msgId, i);
  }
  await applySynProposal(msgId);
  await applyStructProposal(msgId);
}

export async function exportIdeasMarkdown() {
  const w = getWork();
  if (!w) return;
  const key = w.uid || w.id;
  const [syn, st, struct] = await Promise.all(
    [api.synopsis(key), api.settings(key), api.getStructure(key)]);
  const lines = [`# ${w.title} — 아이디어 정리`, ""];
  lines.push("## 전체 줄거리", "", syn.content || "(없음)", "");
  lines.push("## 작품 구조", "",
    `Part ${struct.use_part ? "사용" : "미사용"} · Chapter ${struct.use_chapter ? "사용" : "미사용"} · 총 ${struct.total_episodes}화`, "");
  for (const p of struct.parts ?? []) {
    lines.push(`- ${p.title}: ${p.summary}`);
  }
  if ((struct.parts ?? []).length) lines.push("");
  lines.push("## 설정정보", "");
  for (const s of st.settings ?? []) {
    lines.push(`### [${CAT_LABEL[s.category] ?? s.category}] ${s.title}`,
      "", s.summary || "", "", s.detail || "", "");
  }
  const blob = new Blob([lines.join("\n")], { type: "text/markdown" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${w.title}-ideas.md`;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ---- 설정정보 ----

function settingRowHtml(s) {
  const m = mode();
  const expanded = m !== "full" && expandedSettings.has(s.id);
  const inc = `<input type="checkbox" class="inc" data-inc-setting="${s.id}"${includedSettings.has(s.id) ? " checked" : ""} title="대화를 메시지에 포함">`;
  const codeChip = s.code ? `<span class="chip mono"${expanded ? ' data-collapse-chip title="클릭하면 원래 보기(한줄·이어서)로 접기"' : ""}>${esc(s.code)}</span>` : "";
  const chips = `${codeChip}<span class="chip">${esc(CAT_LABEL[s.category] ?? s.category)}</span>`;
  if (m === "one" && !expanded) {
    const text = s.summary || (s.detail || "").split("\n")[0] || "";
    return `<div class="setting one-line" data-setting="${s.id}">${inc}${chips}<span class="sline"><b>${esc(s.title)}</b> ${esc(text)}</span></div>`;
  }
  if (m === "flow" && !expanded) {
    return `<div class="setting" data-setting="${s.id}">
      <div class="setting-head">${inc}${chips}<b>${esc(s.title)}</b></div>
      ${s.summary ? `<div class="small">${esc(s.summary).replace(/\n/g, "<br>")}</div>` : ""}
      ${s.detail ? `<div class="small muted">${esc(s.detail).replace(/\n/g, "<br>")}</div>` : ""}
    </div>`;
  }
  return `<div class="setting" data-setting="${s.id}">
    <div class="setting-head">${inc}${chips}<b>${esc(s.title)}</b></div>
    ${s.summary ? `<div class="small">${esc(s.summary).replace(/\n/g, "<br>")}</div>` : ""}
    ${s.detail ? `<div class="small muted">${esc(s.detail).replace(/\n/g, "<br>")}</div>` : ""}
    <div class="btn-row">
      <button class="btn sm" data-edit>편집</button>
      <button class="btn sm" data-del>삭제</button>
    </div>
    <div class="setting-edit"></div>
  </div>`;
}

function syncSettingAll() {
  const all = $("setting-all");
  if (!all) return;
  all.checked = settings.length > 0 && settings.every((s) => includedSettings.has(s.id));
  all.indeterminate = !all.checked && settings.some((s) => includedSettings.has(s.id));
}

function renderSettings() {
  document.querySelectorAll("#setting-mode button").forEach((b) =>
    b.classList.toggle("active", b.dataset.mode === mode()));
  const box = $("setting-list");
  box.dataset.mode = mode();
  if (!settings.length) {
    box.innerHTML = `<span class="muted small">설정이 없습니다. AI와 대화한 뒤 제안 버튼을 눌러 반영하세요.</span>`;
    syncSettingAll();
    return;
  }
  box.innerHTML = settings.map(settingRowHtml).join("");
  box.querySelectorAll(".setting").forEach((el) => {
    const id = Number(el.dataset.setting);
    const s = settings.find((x) => x.id === id);
    el.querySelector("[data-inc-setting]").addEventListener("change", (e) => {
      if (e.target.checked) includedSettings.add(id);
      else includedSettings.delete(id);
      syncSettingAll();
    });
    el.querySelector("[data-edit]")?.addEventListener("click", () => openSettingEditor(el, s));
    el.querySelector("[data-del]")?.addEventListener("click", () => deleteSettingFlow(s));
    if (mode() !== "full") {
      if (expandedSettings.has(id)) {
        el.classList.add("expanded");
        el.querySelector("[data-collapse-chip]")?.addEventListener("click", (e) => {
          e.stopPropagation();
          expandedSettings.delete(id);
          renderSettings();
        });
        el.querySelector(".btn-row")?.insertAdjacentHTML("beforeend", `<button class="btn sm" data-collapse>접기</button>`);
        el.querySelector("[data-collapse]")?.addEventListener("click", () => {
          expandedSettings.delete(id);
          renderSettings();
        });
      } else {
        el.classList.add("expandable");
        el.addEventListener("click", (e) => {
          if (e.target.closest("input, button, textarea, select, a")) return;
          expandedSettings.add(id);
          renderSettings();
        });
      }
    }
  });
  syncSettingAll();
}

function openSettingEditor(el, s) {
  const box = el.querySelector(".setting-edit");
  box.innerHTML = `
    <label class="f">분류</label>
    <select data-f="category">${CATEGORIES.map((c) =>
      `<option value="${c}"${s.category === c ? " selected" : ""}>${CAT_LABEL[c]}</option>`).join("")}</select>
    <label class="f">제목</label><input type="text" data-f="title" value="${esc(s.title)}">
    <label class="f">요약</label><textarea data-f="summary">${esc(s.summary)}</textarea>
    <label class="f">상세</label><textarea data-f="detail">${esc(s.detail)}</textarea>
    <div class="btn-row"><button class="btn sm primary" data-save>저장</button>
    <button class="btn sm" data-cancel>취소</button></div>`;
  box.querySelector("[data-cancel]").addEventListener("click", () => { box.innerHTML = ""; });
  box.querySelector("[data-save]").addEventListener("click", () => run(async () => {
    const data = {
      category: box.querySelector('[data-f="category"]').value,
      title: box.querySelector('[data-f="title"]').value,
      summary: box.querySelector('[data-f="summary"]').value,
      detail: box.querySelector('[data-f="detail"]').value,
    };
    await api.patchSetting(s.id, data);
    settings = (await api.settings(getWork().uid || getWork().id)).settings ?? [];
    renderSettings();
    renderChat();
  }));
}

async function deleteSettingFlow(s) {
  await run(async () => {
    await api.deleteSetting(s.id);
    settings = (await api.settings(getWork().uid || getWork().id)).settings ?? [];
    renderSettings();
    renderChat();
  });
}

// ---- 인계 ----

function renderHandoff() {
  $("btn-handoff").textContent = "아이디어 마무리 → 소설설계로";
}

function handoffTreeHtml(nodes) {
  return `<ul class="ho-tree small">` + nodes.map((n) => {
    let label;
    if (n.kind === "episode") {
      label = `${n.episode_no}화 ${esc(n.title)}` +
        (n.stub ? ` <span class="muted">(빈 회차로 자동 생성)</span>`
          : (n.summary ? ` — ${esc(n.summary)}` : ""));
    } else {
      const lv = n.kind === "part" ? "Part" : "Chapter";
      label = `<b>${lv} ${esc(n.title)}</b>` +
        (n.summary ? ` — ${esc(n.summary)}` : "");
    }
    const kids = (n.children && n.children.length) ? handoffTreeHtml(n.children) : "";
    return `<li>${label}${kids}</li>`;
  }).join("") + `</ul>`;
}

function handoffPreviewHtml(p) {
  const counts = p.counts ?? {};
  const aiEps = (counts.episodes ?? 0) - (counts.stub_episodes ?? 0);
  const bits = [];
  if (counts.parts) bits.push(`Part ${counts.parts}개`);
  if (counts.chapters) bits.push(`Chapter ${counts.chapters}개`);
  bits.push(`Episode ${counts.episodes ?? 0}화`);
  const attached = (p.settings ?? []).map((s) =>
    `<span class="chip mono">${esc(s.code || "")}</span> ${esc(s.title)}`).join(" · ");
  return `
    <p class="small">AI가 다음과 같이 설계로 옮깁니다. 확정하면 그대로 생성되며, 인계 후 이 아이디어 화면은 <b>읽기 전용</b>이 됩니다.</p>
    <p class="small"><b>설계 v${p.design_version}</b> · ${esc((p.levels ?? []).join(" → "))} · ${bits.join(" · ")} (AI 작성 ${aiEps}화 · 빈 회차 ${counts.stub_episodes ?? 0}화)</p>
    <p class="small"><b>작품(최상위) 노드에 함께 저장</b> — 전체 줄거리 전문(${(p.synopsis_chars ?? 0).toLocaleString()}자) · 설정 ${(p.settings ?? []).length}개${attached ? `<br>${attached}` : ""}</p>
    ${handoffTreeHtml(p.tree ?? [])}`;
}

async function handoffFlow() {
  const key = getWork().uid || getWork().id;
  const btn = $("btn-handoff");
  btn.disabled = true;
  btn.textContent = "AI가 인계 계획 생성 중…";
  try {
    await run(async () => {
      const p = await api.handoffPreview(key);
      pendingHandoffPlan = p.plan ?? { tree: [] };
      $("handoff-preview").innerHTML = handoffPreviewHtml(p);
      showError("handoff-dialog-error", "");
      $("handoff-backdrop").style.display = "";
    });
  } finally {
    btn.disabled = false;
    renderHandoff();
  }
}

function closeHandoffDialog() {
  $("handoff-backdrop").style.display = "none";
}

async function confirmHandoff() {
  const btn = $("btn-handoff-confirm");
  btn.disabled = true;
  btn.textContent = "AI가 설계로 옮기는 중…";
  try {
    const key = getWork().uid || getWork().id;
    await api.handoff(key, pendingHandoffPlan ?? { tree: [] });
    pendingHandoffPlan = null;
    closeHandoffDialog();
    const { go, novelPath } = await import("./router.js");
    const { refreshWork } = await import("./work.js");
    await refreshWork();
    go(novelPath(getWork().uid || getWork().id, "design"));
  } catch (e) {
    showError("handoff-dialog-error", String(e));
  } finally {
    btn.disabled = false;
    btn.textContent = "AI로 옮기고 인계하기";
  }
}

export function initIdeas() {
  $("thread-select").addEventListener("change", async (e) => {
    threadId = Number(e.target.value);
    await run(async () => {
      messages = (await api.messages(threadId)).messages ?? [];
      resetInclusionDefaults();
      renderChat();
    });
  });
  $("btn-thread-new").addEventListener("click", () => run(async () => {
    const key = getWork().uid || getWork().id;
    const nt = await api.createThread(key, `대화 ${threads.length + 1}`);
    threads.push(nt);
    threadId = nt.id;
    messages = [];
    resetInclusionDefaults();
    renderThreads();
    renderChat();
  }));
  $("synopsis-view").addEventListener("click", enterSynopsisEdit);
  $("btn-chat-send").addEventListener("click", sendChat);
  $("chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendChat();
    }
  });
  document.querySelectorAll("#setting-mode button").forEach((b) =>
    b.addEventListener("click", () => setMode(b.dataset.mode)));
  document.querySelectorAll("#part-mode button").forEach((b) =>
    b.addEventListener("click", () => setPartMode(b.dataset.pmode)));
  $("setting-all").addEventListener("change", (e) => {
    includedSettings = e.target.checked
      ? new Set(settings.map((s) => s.id))
      : new Set();
    renderSettings();
  });
  document.querySelectorAll("#msg-mode button").forEach((b) =>
    b.addEventListener("click", () => {
      msgMode = b.dataset.mmode;
      if (msgMode === "all") includedMessages = new Set(messages.map((m) => m.id));
      else if (msgMode === "none") includedMessages = new Set();
      else {
        const ids = messages.map((m) => m.id);
        includedMessages = new Set(ids.slice(-2));
      }
      updateMsgModeSeg();
      renderChat();
    }));
  $("st-level").addEventListener("change", scheduleStructureSave);
  $("st-total").addEventListener("input", scheduleStructureSave);
  $("part-list").addEventListener("input", (e) => {
    const row = e.target.closest("[data-part]");
    if (!row) return;
    const i = Number(row.dataset.part);
    const f = e.target.dataset.f;
    if ((structure.parts ?? [])[i] && (f === "title" || f === "summary")) {
      structure.parts[i][f] = e.target.value;
    }
    scheduleStructureSave();
  });
  $("btn-part-add").addEventListener("click", () => {
    structure.parts = collectParts();
    structure.parts.push({ title: "", summary: "" });
    if (partMode() !== "full") expandedParts.add(structure.parts.length - 1);
    renderParts();
    const rows = $("part-list").querySelectorAll("[data-part]");
    rows[rows.length - 1]?.querySelector('[data-f="title"]')?.focus();
    scheduleStructureSave();
  });
  $("struct-toggle").addEventListener("click", () => {
    localStorage.setItem("struct-collapsed", isStructCollapsed() ? "0" : "1");
    applyStructCollapsed();
  });
  $("btn-handoff").addEventListener("click", handoffFlow);
  $("btn-handoff-cancel").addEventListener("click", closeHandoffDialog);
  $("btn-handoff-confirm").addEventListener("click", confirmHandoff);
  $("btn-export").addEventListener("click", () => run(exportIdeasMarkdown));
  document.addEventListener("mouseover", (e) => {
    const b = e.target.closest?.(".proposal-btn[data-tip]");
    if (!b || b.disabled) {
      hideTip();
      return;
    }
    const tip = ensureTip();
    tip.textContent = b.dataset.tip;
    tip.style.display = "";
    moveTip(e);
  });
  document.addEventListener("mousemove", (e) => {
    if (tipEl && tipEl.style.display !== "none") moveTip(e);
  });
  document.addEventListener("mouseout", (e) => {
    if (e.target.closest?.(".proposal-btn[data-tip]")) hideTip();
  });
  $("chat-messages").addEventListener("scroll", hideTip, { passive: true });
}
