import { api } from "./api.js";
import { $, esc, showError } from "./util.js";

// 메인 · 소설 목록. 선택/생성 시 enter-work 이벤트를 쏘면 app.js가 스튜디오로 진입한다.
export async function refreshHome() {
  const box = $("home-list");
  showError("home-error", "");
  try {
    const { works } = await api.works();
    const list = works ?? [];
    $("home-count").textContent = `${list.length}편`;
    if (!list.length) {
      box.innerHTML = `<span class="muted">아직 소설이 없습니다. 위 버튼으로 새로 만드세요.</span>`;
      return;
    }
    box.innerHTML = list
      .map(
        (w) => `<div class="work-item" data-enter="${esc(w.id)}">
          <div class="t">${esc(w.title)}</div>
          <div class="meta">${esc(w.logline ?? "")}</div>
        </div>`
      )
      .join("");
    box.querySelectorAll("[data-enter]").forEach((el) => {
      el.addEventListener("click", () => {
        document.dispatchEvent(
          new CustomEvent("enter-work", { detail: Number(el.dataset.enter) })
        );
      });
    });
  } catch (e) {
    showError("home-error", String(e));
  }
}

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
  const title = $("novel-dialog-title-input").value.trim();
  if (!title) {
    showError("novel-dialog-error", "소설 제목을 입력하세요.");
    $("novel-dialog-title-input").focus();
    return;
  }
  const btn = $("btn-novel-dialog-confirm");
  btn.disabled = true;
  btn.textContent = "만드는 중…";
  showError("novel-dialog-error", "");
  try {
    const w = await api.createWork(title, "");
    closeDialog();
    document.dispatchEvent(new CustomEvent("enter-work", { detail: w.id }));
  } catch (e) {
    showError("novel-dialog-error", String(e));
  } finally {
    btn.disabled = false;
    btn.textContent = "만들기 · 시작";
  }
}

export function initHome() {
  $("btn-home-create").addEventListener("click", openDialog);
  $("btn-novel-dialog-cancel").addEventListener("click", closeDialog);
  $("btn-novel-dialog-confirm").addEventListener("click", submitDialog);
  $("novel-dialog-backdrop").addEventListener("click", (e) => {
    if (e.target === $("novel-dialog-backdrop")) closeDialog();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && $("novel-dialog-backdrop").style.display !== "none") {
      closeDialog();
    }
  });
  $("novel-dialog-title-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitDialog();
  });
}
