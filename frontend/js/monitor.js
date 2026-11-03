import { api } from "./api.js";
import { $, esc, showError, stateKo } from "./util.js";
import { getWork } from "./work.js";
import { go, novelPath } from "./router.js";
import { initColResizer, restoreColResizer } from "./resizer.js";
import { mountManuscriptPanel } from "./manuscript.js";

const WRITING_RESIZER = { resizerId: "writing-resizer", varName: "--writing-left-w",
  storageKey: "writing-left-w", side: "left", min: 260, reserve: 320 };

// 화면 3 · 집필: 이 화면 자체가 '집필중'이다. 원고 수정은 여기서만 한다.
let rows = [];
let writingChat = [];
let writingChatEp = null;
let designNodes = [];
let detail = null;
let selectedEp = null;
let expandedEp = null;
let busy = false;
let msPanel = null;
let msPanelEp = null;
let msPanelUiState = null;
let msDoc = null;

function setBusy(v) {
  busy = v;
  for (const b of document.querySelectorAll("#s2 button")) b.disabled = v;
  if (!v) applyStartGate();
}

// 순차 집필: EP N의 첫 시작은 EP N-1이 발간중(PUBLISHING)된 뒤에만 가능.
// 발간된 앞 회차 원고가 다음 회차 검증의 근거가 된다. EP 1은 항상 가능.
function canStart(ep) {
  const prev = rows.find((r) => r.episode_no === ep - 1);
  if (!prev) return true;
  return prev.job?.state === "PUBLISHING";
}

function gateHint(ep) {
  return `EP ${ep - 1} 발간 후 시작할 수 있습니다`;
}

