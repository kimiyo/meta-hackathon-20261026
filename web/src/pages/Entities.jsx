import { useEffect, useState } from "react";
import { api } from "../api.js";

export default function Entities() {
  const [q, setQ] = useState("");
  const [list, setList] = useState([]);
  const [sel, setSel] = useState(null);
  const [asOf, setAsOf] = useState("1");
  const [state, setState] = useState(null);
  const [history, setHistory] = useState([]);
  const [error, setError] = useState("");

  const search = (query) =>
    api
      .entities(query)
      .then((r) => setList(r.entities))
      .catch((e) => setError(String(e)));

  useEffect(() => {
    search("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const open = (id) => {
    setSel(id);
    api
      .entityState(id, Number(asOf))
      .then(setState)
      .catch((e) => setError(String(e)));
    api
      .entityHistory(id)
      .then((r) => setHistory(r.history))
      .catch((e) => setError(String(e)));
  };

  return (
    <div>
      {error && <p style={{ color: "darkred" }}>{error}</p>}
      <p>
        <input placeholder="이름·ID 검색" value={q} onChange={(e) => setQ(e.target.value)} />
        <button onClick={() => search(q)} style={{ marginLeft: 8 }}>
          검색
        </button>
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "260px 1fr", gap: 16 }}>
        <div>
          {list.map((e) => (
            <div
              key={e.entity_id}
              onClick={() => open(e.entity_id)}
              style={{
                border: "1px solid #ccc",
                padding: 8,
                marginBottom: 6,
                cursor: "pointer",
                background: sel === e.entity_id ? "#eef" : "#fff",
              }}
            >
              <b>{e.canonical_name}</b> <span style={{ fontSize: 12 }}>({e.entity_id})</span>
              <br />
              <span style={{ fontSize: 12 }}>
                {e.type} · EP{e.first_episode} · {e.lifecycle_status}
              </span>
            </div>
          ))}
        </div>
        <div>
          {state && (
            <div>
              <h4>
                {sel} 상태 · 회차 지정:{" "}
                <input value={asOf} onChange={(e) => setAsOf(e.target.value)} style={{ width: 60 }} />
                <button onClick={() => open(sel)} style={{ marginLeft: 8 }}>
                  조회
                </button>
              </h4>
              <p style={{ fontSize: 12, color: "#666" }}>승인된 변경만 반영됩니다.</p>
              <pre style={{ background: "#f5f5f5", padding: 8 }}>{JSON.stringify(state.attributes, null, 2)}</pre>
              <h4>속성 이력 ({history.length})</h4>
              <ul>
                {history.map((h) => (
                  <li key={h.change_id}>
                    EP{h.episode_no} · {h.operation} {h.attribute_path}: {h.old_value} → {h.new_value}
                    <br />
                    <span style={{ fontSize: 12, color: "#555" }}>근거: {h.evidence_quote}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
