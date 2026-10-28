import { api } from "./api.js";
import { $, esc } from "./util.js";
import { clearWorkSelection, initDesign, refreshWorkPill, selectWork } from "./design.js";
import { initMonitor, refreshBudgetChips } from "./monitor.js";
import { initReview } from "./review.js";
import { initEntities } from "./entities.js";
import { initHome, refreshHome } from "./home.js";

// 탭 전환
function activateTab(screenId) {
  document.querySelectorAll("#tabs button").forEach((b) =>
    b.classList.toggle("active", b.dataset.screen === screenId)
  );
  document.querySelectorAll("section.screen").forEach((s) =>
    s.classList.toggle("active", s.id === screenId)
  );
  window.scrollTo({ top: 0 });
}

document.querySelectorAll("#tabs button").forEach((btn) => {
  btn.addEventListener("click", () => activateTab(btn.dataset.screen));
});

// 메인(소설 목록) 보기
function showHome() {
  document.querySelector("nav.tabs").style.display = "none";
  document.querySelectorAll("section.screen").forEach((s) =>
    s.classList.toggle("active", s.id === "home")
  );
  $("btn-home").style.display = "none";
  clearWorkSelection();
  refreshWorkPill();
  window.scrollTo({ top: 0 });
  refreshHome();
}

// 스튜디오(탭 화면) 보기
function showStudio(screenId = "s1") {
  document.querySelector("nav.tabs").style.display = "";
  $("btn-home").style.display = "";
  activateTab(screenId);
  refreshWorkPill();
}

// 소설 선택/생성 시 스튜디오로 진입
document.addEventListener("enter-work", async (e) => {
  await selectWork(e.detail);
  showStudio("s1");
});

$("btn-home").addEventListener("click", showHome);

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

// 각 탭 초기화 + 메인 화면으로 시작
initDesign();
initMonitor();
initReview();
initEntities();
initHome();
refreshTopbar();
refreshBudgetChips();
showHome();
