const BASE = "/api/v1";
const TOKEN_KEY = "auth-token";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function setToken(t) {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

function mustWorkKey(workKey) {
  if (!workKey) throw new Error("작품이 선택되지 않았습니다.");
  return workKey;
}

function epQuery(workKey) {
  return new URLSearchParams({ work: mustWorkKey(workKey) }).toString();
}

async function req(path, options = {}) {
  const headers = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  Object.assign(headers, options.headers ?? {});
  const { timeoutMs, ...fetchOptions } = options;
  let resp;
  try {
    resp = await fetch(BASE + path, {
      ...fetchOptions, headers,
      signal: timeoutMs ? AbortSignal.timeout(timeoutMs) : undefined,
    });
  } catch (e) {
    if (timeoutMs && (e?.name === "TimeoutError" || e?.name === "AbortError")) {
      throw new Error("요청 시간이 초과되었습니다. 다시 시도하세요.");
    }
    throw e;
  }
  if (resp.status === 401 && !path.startsWith("/auth/")) {
    document.dispatchEvent(new CustomEvent("auth-expired"));
  }
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`API ${resp.status}: ${text.slice(0, 200)}`);
  }
  if (resp.status === 204) return null;
  return resp.json();
}
// 원고 저장용: 상태·코드·충돌 revision을 담은 오류를 던진다.
async function reqEx(path, options = {}) {
  const headers = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  Object.assign(headers, options.headers ?? {});
  const { timeoutMs, ...fetchOptions } = options;
  let resp;
  try {
    resp = await fetch(BASE + path, {
      ...fetchOptions,
      headers,
      signal: timeoutMs ? AbortSignal.timeout(timeoutMs) : undefined,
    });
  } catch (cause) {
    throw Object.assign(new Error("저장 응답을 확인하지 못했습니다."),
      { uncertain: true, cause });
  }
  if (resp.status === 401 && !path.startsWith("/auth/")) {
    document.dispatchEvent(new CustomEvent("auth-expired"));
  }
  let body = null;
  try {
    body = await resp.json();
  } catch {
    body = null;
  }
  if (!resp.ok) {
    const detail = body?.detail ?? {};
    const message = typeof detail === "string"
      ? detail
      : detail.message ?? `API ${resp.status}`;
    throw Object.assign(new Error(message), {
      status: resp.status,
      code: detail?.code,
      latestRevision: detail?.latestRevision,
      uncertain: resp.status >= 500,
    });
  }
  return body;
}


