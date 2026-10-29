import { api } from "./api.js";
import { $, esc } from "./util.js";
import { clearWorkSelection, initWork, refreshWorkPill, selectWorkByUid } from "./work.js";
import { initMonitor, refreshBudgetChips } from "./monitor.js";
import { initReview } from "./review.js";
import { initEntities } from "./entities.js";
import { initHome, refreshHome, stageScreen } from "./home.js";
import { enterIdeas, initIdeas } from "./ideas.js";
import { enterDesign, initDesign } from "./storydesign.js";
import { initAiLog } from "./ailog.js";
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
  document.querySelector("nav.tabs").style.display = "";
  $("btn-home").style.display = "";
  try {
    await selectWorkByUid(uid);
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

$("btn-home").addEventListener("click", () => go("/"));
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
      ? `<i></i> healthy · ${esc(h.model ?? "?")}`
      : `<i style="background:#d4695e"></i> DB 오류`;
    if (modelInfo) modelInfo.textContent = `${h.model ?? "?"} · ${h.mode ?? ""}`;
  } catch (e) {
    healthChip.innerHTML = `<i style="background:#d4695e"></i> 연결 실패`;
    if (modelInfo) modelInfo.textContent = String(e).slice(0, 80);
  }
}

// 각 탭 초기화 + 라우터 시작
initWork();
initDesign();
initMonitor();
initReview();
initEntities();
initHome();
initIdeas();
initAiLog();
refreshTopbar();
refreshBudgetChips();
onChange(() => route());
route();
