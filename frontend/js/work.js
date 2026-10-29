import { api } from "./api.js";
import { $, showError } from "./util.js";

// 현재 작업 중인 작품 컨텍스트. 라우터가 uid로 지정한다.
let work = null;

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
  if (title === work.title) {
    closeNovelInfo();
    return;
  }
  const btn = $("btn-novel-info-save");
  btn.disabled = true;
  btn.textContent = "저장 중…";
  try {
    work = { ...work, ...(await api.patchWork(work.uid || work.id, { title })) };
    closeNovelInfo();
    refreshWorkPill();
    document.dispatchEvent(new CustomEvent("work-renamed"));
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
}
