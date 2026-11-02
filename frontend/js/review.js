import { api } from "./api.js";
import { $, esc, showError, stateKo, verdictKo } from "./util.js";
import { initColResizer, restoreColResizer } from "./resizer.js";
import { mountManuscriptPanel } from "./manuscript.js";

const REVIEW_RESIZER = { resizerId: "review-resizer", varName: "--review-right-w",
  storageKey: "review-right-w", side: "right", min: 280, reserve: 420 };

// 화면 4 · 작가 검토 (구 React판에서 이식)

// 원고 보기는 집필과 동일한 공용 패널(읽기 전용)으로 제공한다.
const REVIEW_MS_ROLES = {
  versionSelect: "review.manuscript.version-select",
  diffCheckbox: "review.manuscript.diff-checkbox",
  body: "review.manuscript.body",
};

const VCLASS = { ok: "v-ok", error: "v-error", change: "v-change", deferred: "v-deferred" };

let job = null;
let msPanel = null;
let items = [];
let aliases = [];
let busy = false;

function setBusy(v) {
  busy = v;
  for (const b of document.querySelectorAll("#s3 button")) b.disabled = v;
  updateApproveState();
}

function updateApproveState() {
  const btn = $("btn-approve-ep");
  if (!btn) return;
  btn.disabled = busy || !job || job.state !== "READY_FOR_REVIEW";
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
  const prop = await api.proposals(epNo);
  const al = await api.aliases("candidate");
  job = j;
  mountMsPanel(epNo, document);
  items = (prop.items ?? []).filter(
    (it) => j.proposal_set_id == null || it.proposal_set_id === j.proposal_set_id
  );
  aliases = (al.aliases ?? []);
  renderAll();
}

function renderNotice() {
  const box = $("review-notice");
  if (!job) {
    box.innerHTML = "";
    return;
  }
  if (job.state === "READY_FOR_REVIEW") {
    box.innerHTML = `<div class="notice ok"><b>EP ${esc(job.episode_no)} · ${stateKo(job.state)}</b> — 검토한 원고와 설정 변경을 확정할까요? 확정하면 한꺼번에 반영되며 되돌릴 수 없습니다.</div>`;
  } else if (job.state === "NEEDS_AUTHOR") {
    box.innerHTML = `<div class="notice err"><b>EP ${esc(job.episode_no)} · ${stateKo(job.state)}</b> — ${esc(job.last_error || "작가 판정이 필요합니다.")}</div>`;
  } else {
    box.innerHTML = "";
  }
  $("review-state").innerHTML = `상태: <b>${esc(stateKo(job.state))}</b> · 시도 ${esc(job.attempt)}`;
}

function renderVerdicts() {
  const box = $("verdict-list");
  $("verdict-count").textContent = `새 문장 vs 기존 근거 · ${items.length}건`;
  if (!items.length) {
    box.innerHTML = `<span class="muted small">검증 항목이 없습니다.</span>`;
    return;
  }
  box.innerHTML = items
    .map(
      (it) => `<div class="verdict-card">
        <div class="verdict-head"><span class="${VCLASS[it.validation] ?? "v-deferred"}">${esc(verdictKo(it.validation))}</span><span class="op">${esc(it.operation)} · ${esc(it.entity_id || it.candidate_name || "")} ${esc(it.attribute_path || "")}</span></div>
        <div class="quote-box"><span class="src">신규</span>${esc(it.evidence_quote || "(인용 없음)")}</div>
        ${it.counter_quote ? `<div class="quote-box"><span class="src">기존 근거</span>${esc(it.counter_quote)}</div>` : ""}
        ${it.reason ? `<div class="reason">${esc(it.reason)}</div>` : ""}
        ${it.validation === "deferred" && !it.evidence_start ? `<span class="unverified">근거 미확인 — 충돌 증거로 사용하지 않음</span>` : ""}
      </div>`
    )
    .join("");
}

function renderAliases() {
  const box = $("alias-list");
  if (!aliases.length) {
    box.innerHTML = `<p class="muted small">대기 중인 별명 후보 없음</p>`;
    return;
  }
  box.innerHTML = aliases
    .map(
      (a) => `<div class="alias-card" data-alias="${esc(a.alias_id)}">
        <b>${esc(a.alias)}</b> <span class="small muted">— ${esc(a.basis)} (현재 연결: ${esc(a.canonical_name ?? "없음")})</span>
        <div class="btn-row">
          <input type="text" placeholder="연결할 인물 ID" data-ent-input data-role="review.alias.entity-input" style="width:140px" value="">
          <button class="btn sm" data-confirm data-role="review.alias.confirm-btn">확정(빈칸=새 인물)</button>
          <button class="btn sm ghost" data-reject data-role="review.alias.reject-btn">기각</button>
        </div>
      </div>`
    )
    .join("");
  box.querySelectorAll("[data-alias]").forEach((card) => {
    const aliasId = card.dataset.alias;
    card.querySelector("[data-confirm]").addEventListener("click", () =>
      run(async () => {
        const entityId = card.querySelector("[data-ent-input]").value.trim() || null;
        await api.resolveAlias(aliasId, { entity_id: entityId, basis: "작가 판정" });
        aliases = ((await api.aliases("candidate")).aliases ?? []);
        renderAliases();
      })
    );
    card.querySelector("[data-reject]").addEventListener("click", () =>
      run(async () => {
        await api.resolveAlias(aliasId, { reject: true });
        aliases = ((await api.aliases("candidate")).aliases ?? []);
        renderAliases();
      })
    );
  });
}

function renderAll() {
  renderNotice();
  renderVerdicts();
  renderAliases();
  updateApproveState();
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
  $("btn-request-change").addEventListener("click", () =>
    run(async () => {
      const instruction = $("change-req").value.trim();
      if (!instruction) {
        showError("review-error", "수정 지시를 입력하세요.");
        return;
      }
      await api.requestChange(currentEp(), instruction);
      $("change-req").value = "";
      await load(currentEp());
    })
  );
  $("btn-approve-ep").addEventListener("click", () =>
    run(async () => {
      const r = await api.approveEpisode(currentEp());
      alert(`확정 완료: ${r.commit.applied}건 반영`);
      await load(currentEp());
    })
  );
  run(() => load(1)).catch(() => {});
}
