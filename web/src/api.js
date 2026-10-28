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
  // Phase 3+: works & designs
  // Phase 4-6: episodes, entities, ops
};
