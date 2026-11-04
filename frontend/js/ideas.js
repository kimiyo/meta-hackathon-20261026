import { api } from "./api.js";
import { t } from "./i18n.js";
import { $, esc, showError } from "./util.js";
import { getWork } from "./work.js";
import { initColResizer, restoreColResizer } from "./resizer.js";

const IDEAS_RESIZER = { resizerId: "ideas-col-resizer", varName: "--ideas-chat-w",
  storageKey: "ideas-chat-w", side: "left", min: 280, reserve: 320 };

// 화면 1 · 아이디어. 채팅(좌) + 전체 줄거리(우상) + 설정정보(우하).
// 줄거리는 직접 inline 편집 + AI 제안 반영, 설정은 AI 답변의 제안 버튼으로 반영된다.
let threads = [];
let threadId = null;
let messages = [];
let synopsis = { content: "", version: 0 };
let synEditing = false;
let synDraft = "";
let synSaving = false;
// locale-changed 리렌더 중 편집 textarea 교체로 발생하는 blur 자동저장을 건너뛴다.
let suspendSynBlurSave = false;
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
  character: "ideas.cat.character", world: "ideas.cat.world", event: "ideas.cat.event",
  part: "ideas.cat.part", misc: "ideas.cat.misc",
};

function catLabel(c) {
  const key = CAT_LABEL[c];
  return key ? t(key) : c;
}

function useWord(on) {
  return t(on ? "ideas.structure.useOn" : "ideas.structure.useOff");
}

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
    const res = await api.threads(w.uid || w.id);
    threads = res.threads ?? [];
    if (!threads.length && !locked()) {
      const nt = await api.createThread(w.uid || w.id, t("ideas.thread.title", { n: 1 }));
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
    renderAll(); restoreColResizer(IDEAS_RESIZER);
  });
}

function updateMsgModeSeg() {
  document.querySelectorAll("#msg-mode button").forEach((b) =>
    b.classList.toggle("active", b.dataset.mmode === msgMode));
}

// 포함 기본값: 줄거리·구조·설정 전부 체크, 대화는 마지막 2개(직전 질문+답변) 체크.
// 화면 진입·대화 전환·새 대화·AI 응답 반환 시점에 리셋한다. 이후에는 자유롭게 수정.
function resetInclusionDefaults() {
  $("syn-include").checked = true;
  $("struct-include").checked = true;
  includedSettings = new Set(settings.map((s) => s.id));
  const ids = messages.map((m) => m.id);
  includedMessages = new Set(ids.slice(-2));
  msgMode = "select";
  updateMsgModeSeg();
}

function renderAll(opts = {}) {
  renderThreads();
  renderChat();
  renderSynopsis();
  renderStructure();
  if (!opts.skipSettings) renderSettings();
  renderHandoff();
  const banner = $("ideas-locked");
  if (banner) banner.style.display = locked() ? "" : "none";
  document.querySelectorAll("#ideas input, #ideas textarea, #ideas select, #ideas button:not([data-collapse])")
    .forEach((el) => {
      if (el.closest("#ideas-locked")) return;
      if (el.id === "thread-select" || el.id === "struct-toggle") return;
      // locked 여부로만 결정한다. renderAll은 항상 run() 안(busy=true)에서 돌므로
      // !busy로 활성화를 막으면, 한 번 꺼진 컨트롤이 다시 켜지지 않는다.
      el.disabled = locked();
    });
}

// ---- 스레드/채팅 ----

function renderThreads() {
  const sel = $("thread-select");
  sel.innerHTML = threads.map((th) =>
    `<option value="${th.id}"${th.id === threadId ? " selected" : ""}>${esc(th.title || t("ideas.thread.title", { n: th.id }))}</option>`).join("");
}

function normCategory(c) {
  return CATEGORIES.includes(c) ? c : "misc";
}

function propTitle(p) {
  const t = (p.title || "").trim();
  if (t) return t;
  const head = (p.summary || "").trim().split("\n")[0].slice(0, 30).trim();
  return head || t("ideas.untitled");
}