export const api = {
  health: () => req("/health"),
  authStatus: () => req("/auth/status"),
  register: (body) =>
    req("/auth/register", { method: "POST", body: JSON.stringify(body) }),
  login: (body) =>
    req("/auth/login", { method: "POST", body: JSON.stringify(body) }),
  logout: () => req("/auth/logout", { method: "POST" }),
  me: () => req("/auth/me"),
  updateMe: (body) =>
    req("/auth/me", { method: "PATCH", body: JSON.stringify(body) }),
  works: () => req("/works"),
  createWork: (title, logline) =>
    req("/works", { method: "POST", body: JSON.stringify({ title, logline }) }),
  patchWork: (key, body) =>
    req(`/works/${key}`, { method: "PATCH", body: JSON.stringify(body) }),
  workStyle: (key) => req(`/works/${key}/style`),
  patchWorkStyle: (key, body) =>
    req(`/works/${key}/style`, { method: "PATCH", body: JSON.stringify(body) }),
  styleCustoms: () => req("/style-customs"),
  createStyleCustom: (body) =>
    req("/style-customs", { method: "POST", body: JSON.stringify(body) }),
  patchStyleCustom: (id, body) =>
    req(`/style-customs/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteStyleCustom: (id) => req(`/style-customs/${id}`, { method: "DELETE" }),
  deleteWork: (key, title) =>
    req(`/works/${key}`, {
      method: "DELETE",
      body: JSON.stringify({ title }),
    }),
  work: (key) => req(`/works/${key}`),
  uploadCover: (key, body) =>
    req(`/works/${key}/cover`, { method: "POST", body: JSON.stringify(body) }),
  deleteCover: (key) => req(`/works/${key}/cover`, { method: "DELETE" }),
  generateBlurb: (key) =>
    req(`/works/${key}/blurb`, { method: "POST", timeoutMs: 180000 }),
  createDesign: (workKey, body) =>
    req(`/works/${workKey}/designs`, { method: "POST", body: JSON.stringify(body) }),
  design: (id) => req(`/designs/${id}`),
  patchOutline: (designId, ep, body) =>
    req(`/designs/${designId}/outlines/${ep}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  designEpisodes: (designId) => req(`/designs/${designId}/episodes`),
  publishingEpisodes: (designId) => req(`/designs/${designId}/publishing`),
  // Episode identity is (work, episode_no): every /episodes/ call carries ?work=.
  episode: (no, workKey) => req(`/episodes/${no}?${epQuery(workKey)}`),
  redraft: (no, workKey) =>
    req(`/episodes/${no}/redraft?${epQuery(workKey)}`, { method: "POST" }),
  writingChat: (no, message, includeDraft, workKey) =>
    req(`/episodes/${no}/manuscript-chat?${epQuery(workKey)}`, {
      method: "POST",
      body: JSON.stringify({ message, include_draft: includeDraft }),
    }),
  startEpisode: (no, designId, workKey) =>
    req(`/episodes/${no}/start?${epQuery(workKey)}`, {
      method: "POST",
      body: JSON.stringify({ design_id: designId }),
    }),
  submitReview: (no, workKey) =>
    req(`/episodes/${no}/submit-review?${epQuery(workKey)}`, { method: "POST" }),
  backToWriting: (no, reason, workKey) =>
    req(`/episodes/${no}/back-to-writing?${epQuery(workKey)}`, {
      method: "POST",
      body: JSON.stringify({ reason: reason ?? "" }),
    }),
  requestPublish: (no, workKey) =>
    req(`/episodes/${no}/request-publish?${epQuery(workKey)}`, { method: "POST" }),
  manuscript: (no, version, workKey) => {
    const p = new URLSearchParams({ work: mustWorkKey(workKey) });
    if (version) p.set("version", version);
    return req(`/episodes/${no}/manuscript?${p.toString()}`);
  },
  approvedText: (no, workKey) =>
    req(`/episodes/${no}/approved-text?${epQuery(workKey)}`),
  savePublishRecord: (no, body, workKey) =>
    req(`/episodes/${no}/publish-record?${epQuery(workKey)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  consistencyAnalyze: (no, workKey) =>
    req(`/episodes/${no}/consistency/analyze?${epQuery(workKey)}`, { method: "POST" }),
  consistencyReset: (no, workKey) =>
    req(`/episodes/${no}/consistency/reset?${epQuery(workKey)}`, { method: "POST" }),
  manuscriptFix: (no, issueIds, workKey) =>
    req(`/episodes/${no}/manuscript-fix?${epQuery(workKey)}`, {
      method: "POST", body: JSON.stringify({ issue_ids: issueIds }),
      timeoutMs: 300000,
    }),
  consistencyMessages: (no, workKey) =>
    req(`/episodes/${no}/consistency/messages?${epQuery(workKey)}`),
  consistencyChat: (no, message, workKey) =>
    req(`/episodes/${no}/consistency/chat?${epQuery(workKey)}`, {
      method: "POST",
      body: JSON.stringify({ message }),
    }),
  consistencyIssues: (no, status, workKey) => {
    const p = new URLSearchParams({ work: mustWorkKey(workKey) });
    if (status) p.set("status", status);
    return req(`/episodes/${no}/consistency/issues?${p.toString()}`);
  },
  setIssueStatus: (id, status) =>
    req(`/consistency/issues/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),
  saveEntities: (no, workKey) =>
    req(`/episodes/${no}/entities/save?${epQuery(workKey)}`, { method: "POST" }),
  manuscriptDoc: (no, workKey) => req(`/episodes/${no}/manuscript-doc?${epQuery(workKey)}`),
  saveManuscriptDraft: (no, body, workKey) =>
    reqEx(`/episodes/${no}/manuscript-draft?${epQuery(workKey)}`, {
      method: "PATCH", body: JSON.stringify(body), timeoutMs: 15000,
    }),
  createManuscriptVersion: (no, body, workKey) =>
    reqEx(`/episodes/${no}/manuscript-versions?${epQuery(workKey)}`, {
      method: "POST", body: JSON.stringify(body), timeoutMs: 15000,
    }),
  restoreManuscriptVersion: (no, body, workKey) =>
    reqEx(`/episodes/${no}/manuscript-restore?${epQuery(workKey)}`, {
      method: "POST", body: JSON.stringify(body), timeoutMs: 15000,
    }),
  entities: (q, workId, asOfEp, content, any) => {
    const p = new URLSearchParams();
    if (q) p.set("q", q);
    if (workId) p.set("work_id", workId);
    if (asOfEp !== undefined && asOfEp !== null && asOfEp !== "") p.set("as_of_episode", asOfEp);
    if (content) p.set("content", content);
    if (any) p.set("any", any);
    const s = p.toString();
    return req(`/entities${s ? `?${s}` : ""}`);
  },
  entityState: (id, ep) => req(ep === undefined || ep === null || ep === ""
    ? `/entities/${id}/state`
    : `/entities/${id}/state?as_of_episode=${ep}`),
  entityHistory: (id, toEp) => req(toEp === undefined || toEp === null || toEp === ""
    ? `/entities/${id}/history`
    : `/entities/${id}/history?to_episode=${toEp}`),
  entityRelations: (id) => req(`/entities/${id}/relations`),
  aliases: (workKey) => req(`/works/${workKey}/aliases`),
  resolveAlias: (id, body) =>
    req(`/aliases/${id}/resolve`, { method: "POST", body: JSON.stringify(body) }),
  rejectAlias: (id, body) =>
    req(`/aliases/${id}/reject`, { method: "POST", body: JSON.stringify(body ?? {}) }),
  latestAnalysis: (no, workKey) => req(`/episodes/${no}/consistency/analyses/latest?${epQuery(workKey)}`),
  extractEntities: (no, instruction, workKey) =>
    req(`/episodes/${no}/entities/extract?${epQuery(workKey)}`, {
      method: "POST",
      ...(instruction ? { body: JSON.stringify({ instruction }) } : {}),
    }),
  entityProposals: (no, workKey) => req(`/episodes/${no}/entity-proposals?${epQuery(workKey)}`),
  resetEntities: (no, workKey) =>
    req(`/episodes/${no}/entities/reset?${epQuery(workKey)}`, { method: "POST" }),
  deleteEntityItem: (id) => req(`/entity-items/${id}`, { method: "DELETE" }),
  deleteEntityEvent: (no, index, workKey) =>
    req(`/episodes/${no}/entity-events/${index}?${epQuery(workKey)}`, { method: "DELETE" }),
  deleteEntityParticipant: (no, index, pindex, workKey) =>
    req(`/episodes/${no}/entity-events/${index}/participants/${pindex}?${epQuery(workKey)}`, { method: "DELETE" }),
  llmCalls: () => req("/ops/llm-calls?limit=20"),
  opsBudget: () => req("/ops/budget"),
  model: () => req("/ops/model"),
  setModel: (model, adminToken) =>
    req("/ops/model", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(adminToken ? { "X-Admin-Token": adminToken } : {}),
      },
      body: JSON.stringify({ model }),
    }),
  aiLog: (workKey, limit) => req(`/works/${workKey}/ai-log?limit=${limit ?? 50}`),
  // 아이디어
  threads: (workKey) => req(`/works/${workKey}/threads`),
  createThread: (workKey, title) =>
    req(`/works/${workKey}/threads`, {
      method: "POST",
      body: JSON.stringify({ title: title ?? "" }),
    }),
  messages: (threadId, limit) =>
    req(`/threads/${threadId}/messages${limit ? `?limit=${limit}` : ""}`),
  postMessage: (threadId, content, refs, history, extra) =>
    req(`/threads/${threadId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content, refs: refs ?? [], history, ...(extra ?? {}) }),
    }),
  synopsis: (workKey) => req(`/works/${workKey}/synopsis`),
  putSynopsis: (workKey, content) =>
    req(`/works/${workKey}/synopsis`, {
      method: "PUT",
      body: JSON.stringify({ content }),
    }),
  synopsisRevisions: (workKey) => req(`/works/${workKey}/synopsis/revisions`),
  getStructure: (workKey) => req(`/works/${workKey}/structure`),
  putStructure: (workKey, body) =>
    req(`/works/${workKey}/structure`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  settings: (workKey, category) =>
    req(`/works/${workKey}/settings${category ? `?category=${category}` : ""}`),
  createSetting: (workKey, body) =>
    req(`/works/${workKey}/settings`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  patchSetting: (id, body) =>
    req(`/settings/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteSetting: (id) => req(`/settings/${id}`, { method: "DELETE" }),
  extract: (workKey, threadId, limit) =>
    req(`/works/${workKey}/extract`, {
      method: "POST",
      body: JSON.stringify({ thread_id: threadId ?? null, limit: limit ?? 20 }),
    }),
  handoffPreview: (workKey) => req(`/works/${workKey}/handoff/preview`),
  handoff: (workKey, plan) =>
    req(`/works/${workKey}/handoff`, {
      method: "POST",
      body: JSON.stringify(plan ?? {}),
    }),
  // 소설 설계 트리
  nodes: (designId, q, archived) => {
    const p = new URLSearchParams();
    if (q) p.set("q", q);
    if (archived) p.set("archived", "true");
    const s = p.toString();
    return req(`/designs/${designId}/nodes${s ? `?${s}` : ""}`);
  },
  createNode: (designId, body) =>
    req(`/designs/${designId}/nodes`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  node: (nodeId) => req(`/nodes/${nodeId}`),
  patchNode: (nodeId, body) =>
    req(`/nodes/${nodeId}`, { method: "PATCH", body: JSON.stringify(body) }),
  archiveNode: (nodeId, archived) =>
    req(`/nodes/${nodeId}/archive`, {
      method: "POST",
      body: JSON.stringify({ archived }),
    }),
  nodeChat: (nodeId, content, refs) =>
    req(`/nodes/${nodeId}/chat`, {
      method: "POST",
      body: JSON.stringify({ content, refs: refs ?? [] }),
    }),
  nodeExpand: (nodeId, kind) =>
    req(`/nodes/${nodeId}/expand`, {
      method: "POST",
      body: JSON.stringify({ kind: kind ?? null }),
    }),
  designThreads: (designId, nodeId, q) => {
    const par = new URLSearchParams();
    if (nodeId) par.set("node_id", nodeId);
    if (q) par.set("q", q);
    const s = par.toString();
    return req(`/designs/${designId}/threads${s ? `?${s}` : ""}`);
  },
  createDesignThread: (designId, nodeId) =>
    req(`/designs/${designId}/threads`, {
      method: "POST",
      body: JSON.stringify({ node_id: nodeId }),
    }),
  designThreadMessages: (threadId) =>
    req(`/design-threads/${threadId}/messages`),
  postDesignMessage: (threadId, content, refs, includeMessages) =>
    req(`/design-threads/${threadId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content, refs: refs ?? [],
                              include_messages: includeMessages ?? [] }),
    }),
  markDesignApplied: (messageId, body) =>
    req(`/design-messages/${messageId}/applied`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  nodeSettings: (nodeId) => req(`/nodes/${nodeId}/settings`),
  deleteNode: (nodeId) => req(`/nodes/${nodeId}`, { method: "DELETE" }),
  designNodeSettings: (designId) => req(`/designs/${designId}/node-settings`),
  createNodeSetting: (nodeId, body) =>
    req(`/nodes/${nodeId}/settings`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  patchNodeSetting: (id, body) =>
    req(`/node-settings/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteNodeSetting: (id) => req(`/node-settings/${id}`, { method: "DELETE" }),
};
