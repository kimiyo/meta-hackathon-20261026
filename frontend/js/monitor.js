import { api } from "./api.js";
import { $, esc, showError, stateKo } from "./util.js";
import { getWork } from "./work.js";
import { go, novelPath } from "./router.js";
import { initColResizer, restoreColResizer } from "./resizer.js";
import { mountManuscriptPanel } from "./manuscript.js";

const WRITING_RESIZER = { resizerId: "writing-resizer", varName: "--writing-left-w",
  storageKey: "writing-left-w", side: "left", min: 260, reserve: 320 };
const WRITING_RESIZER_RIGHT = { resizerId: "writing-resizer-right", varName: "--writing-right-w",
  storageKey: "writing-right-w", side: "right", min: 260, reserve: 620 };

// 화면 3 · 집필: 회차 작업실 (선택한 1개 회차 중심)
const ORDER = ["PLAN", "DRAFT", "VERIFY", "READY_FOR_REVIEW", "AUTHOR_APPROVED", "COMMITTED"];

let rows = [];
let designNodes = [];
let detail = null;
let selectedEp = null;
let expandedEp = null;
let busy = false;
let chatMode = "partial"; // or "full"
let chatHistory = [];
let settingTitles = [];
let settingsWorkKey = null;
let sugItems = [];
let sugActive = -1;
let msPanel = null;
let msPanelEp = null;
let msPanelUiState = null;
let msDoc = null;

function setBusy(v) {
  busy = v;
  for (const b of document.querySelectorAll("#s2 button")) b.disabled = v;
  if (!v) {
    applyStartGate();
    renderChatGate();
  }
}

// 순차 집필: EP N의 첫 시작은 EP N-1이 확정(COMMITTED)된 뒤에만 가능.
// 확정된 앞 회차 원고가 다음 회차 검증의 근거가 된다. EP 1은 항상 가능.
function canStart(ep) {
  const prev = rows.find((r) => r.episode_no === ep - 1);
  if (!prev) return true;
  return prev.job?.state === "COMMITTED";
}

function gateHint(ep) {
  return `EP ${ep - 1} 확정 후 시작할 수 있습니다`;
}

function applyStartGate() {
  document.querySelectorAll("#ep-rows tr[data-ep]").forEach((tr) => {
    const ep = Number(tr.dataset.ep);
    const btn = tr.querySelector("[data-advance-row]");
    const row = rows.find((r) => r.episode_no === ep);
    if (btn && !row?.job) {
      btn.disabled = !canStart(ep);
      btn.title = canStart(ep) ? "" : gateHint(ep);
    }
  });
  const start = document.querySelector("#ep-detail [data-start]");
  if (start && detail && !detail.hasJob) {
    start.disabled = !canStart(detail.ep);
    start.title = canStart(detail.ep) ? "" : gateHint(detail.ep);
  }
}

async function run(fn) {
  setBusy(true);
  showError("monitor-error", "");
  try {
    await fn();
  } catch (e) {
    showError("monitor-error", String(e));
  } finally {
    setBusy(false);
  }
}

function stateLabel(state) {
  if (!state) return "-";
  return `<span class="st-${esc(state)} state-label">${esc(stateKo(state))}</span>`;
}

function stateSeg(state) {
  const btns = ORDER.map((s) =>
    `<button tabindex="-1"${s === state ? ' class="active"' : ""} title="${esc(stateKo(s))}">${esc(stateKo(s))}</button>`).join("");
  const extra = state && !ORDER.includes(state) ? ` ${stateLabel(state)}` : "";
  return `<span class="seg" data-role="writing.episode.state-seg" style="vertical-align:middle">${btns}</span>${extra}`;
}

function epSubHtml(ep) {
  const { node, scenes } = designEp(ep);
  const goal = node
    ? `<div><span class="chip mono">${esc(node.code ?? "")}</span> <span class="small">${esc(node.logline || "(줄거리 없음)")}</span></div>`
    : `<span class="muted small">연결된 설계 노드가 없습니다.</span>`;
  const sc = scenes.length
    ? `<ul class="ep-scenes">${scenes
        .map((s) => `<li><span class="chip mono">${esc(s.code ?? "")}</span> <b>${esc(s.title)}</b>${s.logline ? ` <span class="small muted">${esc(s.logline)}</span>` : ""}</li>`)
        .join("")}</ul>`
    : "";
  return `<div class="small"><b>설계 줄거리</b></div>${goal}`
    + (scenes.length ? `<div class="small" style="margin-top:6px"><b>장면 (${scenes.length})</b></div>${sc}` : "");
}

