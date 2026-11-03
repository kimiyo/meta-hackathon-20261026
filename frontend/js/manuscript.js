/* 공유 원고 모듈: 중앙 원고 패널의 차이·검색·편집·미리보기.
 * 집필 화면에서 사용하고, 작가 검토(읽기 전용)에서도 재사용한다.
 * 명세: 글쓰기 화면 — 중앙 원고 영역 요구사항 v1.1 (§8 구현 코드 기반). */

"use strict";

import { t, getLocale } from "./i18n.js";

// ---------- §8.5 글자 수와 줄 수 ----------

export function normNewlines(s) {
  return (s ?? "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
}

export function manuscriptCounts(text) {
  return {
    withSpaces: Array.from(text.replace(/[\r\n]/g, "")).length,
    withoutSpaces: Array.from(text.replace(/\s/g, "")).length,
    lines: text.split("\n").length,
  };
}

// 영어 단어 수: 서버 count_words와 동일 정의 (docs/i18n-contracts.md §8).
// 빈 문자열·공백만 → 0, null/undefined → "" 정규화.
export function countWords(text) {
  return ((text ?? "").match(/\S+/g) || []).length;
}

// ---------- §8.1 비교 대상 결정 ----------

export function selectComparison(doc, selectedId, editorText) {
  // editorText는 직접 수정 중에만 문자열로 전달한다.
  if (typeof editorText === "string" && selectedId !== "draft") {
    throw new Error(t("manuscript.switchToDraftFirst"));
  }
  const index = doc.versions.findIndex((v) => v.id === selectedId);
  if (selectedId !== "draft" && index < 0) throw new Error(t("manuscript.unknownVersion"));
  const current = doc.versions.find((v) => v.id === doc.currentVersionId);
  return selectedId === "draft"
    ? { text: editorText ?? doc.draft, previous: current?.content ?? null }
    : { text: doc.versions[index].content,
        previous: index > 0 ? doc.versions[index - 1].content : null };
}

// ---------- §8.2 줄·문자 단위 차이 계산 ----------

function diffOps(a, b, limit = 1500000) {
  let prefix = 0, suffix = 0;
  while (prefix < a.length && prefix < b.length && a[prefix] === b[prefix]) prefix++;
  while (suffix < a.length - prefix && suffix < b.length - prefix &&
         a[a.length - 1 - suffix] === b[b.length - 1 - suffix]) suffix++;
  const x = a.slice(prefix, a.length - suffix), y = b.slice(prefix, b.length - suffix);
  const out = a.slice(0, prefix).map((text) => ({ type: "equal", text }));
  if (x.length * y.length > limit) {
    for (const text of x) out.push({ type: "delete", text });
    for (const text of y) out.push({ type: "insert", text });
  } else {
    const width = y.length + 1, dp = new Uint32Array((x.length + 1) * width);
    for (let i = x.length - 1; i >= 0; i--) for (let j = y.length - 1; j >= 0; j--) {
      dp[i * width + j] = x[i] === y[j]
        ? dp[(i + 1) * width + j + 1] + 1
        : Math.max(dp[(i + 1) * width + j], dp[i * width + j + 1]);
    }
    let i = 0, j = 0;
    while (i < x.length || j < y.length) {
      if (i < x.length && j < y.length && x[i] === y[j]) {
        out.push({ type: "equal", text: x[i++] }); j++;
      } else if (i < x.length && (j === y.length || dp[(i + 1) * width + j] >= dp[i * width + j + 1])) {
        out.push({ type: "delete", text: x[i++] });
      } else {
        out.push({ type: "insert", text: y[j++] });
      }
    }
  }
  for (const text of a.slice(a.length - suffix)) out.push({ type: "equal", text });
  return out;
}

export function computeDiff(oldText, newText) {
  const ops = diffOps(oldText.split("\n"), newText.split("\n")), lines = [];
  let i = 0, number = 0;
  while (i < ops.length) {
    if (ops[i].type === "equal") { lines.push({ ...ops[i++], number: ++number }); continue; }
    const old = [], fresh = [];
    while (i < ops.length && ops[i].type !== "equal") {
      const op = ops[i++];
      (op.type === "delete" ? old : fresh).push(op.text);
    }
    for (let k = 0; k < Math.max(old.length, fresh.length); k++) {
      if (k >= fresh.length) lines.push({ type: "delete", text: old[k] });
      else if (k >= old.length) lines.push({ type: "insert", text: fresh[k], number: ++number });
      else lines.push({ type: "change", text: fresh[k], old: old[k], number: ++number,
                        words: diffOps(Array.from(old[k]), Array.from(fresh[k]), 250000) });
    }
  }
  return { lines,
           stats: { added: lines.filter((l) => l.type === "insert").length,
                    removed: lines.filter((l) => l.type === "delete").length,
                    changed: lines.filter((l) => l.type === "change").length } };
}

// ---------- §8.3 문자 위치를 이용한 검색과 강조 ----------

export function findMatches(text, input) {
  const query = input.trim();
  if (!query) return [];
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return [...text.matchAll(new RegExp(escaped, "giu"))]
    .map((m) => ({ start: m.index, end: m.index + m[0].length }));
}

export function moveMatch(index, delta, count) {
  return count ? (((index + delta) % count) + count) % count : -1;
}

// 변경 강조 조각과 검색 결과를 겹쳐 중첩 mark로 표시한다.
// start는 현재 원고의 UTF-16 오프셋이며 RegExp/textarea와 같다.
export function appendSearchFragment(parent, text, start, matches, currentIndex) {
  let cursor = 0;
  matches.forEach((match, index) => {
    const from = Math.max(0, match.start - start);
    const to = Math.min(text.length, match.end - start);
    if (from >= to) return;
    parent.append(document.createTextNode(text.slice(cursor, from)));
    const mark = document.createElement("mark");
    mark.className = "search-hit" + (index === currentIndex ? " current" : "");
    mark.textContent = text.slice(from, to);
    parent.append(mark);
    cursor = to;
  });
  parent.append(document.createTextNode(text.slice(cursor)));
}

// ---------- §8.6 변경 강조·검색 통합 렌더러와 편집기 스크롤 ----------

export function renderManuscript(container, { text, previous, showDiff, matches, currentIndex }) {
  const compared = showDiff && previous !== null;
  const result = compared ? computeDiff(previous, text) : {
    lines: text.split("\n").map((t, i) => ({ type: "equal", text: t, number: i + 1 })),
    stats: { added: 0, removed: 0, changed: 0 },
  };
  const fragment = document.createDocumentFragment();
  let offset = 0;
  let pendingDeleted = [];
  function flushDeleted() {
    if (!pendingDeleted.length) return;
    const detail = document.createElement("details");
    detail.className = "deleted-marker";
    const summary = document.createElement("summary");
    summary.textContent = pendingDeleted.length === 1
      ? t("manuscript.deletedMarkerOne")
      : t("manuscript.deletedMarkerMany", { count: pendingDeleted.length });
    detail.append(summary);
    for (const oldText of pendingDeleted) {
      const body = document.createElement("p");
      body.textContent = oldText;
      detail.append(body);
    }
    fragment.append(detail);
    pendingDeleted = [];
  }
  for (const line of result.lines) {
    if (line.type === "delete") { pendingDeleted.push(line.text); continue; }
    if (line.type === "change" && line.words.some((w) => w.type === "delete")) pendingDeleted.push(line.old);
    flushDeleted();
    const row = document.createElement("div");
    row.className = "manuscript-line " + line.type;
    row.dataset.line = line.number;
    const content = document.createElement("span");
    content.className = "line-text";
    if (line.words) {
      for (const part of line.words) {
        if (part.type === "delete") continue;
        const target = part.type === "insert" ? document.createElement("mark") : content;
        if (target !== content) { target.className = "diff-insert"; content.append(target); }
        appendSearchFragment(target, part.text, offset, matches, currentIndex);
        offset += part.text.length;
      }
    } else {
      appendSearchFragment(content, line.text, offset, matches, currentIndex);
      offset += line.text.length;
    }
    row.append(content);
    fragment.append(row);
    offset += 1; // 현재 본문의 LF. 마지막 줄 뒤의 가상 1은 이후 사용하지 않는다.
  }
  flushDeleted(); // 맨 끝까지 남은 연속 삭제
  const top = container.scrollTop, left = container.scrollLeft;
  container.replaceChildren(fragment);
  container.scrollTop = top;
  container.scrollLeft = left;
  return { stats: result.stats, compared };
}

export function scrollCurrentMatch(container) {
  const mark = container.querySelector(".search-hit.current");
  if (!mark) return;
  const a = mark.getBoundingClientRect(), b = container.getBoundingClientRect();
  // 외부 페이지를 스크롤하지 않고 본문 내부만 이동한다.
  container.scrollTop += a.top - b.top - container.clientTop - container.clientHeight / 2 + a.height / 2;
}

export function focusEditorMatch(editor, match) {
  editor.focus({ preventScroll: true });
  editor.setSelectionRange(match.start, match.end);
  const mirror = document.createElement("div"), css = getComputedStyle(editor);
  Object.assign(mirror.style, {
    position: "fixed", left: "0", top: "0", visibility: "hidden",
    boxSizing: "border-box", whiteSpace: "pre-wrap", overflowWrap: "break-word",
    width: editor.clientWidth + "px", border: "0",
  });
  for (const key of ["fontFamily", "fontSize", "fontWeight", "fontStyle", "lineHeight",
    "letterSpacing", "wordSpacing", "padding", "tabSize", "textIndent", "textAlign", "wordBreak"]) {
    mirror.style[key] = css[key];
  }
  mirror.append(document.createTextNode(editor.value.slice(0, match.start)));
  const marker = document.createElement("span");
  marker.textContent = editor.value.slice(match.start, match.end) || " ";
  mirror.append(marker);
  document.body.append(mirror);
  editor.scrollTop = Math.max(0, marker.offsetTop - editor.clientHeight / 2);
  mirror.remove();
}

// ---------- §8.4 전체화면과 읽기 설정 ----------

export const readerIcon = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M13 11L19 5M15.5 5H19V8.5M11 13L5 19M8.5 19H5V15.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';

export const copyIcon = '<svg width="16" height="15" viewBox="0 0 16 15" fill="none" aria-hidden="true"><g stroke="#48c5d3" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><polyline points="10.4,4.4 10.4,1.6 1.6,1.6 1.6,10.4 5.6,10.4"/><rect x="5.6" y="4.4" width="8.8" height="9" rx="1.8"/></g></svg>';

const READER_FONTS = [
  ["Noto Serif KR", "Noto+Serif+KR", "serif"], ["Noto Sans KR", "Noto+Sans+KR", "sans-serif"],
  ["Gowun Batang", "Gowun+Batang", "serif"], ["Gowun Dodum", "Gowun+Dodum", "sans-serif"],
  ["Hahmlet", "Hahmlet", "serif"], ["IBM Plex Sans KR", "IBM+Plex+Sans+KR", "sans-serif"],
  ["Nanum Myeongjo", "Nanum+Myeongjo", "serif"], ["Nanum Gothic", "Nanum+Gothic", "sans-serif"],
  ["Nanum Gothic Coding", "Nanum+Gothic+Coding", "monospace"], ["Song Myung", "Song+Myung", "serif"],
];
const READER_KEY = "storywrite.readerSettings";
const READER_DEFAULTS = { font: "Noto Serif KR", size: 18, line: 1.9, spacing: 0, width: 760 };

let readerDialog, readerSettings, readerPreviousFocus, readerPreviousOverflow, readerPreviousScroll;

function readerNumeric(v, min, max, step, fallback) {
  if (!["number", "string"].includes(typeof v) ||
      (typeof v === "string" && !v.trim()) || !Number.isFinite(Number(v))) return fallback;
  const bounded = Math.min(max, Math.max(min, Number(v)));
  return Number((min + Math.round((bounded - min) / step) * step).toFixed(2));
}

function readerLoad() {
  let data;
  try { data = JSON.parse(localStorage.getItem(READER_KEY)) || {}; } catch { data = {}; }
  return { font: READER_FONTS.some((f) => f[0] === data.font) ? data.font : READER_DEFAULTS.font,
           size: readerNumeric(data.size, 14, 40, 1, 18), line: readerNumeric(data.line, 1.2, 3, 0.1, 1.9),
           spacing: readerNumeric(data.spacing, -1, 4, 0.1, 0),
           width: readerNumeric(data.width, 480, 1100, 20, 760) };
}

function readerApply() {
  const font = READER_FONTS.find((f) => f[0] === readerSettings.font) || READER_FONTS[0];
  const fontId = "reader-font-" + font[1];
  if (!document.getElementById(fontId)) {
    const link = document.createElement("link");
    link.id = fontId; link.rel = "stylesheet";
    link.href = `https://fonts.googleapis.com/css2?family=${font[1]}&display=swap`;
    link.onerror = () => link.remove();
    document.head.append(link);
  }
  const body = readerDialog.querySelector("article");
  body.style.fontFamily = `"${font[0]}", ${font[2]}`;
  body.style.fontSize = readerSettings.size + "px";
  body.style.lineHeight = readerSettings.line;
  body.style.letterSpacing = readerSettings.spacing + "px";
  body.style.maxWidth = readerSettings.width + "px";
  for (const name of ["size", "line", "spacing", "width"]) {
    readerDialog.querySelector(`[name="${name}"]`).value = readerSettings[name];
    readerDialog.querySelector(`[data-value="${name}"]`).textContent =
      readerSettings[name] + (name === "line" ? t("manuscript.readerLineUnit") : t("manuscript.readerPxUnit"));
  }
  readerDialog.querySelector('[name="font"]').value = readerSettings.font;
}

function readerBuild() {
  readerDialog = document.createElement("dialog");
  readerDialog.id = "manuscript-reader";
  readerDialog.className = "manuscript-reader";
  readerDialog.setAttribute("aria-labelledby", "reader-title");
  readerDialog.innerHTML =
    `<header class="reader-header"><div><p class="eyebrow">STORY ATELIER / READING ROOM</p><h2 id="reader-title"></h2><p id="reader-version"></p></div><div class="reader-actions"><button type="button" id="reader-settings-toggle" aria-expanded="false" aria-controls="reader-settings">${t("manuscript.readerSettings")}</button><button type="button" id="reader-close" autofocus aria-label="${t("manuscript.readerCloseLabel")}">${t("manuscript.readerClose")} <span aria-hidden="true">✕</span></button></div></header>
    <dialog id="reader-settings" aria-labelledby="reader-settings-title"><header class="reader-settings-head"><h2 id="reader-settings-title">${t("manuscript.readerSettings")}</h2><button type="button" id="reader-settings-close" aria-label="${t("manuscript.readerSettingsCloseLabel")}" autofocus>${t("manuscript.readerClose")} ✕</button></header>
    <label>${t("manuscript.readerFont")}<select name="font" aria-label="${t("manuscript.readerFontLabel")}">${READER_FONTS.map((f) => `<option>${f[0]}</option>`).join("")}</select></label>
    <label>${t("manuscript.readerSize")} <output data-value="size"></output><input name="size" aria-label="${t("manuscript.readerSizeLabel")}" type="range" min="14" max="40" step="1"></label>
    <label>${t("manuscript.readerLine")} <output data-value="line"></output><input name="line" aria-label="${t("manuscript.readerLineLabel")}" type="range" min="1.2" max="3" step="0.1"></label>
    <label>${t("manuscript.readerSpacing")} <output data-value="spacing"></output><input name="spacing" aria-label="${t("manuscript.readerSpacingLabel")}" type="range" min="-1" max="4" step="0.1"></label>
    <label>${t("manuscript.readerWidth")} <output data-value="width"></output><input name="width" aria-label="${t("manuscript.readerWidthLabel")}" type="range" min="480" max="1100" step="20"></label>
    <button type="button" id="reader-reset">${t("manuscript.readerReset")}</button><p class="reader-settings-note">${t("manuscript.readerNote")}</p></dialog>
    <div class="reader-scroll" tabindex="0" aria-label="${t("manuscript.readerPreviewLabel")}"><article></article><p class="reader-end">${t("manuscript.readerEnd")}</p></div>`;
  document.body.append(readerDialog);
  readerDialog.querySelector("#reader-close").onclick = () => readerDialog.close();
  readerDialog.querySelector("#reader-settings-toggle").onclick = (e) => {
    const panel = readerDialog.querySelector("#reader-settings");
    panel.showModal();
    e.currentTarget.setAttribute("aria-expanded", "true");
  };
  const settingsDialog = readerDialog.querySelector("#reader-settings");
  readerDialog.querySelector("#reader-settings-close").onclick = () => settingsDialog.close();
  settingsDialog.addEventListener("close", () => {
    readerDialog.querySelector("#reader-settings-toggle").setAttribute("aria-expanded", "false");
    if (readerDialog.open) readerDialog.querySelector("#reader-settings-toggle").focus({ preventScroll: true });
  });
  function save() {
    readerApply();
    try { localStorage.setItem(READER_KEY, JSON.stringify(readerSettings)); } catch { /* Reading remains available without storage. */ }
  }
  readerDialog.querySelector("#reader-settings").oninput = (e) => {
    if (e.target.name) {
      readerSettings[e.target.name] = e.target.name === "font" ? e.target.value : Number(e.target.value);
      save();
    }
  };
  readerDialog.querySelector("#reader-reset").onclick = () => { readerSettings = { ...READER_DEFAULTS }; save(); };
  readerDialog.addEventListener("close", (e) => {
    if (e.target !== readerDialog) return;
    if (settingsDialog.open) settingsDialog.close();
    document.body.style.overflow = readerPreviousOverflow;
    if (readerPreviousFocus?.isConnected) readerPreviousFocus.focus({ preventScroll: true });
    if (readerPreviousScroll) {
      for (const [el, top, left] of readerPreviousScroll.ancestors) { el.scrollTop = top; el.scrollLeft = left; }
      window.scrollTo(readerPreviousScroll.x, readerPreviousScroll.y);
    }
  });
}

export function openReader({ text, title, version }) {
  if (!readerDialog) readerBuild();
  if (readerDialog.open) return;
  readerSettings = readerLoad();
  readerApply();
  readerDialog.querySelector("#reader-title").textContent = title || t("manuscript.previewTitleDefault");
  readerDialog.querySelector("#reader-version").textContent = version || "";
  // Plain text preserves manuscript line breaks and keeps generated HTML inert.
  readerDialog.querySelector("article").textContent = text || t("manuscript.emptyPreview");
  if (readerDialog.querySelector("#reader-settings").open) readerDialog.querySelector("#reader-settings").close();
  readerDialog.querySelector("#reader-settings-toggle").setAttribute("aria-expanded", "false");
  readerPreviousFocus = document.activeElement;
  readerPreviousOverflow = document.body.style.overflow;
  readerPreviousScroll = { x: window.scrollX, y: window.scrollY, ancestors: [] };
  for (let el = readerPreviousFocus; el; el = el.parentElement) {
    readerPreviousScroll.ancestors.push([el, el.scrollTop, el.scrollLeft]);
  }
  document.body.style.overflow = "hidden";
  try {
    readerDialog.showModal();
  } catch (error) {
    document.body.style.overflow = readerPreviousOverflow;
    throw error;
  }
  readerDialog.querySelector(".reader-scroll").scrollTop = 0;
}

export function connectReader(button, readCurrentText, readEpisodeTitle, readVersionLabel) {
  button.innerHTML = readerIcon;
  button.setAttribute("aria-label", t("manuscript.fullscreenPreview"));
  button.addEventListener("click", () => openReader({
    text: readCurrentText(),
    title: readEpisodeTitle(),
    version: readVersionLabel(),
  }));
}

// ---------- §8.8 브라우저 초안 복구본 ----------

export function createDraftRecovery(episodeId, onWarning) {
  const key = "storywrite.draft:" + episodeId;
  const warn = () => onWarning(t("manuscript.recoverySaveFailed"));
  return {
    read() {
      try {
        const value = JSON.parse(localStorage.getItem(key));
        return value && typeof value.content === "string" && Number.isInteger(value.baseRevision) &&
          typeof value.updatedAt === "string" ? value : null;
      } catch { return null; }
    },
    put(content, baseRevision) {
      try {
        localStorage.setItem(key, JSON.stringify({ content, baseRevision, updatedAt: new Date().toISOString() }));
      } catch { warn(); }
    },
    clearIfSame(content) {
      try { if (this.read()?.content === content) localStorage.removeItem(key); } catch { warn(); }
    },
  };
}

// ---------- §8.7 중앙 원고 패널 (집필용, §9.4 하단 포함) ----------

const LOCKED_STATES = ["approved", "publication_ready", "published"];

function stateLabel(doc, readOnly) {
  if (doc.state === "approved") return t("manuscript.statePublishing");
  if (doc.state === "publication_ready") return t("manuscript.statePubReady");
  if (doc.state === "published") return t("manuscript.statePublished");
  if (readOnly) return t("manuscript.stateInReview");
  return doc.draft.trim() ? t("manuscript.stateWriting") : t("manuscript.stateNotStarted");
}

function initialSelection(doc) {
  const current = doc.versions.find((v) => v.id === doc.currentVersionId);
  if (!doc.versions.length) return "draft";
  return current?.content === doc.draft ? doc.currentVersionId : "draft";
}

function normDoc(doc) {
  const out = structuredClone(doc);
  out.draft = normNewlines(out.draft);
  for (const v of out.versions) v.content = normNewlines(v.content);
  return out;
}

export function mountManuscriptPanel(root, options) {
  const { episodeNo, initialDocument } = options;
  const persist = options.persist ?? null;
  const loadDocument = options.loadDocument ?? null;
  const onQuoteToChat = options.onQuoteToChat ?? (() => {});
  const onHint = options.onHint ?? (() => {});
  const restore = options.restoreState ?? null;
  // 읽기 전용(작가 검토): 조회·검색·비교·전체화면·복사만 제공하고 편집·저장 UI를 숨긴다.
  const readOnly = options.readOnly === true;
  const roles = {
    versionSelect: "writing.episode.version-select",
    diffCheckbox: "writing.episode.diff-checkbox",
    body: "writing.episode.manuscript",
    ...(options.roles ?? {}),
  };

  let doc = normDoc(initialDocument);
  let editing = false, busy = false;
  let selected = initialSelection(doc);
  let query = "", matches = [], index = -1, timer, composing = false, editorComposing = false;
  let pendingRequest = null, activeWrite = null, halted = false,
      failedOperation = null, retryForbidden = false;

  const roHidden = readOnly ? " hidden" : "";
  root.innerHTML =
    `<section class="ms-panel" aria-label="${t("manuscript.panelLabel")}">` +
    `<div class="ms-head"><b>${t("manuscript.panelLabel")}</b> <span class="chip" data-ui="state"></span> ` +
    `<label class="small"${roHidden}><input type="checkbox" data-ui="include" checked> ${t("manuscript.includeDraft")}</label></div>` +
    `<div class="ms-toolbar">` +
    `<label class="small">${t("manuscript.version")} <select data-ui="versions" data-role="${roles.versionSelect}"></select></label>` +
    `<form data-ui="search" role="search" class="ms-search">` +
    `<input data-ui="query" type="search" aria-label="${t("manuscript.searchPlaceholder")}" placeholder="${t("manuscript.searchPlaceholder")}">` +
    `<button class="btn sm">${t("manuscript.search")}</button> <output data-ui="count" aria-live="polite"></output> ` +
    `<button type="button" class="btn sm" data-ui="prev">${t("manuscript.prevMatch")}</button>` +
    `<button type="button" class="btn sm" data-ui="next">${t("manuscript.nextMatch")}</button></form>` +
    `<button type="button" class="btn sm" data-ui="edit"${roHidden}>${t("manuscript.editDirectly")}</button>` +
    `<button type="button" class="btn sm" data-ui="save"${roHidden}>${t("manuscript.saveVersion")}</button>` +
    `<button type="button" class="btn sm" data-ui="restore"${roHidden}>${t("manuscript.restoreVersion")}</button>` +
    `<button type="button" class="btn sm" data-ui="reader">${t("manuscript.fullscreen")}</button>` +
    `<span class="ms-copywrap"><button type="button" class="btn sm" data-ui="copy" aria-label="${t("manuscript.copyLabel")}"></button>` +
    `<span class="ms-tip" data-ui="copy-tip" role="status" hidden></span></span></div>` +
    `<div class="ms-sub"><label class="small"><input type="checkbox" data-ui="diff" data-role="${roles.diffCheckbox}"> ${t("manuscript.diffLabel")}</label> ` +
    `<output data-ui="summary" aria-live="polite" class="small muted"></output></div>` +
    `<p data-ui="meta" class="small muted"></p>` +
    `<div data-ui="body" class="manuscript-body" data-role="${roles.body}" tabindex="0"></div>` +
    `<textarea data-ui="editor" class="manuscript-editor" aria-label="${t("manuscript.editorLabel")}" hidden></textarea>` +
    `<div data-ui="recovery" class="ms-recovery" hidden>` +
    `<p data-ui="rec-msg" class="small"></p>` +
    `<div class="btn-row">` +
    `<button type="button" class="btn sm" data-ui="rec-show">${t("manuscript.recCompare")}</button>` +
    `<button type="button" class="btn sm" data-ui="rec-fetch">${t("manuscript.recFetch")}</button>` +
    `<button type="button" class="btn sm" data-ui="rec-retry">${t("manuscript.recRetry")}</button>` +
    `<button type="button" class="btn sm" data-ui="rec-copy">${t("manuscript.recCopy")}</button>` +
    `<button type="button" class="btn sm" data-ui="rec-download">${t("manuscript.recDownload")}</button></div>` +
    `<div data-ui="rec-compare" hidden>` +
    `<div class="ms-compare"><div><h4>${t("manuscript.recLocal")}</h4><pre data-ui="rec-local"></pre></div>` +
    `<div><h4>${t("manuscript.recServer")}</h4><pre data-ui="rec-server"></pre></div></div>` +
    `<div class="btn-row">` +
    `<button type="button" class="btn sm primary" data-ui="rec-use-local">${t("manuscript.recUseLocal")}</button>` +
    `<button type="button" class="btn sm" data-ui="rec-use-server">${t("manuscript.recUseServer")}</button></div>` +
    `</div></div>` +
    `<div class="ms-foot small"><span data-ui="lines" class="muted"></span> ` +
    `<button type="button" class="btn sm" data-ui="quote"${roHidden}>${t("manuscript.quoteToChat")}</button> ` +
    `<button type="button" class="btn sm" data-ui="clear"${roHidden}>${t("manuscript.clearDraft")}</button></div>` +
    `<p data-ui="status" role="status" class="small muted"></p></section>`;

  const ui = (name) => root.querySelector(`[data-ui="${name}"]`);
  const recovery = createDraftRecovery(episodeNo, (message) => { ui("status").textContent = message; });
  const recovered = recovery.read();
  if (recovered?.content === doc.draft) recovery.clearIfSame(doc.draft);
  else if (recovered && !readOnly) halted = true;

  const locked = () => LOCKED_STATES.includes(doc.state);
  const text = () => selectComparison(doc, selected, editing ? ui("editor").value : undefined).text;
  const dirty = () => editing && ui("editor").value !== doc.draft;
  const label = () => selected === "draft" ? t("manuscript.draftLabel")
    : t("manuscript.versionShort", { number: doc.versions.find((v) => v.id === selected).number });

  function currentVersionLabel() {
    const v = doc.versions.find((x) => x.id === selected);
    if (selected === "draft") return t("manuscript.draftLabel");
    if (v.id === doc.approvedVersionId) {
      return t("manuscript.versionLabelApproved", { number: v.number, label: v.label });
    }
    return t("manuscript.versionLabel", { number: v.number, label: v.label });
  }

  function render() {
    const pair = selectComparison(doc, selected, editing ? ui("editor").value : undefined);
    ui("versions").replaceChildren();
    for (const v of [{ id: "draft", label: t("manuscript.draftLabel") }, ...doc.versions.slice().reverse()]) {
      const option = document.createElement("option");
      option.value = v.id;
      option.textContent = v.id === "draft" ? v.label
        : v.id === doc.approvedVersionId
          ? t("manuscript.versionLabelApproved", { number: v.number, label: v.label })
          : t("manuscript.versionLabel", { number: v.number, label: v.label });
      ui("versions").append(option);
    }
    ui("versions").value = selected;
    matches = findMatches(pair.text, query);
    index = matches.length ? Math.min(Math.max(0, index), matches.length - 1) : -1;
    const r = renderManuscript(ui("body"),
      { ...pair, showDiff: ui("diff").checked, matches, currentIndex: index });
    ui("body").hidden = editing;
    ui("editor").hidden = !editing;
    ui("count").textContent = query ? `${index < 0 ? 0 : index + 1} / ${matches.length}` : "";
    for (const name of ["prev", "next"]) { ui(name).hidden = !query; ui(name).disabled = !matches.length; }
    const counts = manuscriptCounts(pair.text);
    ui("summary").textContent = t("manuscript.countSummary", { withSpaces: counts.withSpaces, withoutSpaces: counts.withoutSpaces }) + " · " +
      (r.compared ? t("manuscript.diffStats", { added: r.stats.added, removed: r.stats.removed, changed: r.stats.changed })
        : pair.previous === null ? t("manuscript.firstVersion") : t("manuscript.diffOff"));
    ui("lines").textContent = t("manuscript.lineCount", { lines: counts.lines });
    const pos = doc.versions.findIndex((v) => v.id === selected), version = doc.versions[pos];
    const base = selected === "draft" ? doc.versions.find((v) => v.id === doc.currentVersionId)
      : doc.versions[pos - 1];
    ui("meta").textContent = `${currentVersionLabel()} · ${base ? t("manuscript.compareWith", { number: base.number }) : t("manuscript.noCompareBase")}` +
      (version ? ` · ${version.label} · ${new Date(version.createdAt).toLocaleString(getLocale())} · ${t("manuscript.readOnlyTag")}` : "") +
      (selected === doc.approvedVersionId ? ` · ${t("manuscript.approvedTag")}` : "");
    ui("state").textContent = stateLabel(doc, readOnly);
    ui("edit").textContent = editing ? t("manuscript.viewManuscript") : t("manuscript.editDirectly");
    ui("edit").disabled = busy || halted || locked() || (selected !== "draft" && selected !== doc.currentVersionId);
    ui("save").hidden = readOnly || selected !== "draft";
    ui("restore").hidden = readOnly || selected === "draft" || selected === doc.currentVersionId;
    for (const name of ["save", "restore", "clear"]) ui(name).disabled = busy || halted || locked();
    ui("quote").disabled = busy || halted;
    ui("versions").disabled = busy || halted;
    ui("editor").readOnly = busy || halted || locked();
    renderRecoveryBar();
  }

  function showRecovery(show) {
    ui("recovery").hidden = !show;
  }

  function renderRecoveryBar() {
    if (readOnly || !halted) { showRecovery(false); return; }
    showRecovery(true);
    ui("rec-show").hidden = false;
    ui("rec-fetch").hidden = false;
    ui("rec-retry").hidden = !(pendingRequest && !retryForbidden);
    const hasLocal = !!(editing ? ui("editor").value : doc.draft) || !!recovery.read();
    ui("rec-copy").hidden = !hasLocal;
    ui("rec-download").hidden = !hasLocal;
  }

  function localInput() {
    return editing ? ui("editor").value : recovery.read()?.content ?? doc.draft;
  }

  async function write(operation, payload) {
    if (readOnly) throw new Error(t("manuscript.readOnlyError"));
    if (busy) throw new Error(t("manuscript.savingNow"));
    if (halted) throw new Error(t("manuscript.resolveConflictFirst"));
    const signature = JSON.stringify({ operation, expectedRevision: doc.revision, ...payload });
    if (pendingRequest?.signature !== signature) {
      pendingRequest = { signature,
        request: { operation, expectedRevision: doc.revision, ...payload,
                   requestId: crypto.randomUUID() } };
    }
    busy = true;
    render();
    ui("status").textContent = t("manuscript.saving");
    try {
      activeWrite = persist(pendingRequest.request);
      const result = await activeWrite;
      doc = normDoc(result);
      recovery.clearIfSame(doc.draft);
      pendingRequest = null;
      failedOperation = null;
      retryForbidden = false;
      ui("status").textContent = t("manuscript.saved");
      return result;
    } catch (error) {
      halted = error.status === 409 || error.status === 423 || error.uncertain === true;
      failedOperation = operation;
      retryForbidden = error.status === 409 || error.status === 423;
      ui("status").textContent = error.message + " · " + t("manuscript.inputKept");
      if (halted) {
        ui("rec-msg").textContent = error.status === 409
          ? t("manuscript.conflictMsg", { revision: error.latestRevision ?? "?" })
          : error.status === 423 ? t("manuscript.lockedMsg")
          : t("manuscript.uncertainMsg");
      }
      throw error;
    } finally {
      busy = false;
      activeWrite = null;
      render();
    }
  }

  async function flush() {
    clearTimeout(timer);
    if (activeWrite) await activeWrite;
    if (editorComposing) throw new Error(t("manuscript.finishComposing"));
    if (dirty()) await write("draft", { content: ui("editor").value });
  }

  function move() {
    if (index < 0) return;
    if (editing) focusEditorMatch(ui("editor"), matches[index]);
    else scrollCurrentMatch(ui("body"));
  }

  const act = (fn) => async (event) => {
    try { await fn(event); }
    catch (error) { ui("status").textContent = error.message + " · " + t("manuscript.inputKept"); }
  };

  ui("versions").onchange = act(async () => {
    const target = ui("versions").value;
    try {
      await flush();
      selected = target;
      editing = false;
      render();
      ui("body").scrollTop = 0;
    } catch (error) {
      ui("versions").value = selected;
      throw error;
    }
  });
  ui("diff").onchange = render;
  ui("search").onsubmit = (event) => {
    event.preventDefault();
    if (composing || event.isComposing) return;
    query = ui("query").value.trim();
    index = 0;
    render();
    move();
  };
  ui("query").addEventListener("compositionstart", () => { composing = true; });
  ui("query").addEventListener("compositionend", () => { composing = false; });
  ui("query").onkeydown = (event) => {
    if (event.key === "Enter" && (composing || event.isComposing)) event.preventDefault();
  };
  ui("query").oninput = () => { if (!ui("query").value.trim()) { query = ""; render(); } };
  for (const [name, delta] of [["prev", -1], ["next", 1]]) {
    ui(name).onclick = () => { index = moveMatch(index, delta, matches.length); render(); move(); };
  }
  ui("edit").onclick = act(async () => {
    await flush();
    if (editing) editing = false;
    else { selected = "draft"; editing = true; ui("editor").value = doc.draft; }
    render();
    if (editing) ui("editor").focus({ preventScroll: true });
  });

  function changed() {
    recovery.put(ui("editor").value, doc.revision);
    clearTimeout(timer);
    if (editorComposing) return;
    // textarea는 브라우저가 LF로 정규화한다. value를 재대입하지 않아 커서를 보존한다.
    render();
    timer = setTimeout(() => { flush().catch(() => {}); }, 600);
  }
  ui("editor").addEventListener("compositionstart", () => { editorComposing = true; clearTimeout(timer); });
  ui("editor").addEventListener("compositionend", () => { editorComposing = false; changed(); });
  ui("editor").oninput = changed;

  ui("save").onclick = act(async () => {
    await flush();
    await write("version", { content: doc.draft, label: t("manuscript.manualSaveLabel") });
    selected = doc.currentVersionId;
    editing = false;
    render();
  });
  ui("restore").onclick = act(async () => {
    await flush();
    await write("restore", { sourceVersionId: selected });
    selected = doc.currentVersionId;
    editing = false;
    render();
  });
  ui("clear").onclick = act(async () => {
    if (!window.confirm(t("manuscript.clearConfirm"))) return;
    await flush();
    await write("draft", { content: "" });
    selected = "draft";
    editing = false;
    render();
  });

  function viewSelectionLines() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) return null;
    const lineOf = (node) => {
      const el = node?.nodeType === 1 ? node : node?.parentElement;
      return el?.closest?.(".line-text") ?? null;
    };
    const a = lineOf(sel.anchorNode), b = lineOf(sel.focusNode);
    if (!a || !b || !ui("body").contains(a) || !ui("body").contains(b)) return null;
    const la = Number(a.parentElement.dataset.line), lb = Number(b.parentElement.dataset.line);
    return la <= lb ? [la, lb] : [lb, la];
  }

  function editorSelectionLines() {
    const ed = ui("editor");
    const s = ed.selectionStart ?? 0, e = ed.selectionEnd ?? 0;
    if (e <= s) return null;
    const startLine = ed.value.slice(0, s).split("\n").length;
    let last = e - 1;
    if (ed.value[last] === "\n") last -= 1; // 다음 줄 첫머리에서 끝나면 다음 줄 제외
    const endLine = last < s ? startLine : ed.value.slice(0, last + 1).split("\n").length;
    return [startLine, Math.max(endLine, startLine)];
  }

  function quoteSelectionToChat() {
    if (busy || halted) return;
    const lines = editing ? editorSelectionLines() : viewSelectionLines();
    if (!lines) {
      onHint(t("manuscript.selectLinesFirst"));
      return;
    }
    onQuoteToChat({ versionId: selected, startLine: lines[0], endLine: lines[1] });
  }
  ui("quote").onclick = quoteSelectionToChat;
  ui("body").addEventListener("mouseup", () => {
    if (!editing) quoteSelectionToChat();
  });

  // ---- 복구·충돌 ----
  ui("rec-show").onclick = () => {
    const show = ui("rec-compare").hidden;
    ui("rec-compare").hidden = !show;
    if (show) {
      ui("rec-local").textContent = localInput();
      ui("rec-server").textContent = doc.draft;
    }
  };
  ui("rec-fetch").onclick = act(async () => {
    const latest = normDoc(await loadDocument());
    doc = latest;
    ui("rec-compare").hidden = false;
    ui("rec-local").textContent = localInput();
    ui("rec-server").textContent = doc.draft;
    ui("rec-msg").textContent = t("manuscript.fetchedLatest");
    render();
  });
  ui("rec-retry").onclick = act(async () => {
    await api_retryPending();
  });
  ui("rec-copy").onclick = act(async () => {
    const input = localInput();
    try {
      await navigator.clipboard.writeText(input);
      ui("status").textContent = t("manuscript.inputCopied");
    } catch {
      window.prompt(t("manuscript.copyPrompt"), input);
    }
  });
  ui("rec-download").onclick = () => {
    const blob = new Blob([localInput()], { type: "text/plain;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `episode-${episodeNo}-draft.txt`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  };
  ui("rec-use-local").onclick = act(async () => {
    await api_resumeFromLatest(structuredClone(doc), true, localInput());
    ui("rec-compare").hidden = true;
  });
  ui("rec-use-server").onclick = act(async () => {
    await api_resumeFromLatest(structuredClone(doc), false);
    ui("rec-compare").hidden = true;
  });

  connectReader(ui("reader"), text, () => doc.title || t("manuscript.panelLabel"), currentVersionLabel);

  let tipTimer;
  ui("copy").innerHTML = copyIcon;
  function showTip(message) {
    const tip = ui("copy-tip");
    tip.textContent = message;
    tip.hidden = false;
    clearTimeout(tipTimer);
    tipTimer = setTimeout(() => { tip.hidden = true; }, 3000);
  }
  async function copyText(value) {
    try {
      await navigator.clipboard.writeText(value);
      return;
    } catch { /* fall through to the legacy path */ }
    const ta = document.createElement("textarea");
    ta.value = value;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.append(ta);
    ta.select();
    try {
      if (!document.execCommand("copy")) throw new Error("copy failed");
    } finally {
      ta.remove();
    }
  }
  ui("copy").onclick = async () => {
    try {
      await copyText(text());
      showTip(t("manuscript.copied"));
    } catch {
      showTip(t("manuscript.copyFailed"));
    }
  };

  async function api_resumeFromLatest(latest, useLocal, localContent) {
    const local = localContent ?? (editing ? ui("editor").value : doc.draft);
    clearTimeout(timer);
    doc = normDoc(latest);
    halted = false;
    pendingRequest = null;
    failedOperation = null;
    retryForbidden = false;
    if (useLocal) {
      if (locked()) { halted = true; render(); throw new Error(t("manuscript.cannotRestoreLocked")); }
      selected = "draft";
      editing = true;
      ui("editor").value = local;
      recovery.put(local, doc.revision);
    } else {
      const old = recovery.read();
      if (old) recovery.clearIfSame(old.content);
      editing = false;
      selected = initialSelection(doc);
    }
    render();
    if (useLocal) { await flush(); recovery.clearIfSame(doc.draft); }
  }

  async function api_retryPending() {
    if (!pendingRequest) return;
    if (retryForbidden) throw new Error(t("manuscript.needLatestFirst"));
    const request = pendingRequest.request;
    // 같은 signature이므로 write가 같은 requestId를 재사용한다.
    halted = false;
    const { operation, expectedRevision, requestId, ...payload } = request;
    await write(operation, payload);
    if (operation !== "draft") { selected = doc.currentVersionId; editing = false; render(); }
  }

  // ---- 외부(전진·대화) 연동 ----
  async function refresh() {
    const latest = normDoc(await loadDocument());
    const keepSelection = latest.versions.some((v) => v.id === selected) || selected === "draft";
    doc = latest;
    if (!keepSelection) selected = initialSelection(doc);
    if (editing && selected === "draft") {
      // 편집 중 입력은 유지한다. flush 시 충돌하면 복구 흐름으로 안내한다.
    } else if (editing) {
      editing = false;
    }
    render();
    return structuredClone(doc);
  }

  function getUiState() {
    return { selected, query, index, showDiff: ui("diff").checked,
             include: ui("include").checked, scrollTop: ui("body").scrollTop,
             editing, editorText: editing ? ui("editor").value : null };
  }

  if (restore) {
    if (restore.selected && (restore.selected === "draft" ||
        doc.versions.some((v) => v.id === restore.selected))) selected = restore.selected;
    query = restore.query ?? "";
    index = restore.index ?? -1;
    ui("diff").checked = restore.showDiff ?? false;
    ui("include").checked = restore.include ?? true;
    if (restore.query) ui("query").value = restore.query;
    if (restore.editing && (selected === "draft") && !locked()) {
      editing = true;
      // 재마운트 전 flush 되었으므로 최신 초안을 연다. 묵은 editorText는 버린다.
      ui("editor").value = doc.draft;
    }
  }
  render();
  if (restore?.scrollTop) ui("body").scrollTop = restore.scrollTop;
  if (recovered && halted) {
    ui("rec-compare").hidden = false;
    ui("rec-local").textContent = recovered.content;
    ui("rec-server").textContent = doc.draft;
    ui("rec-msg").textContent = t("manuscript.browserRecovery");
    ui("status").textContent = t("manuscript.browserRecoveryStatus");
  }

  const beforeLeave = (event) => {
    if (dirty() || editorComposing || halted) { event.preventDefault(); event.returnValue = ""; }
  };
  window.addEventListener("beforeunload", beforeLeave);

  // 근거 이동(§7): 인용 구간으로 버전을 맞추고 검색·스크롤한다.
  function jumpToQuote(quoteText, revisionId) {
    const q = (quoteText || "").trim().slice(0, 200);
    if (!q) return false;
    if (revisionId != null) {
      const id = String(revisionId);
      if (doc.versions.some((v) => v.id === id)) {
        selected = id;
        editing = false;
      }
    }
    query = q;
    ui("query").value = q;
    index = 0;
    render();
    move();
    ui("body").focus({ preventScroll: true });
    return matches.length > 0;
  }

  return {
    flush, refresh, jumpToQuote,
    getDocument: () => structuredClone(doc),
    getRecovery: () => recovery.read(),
    getUiState,
    getIncludeDraft: () => ui("include").checked,
    isHalted: () => halted,
    isBusy: () => busy || !!activeWrite,
    quoteSelectionToChat,
    resumeFromLatest: api_resumeFromLatest,
    retryPending: api_retryPending,
    dispose() { clearTimeout(timer); clearTimeout(tipTimer); window.removeEventListener("beforeunload", beforeLeave); },
  };
}