function applyStartGate() {
  document.querySelectorAll("#ep-rows tr[data-ep]").forEach((tr) => {
    const ep = Number(tr.dataset.ep);
    const btn = tr.querySelector("[data-row-action]");
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

function stateLabel(state, needsAttention) {
  if (!state) return "-";
  const flag = needsAttention ? ` <span class="chip warn">확인 필요</span>` : "";
  return `<span class="st-${esc(state)} state-label">${esc(stateKo(state))}</span>${flag}`;
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

function rowActionLabel(row) {
  if (!row?.job) return "집필 시작";
  if (row.job.state === "WRITING") return "검토 요청";
  return "검토로 이동";
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
        <td>${stateLabel(r.job?.state, r.job?.needs_attention)}</td>
        <td style="white-space:nowrap">
          ${r.job ? `<button class="btn sm" data-detail data-role="writing.progress.row-detail-btn">상세</button> ` : ""}
          <button class="btn sm" data-row-action data-role="writing.progress.row-action-btn"${!r.job && !canStart(r.episode_no) ? " disabled" : ""}${!r.job && !canStart(r.episode_no) ? ` title="${gateHint(r.episode_no)}"` : ""}>${rowActionLabel(r)}</button>
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
    tr.querySelector("[data-row-action]").addEventListener("click", (e) => {
      e.stopPropagation();
      rowAction(ep, row);
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
  const done = rows.filter((r) => r.job?.state === "PUBLISHING").length;
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

function currentWorkKey() {
  const w = getWork();
  return w?.uid || w?.id || null;
}

function manuscriptPersist(ep) {
  return async (request) => {
    const { operation, ...body } = request;
    const wk = currentWorkKey();
    if (operation === "draft") return (await api.saveManuscriptDraft(ep, body, wk)).document;
    if (operation === "version") return (await api.createManuscriptVersion(ep, body, wk)).document;
    if (operation === "restore") return (await api.restoreManuscriptVersion(ep, body, wk)).document;
    throw new Error("지원하지 않는 저장 동작입니다.");
  };
}

function manuscriptLoader(ep) {
  return async () => (await api.manuscriptDoc(ep, currentWorkKey())).document;
}

function mountPanel() {
  const mount = $("ms-mount");
  if (!mount || !detail?.hasJob || !msDoc) return;
  const readOnly = detail.state !== "WRITING";
  msPanel = mountManuscriptPanel(mount, {
    episodeNo: detail.ep,
    initialDocument: msDoc,
    persist: readOnly ? null : manuscriptPersist(detail.ep),
    loadDocument: manuscriptLoader(detail.ep),
    readOnly,
    restoreState: msPanelEp === detail.ep ? msPanelUiState : null,
    onQuoteToChat: insertLinePin,
    onHint: setWritingChatHint,
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
  const notices = [];
  if (d.hasJob && d.needs_attention) {
    notices.push(`<div class="notice err"><b>확인 필요</b> — ${esc(d.last_error || "오류가 발생했습니다. 다시 시도하세요.")}</div>`);
  }
  if (d.hasJob && d.state === "WRITING" && d.return_reason) {
    notices.push(`<div class="notice warn"><b>검토에서 돌아옴</b> — ${esc(d.return_reason)}</div>`);
  }
  box.innerHTML = `
    <h3>EP ${esc(d.ep)} · ${esc(d.title || "")} ${d.hasJob ? stateLabel(d.state, d.needs_attention) : `<span class="muted small">미시작</span>`}</h3>
    ${notices.join("")}
    ${d.hasJob ? "" : `<div class="btn-row"><button class="btn primary" data-start data-role="writing.episode.start-btn"${canStart(d.ep) ? "" : " disabled"}${canStart(d.ep) ? "" : ` title="${gateHint(d.ep)}"`}>이 화 집필 시작</button>${canStart(d.ep) ? "" : `<span class="small muted">${gateHint(d.ep)}</span>`}</div>`}
    ${d.hasJob ? `<div id="ms-mount"></div>` : `<span class="muted small">집필을 시작하면 AI 초안이 생성됩니다.</span>`}
    <div class="btn-row" style="margin-top:10px">
      ${d.hasJob && d.state === "WRITING" ? `<button class="btn primary" data-submit data-role="writing.episode.submit-btn">검토 요청</button>` : ""}
      ${d.hasJob && d.state === "WRITING" ? `<button class="btn" data-redraft data-role="writing.episode.redraft-btn">AI로 다시 쓰기</button>` : ""}
      ${d.hasJob ? `<button class="btn" data-review data-role="writing.episode.review-link">작가 검토로 이동 →</button>` : ""}
    </div>
    ${d.hasJob && d.state !== "WRITING" ? `<div class="small muted" style="margin-top:6px">※ 이 회차는 ${esc(stateKo(d.state))} 상태라 여기서 수정할 수 없습니다.</div>` : ""}
    ${d.hasJob && d.state === "WRITING" ? `
    <div class="card" data-role="writing.chat.panel" style="margin-top:10px">
      <h3>AI 채팅 수정 <span class="hint">지시대로 고쳐 새 버전 저장</span></h3>
      <div id="writing-chat-messages" data-role="writing.chat.messages" class="chat-box"></div>
      <div id="writing-chat-hint" data-role="writing.chat.hint" class="small muted" style="margin-bottom:6px"></div>
      <textarea id="writing-chat-input" data-role="writing.chat.input" placeholder="예: @L12 대사를 더 짧게 고쳐줘 (Enter 전송)"></textarea>
      <div class="btn-row"><button class="btn primary sm" id="btn-writing-chat-send" data-role="writing.chat.send-btn">전송</button></div>
    </div>` : ""}`;
  box.querySelector("[data-start]")?.addEventListener("click", (e) =>
    startCurrent(e.currentTarget));
  box.querySelector("[data-submit]")?.addEventListener("click", (e) =>
    submitCurrent(e.currentTarget));
  box.querySelector("[data-redraft]")?.addEventListener("click", (e) =>
    redraftCurrent(e.currentTarget));
  box.querySelector("[data-review]")?.addEventListener("click", () => goReview());
  box.querySelector("#btn-writing-chat-send")?.addEventListener("click", sendWritingChat);
  box.querySelector("#writing-chat-input")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendWritingChat();
    }
  });
  mountPanel();
  renderWritingChat();
}

function renderWritingChat() {
  const box = $("writing-chat-messages");
  if (!box) return;
  box.innerHTML = writingChat.length
    ? writingChat.map((m) =>
        `<div class="msg ${m.role === "user" ? "user" : "assistant"}"><div class="bubble">${esc(m.content)}</div></div>`
      ).join("")
    : `<span class="muted small">고칠 내용을 지시하세요. 원고에서 줄을 선택한 뒤 '선택한 줄을 채팅에 포함'을 누르면 줄 번호가 들어갑니다.</span>`;
  box.scrollTop = box.scrollHeight;
}

function setWritingChatHint(text) {
  const el = $("writing-chat-hint");
  if (el) el.textContent = text ?? "";
}

function insertLinePin({ startLine, endLine }) {
  const input = $("writing-chat-input");
  if (!input) return;
  const s = input.selectionStart ?? input.value.length;
  const e = input.selectionEnd ?? input.value.length;
  const pin = startLine === endLine ? `@L${startLine} ` : `@L${startLine}-${endLine} `;
  input.value = input.value.slice(0, s) + pin + input.value.slice(e);
  const pos = s + pin.length;
  input.setSelectionRange(pos, pos);
  setWritingChatHint("");
  input.focus();
}

async function sendWritingChat() {
  const input = $("writing-chat-input");
  const text = input?.value.trim() ?? "";
  if (!text || !detail?.hasJob) return;
  const include = msPanel?.getIncludeDraft?.() ?? true;
  input.value = "";
  await withPanelSaved($("btn-writing-chat-send"), async () => {
    const out = await api.writingChat(detail.ep, text, include, currentWorkKey());
    writingChat.push({ role: "user", content: text });
    writingChat.push({ role: "assistant",
      content: `새 버전으로 저장했습니다 (v${out.revision.version} · AI 채팅 수정)` });
  });
}

// ---- 접기 ----

function setProgressCollapsed(collapsed) {
  localStorage.setItem("writing-progress-collapsed", collapsed ? "1" : "0");
  document.querySelector(".grid-monitor")?.classList.toggle("progress-collapsed", collapsed);
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
  if (writingChatEp !== selectedEp) {
    writingChat = [];
    writingChatEp = selectedEp;
  }
  renderRows();
  const row = rows.find((r) => r.episode_no === selectedEp);
  const hasJob = !!row?.job;
  if (hasJob) {
    const wk = currentWorkKey();
    detail = { ep: selectedEp, hasJob: true, title: row.title,
      ...(await api.episode(selectedEp, wk)) };
    msDoc = (await api.manuscriptDoc(selectedEp, wk)).document;
  } else {
    detail = { ep: selectedEp, hasJob: false, title: row?.title ?? "" };
    msDoc = null;
  }
  renderDetail();
}

function openDetail(ep) {
  return run(() => fetchDetail(ep));
}

async function withPanelSaved(btn, fn) {
  if (msPanel?.isHalted()) {
    showError("monitor-error", "원고 저장 문제를 먼저 해결하세요. 입력은 유지됩니다.");
    return;
  }
  await run(async () => {
    if (!(await disposePanelOrAbort())) return;
    const label = btn.textContent;
    btn.textContent = "실행 중…";
    try {
      await fn();
      await load(currentDesignId());
      await fetchDetail(detail?.ep ?? selectedEp);
      await refreshBudgetChips();
    } finally {
      btn.textContent = label;
    }
  });
}

async function startCurrent(btn) {
  await withPanelSaved(btn, async () => {
    await api.startEpisode(detail.ep, Number(currentDesignId()), currentWorkKey());
  });
}

async function submitCurrent(btn) {
  await withPanelSaved(btn, async () => {
    await api.submitReview(detail.ep, currentWorkKey());
  });
}

async function redraftCurrent(btn) {
  await withPanelSaved(btn, async () => {
    await api.redraft(detail.ep, currentWorkKey());
  });
}

async function rowAction(ep, row) {
  if (!row?.job) {
    await run(async () => {
      await api.startEpisode(ep, Number(currentDesignId()), currentWorkKey());
      await load(currentDesignId());
      await fetchDetail(ep);
      await refreshBudgetChips();
    });
    return;
  }
  if (row.job.state === "WRITING") {
    await run(async () => {
      await api.submitReview(ep, currentWorkKey());
      await load(currentDesignId());
      await fetchDetail(ep);
      await refreshBudgetChips();
    });
    return;
  }
  goReview(ep);
}

function goReview(ep) {
  const w = getWork();
  const n = Number(ep);
  const target = Number.isFinite(n) ? n : detail?.ep;
  if (!w || target == null || !Number.isFinite(Number(target))) return;
  go(novelPath(w.uid || w.id, "review"));
  setTimeout(() => {
    document.dispatchEvent(new CustomEvent("review-episode", {
      detail: { ep: target },
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
  setProgressCollapsed(localStorage.getItem("writing-progress-collapsed") === "1");
  $("btn-progress-collapse").addEventListener("click", () => setProgressCollapsed(true));
  $("btn-progress-expand").addEventListener("click", () => setProgressCollapsed(false));
  $("btn-monitor-load").addEventListener("click", () =>
    run(async () => {
      const did = currentDesignId();
      if (did) await load(did);
    })
  );
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
  document.addEventListener("writing-episode", (e) => {
    const ep = Number(e.detail?.ep);
    if (!Number.isFinite(ep)) return;
    run(async () => {
      if (!rows.some((r) => r.episode_no === ep)) {
        const did = currentDesignId();
        if (did) await load(did);
      }
      await fetchDetail(ep);
    });
  });
  document.addEventListener("enter-screen", (e) => {
    if (e.detail?.section !== "s2") return;
    restoreColResizer(WRITING_RESIZER);
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