function renderRows() {
  const tb = $("ep-rows");
  if (!rows.length) {
    tb.innerHTML = `<tr><td colspan="3"><span class="muted small">회차가 없습니다.</span></td></tr>`;
    return;
  }
  tb.innerHTML = rows
    .map(
      (r) => `<tr class="clickable${r.episode_no === selectedEp ? " selected" : ""}" data-ep="${r.episode_no}">
        <td><span class="exp">${r.episode_no === expandedEp ? "▾" : "▸"}</span> <b>EP ${esc(r.episode_no)}</b><br><span class="muted small">${esc(r.title)}</span></td>
        <td>${stateLabel(r.job?.state)}</td>
        <td style="white-space:nowrap">
          ${r.job ? `<button class="btn sm" data-detail data-role="writing.progress.row-detail-btn">상세</button> ` : ""}
          <button class="btn sm" data-advance-row data-role="writing.progress.row-advance-btn"${!r.job && !canStart(r.episode_no) ? " disabled" : ""}${!r.job && !canStart(r.episode_no) ? ` title="${gateHint(r.episode_no)}"` : ""}>전진</button>
        </td>
      </tr>
      <tr class="ep-sub" data-sub="${r.episode_no}" data-role="writing.progress.expander"${r.episode_no === expandedEp ? "" : " hidden"}>
        <td colspan="3">${epSubHtml(r.episode_no)}</td>
      </tr>`
    )
    .join("");
  tb.querySelectorAll("tr[data-ep]").forEach((tr) => {
    const ep = Number(tr.dataset.ep);
    const row = rows.find((r) => r.episode_no === ep);
    const detailBtn = tr.querySelector("[data-detail]");
    if (detailBtn) detailBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      openDetail(ep);
    });
    tr.querySelector("[data-advance-row]").addEventListener("click", (e) => {
      e.stopPropagation();
      advanceRow(ep, !!row?.job, e.currentTarget);
    });
    tr.addEventListener("click", () => {
      if (selectedEp === ep && expandedEp === ep) {
        expandedEp = null;
        renderRows();
        return;
      }
      expandedEp = ep;
      openDetail(ep);
    });
  });
  const done = rows.filter((r) => r.job?.state === "COMMITTED").length;
  const bar = $("monitor-progress");
  bar.style.display = "";
  bar.firstElementChild.style.width = `${rows.length ? Math.round((done / rows.length) * 100) : 0}%`;
}

function designEp(ep) {
  const node = designNodes.find(
    (n) => n.kind === "episode" && n.body?.episode_no === ep) ?? null;
  if (!node) return { node: null, scenes: [] };
  const scenes = designNodes.filter(
    (n) => n.parent_id === node.id && !n.archived);
  return { node, scenes };
}

// ---- 중앙 원고 패널 (공유 원고 모듈) ----

function manuscriptPersist(ep) {
  return async (request) => {
    const { operation, ...body } = request;
    if (operation === "draft") return (await api.saveManuscriptDraft(ep, body)).document;
    if (operation === "version") return (await api.createManuscriptVersion(ep, body)).document;
    if (operation === "restore") return (await api.restoreManuscriptVersion(ep, body)).document;
    throw new Error("지원하지 않는 저장 동작입니다.");
  };
}

function manuscriptLoader(ep) {
  return async () => (await api.manuscriptDoc(ep)).document;
}

function mountPanel() {
  const mount = $("ms-mount");
  if (!mount || !detail?.hasJob || !msDoc) return;
  msPanel = mountManuscriptPanel(mount, {
    episodeNo: detail.ep,
    initialDocument: msDoc,
    persist: manuscriptPersist(detail.ep),
    loadDocument: manuscriptLoader(detail.ep),
    onQuoteToChat: insertLinePin,
    onHint: setChatHint,
    restoreState: msPanelEp === detail.ep ? msPanelUiState : null,
  });
  msPanelEp = detail.ep;
  msPanelUiState = null;
}

// 원고 미저장분을 저장한 뒤 패널을 닫는다. 실패하면 false(화면 전환 중단).
async function disposePanelOrAbort() {
  if (!msPanel) return true;
  msPanelUiState = msPanel.getUiState();
  try {
    await msPanel.flush();
  } catch (e) {
    showError("monitor-error", `원고 저장 실패: ${e.message} · 입력은 유지됩니다.`);
    return false;
  }
  msPanel.dispose();
  msPanel = null;
  return true;
}

