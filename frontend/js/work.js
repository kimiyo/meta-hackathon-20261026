import { api } from "./api.js";
import { $, esc, showError } from "./util.js";
import { t } from "./i18n.js";

// 현재 작업 중인 작품 컨텍스트. 라우터가 uid로 지정한다.
let work = null;
let workStyle = null;

export function getWork() {
  return work;
}

export async function selectWorkByUid(uid) {
  work = await api.work(uid);
  refreshWorkPill();
  return work;
}

export async function refreshWork() {
  if (!work) return null;
  work = await api.work(work.uid || work.id);
  refreshWorkPill();
  return work;
}

export function clearWorkSelection() {
  work = null;
  refreshWorkPill();
}

export function refreshWorkPill() {
  const pill = $("work-pill");
  if (!pill) return;
  pill.textContent = work ? work.title : t("work.noWork");
}

function setNovelTab(style) {
  $("novel-tab-general").setAttribute("aria-selected", style ? "false" : "true");
  $("novel-tab-style").setAttribute("aria-selected", style ? "true" : "false");
  $("novel-panel-general").hidden = style;
  $("novel-panel-style").hidden = !style;
  $("btn-novel-info-save").style.display = style ? "none" : "";
  if (style) autogrowStyleEditors();
}

function openNovelInfo() {
  if (!work) return;
  $("novel-info-title-input").value = work.title;
  showError("novel-info-dialog-error", "");
  setNovelTab(false);
  $("novel-info-dialog-backdrop").style.display = "";
  $("novel-info-title-input").focus();
  $("novel-info-title-input").select();
  loadNovelStyle();
}

function styleValue(p) {
  return p.kind === "custom" ? `c:${p.id}` : `s:${p.key}`;
}

function allStyles() {
  const sys = (workStyle?.presets ?? []).map((p) => ({ ...p, kind: "system" }));
  const own = (workStyle?.custom_styles ?? []).map((s) => ({
    kind: "custom", id: s.id, key: null, label: s.name, description: "",
    pov: s.pov, diction: s.diction, feedback: s.feedback,
  }));
  return [...sys, ...own];
}

function selectedStyle() {
  const v = $("novel-info-preset-select").value;
  return allStyles().find((p) => styleValue(p) === v) ?? null;
}

function editorVoice() {
  return { pov: $("novel-style-pov").value, diction: $("novel-style-diction").value,
           feedback: $("novel-style-feedback").value };
}

function editorDirty() {
  const p = selectedStyle();
  if (!p) return false;
  const e = editorVoice();
  if (p.kind === "custom" && $("novel-style-name").value.trim() !== p.label) return true;
  return e.pov !== (p.pov ?? "") || e.diction !== (p.diction ?? "") ||
    e.feedback !== (p.feedback ?? "");
}

function setStyleHint(text) {
  $("novel-style-hint").textContent = text ?? "";
}

function renderStyleList(keepValue) {
  const sel = $("novel-info-preset-select");
  const sys = (workStyle?.presets ?? [])
    .map((p) => `<option value="s:${esc(p.key)}">${esc(p.label)}</option>`).join("");
  const own = (workStyle?.custom_styles ?? [])
    .map((s) => `<option value="c:${s.id}">${esc(s.name)}</option>`).join("");
  sel.innerHTML = `<optgroup label="${t("work.styleGroupSystem")}">${sys}</optgroup>` +
    (own ? `<optgroup label="${t("work.styleGroupCustom")}">${own}</optgroup>` : "");
  const current = workStyle?.custom_style_id != null
    ? `c:${workStyle.custom_style_id}` : `s:${workStyle?.preset}`;
  sel.value = [...sel.options].some((o) => o.value === keepValue) ? keepValue
    : [...sel.options].some((o) => o.value === current) ? current
    : sel.options[0]?.value ?? "";
  fillStyleEditor();
}

function autogrowStyleEditors() {
  for (const id of ["novel-style-pov", "novel-style-diction", "novel-style-feedback"]) {
    const el = $(id);
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }
}

