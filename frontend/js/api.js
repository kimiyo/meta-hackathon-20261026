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
  deleteWork: (key, title) =>
    req(`/works/${key}`, {
      method: "DELETE",
      body: JSON.stringify({ title }),
    }),
  work: (key) => req(`/works/${key}`),
  createDesign: (workKey, body) =>
    req(`/works/${workKey}/designs`, { method: "POST", body: JSON.stringify(body) }),
  design: (id) => req(`/designs/${id}`),
  approveDesign: (id) => req(`/designs/${id}/approve`, { method: "POST" }),
  patchOutline: (designId, ep, body) =>
    req(`/designs/${designId}/outlines/${ep}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  designEpisodes: (designId) => req(`/designs/${designId}/episodes`),
  episode: (no) => req(`/episodes/${no}`),
  advance: (no, designId) =>
    req(`/episodes/${no}/advance`, {
      method: "POST",
      body: JSON.stringify({ design_id: designId ?? null }),
    }),
  manuscript: (no, version) =>
    req(`/episodes/${no}/manuscript${version ? `?version=${version}` : ""}`),
  requestChange: (no, instruction) =>
    req(`/episodes/${no}/request-change`, {
      method: "POST",
      body: JSON.stringify({ instruction }),
    }),
  approveEpisode: (no) => req(`/episodes/${no}/approve`, { method: "POST" }),
  proposals: (no) => req(`/episodes/${no}/proposals`),
  entities: (q) => req(`/entities${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  entityState: (id, ep) => req(`/entities/${id}/state?as_of_episode=${ep}`),
  entityHistory: (id) => req(`/entities/${id}/history`),
  aliases: (status) => req(`/aliases${status ? `?status=${status}` : ""}`),
  resolveAlias: (id, body) =>
    req(`/aliases/${id}/resolve`, { method: "POST", body: JSON.stringify(body) }),
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