function renderDetail() {
  const box = $("ep-detail");
  if (!detail) {
    box.innerHTML = `<h3>회차 상세</h3><span class="muted">회차를 선택하면 상세 정보가 표시됩니다.</span>`;
    return;
  }
  const d = detail;
  box.innerHTML = `
    <h3>EP ${esc(d.ep)} · ${esc(d.title || "")} ${d.hasJob ? stateSeg(d.state) : `<span class="muted small">미시작</span>`}</h3>
    ${d.hasJob ? "" : `<div class="btn-row"><button class="btn primary" data-start data-role="writing.episode.start-btn"${canStart(d.ep) ? "" : " disabled"}${canStart(d.ep) ? "" : ` title="${gateHint(d.ep)}"`}>이 화 집필 시작</button>${canStart(d.ep) ? "" : `<span class="small muted">${gateHint(d.ep)}</span>`}</div>`}
    ${d.hasJob ? `<div id="ms-mount"></div>` : `<span class="muted small">집필을 시작하면 초안이 생성됩니다.</span>`}
    <div class="btn-row" style="margin-top:10px">
      ${d.hasJob ? `<button class="btn primary" data-advance data-role="writing.episode.advance-btn">다음 단계로 전진</button>` : ""}
      ${d.hasJob ? `<button class="btn" data-review data-role="writing.episode.review-link">작가 검토로 이동 →</button>` : ""}
    </div>
    ${d.hasJob ? `<div class="small muted" style="margin-top:6px">※ 전진은 되돌릴 수 없습니다. 같은 상태에서 반복하면 재시도 횟수를 소모합니다.</div>` : ""}`;
  box.querySelector("[data-start]")?.addEventListener("click", (e) =>
    advanceCurrent(e.currentTarget));
  box.querySelector("[data-advance]")?.addEventListener("click", (e) =>
    advanceCurrent(e.currentTarget));
  box.querySelector("[data-review]")?.addEventListener("click", goReview);
  mountPanel();
}

// ---- 줄 지정 (@L) ----

function insertAtCaret(input, text) {
  const s = input.selectionStart ?? input.value.length;
  const e = input.selectionEnd ?? input.value.length;
  input.value = input.value.slice(0, s) + text + input.value.slice(e);
  const pos = s + text.length;
  input.setSelectionRange(pos, pos);
}

function insertLinePin({ startLine, endLine }) {
  const input = $("draftchat-input");
  insertAtCaret(input, startLine === endLine ? `@L${startLine} ` : `@L${startLine}-${endLine} `);
  setChatHint("");
  input.focus();
}

// ---- 수정 대화 (우측 패널) ----

function chatSendable() {
  return !!detail?.hasJob && ["DRAFT", "READY_FOR_REVIEW"].includes(detail?.state);
}

function setChatHint(text) {
  $("draftchat-hint").textContent = text ?? "";
}

function hintForState() {
  if (!detail) return "";
  if (!detail.hasJob) return "이 화 집필 시작 후 이용 가능합니다.";
  if (chatSendable()) return chatMode === "full" ? "전체 재생성은 지시를 비워도 됩니다." : "";
  if (detail.state === "VERIFY") return "검토 중에는 요청할 수 없습니다. 전진을 계속하세요.";
  if (detail.state === "NEEDS_AUTHOR") return "작가 확인이 필요한 상태입니다.";
  return "확정된 회차는 수정할 수 없습니다.";
}

function highlightMentions(text) {
  return esc(text).replace(/@([^\s@,]+)/g, "<b>@$1</b>");
}

function chatRowHtml(r) {
  const instr = r.instruction?.trim()
    ? highlightMentions(r.instruction)
    : `<span class="muted">(전체 재생성)</span>`;
  const user = `<div class="msg user"><div class="bubble">${instr}</div></div>`;
  let verdict;
  if (r.error) {
    verdict = `<div class="msg assistant"><div class="bubble">실패: ${esc(r.error)}</div></div>`;
  } else {
    const bits = [`→ v${r.result_version} 생성됨`];
    if (r.full) bits.push("전체");
    const tail = (r.refs ?? []).length ? ` · @${r.refs.map(esc).join(" @")}` : "";
    const unk = (r.unknown ?? []).length ? ` <span class="muted">(못 찾음: @${r.unknown.map(esc).join(" @")})</span>` : "";
    verdict = `<div class="msg assistant"><div class="bubble">${esc(bits.join(" · "))}${tail}${unk}</div></div>`;
  }
  return user + verdict;
}

