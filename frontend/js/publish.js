import { api } from "./api.js";
import { $, esc, showError, showToast } from "./util.js";
import { getWork } from "./work.js";
import { go, novelPath } from "./router.js";
import { openReader } from "./manuscript.js";

// 화면 5 · 발간중: 작품 발간 정보 + 커버 + 회차별 최종 출판본.

let detail = null; // { ep, title, platforms, nextNo, nextAvailable, hasApproved }

function workKey() {
  const w = getWork();
  return w ? w.uid || w.id : "";
}

function selectPublishTab(which) {
  const work = which !== "episodes";
  $("tabpane-pub-work").style.display = work ? "" : "none";
  $("tabpane-pub-episodes").style.display = work ? "none" : "";
  $("tab-pub-work").classList.toggle("active", work);
  $("tab-pub-episodes").classList.toggle("active", !work);
  $("tab-pub-work").setAttribute("aria-selected", String(work));
  $("tab-pub-episodes").setAttribute("aria-selected", String(!work));
}

function setBusy(btn, busy, label) {
  if (!btn) return;
  if (busy) {
    btn.dataset.label = btn.textContent;
    btn.textContent = label;
    btn.disabled = true;
  } else {
    btn.textContent = btn.dataset.label ?? btn.textContent;
    btn.disabled = false;
  }
}

async function loadWorkPanel() {
  const w = getWork();
  const coverView = $("pub-cover-view");
  if (!w) {
    $("pub-title").value = "";
    $("pub-title").placeholder = "";
    $("pub-pen").value = "";
    $("pub-pen").placeholder = "";
    $("pub-blurb").value = "";
    coverView.innerHTML = `<span class="muted small">작품을 선택하세요.</span>`;
    return;
  }
  const full = await api.work(w.uid || w.id);
  const titleInput = $("pub-title");
  titleInput.value = full.publish_title || full.title || "";
  titleInput.placeholder = "게재용 작품명";
  const penInput = $("pub-pen");
  penInput.value = full.publish_pen_name || full.author_pen_name || "";
  penInput.placeholder = "게재용 필명";
  $("pub-blurb").value = full.publish_blurb || "";
  renderCover(full.cover_url || "");
}

function renderCover(url) {
  const view = $("pub-cover-view");
  const dl = $("btn-pub-cover-download");
  const del = $("btn-pub-cover-delete");
  if (url) {
    view.innerHTML = `<img class="pub-cover-img" src="${esc(url)}?t=${Date.now()}" alt="커버 이미지">`;
    dl.href = url;
    dl.hidden = false;
    del.hidden = false;
  } else {
    view.innerHTML = `<span class="muted small">등록된 커버가 없습니다.</span>`;
    dl.hidden = true;
    del.hidden = true;
  }
  $("pub-cover-file").value = "";
  $("btn-pub-cover-upload").disabled = true;
}

async function generateBlurb() {
  if ($("pub-blurb").value.trim() &&
      !window.confirm("현재 소개글을 AI 생성 결과로 덮어쓸까요?")) {
    return;
  }
  const btn = $("btn-pub-blurb");
  setBusy(btn, true, "생성중…");
  try {
    const d = await api.generateBlurb(workKey());
    $("pub-blurb").value = d.blurb || "";
    await api.patchWork(workKey(), { publish_blurb: $("pub-blurb").value });
    showToast("AI 소개글을 생성·저장했습니다.");
  } catch (e) {
    showError("publish-error", String(e));
  } finally {
    setBusy(btn, false);
  }
}

const WORK_FIELDS = [
  ["pub-title", "publish_title", "게재 작품명"],
  ["pub-pen", "publish_pen_name", "작가 필명"],
  ["pub-blurb", "publish_blurb", "작품 소개글"],
];

async function autoSaveWorkField(inputId, apiField, label) {
  try {
    await api.patchWork(workKey(), { [apiField]: $(inputId).value });
    showToast(`${label}을(를) 저장했습니다.`);
  } catch (e) {
    showError("publish-error", String(e));
  }
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const s = String(reader.result || "");
      const idx = s.indexOf(",");
      resolve(idx >= 0 ? s.slice(idx + 1) : s);
    };
    reader.onerror = () => reject(new Error("파일을 읽지 못했습니다."));
    reader.readAsDataURL(file);
  });
}