function fillStyleEditor() {
  const p = selectedStyle();
  $("novel-info-preset-desc").textContent = p?.description ?? "";
  $("novel-style-name").value = p?.kind === "custom" ? p.label : "";
  $("novel-style-pov").value = p?.pov ?? "";
  $("novel-style-diction").value = p?.diction ?? "";
  $("novel-style-feedback").value = p?.feedback ?? "";
  const own = p?.kind === "custom";
  $("btn-style-update").disabled = !own;
  $("btn-style-delete").disabled = !own;
  autogrowStyleEditors();
}

function renderStyleCurrent() {
  $("novel-style-current").textContent = workStyle?.custom_style_id != null
    ? t("work.styleCustomLabel", { name: workStyle.custom_style_name ?? t("work.styleDeletedName") })
    : (workStyle?.presets ?? []).find((p) => p.key === workStyle?.preset)?.label
      ?? workStyle?.preset ?? "-";
}

async function loadNovelStyle(keepValue) {
  const keep = keepValue ?? $("novel-info-preset-select").value ?? "";
  workStyle = null;
  $("novel-info-length-min").value = "";
  $("novel-info-length-max").value = "";
  $("novel-info-length-hint").textContent = t("work.lengthLoading");
  $("novel-info-preset-select").innerHTML = "";
  $("novel-info-preset-desc").textContent = "";
  try {
    workStyle = await api.workStyle(work.uid || work.id);
    $("novel-info-length-min").value = workStyle.length_min;
    $("novel-info-length-max").value = workStyle.length_max;
    $("novel-info-length-hint").textContent = workStyle.is_default ? t("work.lengthDefault") : "";
    renderStyleList(keep);
    renderStyleCurrent();
  } catch (e) {
    $("novel-info-length-hint").textContent = "";
    showError("novel-info-dialog-error", t("work.styleLoadFailed", { error: e }));
  }
}

async function saveCustomStyleAsNew() {
  const name = $("novel-style-name").value.trim();
  if (!name) {
    setStyleHint(t("work.styleNameRequiredNew"));
    $("novel-style-name").focus();
    return;
  }
  setStyleHint(t("work.saving"));
  try {
    const row = await api.createStyleCustom({ name, ...editorVoice() });
    setStyleHint(t("work.styleSaved", { name: row.name }));
    await loadNovelStyle(`c:${row.id}`);
  } catch (e) {
    setStyleHint(t("work.saveFailed", { error: e.message ?? e }));
  }
}

async function updateCustomStyle() {
  const p = selectedStyle();
  if (p?.kind !== "custom") return;
  const name = $("novel-style-name").value.trim();
  if (!name) {
    setStyleHint(t("work.styleNameRequired"));
    return;
  }
  setStyleHint(t("work.saving"));
  try {
    const row = await api.patchStyleCustom(p.id, { name, ...editorVoice() });
    setStyleHint(t("work.styleSaved", { name: row.name }));
    await loadNovelStyle(`c:${row.id}`);
  } catch (e) {
    setStyleHint(t("work.saveFailed", { error: e.message ?? e }));
  }
}

async function deleteCustomStyle() {
  const p = selectedStyle();
  if (p?.kind !== "custom") return;
  if (!window.confirm(t("work.styleDeleteConfirm", { name: p.label }))) return;
  setStyleHint(t("work.deleting"));
  try {
    const out = await api.deleteStyleCustom(p.id);
    const wasInUse = workStyle?.custom_style_id === p.id;
    await loadNovelStyle();
    setStyleHint(wasInUse || (out.works_reset ?? 0) > 0
      ? t("work.styleDeletedWithReset", { name: p.label })
      : t("work.styleDeleted", { name: p.label }));
  } catch (e) {
    setStyleHint(t("work.deleteFailed", { error: e.message ?? e }));
  }
}