function renderChatGate() {
  const ok = chatSendable() && !busy;
  $("btn-draftchat-send").disabled = !ok;
  $("draftchat-input").disabled = !chatSendable();
  setChatHint(hintForState());
}

function renderChat() {
  const box = $("draftchat-messages");
  if (!detail) {
    box.innerHTML = `<span class="muted small">회차를 선택하세요.</span>`;
  } else if (!detail.hasJob) {
    box.innerHTML = `<span class="muted small">집필을 시작하면 수정 대화를 할 수 있습니다.</span>`;
  } else if (!chatHistory.length) {
    box.innerHTML = `<span class="muted small">초안에 대한 수정 요청을 보내보세요. @로 설정·노드·줄을 참조할 수 있습니다.</span>`;
  } else {
    box.innerHTML = chatHistory.map(chatRowHtml).join("");
  }
  box.scrollTop = box.scrollHeight;
  document.querySelectorAll("#draftchat-mode [data-dmode]").forEach((b) =>
    b.classList.toggle("active", b.dataset.dmode === chatMode));
  renderChatGate();
}

function appendPendingChat(instruction, full) {
  const box = $("draftchat-messages");
  const instr = instruction
    ? highlightMentions(instruction)
    : `<span class="muted">(전체 재생성)</span>`;
  box.insertAdjacentHTML("beforeend",
    `<div data-pending><div class="msg user"><div class="bubble">${instr}</div></div>` +
    `<div class="msg assistant"><div class="typing" aria-label="처리 중"><span></span><span></span><span></span></div></div></div>`);
  box.scrollTop = box.scrollHeight;
}

function removePendingChat() {
  document.querySelector('#draftchat-messages [data-pending]')?.remove();
}

async function sendChat() {
  const input = $("draftchat-input");
  const instruction = input.value.trim();
  const full = chatMode === "full";
  if (!instruction && !full) {
    setChatHint("수정 지시를 입력하세요.");
    input.focus();
    return;
  }
  hideSuggest();
  if (msPanel?.isHalted()) {
    setChatHint("원고 저장 문제를 먼저 해결하세요.");
    return;
  }
  if (msPanel) {
    try {
      await msPanel.flush();
    } catch (e) {
      setChatHint(`원고 저장 실패: ${e.message}`);
      return;
    }
  }
  appendPendingChat(instruction, full);
  const btn = $("btn-draftchat-send");
  btn.textContent = "처리중…";
  try {
    await run(async () => {
      const base = msPanel && !msPanel.getIncludeDraft() ? "version" : "draft";
      const out = await api.requestChange(detail.ep, instruction, full, base);
      input.value = "";
      await load(currentDesignId());
      await fetchDetail(detail.ep);
      await refreshBudgetChips();
      if (out.unknown_refs?.length) setChatHint(`못 찾은 참조: @${out.unknown_refs.join(" @")}`);
    });
  } finally {
    btn.textContent = "요청";
    removePendingChat();
  }
}

function mentionCandidates() {
  const out = [];
  for (const t of settingTitles) out.push({ token: t, label: t, kind: "설정" });
  for (const n of designNodes) {
    if (n.archived) continue;
    const code = n.code ?? n.full_code ?? n.id;
    if (!code) continue;
    out.push({ token: String(code), label: `${code} ${n.title ?? ""}`.trim(), kind: "노드" });
  }
  return out;
}

function updateSuggest() {
  const input = $("draftchat-input");
  const caret = input.selectionStart ?? input.value.length;
  const m = /@([^\s@,]*)$/.exec(input.value.slice(0, caret));
  if (!m) {
    hideSuggest();
    return;
  }
  const q = m[1].toLowerCase();
  sugItems = mentionCandidates().filter((c) =>
    c.token.toLowerCase().includes(q) || c.label.toLowerCase().includes(q)).slice(0, 8);
  if (!sugItems.length) {
    hideSuggest();
    return;
  }
  sugActive = 0;
  renderSuggest();
}

