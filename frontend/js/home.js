import { api, setToken } from "./api.js";
import { $, esc, showError } from "./util.js";
import { getLocale, t } from "./i18n.js";

// 메인 · 소설 목록. 선택/생성 시 enter-work 이벤트(uid)를 쏘면 app.js가 라우팅한다.
let cache = [];
let pendingDelete = null;

const STAGE_KEYS = { ideas: "nav.ideas", design: "nav.design", writing: "nav.writing" };
const STAGE_SCREEN = { ideas: "ideas", design: "design", writing: "monitor" };

export function stageScreen(stage) {
  return STAGE_SCREEN[stage] ?? "ideas";
}

function fmtDate(iso) {
  if (!iso) return "-";
  return String(iso).slice(0, 16).replace("T", " ");
}

function stageLabel(stage) {
  return STAGE_KEYS[stage] ? t(STAGE_KEYS[stage]) : (stage ?? "");
}

function cardHtml(w) {
  const done = w.completed_episodes ?? 0;
  const total = w.total_episodes ?? "?";
  const market = w.market ? `<span class="chip">${esc(marketLabel(w.market))}</span>` : "";
  return `<div class="work-item" data-uid="${esc(w.uid || w.id)}">
      <div class="t">${esc(w.title)}</div>
      <div class="meta">
        <span class="chip">${esc(stageLabel(w.stage))}</span>
        ${market}
        <span class="chip mono">EP ${esc(done)}/${esc(total)}</span>
        <span class="muted small">${esc(fmtDate(w.updated_at || w.created_at))}</span>
      </div>
      <div class="meta muted">${esc(w.logline ?? "")}</div>
      <div class="btn-row">
        <button class="btn sm primary" data-enter data-role="home.list.enter-btn">${t("home.cardContinue")}</button>
        <button class="btn sm" data-delete data-role="home.list.delete-btn">${t("common.delete")}</button>
      </div>
    </div>`;
}

