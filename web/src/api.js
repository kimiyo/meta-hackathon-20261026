const BASE = "/api/v1";

async function req(path, options = {}) {
  const resp = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`API ${resp.status}: ${text.slice(0, 200)}`);
  }
  return resp.json();
}

export const api = {
  health: () => req("/health"),
  works: () => req("/works"),
  createWork: (title, logline) =>
    req("/works", { method: "POST", body: JSON.stringify({ title, logline }) }),
  work: (id) => req(`/works/${id}`),
  createDesign: (workId, body) =>
    req(`/works/${workId}/designs`, { method: "POST", body: JSON.stringify(body) }),
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
};