function renderSuggest() {
  const box = $("draftchat-suggest");
  box.innerHTML = sugItems.map((c, i) =>
    `<div class="sug-it${i === sugActive ? " active" : ""}" data-sug="${i}"><b>@${esc(c.token)}</b><span class="muted small">${esc(c.kind)} · ${esc(c.label)}</span></div>`).join("");
  box.style.display = "";
  box.querySelectorAll("[data-sug]").forEach((el) => {
    el.addEventListener("mousedown", (e) => {
      e.preventDefault();
      acceptSuggest(Number(el.dataset.sug));
    });
  });
}

function acceptSuggest(i) {
  const c = sugItems[i];
  if (!c) return;
  const input = $("draftchat-input");
  const caret = input.selectionStart ?? input.value.length;
  const before = input.value.slice(0, caret);
  const m = /@([^\s@,]*)$/.exec(before);
  if (!m) return;
  const head = before.slice(0, before.length - m[0].length);
  input.value = `${head}@${c.token} ${input.value.slice(caret)}`;
  const pos = head.length + c.token.length + 2;
  input.setSelectionRange(pos, pos);
  hideSuggest();
  input.focus();
}

function hideSuggest() {
  sugItems = [];
  sugActive = -1;
  const box = $("draftchat-suggest");
  if (box) {
    box.innerHTML = "";
    box.style.display = "none";
  }
}

async function ensureSettingTitles() {
  const w = getWork();
  const key = w ? (w.uid || w.id) : null;
  if (!key) {
    settingTitles = [];
    settingsWorkKey = null;
    return;
  }
  if (key === settingsWorkKey) return;
  try {
    const s = await api.settings(key);
    settingTitles = (s.settings ?? []).map((x) => x.title).filter(Boolean);
  } catch {
    settingTitles = [];
  }
  settingsWorkKey = key;
}

// ---- 접기 ----

function setProgressCollapsed(collapsed) {
  localStorage.setItem("writing-progress-collapsed", collapsed ? "1" : "0");
  document.querySelector(".grid-monitor")?.classList.toggle("progress-collapsed", collapsed);
}

function setChatCollapsed(collapsed) {
  localStorage.setItem("writing-chat-collapsed", collapsed ? "1" : "0");
  document.querySelector(".grid-monitor")?.classList.toggle("chat-collapsed", collapsed);
}

function currentDesignId() {
  return $("monitor-design-id").value.trim();
}

async function load(did) {
  const d = await api.designEpisodes(did);
  rows = d.episodes ?? [];
  try {
    designNodes = (await api.nodes(did)).nodes ?? [];
  } catch {
    designNodes = [];
  }
  renderRows();
}

async function fetchDetail(ep) {
  if (!(await disposePanelOrAbort())) return;
  selectedEp = Number(ep);
  renderRows();
  const row = rows.find((r) => r.episode_no === selectedEp);
  const hasJob = !!row?.job;
  if (hasJob) {
    detail = { ep: selectedEp, hasJob: true, title: row.title,
      ...(await api.episode(selectedEp)) };
    msDoc = (await api.manuscriptDoc(selectedEp)).document;
    try {
      chatHistory = (await api.draftRequests(selectedEp)).requests ?? [];
    } catch {
      chatHistory = [];
    }
  } else {
    detail = { ep: selectedEp, hasJob: false, title: row?.title ?? "" };
    msDoc = null;
    chatHistory = [];
  }
  await ensureSettingTitles();
  renderDetail();
  renderChat();
}

function openDetail(ep) {
  return run(() => fetchDetail(ep));
}

async function advanceCurrent(btn) {
  if (msPanel?.isHalted()) {
    showError("monitor-error", "원고 저장 문제를 먼저 해결하세요. 입력은 유지됩니다.");
    return;
  }
  await run(async () => {
    if (!(await disposePanelOrAbort())) return;
    const label = btn.textContent;
    btn.textContent = "실행 중… (취소 불가)";
    try {
      await api.advance(detail.ep, detail.hasJob ? undefined : Number(currentDesignId()));
      await load(currentDesignId());
      await fetchDetail(detail.ep);
      await refreshBudgetChips();
    } finally {
      btn.textContent = label;
    }
  });
}

async function advanceRow(ep, hasJob, btn) {
  if (msPanel?.isHalted()) {
    showError("monitor-error", "원고 저장 문제를 먼저 해결하세요. 입력은 유지됩니다.");
    return;
  }
  await run(async () => {
    if (!(await disposePanelOrAbort())) return;
    const label = btn.textContent;
    btn.textContent = "실행 중… (취소 불가)";
    try {
      await api.advance(ep, hasJob ? undefined : Number(currentDesignId()));
      await load(currentDesignId());
      await fetchDetail(ep);
      await refreshBudgetChips();
    } finally {
      btn.textContent = label;
    }
  });
}