// 제안의 code(D번호)가 있으면 그 항목, 없으면 분류+제목이 같은 항목을 수정 대상으로 본다.
function findSetting(p) {
  if (p.code) {
    const byCode = settings.find((s) => s.code === p.code);
    if (byCode) return byCode;
  }
  const title = propTitle(p);
  return settings.find((s) => s.category === normCategory(p.category) && s.title === title);
}

function sameContent(s, p) {
  return (s.summary || "") === (p.summary || "") && (s.detail || "") === (p.detail || "")
    && (!p.code || s.title === propTitle(p));
}

// 통합 제안에서 아직 남아 있는 원본 설정
function pendingMerge(m) {
  const codes = (m.proposals?.merge_from ?? []).map((x) => x.code);
  return settings.filter((s) => codes.includes(s.code));
}

function tipAttr(text) {
  const s = ((text || "").trim() || t("ideas.tip.empty")).slice(0, 300);
  return ` data-tip="${esc(s).replace(/\n/g, "&#10;")}"`;
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
  const merges = props.merge_from ?? [];
  const blockedNote = props.synopsis_blocked
    ? `<div class="small muted" style="margin-top:6px">${t("ideas.chat.synopsisBlocked")}</div>`
    : "";
  if (!sps.length && !syn && !hasStruct && !merges.length) return blockedNote;
  const settingBtns = sps.map((p, i) => {
    const title = propTitle(p);
    const cat = catLabel(normCategory(p.category));
    const tip = tipAttr(`${p.summary || ""}\n${p.detail || ""}`);
    const s = findSetting(p);
    if (!s) {
      return `<button class="btn sm proposal-btn" data-msg="${m.id}" data-sp="${i}" data-role="ideas.chat.proposal-apply"${tip}>＋ [${esc(cat)}] ${esc(title)}</button>`;
    }
    if (sameContent(s, p)) {
      return `<button class="btn sm proposal-btn" data-msg="${m.id}" data-sp="${i}" data-role="ideas.chat.proposal-apply" disabled>${t("ideas.chat.proposalApplied")}</button>`;
    }
    const label = s.title === title
      ? t("ideas.chat.proposalUpdateTitle", { title: esc(title) })
      : t("ideas.chat.proposalUpdateCode", { code: esc(s.code || s.title), title: esc(title) });
    return `<button class="btn sm proposal-btn" data-msg="${m.id}" data-sp="${i}" data-role="ideas.chat.proposal-apply"${tip}>${label}</button>`;
  }).join("");
  const mergeLeft = pendingMerge(m);
  const mergeBtn = merges.length
    ? `<button class="btn sm proposal-btn" data-msg="${m.id}" data-merge="1" data-role="ideas.chat.proposal-merge"${mergeLeft.length ? tipAttr(`${t("ideas.chat.mergeTipHeader")}\n${mergeLeft.map((x) => `${x.code} [${catLabel(normCategory(x.category))}] ${x.title}`).join("\n")}`) : " disabled"}>${mergeLeft.length ? t("ideas.chat.mergeDelete", { codes: mergeLeft.map((x) => esc(x.code)).join("·") }) : t("ideas.chat.mergeDone")}</button>`
    : "";
  const synBtn = syn
    ? `<button class="btn sm proposal-btn syn" data-msg="${m.id}" data-syn="1" data-role="ideas.chat.proposal-synopsis"${isSynApplied(syn) ? " disabled" : ""}${isSynApplied(syn) ? "" : tipAttr(syn)}>${isSynApplied(syn) ? t("ideas.chat.synopsisApplied") : t("ideas.chat.synopsisApply")}</button>`
    : "";
  const structBtn = hasStruct
    ? `<button class="btn sm proposal-btn" data-msg="${m.id}" data-struct="1" data-role="ideas.chat.proposal-structure"${isStructApplied(st) ? " disabled" : ""}${isStructApplied(st) ? "" : structTip(st)}>${isStructApplied(st) ? t("ideas.chat.structureApplied") : t("ideas.chat.structureApply", { bits: structBits(st).join("·") })}</button>`
    : "";
  const pendingSettings = sps.some((p) => {
    const s = findSetting(p);
    return !s || !sameContent(s, p);
  });
  const pendingSyn = !!syn && !isSynApplied(syn);
  const pendingStruct = !!hasStruct && !isStructApplied(st);
  const allDone = !pendingSettings && !pendingSyn && !pendingStruct;
  const allBtn = `<button class="btn sm proposal-btn" data-msg="${m.id}" data-apply-all data-role="ideas.chat.proposal-apply-all" title="${t("ideas.chat.applyAllTip")}"${allDone ? " disabled" : ""}>${allDone ? t("ideas.chat.applyAllDone") : t("ideas.chat.applyAll")}</button>`;
  return `<div class="proposals"><div class="proposals-head"><span class="small muted"><b>${t("ideas.chat.applyNow")}</b></span>${allBtn}</div>
    <div class="btn-row" style="margin-top:6px">${settingBtns}${synBtn}${structBtn}${mergeBtn}</div>${blockedNote}</div>`;
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
    box.innerHTML = `<span class="muted small">${t("ideas.chat.empty")}</span>`;
  } else {
    box.innerHTML = messages.map((m) => {
      const chk = `<input type="checkbox" class="inc" data-inc-msg="${m.id}" data-role="ideas.chat.include-checkbox"${includedMessages.has(m.id) ? " checked" : ""} title="${t("ideas.includeInMessage")}">`;
      if (m.role !== "assistant") {
        return `<div class="msg user">${chk}<div class="bubble">${esc(m.content).replace(/\n/g, "<br>")}</div></div>`;
      }
      return `<div class="msg assistant">${chk}<div class="msg-main"><div class="bubble">${esc(m.content).replace(/\n/g, "<br>")}</div>${proposalsHtml(m)}</div></div>`;
    }).join("");
  }
  if (sending) {
    box.insertAdjacentHTML("beforeend",
      `<div class="msg assistant"><div class="typing" aria-label="${t("ideas.chat.typingLabel")}"><span></span><span></span><span></span></div></div>`);
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
  box.querySelectorAll("[data-merge]").forEach((b) => {
    b.addEventListener("click", () => applyMergeProposal(Number(b.dataset.msg)));
  });
  box.querySelectorAll("[data-apply-all]").forEach((b) => {
    b.addEventListener("click", () => applyAllProposals(Number(b.dataset.msg)));
  });
  const last = [...messages].reverse().find((m) => m.role === "assistant");
  const choices = (last && last.id === messages[messages.length - 1]?.id)
    ? (last.choices ?? []) : [];
  $("choice-row").innerHTML = choices.length
    ? `<div class="small muted" style="width:100%">${t("ideas.chat.followupHead")}</div>` + choices.map((c, i) =>
      `<button class="btn sm" data-choice="${i}" data-role="ideas.chat.choice-btn">${esc(c)}</button>`).join("")
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
  send.textContent = t("ideas.chat.sending");
  send.disabled = true;
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
  send.disabled = false;
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
      const patch = { summary: p.summary || "", detail: p.detail || "" };
      if (p.code) patch.title = propTitle(p); // 번호로 지목한 수정은 제목 변경도 반영
      await api.patchSetting(s.id, patch);
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

// 통합 원본 삭제는 되돌릴 수 없으므로 확인 후 진행하며, '모두 반영'에는 포함하지 않는다.
async function applyMergeProposal(msgId) {
  const m = messages.find((x) => x.id === msgId);
  const left = m ? pendingMerge(m) : [];
  if (!left.length) return;
  const names = left.map((x) => `${x.code} ${x.title}`).join(", ");
  if (!confirm(t("ideas.chat.mergeConfirm", { count: left.length, names }))) return;
  await run(async () => {
    for (const s of left) await api.deleteSetting(s.id);
    settings = (await api.settings(getWork().uid || getWork().id)).settings ?? [];
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
    ta.dataset.role = "ideas.synopsis.edit-input";
    ta.value = synDraft;
    ta.placeholder = t("ideas.synopsis.placeholder");
    const autoresize = () => {
      ta.style.height = "auto";
      ta.style.height = `${ta.scrollHeight}px`;
    };
    ta.addEventListener("input", () => {
      synDraft = ta.value;
      autoresize();
    });
    ta.addEventListener("blur", () => {
      if (!suspendSynBlurSave) saveSynopsisEdit();
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
    sub.textContent = synSaving ? t("ideas.synopsis.saving") : t("ideas.synopsis.editHint");
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
  if (canEdit) view.title = t("ideas.synopsis.clickToEdit");
  else view.removeAttribute("title");
  view.innerHTML = synopsis.content
    ? esc(synopsis.content).replace(/\n/g, "<br>")
    : `<span class="muted">${t("ideas.synopsis.empty")}</span>`;
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
  if (sub) sub.textContent = t("ideas.synopsis.saving");
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
  part: { key: "ideas.structure.level.part", use_part: true, use_chapter: true },
  chapter: { key: "ideas.structure.level.chapter", use_part: false, use_chapter: true },
  none: { key: "ideas.structure.level.none", use_part: false, use_chapter: false },
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
    t("ideas.structure.summary", { label: t(lv.key), total: structure.total_episodes ?? 25 })
    + (n ? t("ideas.structure.summaryParts", { n }) : "");
}

function isStructCollapsed() {
  return localStorage.getItem("struct-collapsed") === "1";
}

function applyStructCollapsed() {
  const collapsed = isStructCollapsed();
  $("struct-body").style.display = collapsed ? "none" : "";
  $("struct-summary").style.display = collapsed ? "" : "none";
  $("struct-toggle").textContent = t("ideas.structure.toggle", { arrow: collapsed ? "▸" : "▾" });
  const cp = $("struct-collapsed-parts");
  if (collapsed) {
    cp.style.display = "";
    cp.innerHTML = (structure.parts ?? []).map((p, i) =>
      `<div class="cpart"><span class="pnum">${i + 1}</span> <b>${esc(p.title || t("ideas.untitled"))}</b> - ${esc(p.summary || "")}</div>`).join("");
  } else {
    cp.style.display = "none";
    cp.innerHTML = "";
  }
}

function partRowHtml(p, i) {
  const m = partMode();
  const expanded = m !== "full" && expandedParts.has(i);
  const title = (p.title || "").trim() || t("ideas.untitled");
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
    <div class="part-title-row"><span class="pnum">${i + 1}</span><input type="text" data-f="title" value="${esc(p.title || "")}" placeholder="${t("ideas.structure.partTitlePlaceholder")}" data-role="ideas.structure.part-title-input"></div>
    <textarea data-f="summary" rows="3" placeholder="${t("ideas.structure.partSummaryPlaceholder")}" data-role="ideas.structure.part-summary-input">${esc(p.summary || "")}</textarea>
    <div class="btn-row"><button class="btn sm" data-del data-role="ideas.structure.part-delete-btn">${t("ideas.structure.partDelete")}</button>${expanded ? `<button class="btn sm" data-collapse data-role="ideas.structure.part-collapse-btn">${t("ideas.structure.partCollapse")}</button>` : ""}</div>
  </div>`;
}

function renderParts() {
  document.querySelectorAll("#part-mode button").forEach((b) =>
    b.classList.toggle("active", b.dataset.pmode === partMode()));
  const box = $("part-list");
  box.dataset.mode = partMode();
  const parts = structure.parts ?? [];
  box.innerHTML = parts.map(partRowHtml).join("") || `<span class="muted small">${t("ideas.structure.noParts")}</span>`;
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
    return t("ideas.structure.totalNotInteger", { limit: EPISODE_LIMIT, where, total });
  }
  if (total > EPISODE_LIMIT) {
    return t("ideas.structure.totalOverLimit", { limit: EPISODE_LIMIT, where, total });
  }
  return "";
}

let structSaveTimer = null;
let structSaveSeq = 0;
const STRUCT_SAVE_DELAY = 700;

function scheduleStructureSave() {
  if (locked()) return;
  $("struct-info").textContent = t("ideas.structure.typing");
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
  const err = totalError(body.total_episodes, t("ideas.structure.whereInput"));
  if (err) {
    $("struct-info").textContent = `${err} ${t("ideas.structure.enterUnderLimit", { limit: EPISODE_LIMIT })}`;
    return;
  }
  const seq = ++structSaveSeq;
  $("struct-info").textContent = t("ideas.structure.saving");
  await run(async () => {
    const auto = normalizeStructure(body);
    const saved = await api.putStructure(getWork().uid || getWork().id, body);
    if (seq !== structSaveSeq) return;
    structure = saved;
    setControlValues();
    updateStructSummary();
    applyStructCollapsed();
    $("struct-info").textContent = auto ? t("ideas.structure.savedAuto") : t("ideas.structure.saved");
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
    bits.push(t("ideas.structure.bitPart", { state: useWord(prop.use_part) }));
  }
  if (prop.use_chapter !== null && prop.use_chapter !== undefined) {
    bits.push(t("ideas.structure.bitChapter", { state: useWord(prop.use_chapter) }));
  }
  if (prop.total_episodes !== null && prop.total_episodes !== undefined) {
    bits.push(t("ideas.structure.bitEpisodes", { n: prop.total_episodes }));
  }
  if (prop.parts !== null && prop.parts !== undefined) {
    bits.push(t("ideas.structure.bitParts", { n: prop.parts.length }));
  }
  return bits;
}

function structTip(prop) {
  const bits = [];
  if (prop.use_part !== null && prop.use_part !== undefined) {
    bits.push(t("ideas.structure.tipPart", { state: useWord(prop.use_part) }));
  }
  if (prop.use_chapter !== null && prop.use_chapter !== undefined) {
    bits.push(t("ideas.structure.tipChapter", { state: useWord(prop.use_chapter) }));
  }
  if (prop.total_episodes !== null && prop.total_episodes !== undefined) {
    bits.push(t("ideas.structure.tipTotal", { n: prop.total_episodes }));
  }
  if (prop.parts !== null && prop.parts !== undefined) {
    bits.push(t("ideas.structure.tipParts", { n: prop.parts.length }));
    prop.parts.forEach((p) => bits.push(`- ${p.title || t("ideas.untitled")}: ${p.summary || ""}`));
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
    const err = totalError(merged.total_episodes, t("ideas.structure.whereAi"));
    if (err) {
      showError("ideas-error", `${err} ${t("ideas.structure.askUnderLimit", { limit: EPISODE_LIMIT })}`);
      return;
    }
    structure = await api.putStructure(getWork().uid || getWork().id, merged);
    expandedParts = new Set();
    renderStructure();
    renderChat();
    if (auto) $("struct-info").textContent = t("ideas.structure.appliedAuto");
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
  const lines = [t("ideas.export.title", { title: w.title }), ""];
  lines.push(t("ideas.export.synopsis"), "", syn.content || t("ideas.export.none"), "");
  lines.push(t("ideas.export.structure"), "",
    `${t("ideas.structure.tipPart", { state: useWord(struct.use_part) })} · ${t("ideas.structure.tipChapter", { state: useWord(struct.use_chapter) })} · ${t("ideas.structure.tipTotal", { n: struct.total_episodes })}`, "");
  for (const p of struct.parts ?? []) {
    lines.push(`- ${p.title}: ${p.summary}`);
  }
  if ((struct.parts ?? []).length) lines.push("");
  lines.push(t("ideas.export.settings"), "");
  for (const s of st.settings ?? []) {
    lines.push(`### [${catLabel(s.category)}] ${s.title}`,
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
  const inc = `<input type="checkbox" class="inc" data-inc-setting="${s.id}" data-role="ideas.settings.item-checkbox"${includedSettings.has(s.id) ? " checked" : ""} title="${t("ideas.includeInMessage")}">`;
  const codeChip = s.code ? `<span class="chip mono"${expanded ? ` data-collapse-chip title="${t("ideas.settings.collapseChipTip")}"` : ""}>${esc(s.code)}</span>` : "";
  const chips = `${codeChip}<span class="chip">${esc(catLabel(s.category))}</span>`;
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
      <button class="btn sm" data-edit data-role="ideas.settings.edit-btn">${t("ideas.settings.edit")}</button>
      <button class="btn sm" data-del data-role="ideas.settings.delete-btn">${t("ideas.settings.delete")}</button>
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
    box.innerHTML = `<span class="muted small">${t("ideas.settings.empty")}</span>`;
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
        el.querySelector(".btn-row")?.insertAdjacentHTML("beforeend", `<button class="btn sm" data-collapse data-role="ideas.settings.collapse-btn">${t("ideas.settings.collapse")}</button>`);
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
    <label class="f">${t("ideas.settings.formCategory")}</label>
    <select data-f="category" data-role="ideas.settings.edit-category-select">${CATEGORIES.map((c) =>
      `<option value="${c}"${s.category === c ? " selected" : ""}>${t(CAT_LABEL[c])}</option>`).join("")}</select>
    <label class="f">${t("ideas.settings.formTitle")}</label><input type="text" data-f="title" value="${esc(s.title)}" data-role="ideas.settings.edit-title-input">
    <label class="f">${t("ideas.settings.formSummary")}</label><textarea data-f="summary" data-role="ideas.settings.edit-summary-input">${esc(s.summary)}</textarea>
    <label class="f">${t("ideas.settings.formDetail")}</label><textarea data-f="detail" data-role="ideas.settings.edit-detail-input">${esc(s.detail)}</textarea>
    <div class="btn-row"><button class="btn sm primary" data-save data-role="ideas.settings.edit-save-btn">${t("ideas.settings.save")}</button>
    <button class="btn sm" data-cancel data-role="ideas.settings.edit-cancel-btn">${t("ideas.settings.cancel")}</button></div>`;
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
  $("btn-handoff").textContent = t("ideas.handoff.button");
}

function handoffTreeHtml(nodes) {
  return `<ul class="ho-tree small">` + nodes.map((n) => {
    let label;
    if (n.kind === "episode") {
      label = t("ideas.handoff.episodeLabel", { no: n.episode_no, title: esc(n.title) }) +
        (n.stub ? ` <span class="muted">${t("ideas.handoff.stubNote")}</span>`
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
  if (counts.parts) bits.push(t("ideas.structure.tipParts", { n: counts.parts }));
  if (counts.chapters) bits.push(t("ideas.handoff.chapters", { n: counts.chapters }));
  bits.push(t("ideas.handoff.episodes", { n: counts.episodes ?? 0 }));
  const attached = (p.settings ?? []).map((s) =>
    `<span class="chip mono">${esc(s.code || "")}</span> ${esc(s.title)}`).join(" · ");
  return `
    <p class="small">${t("ideas.handoff.intro")}</p>
    <p class="small">${t("ideas.handoff.meta", { version: p.design_version, levels: esc((p.levels ?? []).join(" → ")), bits: bits.join(" · "), ai: aiEps, stub: counts.stub_episodes ?? 0 })}</p>
    <p class="small">${t("ideas.handoff.stored", { chars: (p.synopsis_chars ?? 0).toLocaleString(), count: (p.settings ?? []).length, attached: attached ? `<br>${attached}` : "" })}</p>
    ${handoffTreeHtml(p.tree ?? [])}`;
}

async function handoffFlow() {
  const key = getWork().uid || getWork().id;
  const btn = $("btn-handoff");
  btn.disabled = true;
  btn.textContent = t("ideas.handoff.planning");
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
  btn.textContent = t("ideas.handoff.moving");
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
    btn.textContent = t("ideas.handoff.confirm");
  }
}

export function initIdeas() {
  initColResizer(IDEAS_RESIZER);
  $("thread-select").addEventListener("change", async (e) => {
    threadId = Number(e.target.value);
    await run(async () => {
      messages = (await api.messages(threadId)).messages ?? [];
      resetInclusionDefaults();
      renderChat();
      renderSettings();
    });
  });
  $("btn-thread-new").addEventListener("click", () => run(async () => {
    const key = getWork().uid || getWork().id;
    const nt = await api.createThread(key, t("ideas.thread.title", { n: threads.length + 1 }));
    threads.push(nt);
    threadId = nt.id;
    messages = [];
    resetInclusionDefaults();
    renderThreads();
    renderChat();
    renderSettings();
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
  // 화면 언어 전환: 입력·편집 상태는 유지하고 문구만 다시 그린다.
  // 설정 편집기가 열려 있으면 입력 보호를 위해 설정 목록은 건너뛴다.
  document.addEventListener("locale-changed", () => {
    const chatBox = $("chat-messages");
    const scroll = chatBox ? chatBox.scrollTop : 0;
    const editorOpen = [...document.querySelectorAll("#setting-list .setting-edit")]
      .some((box) => box.childElementCount > 0);
    suspendSynBlurSave = true;
    try {
      renderAll({ skipSettings: editorOpen });
    } finally {
      suspendSynBlurSave = false;
    }
    if (chatBox) chatBox.scrollTop = scroll;
  });
}
