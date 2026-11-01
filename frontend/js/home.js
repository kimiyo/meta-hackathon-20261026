import { api } from "./api.js";
import { $, esc, showError } from "./util.js";

// 메인 · 소설 목록. 선택/생성 시 enter-work 이벤트(uid)를 쏘면 app.js가 라우팅한다.
let cache = [];
let pendingDelete = null;

const STAGE_LABEL = { ideas: "아이디어", design: "소설 설계", writing: "집필" };
const STAGE_SCREEN = { ideas: "ideas", design: "design", writing: "monitor" };

export function stageScreen(stage) {
  return STAGE_SCREEN[stage] ?? "ideas";
}

function fmtDate(iso) {
  if (!iso) return "-";
  return String(iso).slice(0, 16).replace("T", " ");
}

function cardHtml(w) {
  const done = w.completed_episodes ?? 0;
  const total = w.total_episodes ?? "?";
  return `<div class="work-item" data-uid="${esc(w.uid || w.id)}">
      <div class="t">${esc(w.title)}</div>
      <div class="meta">
        <span class="chip">${esc(STAGE_LABEL[w.stage] ?? w.stage ?? "")}</span>
        <span class="chip mono">EP ${esc(done)}/${esc(total)}</span>
        <span class="muted small">${esc(fmtDate(w.updated_at || w.created_at))}</span>
      </div>
      <div class="meta muted">${esc(w.logline ?? "")}</div>
      <div class="btn-row">
        <button class="btn sm primary" data-enter data-role="home.list.enter-btn">이어 쓰기</button>
        <button class="btn sm" data-delete data-role="home.list.delete-btn">삭제</button>
      </div>
    </div>`;
}

export async function refreshHome() {
  const box = $("home-list");
  showError("home-error", "");
  const q = ($("home-search")?.value ?? "").trim();
  try {
    const { works } = await api.works();
    cache = works ?? [];
    $("home-count").textContent = `${cache.length}편 · 최근 수정 순`;
    const list = q ? cache.filter((w) => (w.title ?? "").includes(q)) : cache;
    if (!cache.length) {
      box.innerHTML = `<span class="muted">첫 아이디어에 이야기의 뼈대를 세워 보세요.</span>`;
      return;
    }
    if (!list.length) {
      box.innerHTML = `<span class="muted">‘${esc(q)}’ 검색 결과가 없습니다.</span>
        <div class="btn-row"><button class="btn sm" id="btn-home-clear" data-role="home.list.clear-search-btn">전체 소설 보기</button></div>`;
      $("btn-home-clear").addEventListener("click", () => {
        $("home-search").value = "";
        refreshHome();
      });
      return;
    }
    box.innerHTML = list.map(cardHtml).join("");
    box.querySelectorAll(".work-item").forEach((el) => {
      const uid = el.dataset.uid;
      el.querySelector("[data-enter]").addEventListener("click", (e) => {
        e.stopPropagation();
        document.dispatchEvent(new CustomEvent("enter-work", { detail: uid }));
      });
      el.querySelector("[data-delete]").addEventListener("click", (e) => {
        e.stopPropagation();
        openDelete(uid);
      });
      el.addEventListener("click", () => {
        document.dispatchEvent(new CustomEvent("enter-work", { detail: uid }));
      });
    });
  } catch (e) {
    box.innerHTML = `<span class="muted small">작업을 마치지 못했습니다. 다시 시도해 주세요.</span>
      <div class="btn-row"><button class="btn sm" id="btn-home-retry" data-role="home.list.retry-btn">재시도</button></div>`;
    $("btn-home-retry").addEventListener("click", refreshHome);
    showError("home-error", String(e));
  }
}

// ---- 새 소설 다이얼로그 ----

function openDialog() {
  $("novel-dialog-title-input").value = "";
  showError("novel-dialog-error", "");
  $("novel-dialog-backdrop").style.display = "";
  $("novel-dialog-title-input").focus();
}

function closeDialog() {
  $("novel-dialog-backdrop").style.display = "none";
}

async function submitDialog() {
  const btn = $("btn-novel-dialog-confirm");
  if (btn.disabled) return; // Enter + 클릭 중복 발화 방지
  const title = $("novel-dialog-title-input").value.trim();
  if (!title) {
    showError("novel-dialog-error", "소설 제목을 입력하세요.");
    $("novel-dialog-title-input").focus();
    return;
  }
  if (title.length > 80) {
    showError("novel-dialog-error", "제목은 최대 80자까지 입력할 수 있습니다.");
    return;
  }
  btn.disabled = true;
  btn.textContent = "만드는 중…";
  showError("novel-dialog-error", "");
  try {
    const w = await api.createWork(title, "");
    closeDialog();
    document.dispatchEvent(new CustomEvent("enter-work", { detail: w.uid || w.id }));
  } catch (e) {
    showError("novel-dialog-error", String(e));
  } finally {
    btn.disabled = false;
    btn.textContent = "만들기 · 시작";
  }
}

// ---- 삭제 확인 다이얼로그 (제목을 동일하게 입력) ----

function openDelete(uid) {
  pendingDelete = cache.find((w) => String(w.uid || w.id) === String(uid)) ?? null;
  if (!pendingDelete) return;
  $("novel-delete-name").textContent = pendingDelete.title;
  $("novel-delete-title-input").value = "";
  showError("novel-delete-dialog-error", "");
  $("novel-delete-dialog-backdrop").style.display = "";
  $("novel-delete-title-input").focus();
}

function closeDelete() {
  pendingDelete = null;
  $("novel-delete-dialog-backdrop").style.display = "none";
}

async function submitDelete() {
  if (!pendingDelete) return;
  const btn = $("btn-novel-delete-confirm");
  if (btn.disabled) return;
  const typed = $("novel-delete-title-input").value;
  if (typed !== pendingDelete.title) {
    showError("novel-delete-dialog-error", "작품 제목을 동일하게 입력하세요.");
    return;
  }
  btn.disabled = true;
  btn.textContent = "삭제 중…";
  try {
    await api.deleteWork(pendingDelete.uid || pendingDelete.id, typed);
    closeDelete();
    refreshHome();
  } catch (e) {
    showError("novel-delete-dialog-error", String(e));
  } finally {
    btn.disabled = false;
    btn.textContent = "영구 삭제";
  }
}

export function initHome() {
  $("btn-home-create").addEventListener("click", openDialog);
  $("btn-novel-dialog-cancel").addEventListener("click", closeDialog);
  $("btn-novel-dialog-confirm").addEventListener("click", submitDialog);
  $("novel-dialog-backdrop").addEventListener("click", (e) => {
    if (e.target === $("novel-dialog-backdrop")) closeDialog();
  });
  $("novel-dialog-title-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitDialog();
  });
  $("btn-novel-delete-cancel").addEventListener("click", closeDelete);
  $("btn-novel-delete-confirm").addEventListener("click", submitDelete);
  $("novel-delete-dialog-backdrop").addEventListener("click", (e) => {
    if (e.target === $("novel-delete-dialog-backdrop")) closeDelete();
  });
  $("novel-delete-title-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitDelete();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if ($("novel-dialog-backdrop").style.display !== "none") closeDialog();
      if ($("novel-delete-dialog-backdrop").style.display !== "none") closeDelete();
    }
  });
  $("home-search")?.addEventListener("input", () => refreshHome());
  document.addEventListener("work-renamed", () => refreshHome());
}
