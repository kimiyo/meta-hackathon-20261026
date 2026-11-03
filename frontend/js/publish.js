import { api } from "./api.js";
import { $, esc, showError, showToast } from "./util.js";
import { getWork } from "./work.js";
import { openReader } from "./manuscript.js";
import { t } from "./i18n.js";

// 화면 5 · 발간중: 작품 발간 정보 + 커버 + 회차별 최종 출판본.

let detail = null; // { ep, title, text, record, editing }
let episodeRows = [];

function todayLocal() {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

function rowOf(ep) {
  return episodeRows.find((r) => r.episode_no === ep);
}

function workKey() {
  const w = getWork();
  return w ? w.uid || w.id : "";
}

function selectPublishTab(which) {
  const work = which !== "episodes";
  $("tabpane-pub-work").style.display = work ? "" : "none";
  $("tabpane-pub-episodes").style.display = work ? "none" : "";
  $("tab-pub-work").classList.toggle("active", work);
  $("tab-pub-episodes").classList.toggle("active", !work);
  $("tab-pub-work").setAttribute("aria-selected", String(work));
  $("tab-pub-episodes").setAttribute("aria-selected", String(!work));
}

function setBusy(btn, busy, label) {
  if (!btn) return;
  if (busy) {
    btn.dataset.label = btn.textContent;
    btn.textContent = label;
    btn.disabled = true;
  } else {
    btn.textContent = btn.dataset.label ?? btn.textContent;
    btn.disabled = false;
  }
}

async function loadWorkPanel() {
  const w = getWork();
  const coverView = $("pub-cover-view");
  if (!w) {
    $("pub-title").value = "";
    $("pub-title").placeholder = "";
    $("pub-pen").value = "";
    $("pub-pen").placeholder = "";
    $("pub-blurb").value = "";
    $("pub-platform").innerHTML = "";
    coverView.innerHTML = `<span class="muted small">${t("publish.work.noWork")}</span>`;
    return;
  }
  const full = await api.work(w.uid || w.id);
  const titleInput = $("pub-title");
  titleInput.value = full.publish_title || full.title || "";
  titleInput.placeholder = t("publish.work.titlePlaceholder");
  const penInput = $("pub-pen");
  penInput.value = full.publish_pen_name || full.author_pen_name || "";
  penInput.placeholder = t("publish.work.penPlaceholder");
  $("pub-blurb").value = full.publish_blurb || "";
  const platform = full.platform || "문피아";
  $("pub-platform").innerHTML = (full.platforms ?? ["문피아"])
    .map((p) => `<option${p === platform ? " selected" : ""}>${esc(p)}</option>`).join("");
  renderCover(full.cover_url || "");
}

function renderCover(url) {
  const view = $("pub-cover-view");
  const dl = $("btn-pub-cover-download");
  const del = $("btn-pub-cover-delete");
  if (url) {
    view.innerHTML = `<img class="pub-cover-img" src="${esc(url)}?t=${Date.now()}" alt="${t("publish.cover.alt")}">`;
    dl.href = url;
    dl.hidden = false;
    del.hidden = false;
  } else {
    view.innerHTML = `<span class="muted small">${t("publish.cover.none")}</span>`;
    dl.hidden = true;
    del.hidden = true;
  }
  $("pub-cover-file").value = "";
  $("btn-pub-cover-upload").disabled = true;
}

async function generateBlurb() {
  if ($("pub-blurb").value.trim() &&
      !window.confirm(t("publish.blurb.overwriteConfirm"))) {
    return;
  }
  const btn = $("btn-pub-blurb");
  setBusy(btn, true, t("publish.blurb.generating"));
  try {
    const d = await api.generateBlurb(workKey());
    $("pub-blurb").value = d.blurb || "";
    await api.patchWork(workKey(), { publish_blurb: $("pub-blurb").value });
    showToast(t("publish.blurb.generated"));
  } catch (e) {
    showError("publish-error", String(e));
  } finally {
    setBusy(btn, false);
  }
}

const WORK_FIELDS = [
  ["pub-title", "publish_title", "publish.work.fieldTitle"],
  ["pub-pen", "publish_pen_name", "publish.work.fieldPen"],
  ["pub-blurb", "publish_blurb", "publish.work.fieldBlurb"],
  ["pub-platform", "platform", "publish.work.fieldPlatform"],
];

async function autoSaveWorkField(inputId, apiField, labelKey) {
  try {
    await api.patchWork(workKey(), { [apiField]: $(inputId).value });
    showToast(t("publish.work.fieldSaved", { field: t(labelKey) }));
  } catch (e) {
    showError("publish-error", String(e));
  }
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const s = String(reader.result || "");
      const idx = s.indexOf(",");
      resolve(idx >= 0 ? s.slice(idx + 1) : s);
    };
    reader.onerror = () => reject(new Error(t("publish.cover.readError")));
    reader.readAsDataURL(file);
  });
}

