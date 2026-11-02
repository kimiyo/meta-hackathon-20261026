import { api } from "./api.js";
import { $, esc, showError, stateKo, severityKo, issueStatusKo } from "./util.js";
import { initColResizer, restoreColResizer } from "./resizer.js";
import { mountManuscriptPanel } from "./manuscript.js";

const REVIEW_RESIZER = { resizerId: "review-resizer", varName: "--review-right-w",
  storageKey: "review-right-w", side: "right", min: 280, reserve: 420 };

// 화면 4 · 작가 검토 (읽기 전용 원고 + 일관성 분석 + 이슈 + 기억)

// 원고 보기는 공용 패널(읽기 전용)으로 제공한다.
const REVIEW_MS_ROLES = {
  versionSelect: "review.manuscript.version-select",
  diffCheckbox: "review.manuscript.diff-checkbox",
  body: "review.manuscript.body",
};

let job = null;
let msPanel = null;
let issues = [];
let messages = [];
let busy = false;

function setBusy(v) {
  busy = v;
  for (const b of document.querySelectorAll("#s3 button")) b.disabled = v;
  if (!v) updateActionState();
}

function inReview() {
  return !!job && job.state === "IN_REVIEW";
}

function updateActionState() {
  const ok = !busy && inReview();
  for (const id of ["btn-back-to-writing", "btn-request-publish",
                    "btn-consistency-analyze", "btn-consistency-send"]) {
    const b = $(id);
    if (b) b.disabled = !ok;
  }
  const input = $("consistency-input");
  if (input) input.disabled = !ok;
}

async function run(fn) {
  setBusy(true);
  showError("review-error", "");
  try {
    await fn();
  } catch (e) {
    showError("review-error", String(e));
  } finally {
    setBusy(false);
  }
}

function currentEp() {
  return Number($("review-ep").value);
}

function disposeMsPanel() {
  if (!msPanel) return;
  msPanel.dispose();
  msPanel = null;
}

function mountMsPanel(epNo, document) {
  disposeMsPanel();
  msPanel = mountManuscriptPanel($("review-ms-mount"), {
    episodeNo: epNo,
    initialDocument: document,
    readOnly: true,
    roles: REVIEW_MS_ROLES,
  });
}

async function load(epNo) {
  const j = await api.episode(epNo);
  const { document } = await api.manuscriptDoc(epNo);
  const [iss, msgs, mem, ents] = await Promise.all([
    api.consistencyIssues(epNo),
    api.consistencyMessages(epNo),
    api.memoryDraft(epNo),
    api.entities(""),
  ]);
  job = j;
  mountMsPanel(epNo, document);
  issues = iss.issues ?? [];
  messages = msgs.messages ?? [];
  $("memory-draft-input").value = mem.content ?? "";
  renderAll(ents.entities ?? []);
}

function renderNotice() {
  const box = $("review-notice");
  if (!job) {
    box.innerHTML = "";
    return;
  }
  const open = issues.filter((i) => i.status === "open").length;
  if (job.state === "IN_REVIEW") {
    box.innerHTML = `<div class="notice ok"><b>EP ${esc(job.episode_no)} · ${stateKo(job.state)}</b> — 열린 이슈 ${open}건. 수정이 필요하면 집필중으로 돌아가세요. 문제없으면 발간요청을 누르세요.</div>`;
  } else if (job.state === "PUBLISHING") {
    box.innerHTML = `<div class="notice"><b>EP ${esc(job.episode_no)} · ${stateKo(job.state)}</b> — 발간된 회차입니다. 읽기 전용입니다.</div>`;
  } else if (job.needs_attention) {
    box.innerHTML = `<div class="notice err"><b>EP ${esc(job.episode_no)} · ${stateKo(job.state)}</b> — ${esc(job.last_error || "확인이 필요합니다.")}</div>`;
  } else {
    box.innerHTML = `<div class="notice warn"><b>EP ${esc(job.episode_no)} · ${stateKo(job.state)}</b> — 집필중인 회차입니다. 집필 화면에서 검토를 요청하세요.</div>`;
  }
  $("review-state").innerHTML = `상태: <b>${esc(stateKo(job.state))}</b>`;
}

function renderMessages() {
  const box = $("consistency-messages");
  if (!messages.length) {
    box.innerHTML = `<span class="muted small">전체 분석을 실행하거나 질문을 입력하세요. 발견된 오류는 오른쪽 목록에 등록됩니다.</span>`;
    return;
  }
  box.innerHTML = messages.map((m) =>
    `<div class="msg ${m.role === "user" ? "user" : "assistant"}"><div class="bubble">${esc(m.content)}</div></div>`
  ).join("");
  box.scrollTop = box.scrollHeight;
}

