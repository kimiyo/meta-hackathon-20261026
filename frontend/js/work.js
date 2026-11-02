import { api } from "./api.js";
import { $, esc, showError } from "./util.js";

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
  pill.textContent = work ? work.title : "작품 없음";
}

function openNovelInfo() {
  if (!work) return;
  $("novel-info-title-input").value = work.title;
  showError("novel-info-dialog-error", "");
  $("novel-info-dialog-backdrop").style.display = "";
  $("novel-info-title-input").focus();
  $("novel-info-title-input").select();
  loadNovelStyle();
}

async function loadNovelStyle() {
  workStyle = null;
  $("novel-info-length-min").value = "";
  $("novel-info-length-max").value = "";
  $("novel-info-length-hint").textContent = "불러오는 중…";
  $("novel-info-preset-select").innerHTML = "";
  $("novel-info-preset-desc").textContent = "";
  try {
    workStyle = await api.workStyle(work.uid || work.id);
    $("novel-info-length-min").value = workStyle.length_min;
    $("novel-info-length-max").value = workStyle.length_max;
    $("novel-info-length-hint").textContent = workStyle.is_default ? "(기본값)" : "";
    $("novel-info-preset-select").innerHTML = (workStyle.presets ?? [])
      .map((p) => `<option value="${esc(p.key)}">${esc(p.label)}</option>`).join("");
    $("novel-info-preset-select").value = workStyle.preset;
    updatePresetDesc();
  } catch (e) {
    $("novel-info-length-hint").textContent = "";
    showError("novel-info-dialog-error", `소설 설정을 불러오지 못했습니다: ${e}`);
  }
}

function updatePresetDesc() {
  const key = $("novel-info-preset-select").value;
  const p = (workStyle?.presets ?? []).find((x) => x.key === key);
  $("novel-info-preset-desc").textContent = p ? p.description : "";
}

function closeNovelInfo() {
  $("novel-info-dialog-backdrop").style.display = "none";
}

async function saveNovelInfo() {
  if (!work) return;
  const title = $("novel-info-title-input").value.trim();
  if (!title) {
    showError("novel-info-dialog-error", "소설 제목을 입력하세요.");
    $("novel-info-title-input").focus();
    return;
  }
  if (title.length > 80) {
    showError("novel-info-dialog-error", "제목은 최대 80자까지 입력할 수 있습니다.");
    return;
  }
  const minRaw = $("novel-info-length-min").value.trim();
  const maxRaw = $("novel-info-length-max").value.trim();
  const lo = Number(minRaw);
  const hi = Number(maxRaw);
  if (workStyle && (!minRaw || !maxRaw || !Number.isInteger(lo) || !Number.isInteger(hi))) {
    showError("novel-info-dialog-error", "목표 분량을 숫자로 입력하세요.");
    return;
  }
  if (workStyle && !(1 <= lo && lo <= hi && hi <= 10000)) {
    showError("novel-info-dialog-error", "분량은 1 ≤ 최소 ≤ 최대 ≤ 10000 이어야 합니다.");
    return;
  }
  const preset = $("novel-info-preset-select").value;
  const titleChanged = title !== work.title;
  const lengthChanged = workStyle && (lo !== workStyle.length_min || hi !== workStyle.length_max);
  const presetChanged = workStyle && preset && preset !== workStyle.preset;
  const styleChanged = lengthChanged || presetChanged;
  if (!titleChanged && !styleChanged) {
    closeNovelInfo();
    return;
  }
  const btn = $("btn-novel-info-save");
  btn.disabled = true;
  btn.textContent = "저장 중…";
  try {
    if (titleChanged) {
      work = { ...work, ...(await api.patchWork(work.uid || work.id, { title })) };
      refreshWorkPill();
      document.dispatchEvent(new CustomEvent("work-renamed"));
    }
    if (styleChanged) {
      try {
        const body = {};
        if (lengthChanged) {
          body.length_min = lo;
          body.length_max = hi;
        }
        if (presetChanged) body.preset = preset;
        workStyle = await api.patchWorkStyle(work.uid || work.id, body);
      } catch (e) {
        showError("novel-info-dialog-error", `${titleChanged ? "제목은 저장됐으나 " : ""}설정 저장 실패: ${e}`);
        return;
      }
    }
    closeNovelInfo();
  } catch (e) {
    showError("novel-info-dialog-error", String(e));
  } finally {
    btn.disabled = false;
    btn.textContent = "저장";
  }
}

export function initWork() {
  $("work-pill").addEventListener("click", openNovelInfo);
  $("btn-novel-info-cancel").addEventListener("click", closeNovelInfo);
  $("btn-novel-info-save").addEventListener("click", saveNovelInfo);
  $("novel-info-preset-select").addEventListener("change", updatePresetDesc);
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
