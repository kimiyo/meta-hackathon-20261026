import { api, getToken, setToken } from "./api.js";
import { $, esc, showError } from "./util.js";
import { getLocale, setLocale, storedLocale, t } from "./i18n.js";
import { clearWorkSelection, initWork, refreshWorkPill, selectWorkByUid } from "./work.js";
import { initMonitor, refreshBudgetChips } from "./monitor.js";
import { initReview } from "./review.js";
import { initPublish } from "./publish.js";
import { initEntities } from "./entities.js";
import { initHome, refreshHome, stageScreen } from "./home.js";
import { enterIdeas, initIdeas } from "./ideas.js";
import { enterDesign, initDesign } from "./storydesign.js";
import { initAiLog } from "./ailog.js";
import { initModel } from "./model.js";
import { SCREENS, current, go, novelPath, onChange } from "./router.js";

// 탭 전환
function activateTab(sectionId) {
  document.querySelectorAll("#tabs button").forEach((b) =>
    b.classList.toggle("active", b.dataset.screen === sectionId));
  document.querySelectorAll("section.screen").forEach((s) =>
    s.classList.toggle("active", s.id === sectionId));
  window.scrollTo({ top: 0 });
}

// 메인(소설 목록) 보기
function showHome() {
  document.title = "StorySpine";
  document.querySelector("nav.tabs").style.display = "none";
  document.querySelectorAll("section.screen").forEach((s) =>
    s.classList.toggle("active", s.id === "home"));
  $("btn-home").style.display = "none";
  $("btn-export").style.display = "none";
  clearWorkSelection();
  refreshWorkPill();
  window.scrollTo({ top: 0 });
  refreshHome();
}

// 스튜디오 보기
async function showStudio(uid, screen) {
  if (!getToken()) {
    go("/");
    return;
  }
  document.querySelector("nav.tabs").style.display = "";
  $("btn-home").style.display = "";
  try {
    const w = await selectWorkByUid(uid);
    document.title = `${w.title} · StorySpine`;
  } catch {
    go("/");
    return;
  }
  const section = SCREENS[screen] ?? SCREENS.ideas;
  activateTab(section);
  $("btn-export").style.display = section === "ideas" ? "" : "none";
  refreshWorkPill();
  document.dispatchEvent(new CustomEvent("enter-screen", { detail: { section, uid } }));
  if (section === "ideas") enterIdeas();
  else if (section === "sdesign") enterDesign();
}

async function route() {
  const r = current();
  if (r.name === "home") {
    showHome();
    return;
  }
  await showStudio(r.uid, r.screen);
}

// 탭 클릭 → 현재 작품 uid 유지하며 화면 이동
document.querySelector("#tabs").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-screen]");
  if (!btn) return;
  const { getWork } = await import("./work.js");
  const w = getWork();
  if (!w) {
    go("/");
    return;
  }
  const entry = Object.entries(SCREENS).find(([, section]) => section === btn.dataset.screen);
  go(novelPath(w.uid || w.id, entry ? entry[0] : "ideas"));
});

// 소설 선택/생성 시 해당 작품의 마지막 단계로 진입
document.addEventListener("enter-work", async (e) => {
  try {
    const w = await api.work(e.detail);
    go(novelPath(w.uid || w.id, stageScreen(w.stage)));
  } catch {
    go("/");
  }
});

// 소설 제목 변경 시 브라우저 타이틀 동기화
document.addEventListener("work-renamed", async () => {
  const { getWork } = await import("./work.js");
  const w = getWork();
  if (w) document.title = `${w.title} · StorySpine`;
});

// 모델 변경 시 상단 칩 새로고침
document.addEventListener("model-changed", () => {
  refreshTopbar();
});

// 인증 만료 → 로그아웃 후 메인으로
document.addEventListener("auth-expired", () => {
  setToken("");
  updateAuthChip(null);
  go("/");
});

// 로그인·등록 후 상단 칩 갱신
document.addEventListener("auth-changed", async () => {
  try {
    const st = await api.authStatus();
    updateAuthChip(st.logged_in ? st : null);
  } catch {
    updateAuthChip(null);
  }
});

// 작가명 수정 다이얼로그
function openRename() {
  $("rename-pen-name").value = $("user-chip").textContent;
  $("rename-password").value = "";
  showError("rename-error", "");
  $("rename-backdrop").style.display = "";
  $("rename-pen-name").focus();
  $("rename-pen-name").select();
}

function closeRename() {
  $("rename-backdrop").style.display = "none";
}

async function submitRename() {
  const btn = $("btn-rename-confirm");
  if (btn.disabled) return;
  const penName = $("rename-pen-name").value.trim();
  const password = $("rename-password").value;
  if (!penName) {
    showError("rename-error", t("common.renamePenNameRequired"));
    return;
  }
  if (!password) {
    showError("rename-error", t("common.renamePasswordRequired"));
    return;
  }
  btn.disabled = true;
  try {
    const out = await api.updateMe({ pen_name: penName, password });
    $("user-chip").textContent = out.pen_name;
    closeRename();
  } catch (err) {
    showError("rename-error", String(err));
  } finally {
    btn.disabled = false;
  }
}

function updateAuthChip(st) {
  const chip = $("user-chip");
  const btn = $("btn-logout");
  if (st?.logged_in) {
    chip.style.display = "";
    chip.textContent = st.pen_name || st.login_id;
    btn.style.display = "";
  } else {
    chip.style.display = "none";
    btn.style.display = "none";
  }
}

