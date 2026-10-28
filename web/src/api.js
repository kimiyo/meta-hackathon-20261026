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
  // Phase 4-6: episodes, entities, ops
};
