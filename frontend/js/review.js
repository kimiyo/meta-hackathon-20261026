import { api } from "./api.js";
import { $, esc, showError } from "./util.js";

// 화면 3 · 작가 검토 (구 React판에서 이식)

// 작은 단어 단위 diff (삽입/삭제만 표시)
function diffWords(a, b) {
  const aw = a.split(/(\s+)/);
  const bw = b.split(/(\s+)/);
  const m = aw.length;
  const n = bw.length;
  const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--)
    for (let j = n - 1; j >= 0; j--)
      dp[i][j] = aw[i] === bw[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out = [];
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (aw[i] === bw[j]) {
      out.push({ t: "same", s: aw[i] });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ t: "del", s: aw[i++] });
    } else {
      out.push({ t: "add", s: bw[j++] });
    }
  }
  while (i < m) out.push({ t: "del", s: aw[i++] });
  while (j < n) out.push({ t: "add", s: bw[j++] });
  return out;
}

const VCLASS = { ok: "v-ok", error: "v-error", change: "v-change", deferred: "v-deferred" };

let job = null;
let versions = [];
let version = null;
let text = "";
let prevText = "";
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

async function load(epNo) {
  const j = await api.episode(epNo);
  job = j;
  versions = j.revisions ?? [];
  if (versions.length) {
    const latest = versions[versions.length - 1];
    version = latest.version;
    text = (await api.manuscript(epNo, latest.version)).text;
    prevText = versions.length > 1
      ? (await api.manuscript(epNo, versions[versions.length - 2].version)).text
      : "";
  } else {
    version = null;
    text = "";
    prevText = "";
  }
  const prop = await api.proposals(epNo);
  items = (prop.items ?? []).filter(
    (it) => j.proposal_set_id == null || it.proposal_set_id === j.proposal_set_id
  );
  aliases = ((await api.aliases("candidate")).aliases ?? []);
  renderAll();
}

function renderNotice() {
  const box = $("review-notice");
  if (!job) {
    box.innerHTML = "";
    return;
  }
  if (job.state === "READY_FOR_REVIEW") {
    box.innerHTML = `<div class="notice ok"><b>EP ${esc(job.episode_no)} · READY_FOR_REVIEW</b> — 승인하면 원고와 기억 변경안이 <b>원자적으로 커밋</b>됩니다. (멱등, 1회만 적용)</div>`;
  } else if (job.state === "NEEDS_AUTHOR") {
    box.innerHTML = `<div class="notice err"><b>EP ${esc(job.episode_no)} · NEEDS_AUTHOR</b> — ${esc(job.last_error || "작가 판정이 필요합니다.")}</div>`;
  } else {
    box.innerHTML = "";
  }
  $("review-state").innerHTML = `상태: <b>${esc(job.state)}</b> · 시도 ${esc(job.attempt)}`;
}

function renderManuscript() {
  const box = $("manuscript");
  const sel = $("rev-select");
  sel.innerHTML = versions
    .map((r) => `<option value="${r.version}"${r.version === version ? " selected" : ""}>v${r.version} (${esc(r.approval_status)})</option>`)
    .join("");
  const compare = $("diff-toggle").checked;
  if (compare && prevText) {
    box.innerHTML = `<div style="white-space:pre-wrap;text-indent:0">` + diffWords(prevText, text)
      .map((d) => {
        if (d.t === "del") return `<span class="diff-old">${esc(d.s)}</span>`;
        if (d.t === "add") return `<span class="diff-new">${esc(d.s)}</span>`;
        return esc(d.s);
      })
      .join("") + `</div>`;
  } else if (text) {
    box.innerHTML = text
      .split("\n")
      .map((p) => `<p>${esc(p) || "&nbsp;"}</p>`)
      .join("");
  } else {
    box.innerHTML = `<span class="muted">원고가 없습니다.</span>`;
  }
  $("manuscript-meta").textContent = text ? `${text.length}자 · v${version ?? "?"}` : "";
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
        <div class="verdict-head"><span class="${VCLASS[it.validation] ?? "v-deferred"}">${esc(it.validation)}</span><span class="op">${esc(it.operation)} · ${esc(it.entity_id || it.candidate_name || "")} ${esc(it.attribute_path || "")}</span></div>
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
          <input type="text" placeholder="연결할 entity_id" data-ent-input style="width:140px" value="">
          <button class="btn sm" data-confirm>확정(빈칸=새 인물)</button>
          <button class="btn sm ghost" data-reject>기각</button>
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
  renderManuscript();
  renderVerdicts();
  renderAliases();
  updateApproveState();
}

export function initReview() {
  $("btn-review-load").addEventListener("click", () => run(() => load(currentEp())));
  $("rev-select").addEventListener("change", (e) =>
    run(async () => {
      version = Number(e.target.value);
      text = (await api.manuscript(currentEp(), version)).text;
      renderManuscript();
    })
  );
  $("diff-toggle").addEventListener("change", renderManuscript);
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
      alert(`커밋 완료: ${r.commit.applied}건 반영`);
      await load(currentEp());
    })
  );
  run(() => load(1)).catch(() => {});
}