async function uploadCover() {
  const input = $("pub-cover-file");
  const file = input.files?.[0];
  if (!file) return;
  if (!/\.(jpe?g|png)$/i.test(file.name)) {
    showError("publish-error", "커버는 jpg/png만 가능합니다.");
    return;
  }
  if (file.size > 5 * 1024 * 1024) {
    showError("publish-error", "커버는 5MB 이하여야 합니다.");
    return;
  }
  const btn = $("btn-pub-cover-upload");
  setBusy(btn, true, "업로드중…");
  try {
    const out = await api.uploadCover(workKey(), {
      filename: file.name,
      content_base64: await fileToBase64(file),
    });
    renderCover(out.cover_url || "");
    showToast("커버를 등록했습니다.");
  } catch (e) {
    showError("publish-error", String(e));
  } finally {
    setBusy(btn, false);
  }
}

async function deleteCover() {
  if (!window.confirm("커버 이미지를 삭제할까요?")) return;
  try {
    await api.deleteCover(workKey());
    renderCover("");
    showToast("커버를 삭제했습니다.");
  } catch (e) {
    showError("publish-error", String(e));
  }
}

async function loadCards() {
  const box = $("publish-cards");
  const w = getWork();
  if (!w) {
    box.innerHTML = `<span class="muted small">작품을 선택하세요.</span>`;
    $("publish-count").textContent = "";
    return [];
  }
  let designId = null;
  try {
    const full = await api.work(w.uid || w.id);
    designId = full.latest_design?.id;
  } catch (e) {
    box.innerHTML = `<span class="muted small">불러오기 실패</span>`;
    showError("publish-error", String(e));
    return [];
  }
  if (!designId) {
    box.innerHTML = `<span class="muted small">설계가 없습니다.</span>`;
    return [];
  }
  const d = await api.publishingEpisodes(designId);
  const rows = d.episodes ?? [];
  $("publish-count").textContent = `${rows.length}건`;
  if (!rows.length) {
    box.innerHTML = `<span class="muted small">발간중인 회차가 없습니다.</span>`;
    return [];
  }
  box.innerHTML = `<div class="pub-cards">${rows.map((r) => `
    <button class="pub-card" data-ep="${r.episode_no}" data-role="publish.list.card">
      <div class="t">EP ${esc(r.episode_no)} · ${esc(r.title)}</div>
      <div class="s">원고 ${r.manuscript_revision_id ? `rev ${esc(r.manuscript_revision_id)}` : "-"} · 이슈 열림 ${esc(r.issues.open)}/${esc(r.issues.total)} · ${esc((r.updated_at || "").slice(0, 16).replace("T", " "))}</div>
    </button>`).join("")}</div>`;
  box.querySelectorAll("[data-ep]").forEach((card) => {
    card.addEventListener("click", () => selectEpisode(Number(card.dataset.ep)));
  });
  return rows;
}

async function selectEpisode(ep) {
  document.querySelectorAll("#publish-cards [data-ep]").forEach((c) => {
    c.classList.toggle("active", Number(c.dataset.ep) === ep);
  });
  const body = $("pub-detail-body");
  body.innerHTML = `<span class="muted small">불러오는 중…</span>`;
  try {
    const d = await api.publishEdition(ep, workKey());
    const cards = document.querySelector(`#publish-cards [data-ep="${ep}"] .t`);
    detail = {
      ep,
      title: cards ? cards.textContent : `EP ${ep}`,
      platforms: d.platforms ?? ["문피아"],
      defaultPlatform: d.default_platform ?? "문피아",
      nextNo: d.next_episode_no,
      nextAvailable: !!d.next_available,
      hasApproved: !!d.has_approved,
    };
    renderDetail(d.edition);
  } catch (e) {
    showError("publish-error", String(e));
    body.innerHTML = `<span class="muted small">불러오기 실패</span>`;
  }
}