function goReview() {
  const w = getWork();
  if (!w || !detail) return;
  const ep = detail.ep;
  go(novelPath(w.uid || w.id, "review"));
  setTimeout(() => {
    document.dispatchEvent(new CustomEvent("review-episode", {
      detail: { ep },
    }));
  }, 50);
}

export async function refreshBudgetChips() {
  try {
    const b = await api.opsBudget();
    const text = `오늘 토큰 ${b.total_tokens} / ${b.budget} (잔여 ${b.remaining})`;
    const chip = $("budget-chip");
    if (chip) chip.textContent = text;
    const line = $("monitor-budget");
    if (line) line.textContent = text;
  } catch {
    /* 예산 조회 실패는 치명적이지 않음 */
  }
}

export function initMonitor() {
  initColResizer(WRITING_RESIZER);
  initColResizer(WRITING_RESIZER_RIGHT);
  setProgressCollapsed(localStorage.getItem("writing-progress-collapsed") === "1");
  setChatCollapsed(localStorage.getItem("writing-chat-collapsed") === "1");
  $("btn-progress-collapse").addEventListener("click", () => setProgressCollapsed(true));
  $("btn-progress-expand").addEventListener("click", () => setProgressCollapsed(false));
  $("btn-draftchat-collapse").addEventListener("click", () => setChatCollapsed(true));
  $("btn-draftchat-expand").addEventListener("click", () => setChatCollapsed(false));
  $("btn-monitor-load").addEventListener("click", () =>
    run(async () => {
      const did = currentDesignId();
      if (did) await load(did);
    })
  );
  $("btn-draftchat-send").addEventListener("click", sendChat);
  document.querySelectorAll("#draftchat-mode [data-dmode]").forEach((b) =>
    b.addEventListener("click", () => {
      chatMode = b.dataset.dmode;
      renderChat();
    }));
  const chatInput = $("draftchat-input");
  chatInput.addEventListener("input", updateSuggest);
  chatInput.addEventListener("blur", () => setTimeout(hideSuggest, 150));
  chatInput.addEventListener("keydown", (e) => {
    if ($("draftchat-suggest").style.display !== "none" && sugItems.length) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        sugActive = (sugActive + 1) % sugItems.length;
        renderSuggest();
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        sugActive = (sugActive - 1 + sugItems.length) % sugItems.length;
        renderSuggest();
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        acceptSuggest(sugActive);
        return;
      }
      if (e.key === "Escape") {
        hideSuggest();
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendChat();
    }
  });
  renderChat();
  // 첫 작품의 최신 설계로 자동 초기화
  api
    .works()
    .then(async (w) => {
      if (!w.works.length) return;
      const cur = getWork();
      const target = cur ? cur.uid || cur.id : w.works[0].uid || w.works[0].id;
      const full = await api.work(target);
      if (full.latest_design) {
        $("monitor-design-id").value = full.latest_design.id;
        await load(full.latest_design.id);
      }
    })
    .catch(() => {});
  document.addEventListener("write-episode", (e) => {
    const { designId, ep } = e.detail ?? {};
    if (!designId) return;
    run(async () => {
      $("monitor-design-id").value = designId;
      await load(designId);
      await fetchDetail(Number(ep));
      document.querySelector(`#ep-rows tr[data-ep="${Number(ep)}"]`)
        ?.scrollIntoView({ block: "nearest" });
    });
  });
  document.addEventListener("enter-screen", (e) => {
    if (e.detail?.section !== "s2") return;
    restoreColResizer(WRITING_RESIZER);
    restoreColResizer(WRITING_RESIZER_RIGHT);
    const cur = getWork();
    if (!cur) return;
    run(async () => {
      const full = await api.work(cur.uid || cur.id);
      const did = full.latest_design?.id;
      if (did && String($("monitor-design-id").value).trim() !== String(did)) {
        $("monitor-design-id").value = did;
        await load(did);
      }
      if (selectedEp != null && rows.some((r) => r.episode_no === selectedEp)) {
        await fetchDetail(selectedEp);
      }
    });
  });
  refreshBudgetChips();
}