async function useSelectedStyle() {
  const p = selectedStyle();
  if (!p) return;
  if (editorDirty()) {
    setStyleHint(t("work.saveEditsFirst"));
    return;
  }
  setStyleHint(t("work.applying"));
  try {
    workStyle = p.kind === "custom"
      ? await api.patchWorkStyle(work.uid || work.id, { custom_style_id: p.id })
      : await api.patchWorkStyle(work.uid || work.id, { preset: p.key });
    renderStyleList(styleValue(p));
    renderStyleCurrent();
    setStyleHint(t("work.styleInUse", { name: p.label }));
  } catch (e) {
    setStyleHint(t("work.applyFailed", { error: e.message ?? e }));
  }
}

function closeNovelInfo() {
  $("novel-info-dialog-backdrop").style.display = "none";
}

async function saveNovelInfo() {
  if (!work) return;
  const title = $("novel-info-title-input").value.trim();
  if (!title) {
    showError("novel-info-dialog-error", t("work.titleRequired"));
    $("novel-info-title-input").focus();
    return;
  }
  if (title.length > 80) {
    showError("novel-info-dialog-error", t("work.titleTooLong"));
    return;
  }
  const minRaw = $("novel-info-length-min").value.trim();
  const maxRaw = $("novel-info-length-max").value.trim();
  const lo = Number(minRaw);
  const hi = Number(maxRaw);
  if (workStyle && (!minRaw || !maxRaw || !Number.isInteger(lo) || !Number.isInteger(hi))) {
    showError("novel-info-dialog-error", t("work.lengthMustBeNumber"));
    return;
  }
  if (workStyle && !(1 <= lo && lo <= hi && hi <= 10000)) {
    showError("novel-info-dialog-error", t("work.lengthRange"));
    return;
  }
  const titleChanged = title !== work.title;
  const lengthChanged = workStyle && (lo !== workStyle.length_min || hi !== workStyle.length_max);
  if (!titleChanged && !lengthChanged) {
    closeNovelInfo();
    return;
  }
  const btn = $("btn-novel-info-save");
  btn.disabled = true;
  btn.textContent = t("work.saving");
  try {
    if (titleChanged) {
      work = { ...work, ...(await api.patchWork(work.uid || work.id, { title })) };
      refreshWorkPill();
      document.dispatchEvent(new CustomEvent("work-renamed"));
    }
    if (lengthChanged) {
      try {
        workStyle = await api.patchWorkStyle(work.uid || work.id, {
          length_min: lo, length_max: hi,
        });
      } catch (e) {
        showError("novel-info-dialog-error", titleChanged
          ? t("work.titleSavedSettingsFailed", { error: e })
          : t("work.settingsSaveFailed", { error: e }));
        return;
      }
    }
    closeNovelInfo();
  } catch (e) {
    showError("novel-info-dialog-error", String(e));
  } finally {
    btn.disabled = false;
    btn.textContent = t("work.save");
  }
}

export function initWork() {
  $("work-pill").addEventListener("click", openNovelInfo);
  $("btn-novel-info-cancel").addEventListener("click", closeNovelInfo);
  $("btn-novel-info-save").addEventListener("click", saveNovelInfo);
  $("novel-tab-general").addEventListener("click", () => setNovelTab(false));
  $("novel-tab-style").addEventListener("click", () => setNovelTab(true));
  $("novel-info-preset-select").addEventListener("change", () => {
    setStyleHint("");
    fillStyleEditor();
  });
  $("btn-style-save-new").addEventListener("click", saveCustomStyleAsNew);
  $("btn-style-update").addEventListener("click", updateCustomStyle);
  $("btn-style-delete").addEventListener("click", deleteCustomStyle);
  $("btn-style-use").addEventListener("click", useSelectedStyle);
  for (const id of ["novel-style-pov", "novel-style-diction", "novel-style-feedback"]) {
    $(id).addEventListener("input", autogrowStyleEditors);
  }
  $("novel-info-dialog-backdrop").addEventListener("click", (e) => {
    if (e.target === $("novel-info-dialog-backdrop")) closeNovelInfo();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && $("novel-info-dialog-backdrop").style.display !== "none") {
      closeNovelInfo();
    }
  });
  for (const id of ["novel-info-title-input", "novel-info-length-min", "novel-info-length-max"]) {
    $(id).addEventListener("keydown", (e) => {
      if (e.key === "Enter") saveNovelInfo();
    });
  }
}
