const BASE = "/api/v1";
const TOKEN_KEY = "auth-token";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function setToken(t) {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

async function req(path, options = {}) {
  const headers = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  Object.assign(headers, options.headers ?? {});
  const resp = await fetch(BASE + path, { ...options, headers });
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
  episode: (no) => req(`/episodes/${no}`),
  startEpisode: (no, designId) =>
    req(`/episodes/${no}/start`, {
      method: "POST",
      body: JSON.stringify({ design_id: designId }),
    }),
  submitReview: (no) =>
    req(`/episodes/${no}/submit-review`, { method: "POST" }),
  backToWriting: (no, reason) =>
    req(`/episodes/${no}/back-to-writing`, {
      method: "POST",
      body: JSON.stringify({ reason: reason ?? "" }),
    }),
  requestPublish: (no) =>
    req(`/episodes/${no}/request-publish`, { method: "POST" }),
  manuscript: (no, version) =>
    req(`/episodes/${no}/manuscript${version ? `?version=${version}` : ""}`),
  consistencyAnalyze: (no) =>
    req(`/episodes/${no}/consistency/analyze`, { method: "POST" }),
  consistencyMessages: (no) =>
    req(`/episodes/${no}/consistency/messages`),
  consistencyChat: (no, message) =>
    req(`/episodes/${no}/consistency/chat`, {
      method: "POST",
      body: JSON.stringify({ message }),
    }),
  consistencyIssues: (no, status) =>
    req(`/episodes/${no}/consistency/issues${status ? `?status=${status}` : ""}`),
  setIssueStatus: (id, status) =>
    req(`/consistency/issues/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),
  memoryDraft: (no) => req(`/episodes/${no}/memory-draft`),
  saveMemoryDraft: (no, content) =>
    req(`/episodes/${no}/memory-draft`, {
      method: "PUT",
      body: JSON.stringify({ content }),
    }),
  manuscriptDoc: (no) => req(`/episodes/${no}/manuscript-doc`),
  saveManuscriptDraft: (no, body) =>
    reqEx(`/episodes/${no}/manuscript-draft`, {
      method: "PATCH", body: JSON.stringify(body), timeoutMs: 15000,
    }),
  createManuscriptVersion: (no, body) =>
    reqEx(`/episodes/${no}/manuscript-versions`, {
      method: "POST", body: JSON.stringify(body), timeoutMs: 15000,
    }),
  restoreManuscriptVersion: (no, body) =>
    reqEx(`/episodes/${no}/manuscript-restore`, {
      method: "POST", body: JSON.stringify(body), timeoutMs: 15000,
    }),
  entities: (q) => req(`/entities${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  entityState: (id, ep) => req(`/entities/${id}/state?as_of_episode=${ep}`),
  entityHistory: (id) => req(`/entities/${id}/history`),
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
