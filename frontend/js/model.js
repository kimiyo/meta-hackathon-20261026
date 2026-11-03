import { api } from "./api.js";
import { $, esc, showError } from "./util.js";
import { t } from "./i18n.js";

// 상단 모델 칩 + 모델 변경 다이얼로그.
let presets = [];
let current = "";

function renderList() {
  const box = $("model-list");
  box.innerHTML = presets
    .map(
      (p) => `<label class="model-opt">
        <input type="radio" name="model-pick" data-role="shell.model.pick-radio" value="${esc(p.id)}"${
          p.id === current ? " checked" : ""
        }>
        <span><span class="t">${esc(p.label)}</span>${
          p.id === current ? ` <span class="chip">${t("model.inUse")}</span>` : ""
        }<br>
        <span class="id">${esc(p.id)}</span><br>
        <span class="d">${esc(p.desc ?? "")}</span></span>
      </label>`
    )
    .join("");
  box.querySelectorAll('input[name="model-pick"]').forEach((r) =>
    r.addEventListener("change", () => {
      $("model-custom").value = "";
      updateContribWarn();
    })
  );
}

function updateContribWarn() {
  const v = $("model-custom").value.trim().toLowerCase();
  $("model-contrib-warn").style.display = v.includes("contributor") ? "" : "none";
}

function picked() {
  const custom = $("model-custom").value.trim();
  if (custom) return custom;
  const r = document.querySelector('input[name="model-pick"]:checked');
  return r ? r.value : "";
}

async function open() {
  showError("model-error", "");
  $("model-custom").value = "";
  $("model-token").value = "";
  updateContribWarn();
  try {
    const m = await api.model();
    current = m.model;
    presets = m.presets ?? [];
    renderList();
  } catch (e) {
    presets = [];
    current = "";
    renderList();
    showError("model-error", String(e));
  }
  $("model-backdrop").style.display = "";
}

function close() {
  $("model-backdrop").style.display = "none";
}

async function apply() {
  const btn = $("btn-model-apply");
  if (btn.disabled) return;
  const id = picked();
  if (!id) {
    showError("model-error", t("model.selectRequired"));
    return;
  }
  btn.disabled = true;
  btn.textContent = t("model.checking");
  showError("model-error", "");
  try {
    const token = $("model-token").value.trim();
    await api.setModel(id, token || undefined);
    close();
    document.dispatchEvent(new CustomEvent("model-changed"));
  } catch (e) {
    showError("model-error", String(e));
  } finally {
    btn.disabled = false;
    btn.textContent = t("model.apply");
  }
}

export function initModel() {
  $("model-info").addEventListener("click", (e) => {
    e.stopPropagation();
    open();
  });
  $("btn-model-cancel").addEventListener("click", close);
  $("btn-model-apply").addEventListener("click", apply);
  $("model-backdrop").addEventListener("click", (e) => {
    if (e.target === $("model-backdrop")) close();
  });
  $("model-custom").addEventListener("input", () => {
    if ($("model-custom").value.trim()) {
      document
        .querySelectorAll('input[name="model-pick"]')
        .forEach((r) => {
          r.checked = false;
        });
    }
    updateContribWarn();
  });
  document.addEventListener("keydown", (e) => {
    if ($("model-backdrop").style.display === "none") return;
    if (e.key === "Escape") close();
    if (e.key === "Enter") apply();
  });
  // 화면 언어 전환: 선택·입력값은 그대로 두고 목록 칩만 다시 그린다.
  document.addEventListener("locale-changed", () => {
    if ($("model-backdrop").style.display === "none") return;
    const sel = document.querySelector('input[name="model-pick"]:checked')?.value ?? "";
    renderList();
    if (sel) {
      for (const r of document.querySelectorAll('input[name="model-pick"]')) {
        r.checked = r.value === sel;
      }
    }
  });
}