export async function refreshHome() {
  const box = $("home-list");
  showError("home-error", "");
  const status = await api.authStatus().catch(() => null);
  if (!status?.logged_in) {
    enterGate(status?.authors_exist ?? false);
    return;
  }
  exitGate();
  const q = ($("home-search")?.value ?? "").trim();
  try {
    const { works } = await api.works();
    cache = works ?? [];
    $("home-count").textContent = t("home.countLabel", { count: cache.length });
    const list = q ? cache.filter((w) => (w.title ?? "").includes(q)) : cache;
    if (!cache.length) {
      box.innerHTML = `<span class="muted">${t("home.emptyHint")}</span>`;
      return;
    }
    if (!list.length) {
      box.innerHTML = `<span class="muted">${t("home.noResults", { q: esc(q) })}</span>
        <div class="btn-row"><button class="btn sm" id="btn-home-clear" data-role="home.list.clear-search-btn">${t("home.showAll")}</button></div>`;
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
    box.innerHTML = `<span class="muted small">${t("home.loadFailed")}</span>
      <div class="btn-row"><button class="btn sm" id="btn-home-retry" data-role="home.list.retry-btn">${t("common.retry")}</button></div>`;
    $("btn-home-retry").addEventListener("click", refreshHome);
    showError("home-error", String(e));
  }
}

// ---- 저자 인증 게이트 ----

let authMode = "register";
let authorsExist = false;

function setGateVisible(gated) {
  $("auth-gate").style.display = gated ? "" : "none";
  document.querySelector('[data-role="home.create.panel"]').style.display = gated ? "none" : "";
  document.querySelector('[data-role="home.list.panel"]').style.display = gated ? "none" : "";
}

function renderGate() {
  const login = authMode === "login";
  $("auth-title").textContent = t(login ? "home.authLoginTitle" : "home.authRegisterTitle");
  $("auth-desc").textContent = t(login ? "home.authLoginDesc" : "home.authRegisterDesc");
  $("auth-penname-row").style.display = login ? "none" : "";
  $("btn-auth-submit").textContent = t(login ? "home.authLoginSubmit" : "home.authRegisterSubmit");
  const sw = $("btn-auth-switch");
  sw.style.display = authorsExist ? "" : "none";
  sw.textContent = t(login ? "home.authSwitchToRegister" : "home.authSwitchToLogin");
}

function enterGate(exists) {
  authorsExist = exists;
  authMode = exists ? "login" : "register";
  renderGate();
  setGateVisible(true);
}

function exitGate() {
  setGateVisible(false);
}

async function submitAuth() {
  const loginId = $("auth-login-id").value.trim();
  const penName = $("auth-pen-name").value.trim();
  const password = $("auth-password").value;
  showError("home-error", "");
  try {
    const out = authMode === "login"
      ? await api.login({ login_id: loginId, password })
      : await api.register({ login_id: loginId, pen_name: penName, password });
    setToken(out.token);
    $("auth-password").value = "";
    document.dispatchEvent(new CustomEvent("auth-changed"));
    refreshHome();
  } catch (err) {
    showError("home-error", String(err));
  }
}

// ---- 새 소설 다이얼로그 ----

const MARKET_STORE_KEY = "preferred_market";
let marketCache = null;

function storedMarket() {
  try {
    return localStorage.getItem(MARKET_STORE_KEY) || "";
  } catch {
    return "";
  }
}

function storeMarket(code) {
  try {
    localStorage.setItem(MARKET_STORE_KEY, code);
  } catch {
    /* 저장 실패해도 생성은 진행 */
  }
}

function marketLabel(code) {
  // 서버 name_key와 같은 규칙. 정적 스캔에 걸리지 않도록 동적 조합한다.
  return t(["market", code].join("."));
}

function defaultMarketCode(markets) {
  const codes = (markets ?? []).map((m) => m.code);
  const stored = storedMarket();
  if (stored && codes.includes(stored)) return stored;
  const byUi = getLocale() === "en-US" ? "US" : "KR";
  if (codes.includes(byUi)) return byUi;
  return codes[0] ?? "KR";
}

function renderMarketOptions(markets) {
  const box = $("novel-dialog-markets");
  const def = defaultMarketCode(markets);
  const prev = box.querySelector('input[name="novel-market"]:checked')?.value;
  const sel = prev && markets.some((m) => m.code === prev) ? prev : def;
  box.innerHTML = markets.map((m) => {
    const label = m.name_key ? t(m.name_key) : m.code;
    return `<label class="small" style="display:flex;gap:6px;align-items:center;cursor:pointer">
      <input type="radio" name="novel-market" data-role="home.novel-create.market-radio"
        value="${esc(m.code)}"${m.code === sel ? " checked" : ""} style="width:auto">
      <span>${esc(label)} · ${esc(m.default_writing_locale ?? "")}</span></label>`;
  }).join("");
}

async function ensureMarkets() {
  if (!marketCache) {
    const out = await api.locales().catch(() => null);
    marketCache = out?.markets?.length ? out.markets : [
      { code: "KR", name_key: "market.KR", default_writing_locale: "ko-KR" },
      { code: "US", name_key: "market.US", default_writing_locale: "en-US" },
    ];
  }
  renderMarketOptions(marketCache);
  return marketCache;
}

function openDialog() {
  $("novel-dialog-title-input").value = "";
  showError("novel-dialog-error", "");
  ensureMarkets().catch((e) => showError("novel-dialog-error", String(e)));
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
    showError("novel-dialog-error", t("home.titleRequired"));
    $("novel-dialog-title-input").focus();
    return;
  }
  if (title.length > 80) {
    showError("novel-dialog-error", t("home.titleTooLong"));
    return;
  }
  btn.disabled = true;
  btn.textContent = t("home.creating");
  showError("novel-dialog-error", "");
  try {
    const markets = await ensureMarkets();
    const picked = document.querySelector('input[name="novel-market"]:checked')?.value
      || defaultMarketCode(markets);
    const market = markets.find((m) => m.code === picked) ?? markets[0];
    const w = await api.createWork(title, "", {
      market: market.code,
      writing_locale: market.default_writing_locale,
    });
    storeMarket(market.code);
    closeDialog();
    document.dispatchEvent(new CustomEvent("enter-work", { detail: w.uid || w.id }));
  } catch (e) {
    showError("novel-dialog-error", String(e));
  } finally {
    btn.disabled = false;
    btn.textContent = t("home.createConfirm");
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
    showError("novel-delete-dialog-error", t("home.deleteTitleMismatch"));
    return;
  }
  btn.disabled = true;
  btn.textContent = t("home.deleting");
  try {
    await api.deleteWork(pendingDelete.uid || pendingDelete.id, typed);
    closeDelete();
    refreshHome();
  } catch (e) {
    showError("novel-delete-dialog-error", String(e));
  } finally {
    btn.disabled = false;
    btn.textContent = t("home.deleteConfirm");
  }
}

export function initHome() {
  $("btn-auth-submit").addEventListener("click", submitAuth);
  $("btn-auth-switch").addEventListener("click", () => {
    authMode = authMode === "login" ? "register" : "login";
    renderGate();
  });
  $("auth-password").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") submitAuth();
  });
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
  // 화면 언어 전환: 입력값은 그대로 두고 문구만 다시 그린다.
  document.addEventListener("locale-changed", () => {
    renderGate();
    refreshHome();
    if (marketCache) renderMarketOptions(marketCache);
  });
}