$("btn-home").addEventListener("click", () => go("/"));
$("btn-logout").addEventListener("click", async () => {
  try {
    await api.logout();
  } catch {
    /* 토큰이 이미 무효해도 로컬은 비운다 */
  }
  setToken("");
  updateAuthChip(null);
  go("/");
});
document.querySelector(".logo").style.cursor = "pointer";
document.querySelector(".logo").addEventListener("click", () => go("/"));

// 상단 상태: 모델·헬스·토큰 예산
async function refreshTopbar() {
  const healthChip = $("health-chip");
  const modelInfo = $("model-info");
  try {
    const h = await api.health();
    const ok = h.db === "ok";
    healthChip.innerHTML = ok
      ? `<i></i> ${t("common.healthHealthy", { model: esc(h.model ?? "?") })}`
      : `<i style="background:#d4695e"></i> ${t("common.healthDbError")}`;
    if (modelInfo) modelInfo.textContent = `${h.model ?? "?"} · ${h.mode ?? ""}`;
  } catch (e) {
    healthChip.innerHTML = `<i style="background:#d4695e"></i> ${t("common.healthOffline")}`;
    if (modelInfo) modelInfo.textContent = String(e).slice(0, 80);
  }
}

// 정적 HTML의 data-i18n(*) 속성을 현재 화면 언어로 반영한다.
// 번역 누락 키는 원본 HTML(한국어)을 그대로 둔다.
export function applyI18n(root = document) {
  for (const el of root.querySelectorAll("[data-i18n]")) {
    const key = el.getAttribute("data-i18n");
    const value = t(key);
    if (value !== key) el.textContent = value;
  }
  for (const el of root.querySelectorAll("[data-i18n-placeholder]")) {
    const key = el.getAttribute("data-i18n-placeholder");
    const value = t(key);
    if (value !== key) el.setAttribute("placeholder", value);
  }
  for (const el of root.querySelectorAll("[data-i18n-title]")) {
    const key = el.getAttribute("data-i18n-title");
    const value = t(key);
    if (value !== key) el.setAttribute("title", value);
  }
  for (const el of root.querySelectorAll("[data-i18n-aria-label]")) {
    const key = el.getAttribute("data-i18n-aria-label");
    const value = t(key);
    if (value !== key) el.setAttribute("aria-label", value);
  }
  for (const el of root.querySelectorAll("[data-i18n-content]")) {
    const key = el.getAttribute("data-i18n-content");
    const value = t(key);
    if (value !== key) el.setAttribute("content", value);
  }
}

function syncLocaleDocument() {
  document.documentElement.lang = getLocale() === "en-US" ? "en" : "ko";
  const sel = $("locale-select");
  if (sel) sel.value = getLocale();
}

// 언어 전환 전후로 입력값을 보존한다. 다시 그려진 노드는 id 기준으로 복원.
function snapshotInputs() {
  const snap = [];
  document.querySelectorAll("input, textarea, select").forEach((el) => {
    if (el.type === "file") return;
    snap.push({
      el,
      id: el.id || "",
      checked: el.type === "checkbox" || el.type === "radio" ? el.checked : null,
      value: el.value,
    });
  });
  return snap;
}

function restoreInputs(snap) {
  for (const s of snap) {
    const target = s.el.isConnected
      ? s.el
      : (s.id && document.getElementById(s.id)) || null;
    if (!target) continue;
    if (target.type === "checkbox" || target.type === "radio") {
      if (s.checked !== null) target.checked = s.checked;
    } else if ("value" in target) {
      target.value = s.value;
    }
  }
}

function initLocaleSelect() {
  const sel = $("locale-select");
  if (!sel) return;
  sel.value = storedLocale() || "ko-KR";
  sel.addEventListener("change", async () => {
    const next = sel.value;
    if (next === getLocale()) return;
    const snap = snapshotInputs();
    try {
      await setLocale(next);
    } catch (e) {
      console.error("locale switch failed", e);
      sel.value = getLocale();
    } finally {
      restoreInputs(snap);
    }
  });
}

// 각 탭 초기화 + 라우터 시작
initWork();
initDesign();
initMonitor();
initReview();
initPublish();
initEntities();
initHome();
initIdeas();
initAiLog();
initModel();
$("user-chip").addEventListener("click", openRename);
$("btn-rename-cancel").addEventListener("click", closeRename);
$("btn-rename-confirm").addEventListener("click", submitRename);
$("rename-backdrop").addEventListener("click", (ev) => {
  if (ev.target === $("rename-backdrop")) closeRename();
});
$("rename-password").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") submitRename();
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && $("rename-backdrop").style.display !== "none") closeRename();
});
// 화면 언어 전환: 셸 정적 문구·문서 언어·상단 상태를 다시 그린다.
// 각 화면의 동적 문구는 해당 모듈의 locale-changed 리스너가 담당한다.
document.addEventListener("locale-changed", () => {
  syncLocaleDocument();
  applyI18n();
  refreshTopbar();
  refreshWorkPill();
});
async function boot() {
  initLocaleSelect();
  try {
    await setLocale(storedLocale() || "ko-KR");
  } catch (e) {
    console.error("locale unavailable, staying with built-in Korean", e);
  }
  syncLocaleDocument();
  applyI18n();
  refreshTopbar();
  refreshBudgetChips();
  api.authStatus()
    .then((st) => updateAuthChip(st.logged_in ? st : null))
    .catch(() => updateAuthChip(null));
  onChange(() => route());
  route();
}
boot();