function renderDetail(edition) {
  const platform = edition?.platform || detail.defaultPlatform;
  const text = edition?.text || "";
  const teaserInfo = edition?.teaser_text
    ? `EP ${esc(edition.teaser_episode)} 예고 포함 (${esc(String(edition.teaser_text).length)}자)`
    : "예고 없음";
  $("pub-detail-title").textContent = detail.title;
  $("pub-detail-body").innerHTML = `
    <label class="f" for="pub-ed-platform">게시출판 플랫폼</label>
    <select id="pub-ed-platform" data-role="publish.detail.platform">
      ${detail.platforms.map((p) => `<option${p === platform ? " selected" : ""}>${esc(p)}</option>`).join("")}
    </select>
    <div class="btn-row">
      <button class="btn sm" id="btn-pub-ed-from" data-role="publish.detail.from-btn"${detail.hasApproved ? "" : " disabled"}>승인본으로 출판본 만들기</button>
      <button class="btn sm" id="btn-pub-ed-teaser" data-role="publish.detail.teaser-btn"${detail.nextAvailable ? "" : " disabled"}>다음화(EP ${esc(detail.nextNo)})의 시작 가져와서 출판본 만들기</button>
    </div>
    <div class="btn-row">
      <button class="btn sm primary" id="btn-pub-ed-save" data-role="publish.detail.save-btn">출판본 저장</button>
      <button class="btn sm" id="btn-pub-ed-full" data-role="publish.detail.full-btn">전체화면</button>
      <button class="btn sm ghost" id="btn-pub-ed-review" data-role="publish.detail.review-btn">작가검토로 이동</button>
    </div>
    <p class="muted small">${esc(teaserInfo)}${detail.hasApproved ? "" : " · 승인된 원고가 없습니다"}${detail.nextAvailable ? "" : ` · EP ${esc(detail.nextNo)} 원고가 없어 티저를 붙일 수 없습니다`}</p>
    <label class="f" for="pub-ed-text">최종 출판본 (<span id="pub-ed-count">${text.length}</span>자)</label>
    <textarea class="pub-edition-text" id="pub-ed-text" data-role="publish.detail.text">${esc(text)}</textarea>`;
  $("pub-ed-text").addEventListener("input", (e) => {
    $("pub-ed-count").textContent = e.target.value.length;
  });
  $("btn-pub-ed-from").addEventListener("click", fromApproved);
  $("btn-pub-ed-teaser").addEventListener("click", attachTeaser);
  $("btn-pub-ed-save").addEventListener("click", saveEdition);
  $("btn-pub-ed-full").addEventListener("click", () => {
    openReader({
      text: $("pub-ed-text").value,
      title: `최종 출판본 · ${detail.title}`,
      version: `플랫폼: ${$("pub-ed-platform").value}`,
    });
  });
  $("btn-pub-ed-review").addEventListener("click", () => {
    go(novelPath(workKey(), "review"));
    setTimeout(() => {
      document.dispatchEvent(new CustomEvent("review-episode", {
        detail: { ep: detail.ep },
      }));
    }, 50);
  });
}

function currentEditionText() {
  return $("pub-ed-text")?.value ?? "";
}

async function fromApproved() {
  if (currentEditionText().trim() &&
      !window.confirm("현재 출판본을 승인된 원고로 덮어쓸까요? 직접 수정한 내용은 사라집니다.")) {
    return;
  }
  const btn = $("btn-pub-ed-from");
  setBusy(btn, true, "생성중…");
  try {
    const d = await api.editionFromApproved(detail.ep, workKey());
    renderDetail(d.edition);
    showToast("승인본으로 출판본을 만들었습니다.");
  } catch (e) {
    showError("publish-error", String(e));
  } finally {
    setBusy(btn, false);
  }
}

async function attachTeaser() {
  const btn = $("btn-pub-ed-teaser");
  setBusy(btn, true, "티저 생성중…");
  try {
    const d = await api.editionTeaser(detail.ep, workKey());
    renderDetail(d.edition);
    showToast(`EP ${detail.nextNo} 예고(${d.teaser.length}자)를 붙였습니다.`);
  } catch (e) {
    showError("publish-error", String(e));
  } finally {
    setBusy(btn, false);
  }
}

async function saveEdition() {
  const btn = $("btn-pub-ed-save");
  setBusy(btn, true, "저장중…");
  try {
    const d = await api.savePublishEdition(detail.ep, {
      platform: $("pub-ed-platform").value,
      text: currentEditionText(),
    }, workKey());
    renderDetail(d.edition);
    showToast("출판본을 저장했습니다.");
  } catch (e) {
    showError("publish-error", String(e));
  } finally {
    setBusy(btn, false);
  }
}

async function load() {
  showError("publish-error", "");
  await loadWorkPanel();
  const rows = await loadCards();
  if (rows.length) await selectEpisode(rows[0].episode_no);
  else {
    detail = null;
    $("pub-detail-title").textContent = "회차 상세";
    $("pub-detail-body").innerHTML = `<span class="muted">회차를 선택하세요.</span>`;
  }
}

export function initPublish() {
  for (const [inputId, apiField, label] of WORK_FIELDS) {
    $(inputId).addEventListener("change", () => autoSaveWorkField(inputId, apiField, label));
  }
  $("btn-pub-blurb").addEventListener("click", generateBlurb);
  $("tab-pub-work").addEventListener("click", () => selectPublishTab("work"));
  $("tab-pub-episodes").addEventListener("click", () => selectPublishTab("episodes"));
  $("pub-cover-file").addEventListener("change", (e) => {
    $("btn-pub-cover-upload").disabled = !e.target.files?.length;
    showError("publish-error", "");
  });
  $("btn-pub-cover-upload").addEventListener("click", uploadCover);
  $("btn-pub-cover-delete").addEventListener("click", deleteCover);
  document.addEventListener("enter-screen", (e) => {
    if (e.detail?.section !== "s5") return;
    load().catch((err) => showError("publish-error", String(err)));
  });
}
