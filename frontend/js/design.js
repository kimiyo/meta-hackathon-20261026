import { api } from "./api.js";
import { $, esc, showError } from "./util.js";

// 화면 1 · 작품/설계 (web/src/pages/Design.jsx 이식)
let works = [];
let workId = null;
let design = null;
let busy = false;

function setBusy(v, label) {
  busy = v;
  const b2 = $("btn-create-design");
  if (b2) {
    b2.disabled = v;
    b2.textContent = v && label ? label : designButtonLabel();
  }
  const approve = document.querySelector("#design-detail [data-approve]");
  if (approve) approve.disabled = v;
}

async function run(fn, busyLabel) {
  setBusy(true, busyLabel);
  showError("design-error", "");
  try {
    await fn();
  } catch (e) {
    showError("design-error", String(e));
  } finally {
    setBusy(false);
  }
}

function designButtonLabel() {
  return design ? `설계도 재생성 (다음 v${design.version + 1})` : "설계도 생성";
}

function currentWork() {
  return works.find((x) => x.id === workId) ?? null;
}

export function clearWorkSelection() {
  workId = null;
  design = null;
  renderDesign();
}

function openNovelInfo() {
  const w = currentWork();
  if (!w) return;
  $("novel-info-title-input").value = w.title;
  showError("novel-info-dialog-error", "");
  $("novel-info-dialog-backdrop").style.display = "";
  $("novel-info-title-input").focus();
  $("novel-info-title-input").select();
}

function closeNovelInfo() {
  $("novel-info-dialog-backdrop").style.display = "none";
}

async function saveNovelInfo() {
  const w = currentWork();
  const title = $("novel-info-title-input").value.trim();
  if (!w) return;
  if (!title) {
    showError("novel-info-dialog-error", "소설 제목을 입력하세요.");
    $("novel-info-title-input").focus();
    return;
  }
  if (title === w.title) {
    closeNovelInfo();
    return;
  }
  const btn = $("btn-novel-info-save");
  btn.disabled = true;
  btn.textContent = "저장 중…";
  try {
    const updated = await api.patchWork(w.id, { title });
    works = works.map((x) => (x.id === w.id ? { ...x, ...updated } : x));
    closeNovelInfo();
    refreshWorkPill();
  } catch (e) {
    showError("novel-info-dialog-error", String(e));
  } finally {
    btn.disabled = false;
    btn.textContent = "저장";
  }
}

function statusLabel(status) {
  if (status === "approved") {
    return `<span class="st-AUTHOR_APPROVED state-label">approved</span>`;
  }
  return `<span class="chip mono">${esc(status)}</span>`;
}

function renderDesign() {
  const box = $("design-detail");
  if (!design) {
    box.innerHTML = `<div class="card"><span class="muted">이 작품에는 설계도가 없습니다. 왼쪽에서 생성하세요.</span></div>`;
    if (!busy) $("btn-create-design").textContent = designButtonLabel();
    return;
  }
  const c = design.concept ?? {};
  const sections = (design.sections ?? [])
    .map(
      (s) => `<div class="section-row" style="margin-bottom:8px">
        <span class="section-range">${esc(s.range)}</span>
        <div><span class="muted small">${esc(s.summary)}</span></div>
      </div>`
    )
    .join("");
  const outlines = (design.outlines ?? [])
    .map(
      (o) => `<details class="outline">
        <summary><span class="ep-no">EP ${esc(o.episode_no)}</span> ${esc(o.title)}</summary>
        <div class="body">
          <b>목표:</b> ${esc(o.goal)}<br>
          <b>사건:</b> ${esc((o.events ?? []).join(" / "))}<br>
          <b>인물 변화:</b> ${esc((o.char_changes ?? []).join(" / "))}<br>
          <b>복선:</b> ${esc((o.foreshadowing ?? []).join(" / "))}
        </div>
      </details>`
    )
    .join("");
  box.innerHTML = `
    <div class="card">
      <h3>설계도 v${esc(design.version)}
        <span class="hint">concept · sections · episode outlines</span></h3>
      <dl class="kv" style="margin-bottom:12px">
        <dt>장르 / 톤</dt><dd>${esc(c.genre)} / ${esc(c.tone)}</dd>
        <dt>주제</dt><dd>${esc(c.theme)}</dd>
        <dt>결말 방향</dt><dd>${esc(c.ending)}</dd>
        <dt>상태</dt><dd>${statusLabel(design.status)}
          ${design.status !== "approved" ? `<button class="btn sm primary" data-approve style="margin-left:8px">설계 승인</button> <button class="btn sm" data-regen>새 버전으로 재생성</button>` : ""}</dd>
      </dl>
      ${sections}
    </div>
    <div class="card">
      <h3>회차 개요 <span class="hint">클릭하면 펼침 · ${(design.outlines ?? []).length}화</span></h3>
      ${outlines}
    </div>`;
  const approve = box.querySelector("[data-approve]");
  if (approve) {
    approve.addEventListener("click", () =>
      run(async () => {
        await api.approveDesign(design.id);
        design = await api.design(design.id);
        renderDesign();
        refreshWorkPill();
      })
    );
  }
  const regen = box.querySelector("[data-regen]");
  if (regen) {
    regen.addEventListener("click", () => {
      const kw = $("design-keywords");
      kw.scrollIntoView({ behavior: "smooth", block: "center" });
      kw.focus();
    });
  }
  if (!busy) $("btn-create-design").textContent = designButtonLabel();
}

export async function selectWork(id) {
  workId = id;
  await run(async () => {
    const w = await api.work(id);
    if (w.latest_design) {
      design = await api.design(w.latest_design.id);
    } else {
      design = null;
    }
    renderDesign();
    refreshWorkPill();
  });
}

async function reload() {
  await run(async () => {
    const w = await api.works();
    works = w.works ?? [];
    refreshWorkPill();
    if (workId) {
      const full = await api.work(workId);
      design = full.latest_design ? await api.design(full.latest_design.id) : null;
      renderDesign();
      refreshWorkPill();
    }
  });
}

export async function refreshWorkPill() {
  const pill = $("work-pill");
  if (!pill) return;
  const w = currentWork();
  pill.textContent = w ? w.title : works.length ? "작품 선택" : "작품 없음";
}

export function initDesign() {
  $("work-pill").addEventListener("click", openNovelInfo);
  $("btn-novel-info-cancel").addEventListener("click", closeNovelInfo);
  $("btn-novel-info-save").addEventListener("click", saveNovelInfo);
  $("novel-info-dialog-backdrop").addEventListener("click", (e) => {
    if (e.target === $("novel-info-dialog-backdrop")) closeNovelInfo();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && $("novel-info-dialog-backdrop").style.display !== "none") {
      closeNovelInfo();
    }
  });
  $("novel-info-title-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") saveNovelInfo();
  });
  $("btn-create-design").addEventListener("click", () =>
    run(async () => {
      const keywords = $("design-keywords").value.trim();
      if (!workId || !keywords) {
        showError("design-error", !workId ? "작품을 먼저 선택하세요." : "키워드를 입력하세요.");
        return;
      }
      const d = await api.createDesign(workId, {
        keywords,
        total_episodes: Number($("design-total").value),
        constraints: $("design-constraints").value.split("\n").map((s) => s.trim()).filter(Boolean),
      });
      design = await api.design(d.design_id);
      renderDesign();
      refreshWorkPill();
    }, "생성 중… (취소 불가)")
  );
  reload();
}