function renderIssues() {
  const box = $("issue-list");
  const open = issues.filter((i) => i.status === "open").length;
  $("issue-count").textContent = `열린 이슈 ${open}건 / 전체 ${issues.length}건`;
  if (!issues.length) {
    box.innerHTML = `<span class="muted small">등록된 이슈가 없습니다.</span>`;
    return;
  }
  box.innerHTML = issues.map((it) => `
    <div class="issue-card" data-issue="${it.issue_id}" data-status="${esc(it.status)}">
      <div class="issue-head">
        <span class="sev-${esc(it.severity)}">${esc(severityKo(it.severity))}</span>
        <span class="title">${esc(it.title || "(제목 없음)")}</span>
        <span class="issue-status">${esc(issueStatusKo(it.status))}</span>
      </div>
      ${it.detail ? `<div class="issue-detail">${esc(it.detail)}</div>` : ""}
      ${it.manuscript_quote ? `<div class="quote-box"><span class="src">원고 인용</span>${esc(it.manuscript_quote)}</div>` : ""}
      ${it.evidence_quote ? `<div class="quote-box"><span class="src">근거${it.evidence_episode ? ` · EP ${esc(it.evidence_episode)}` : ""}${it.entity_id ? ` · ${esc(it.entity_id)}` : ""}</span>${esc(it.evidence_quote)}</div>` : ""}
      <div class="btn-row">
        ${it.status === "open"
          ? `<button class="btn sm" data-resolve>해결됨</button>
             <button class="btn sm ghost" data-wontfix>수정 안 함</button>`
          : `<button class="btn sm ghost" data-reopen>다시 열기</button>`}
      </div>
    </div>`).join("");
  box.querySelectorAll("[data-issue]").forEach((card) => {
    const id = Number(card.dataset.issue);
    const set = (status) => run(async () => {
      await api.setIssueStatus(id, status);
      issues = (await api.consistencyIssues(currentEp())).issues ?? [];
      renderIssues();
      renderNotice();
    });
    card.querySelector("[data-resolve]")?.addEventListener("click", () => set("resolved"));
    card.querySelector("[data-wontfix]")?.addEventListener("click", () => set("wontfix"));
    card.querySelector("[data-reopen]")?.addEventListener("click", () => set("open"));
  });
}

function renderMemory(entities) {
  const box = $("memory-entities");
  if (!entities.length) {
    box.innerHTML = `<span class="muted small">확정된 Entity가 없습니다.</span>`;
    return;
  }
  box.innerHTML = `<ul class="ep-scenes">${entities.map((e) =>
    `<li><span class="chip mono">${esc(e.entity_id)}</span> <b>${esc(e.canonical_name)}</b> <span class="small muted">${esc(e.type)}</span></li>`
  ).join("")}</ul>`;
}

function renderAll(entities) {
  renderNotice();
  renderMessages();
  renderIssues();
  renderMemory(entities);
}

async function refreshIssuesAndDraft() {
  const ep = currentEp();
  const [iss, mem] = await Promise.all([
    api.consistencyIssues(ep),
    api.memoryDraft(ep),
  ]);
  issues = iss.issues ?? [];
  $("memory-draft-input").value = mem.content ?? "";
  renderIssues();
  renderNotice();
}

export function initReview() {
  initColResizer(REVIEW_RESIZER);
  document.addEventListener("enter-screen", (e) => {
    if (e.detail?.section !== "s3") return;
    restoreColResizer(REVIEW_RESIZER);
  });
  $("btn-review-load").addEventListener("click", () => run(() => load(currentEp())));
  document.addEventListener("review-episode", (e) => {
    const ep = e.detail?.ep;
    if (!ep) return;
    $("review-ep").value = ep;
    run(() => load(Number(ep)));
  });
  $("btn-back-to-writing").addEventListener("click", () =>
    run(async () => {
      const reason = window.prompt("집필중으로 돌아가는 이유를 입력하세요 (집필 화면에 표시됩니다).", "") ?? "";
      await api.backToWriting(currentEp(), reason);
      await load(currentEp());
    })
  );
  $("btn-request-publish").addEventListener("click", () =>
    run(async () => {
      const open = issues.filter((i) => i.status === "open").length;
      if (open > 0) {
        showError("review-error", `열린 이슈 ${open}건이 남아 있어 발간요청을 할 수 없습니다.`);
        return;
      }
      const r = await api.requestPublish(currentEp());
      alert(`발간요청 완료: EP ${r.episode_no}`);
      await load(currentEp());
    })
  );
  $("btn-consistency-analyze").addEventListener("click", () =>
    run(async () => {
      const btn = $("btn-consistency-analyze");
      btn.textContent = "분석 중…";
      try {
        await api.consistencyAnalyze(currentEp());
        messages = (await api.consistencyMessages(currentEp())).messages ?? [];
        await refreshIssuesAndDraft();
        renderMessages();
      } finally {
        btn.textContent = "전체 분석 실행";
      }
    })
  );
  const sendChat = () => run(async () => {
    const input = $("consistency-input");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    const out = await api.consistencyChat(currentEp(), text);
    messages = (await api.consistencyMessages(currentEp())).messages ?? [];
    renderMessages();
    await refreshIssuesAndDraft();
    if (out.issues?.length) {
      showError("review-error", "");
    }
  });
  $("btn-consistency-send").addEventListener("click", sendChat);
  $("consistency-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendChat();
    }
  });
  $("btn-memory-save").addEventListener("click", () =>
    run(async () => {
      await api.saveMemoryDraft(currentEp(), $("memory-draft-input").value);
      alert("기억 초안을 저장했습니다.");
    })
  );
  run(() => load(1)).catch(() => {});
}