async function uploadCover() {
  const input = $("pub-cover-file");
  const file = input.files?.[0];
  if (!file) return;
  if (!/\.(jpe?g|png)$/i.test(file.name)) {
    showError("publish-error", t("publish.cover.typeError"));
    return;
  }
  if (file.size > 5 * 1024 * 1024) {
    showError("publish-error", t("publish.cover.sizeError"));
    return;
  }
  const btn = $("btn-pub-cover-upload");
  setBusy(btn, true, t("publish.cover.uploading"));
  try {
    const out = await api.uploadCover(workKey(), {
      filename: file.name,
      content_base64: await fileToBase64(file),
    });
    renderCover(out.cover_url || "");
    showToast(t("publish.cover.uploaded"));
  } catch (e) {
    showError("publish-error", String(e));
  } finally {
    setBusy(btn, false);
  }
}

async function deleteCover() {
  if (!window.confirm(t("publish.cover.deleteConfirm"))) return;
  try {
    await api.deleteCover(workKey());
    renderCover("");
    showToast(t("publish.cover.deleted"));
  } catch (e) {
    showError("publish-error", String(e));
  }
}

async function loadCards() {
  const box = $("publish-cards");
  const w = getWork();
  if (!w) {
    box.innerHTML = `<span class="muted small">${t("publish.work.noWork")}</span>`;
    $("publish-count").textContent = "";
    return [];
  }
  let designId = null;
  try {
    const full = await api.work(w.uid || w.id);
    designId = full.latest_design?.id;
  } catch (e) {
    box.innerHTML = `<span class="muted small">${t("publish.list.loadError")}</span>`;
    showError("publish-error", String(e));
    return [];
  }
  if (!designId) {
    box.innerHTML = `<span class="muted small">${t("publish.list.noDesign")}</span>`;
    return [];
  }
  const d = await api.publishingEpisodes(designId);
  const rows = d.episodes ?? [];
  $("publish-count").textContent = t("publish.list.count", { count: rows.length });
  if (!rows.length) {
    box.innerHTML = `<span class="muted small">${t("publish.list.empty")}</span>`;
    return [];
  }
  episodeRows = rows;
  box.innerHTML = `<div class="pub-cards">${rows.map((r) => `
    <button class="pub-card" data-ep="${r.episode_no}" data-role="publish.list.card">
      <div class="t">EP ${esc(r.episode_no)} · ${esc(r.title)}${r.published_date ? ` <span class="pub-badge" data-role="publish.list.reg-badge">${t("publish.list.regBadge", { date: esc(r.published_date) })}</span>` : ""}</div>
      <div class="s">${t("publish.list.cardSub", { rev: r.manuscript_revision_id ? `rev ${esc(r.manuscript_revision_id)}` : "-", open: esc(r.issues.open), total: esc(r.issues.total), date: esc((r.updated_at || "").slice(0, 16).replace("T", " ")) })}</div>
    </button>`).join("")}</div>`;
  box.querySelectorAll("[data-ep]").forEach((card) => {
    card.addEventListener("click", () => selectEpisode(Number(card.dataset.ep)));
  });
  return rows;
}

async function selectEpisode(ep) {
  document.querySelectorAll("#publish-cards [data-ep]").forEach((c) => {
    c.classList.toggle("active", Number(c.dataset.ep) === ep);
  });
  const body = $("pub-detail-body");
  body.innerHTML = `<span class="muted small">${t("publish.detail.loading")}</span>`;
  try {
    const d = await api.approvedText(ep, workKey());
    const row = rowOf(ep) ?? {};
    // 카드 제목에는 뱃지 텍스트가 섞여 있어 row에서 제목을 복원한다.
    detail = {
      ep,
      title: row.title ? `EP ${ep} · ${row.title}` : `EP ${ep}`,
      text: d.text || "",
      record: {
        published_date: row.published_date || "",
        published_note: row.published_note || "",
        published_platform: row.published_platform || "",
      },
      editing: false,
    };
    renderDetail();
  } catch (e) {
    showError("publish-error", String(e));
    body.innerHTML = `<span class="muted small">${t("publish.list.loadError")}</span>`;
  }
}

function recordHtml() {
  const rec = detail.record;
  if (detail.editing) {
    return `
    <div data-role="publish.record.form">
      <label class="f" for="pub-reg-date">${t("publish.record.dateLabel")}</label>
      <input type="date" id="pub-reg-date" data-role="publish.record.date"
        value="${esc(rec.published_date || todayLocal())}">
      <label class="f" for="pub-reg-note">${t("publish.record.noteLabel")}</label>
      <input type="text" id="pub-reg-note" data-role="publish.record.note"
        value="${esc(rec.published_note)}" maxlength="200" style="width:100%"
        placeholder="${t("publish.record.notePlaceholder")}">
      <div class="btn-row">
        <button class="btn sm primary" id="btn-pub-reg-save" data-role="publish.record.save-btn">${t("publish.record.save")}</button>
        <button class="btn sm ghost" id="btn-pub-reg-cancel" data-role="publish.record.cancel-btn">${t("publish.record.cancel")}</button>
      </div>
    </div>`;
  }
  if (rec.published_date) {
    return `
    <div data-role="publish.record.view">
      <p style="margin:6px 0"><b>${t("publish.record.registered")}</b> · ${esc(rec.published_platform)} · ${esc(rec.published_date)}${rec.published_note ? `<br><span class="muted">${esc(rec.published_note)}</span>` : ""}</p>
      <div class="btn-row" style="margin-top:0">
        <button class="btn sm" id="btn-pub-reg-edit" data-role="publish.record.edit-btn">${t("publish.record.edit")}</button>
        <button class="btn sm danger" id="btn-pub-reg-clear" data-role="publish.record.clear-btn">${t("publish.record.clear")}</button>
      </div>
    </div>`;
  }
  return `
    <div class="btn-row" style="margin-top:0">
      <button class="btn sm" id="btn-pub-reg-edit" data-role="publish.record.add-btn">${t("publish.record.add")}</button>
    </div>`;
}

