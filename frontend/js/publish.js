import { api } from "./api.js";
import { $, esc, showError } from "./util.js";
import { getWork } from "./work.js";
import { go, novelPath } from "./router.js";

// 화면 5 · 발간중: 발간요청 완료 회차 목록 (읽기 전용)

async function load() {
  showError("publish-error", "");
  const tb = $("publish-rows");
  const w = getWork();
  if (!w) {
    tb.innerHTML = `<tr><td colspan="6"><span class="muted small">작품을 선택하세요.</span></td></tr>`;
    return;
  }
  let designId = null;
  try {
    const full = await api.work(w.uid || w.id);
    designId = full.latest_design?.id;
  } catch (e) {
    tb.innerHTML = `<tr><td colspan="6"><span class="muted small">불러오기 실패</span></td></tr>`;
    showError("publish-error", String(e));
    return;
  }
  if (!designId) {
    tb.innerHTML = `<tr><td colspan="6"><span class="muted small">설계가 없습니다.</span></td></tr>`;
    return;
  }
  const d = await api.publishingEpisodes(designId);
  const rows = d.episodes ?? [];
  $("publish-count").textContent = `${rows.length}건`;
  if (!rows.length) {
    tb.innerHTML = `<tr><td colspan="6"><span class="muted small">발간중인 회차가 없습니다.</span></td></tr>`;
    return;
  }
  tb.innerHTML = rows.map((r) => `
    <tr data-ep="${r.episode_no}">
      <td><b>EP ${esc(r.episode_no)}</b></td>
      <td>${esc(r.title)}</td>
      <td class="mono small">${r.manuscript_revision_id ? `rev ${esc(r.manuscript_revision_id)}` : "-"}</td>
      <td class="small">열림 ${esc(r.issues.open)} / 전체 ${esc(r.issues.total)}</td>
      <td class="small muted">${esc((r.updated_at || "").slice(0, 16).replace("T", " "))}</td>
      <td><button class="btn sm" data-view data-role="publish.list.view-btn">보기</button></td>
    </tr>`).join("");
  tb.querySelectorAll("tr[data-ep]").forEach((tr) => {
    tr.querySelector("[data-view]").addEventListener("click", () => {
      const ep = Number(tr.dataset.ep);
      go(novelPath(w.uid || w.id, "review"));
      setTimeout(() => {
        document.dispatchEvent(new CustomEvent("review-episode", {
          detail: { ep },
        }));
      }, 50);
    });
  });
}

export function initPublish() {
  document.addEventListener("enter-screen", (e) => {
    if (e.detail?.section !== "s5") return;
    load().catch((err) => showError("publish-error", String(err)));
  });
}