function renderDetail() {
  $("pub-detail-title").textContent = detail.title;
  $("pub-detail-body").innerHTML = `
    ${recordHtml()}
    <div class="btn-row">
      <button class="btn sm" id="btn-pub-ed-full" data-role="publish.detail.full-btn">${t("publish.detail.fullscreen")}</button>
      <span class="hint">${t("publish.detail.approvedHint", { length: detail.text.length })}</span>
    </div>
    <div class="pub-read-text" data-role="publish.detail.text">${esc(detail.text)}</div>`;
  $("btn-pub-ed-full").addEventListener("click", () => {
    openReader({
      text: detail.text,
      title: t("publish.detail.readerTitle", { title: detail.title }),
      version: "",
    });
  });
  $("btn-pub-reg-edit")?.addEventListener("click", () => {
    detail.editing = true;
    renderDetail();
  });
  $("btn-pub-reg-cancel")?.addEventListener("click", () => {
    detail.editing = false;
    renderDetail();
  });
  $("btn-pub-reg-save")?.addEventListener("click", saveRecord);
  $("btn-pub-reg-clear")?.addEventListener("click", clearRecord);
}

function syncRowRecord(ep, rec) {
  const row = rowOf(ep);
  if (row) {
    row.published_date = rec.published_date;
    row.published_note = rec.published_note;
    row.published_platform = rec.published_platform;
  }
  const title = document.querySelector(`#publish-cards [data-ep="${ep}"] .t`);
  if (!title || !row) return;
  title.innerHTML = `EP ${esc(ep)} · ${esc(row.title)}${rec.published_date ? ` <span class="pub-badge" data-role="publish.list.reg-badge">${t("publish.list.regBadge", { date: esc(rec.published_date) })}</span>` : ""}`;
}

async function saveRecord() {
  const btn = $("btn-pub-reg-save");
  setBusy(btn, true, t("publish.record.saving"));
  try {
    const rec = await api.savePublishRecord(detail.ep, {
      published_date: $("pub-reg-date").value,
      note: $("pub-reg-note").value,
    }, workKey());
    detail.record = rec;
    detail.editing = false;
    syncRowRecord(detail.ep, rec);
    renderDetail();
    showToast(t("publish.record.saved"));
  } catch (e) {
    showError("publish-error", String(e));
  } finally {
    setBusy(btn, false);
  }
}

async function clearRecord() {
  if (!window.confirm(t("publish.record.clearConfirm"))) return;
  try {
    const rec = await api.savePublishRecord(detail.ep, {
      published_date: "", note: "",
    }, workKey());
    detail.record = rec;
    syncRowRecord(detail.ep, rec);
    renderDetail();
    showToast(t("publish.record.cleared"));
  } catch (e) {
    showError("publish-error", String(e));
  }
}

async function load() {
  showError("publish-error", "");
  await loadWorkPanel();
  const rows = await loadCards();
  if (rows.length) await selectEpisode(rows[0].episode_no);
  else {
    detail = null;
    $("pub-detail-title").textContent = t("publish.detail.emptyTitle");
    $("pub-detail-body").innerHTML = `<span class="muted">${t("publish.detail.emptyHint")}</span>`;
  }
}

export function initPublish() {
  for (const [inputId, apiField, labelKey] of WORK_FIELDS) {
    $(inputId).addEventListener("change", () => autoSaveWorkField(inputId, apiField, labelKey));
  }
  $("btn-pub-blurb").addEventListener("click", generateBlurb);
  $("tab-pub-work").addEventListener("click", () => selectPublishTab("work"));
  $("tab-pub-episodes").addEventListener("click", () => selectPublishTab("episodes"));
  $("pub-cover-file").addEventListener("change", (e) => {
    $("btn-pub-cover-upload").disabled = !e.target.files?.length;
    showError("publish-error", "");
  });
  $("btn-pub-cover-upload").addEventListener("click", uploadCover);
  $("btn-pub-cover-delete").addEventListener("click", deleteCover);
  document.addEventListener("enter-screen", (e) => {
    if (e.detail?.section !== "s5") return;
    load().catch((err) => showError("publish-error", String(err)));
  });
}
