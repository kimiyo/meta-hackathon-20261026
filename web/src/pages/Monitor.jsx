import { useEffect, useState } from "react";
import { api } from "../api.js";

const ORDER = ["PLAN", "DRAFT", "VERIFY", "READY_FOR_REVIEW", "AUTHOR_APPROVED", "COMMITTED"];
const SHORT = { PLAN: "P", DRAFT: "D", VERIFY: "V", READY_FOR_REVIEW: "R", AUTHOR_APPROVED: "A", COMMITTED: "C" };

function Badges({ state }) {
  if (!state) return <span style={{ color: "#999" }}>미시작</span>;
  if (state === "NEEDS_AUTHOR") return <b style={{ color: "darkred" }}>NEEDS_AUTHOR</b>;
  const idx = ORDER.indexOf(state);
  return (
    <span>
      {ORDER.map((s, i) => (
        <span
          key={s}
          title={s}
          style={{
            display: "inline-block",
            minWidth: 20,
            textAlign: "center",
            marginRight: 2,
            borderRadius: 4,
            background: i <= idx ? "#0e6b5c" : "#eee",
            color: i <= idx ? "#fff" : "#999",
            fontSize: 12,
            fontWeight: "bold",
          }}
        >
          {SHORT[s]}
        </span>
      ))}
    </span>
  );
}

export default function Monitor() {
  const [designId, setDesignId] = useState("");
  const [rows, setRows] = useState([]);
  const [detail, setDetail] = useState(null);
  const [calls, setCalls] = useState([]);
  const [budget, setBudget] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = (did) =>
    api
      .designEpisodes(did)
      .then((d) => setRows(d.episodes))
      .catch((e) => setError(String(e)));

  useEffect(() => {
    api
      .works()
      .then(async (w) => {
        if (!w.works.length) return;
        const full = await api.work(w.works[0].id);
        if (full.latest_design) {
          setDesignId(full.latest_design.id);
          load(full.latest_design.id);
        }
      })
      .catch(() => {});
    api.llmCalls().then((c) => setCalls(c.calls)).catch(() => {});
    api.opsBudget().then(setBudget).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const run = async (fn) => {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const openDetail = (ep) =>
    run(async () => {
      setDetail({ ep, ...(await api.episode(ep)) });
      setCalls((await api.llmCalls()).calls);
      setBudget(await api.opsBudget());
    });

  return (
    <div>
      {error && <p style={{ color: "darkred" }}>{error}</p>}
      <p>
        설계 ID:{" "}
        <input value={designId} onChange={(e) => setDesignId(e.target.value)} style={{ width: 80 }} />
        <button disabled={busy || !designId} onClick={() => load(designId)} style={{ marginLeft: 8 }}>
          불러오기
        </button>
        {budget && (
          <span style={{ marginLeft: 16 }}>
            오늘 토큰 {budget.total_tokens} / {budget.budget} (잔여 {budget.remaining})
          </span>
        )}
      </p>
      <table border="1" cellPadding="6" style={{ borderCollapse: "collapse", width: "100%" }}>
        <thead>
          <tr>
            <th>회차</th>
            <th>게이트</th>
            <th>상태</th>
            <th>시도</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.episode_no}>
              <td>
                EP {r.episode_no} — {r.title}
              </td>
              <td>
                <Badges state={r.job?.state} />
              </td>
              <td>{r.job?.state ?? "-"}</td>
              <td>{r.job?.attempt ?? "-"}</td>
              <td>
                {r.job && <button onClick={() => openDetail(r.episode_no)}>상세</button>}{" "}
                <button
                  disabled={busy}
                  onClick={() =>
                    run(async () => {
                      await api.advance(r.episode_no, r.job ? undefined : Number(designId));
                      await load(designId);
                    })
                  }
                >
                  {busy ? "실행 중… (취소 불가)" : "전진"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {detail && (
        <div style={{ marginTop: 12, border: "1px solid #ccc", padding: 12 }}>
          <h4>EP {detail.ep} 상세</h4>
          <p>
            상태: {detail.state} · 시도: {detail.attempt} · 원고 rev: {detail.manuscript_revision_id} ·
            제안 set: {detail.proposal_set_id}
          </p>
          {detail.last_error && <p style={{ color: "darkred" }}>사유: {detail.last_error}</p>}
          {detail.verification && <p>검증: {JSON.stringify(detail.verification.counts)}</p>}
          {detail.quality && <p>품질: {JSON.stringify(detail.quality.scores)}</p>}
          <p>원고 버전: {detail.revisions.map((r) => `v${r.version}(${r.approval_status}, ${r.length}자)`).join(", ")}</p>
        </div>
      )}
      <h4>최근 LLM 호출</h4>
      <table border="1" cellPadding="4" style={{ borderCollapse: "collapse", fontSize: 12 }}>
        <thead>
          <tr>
            <th>bundle</th>
            <th>회차</th>
            <th>in/out</th>
            <th>지연</th>
            <th>결과</th>
          </tr>
        </thead>
        <tbody>
          {calls.map((c) => (
            <tr key={c.id}>
              <td>{c.bundle}</td>
              <td>{c.episode_no}</td>
              <td>
                {c.input_tokens}/{c.output_tokens}
              </td>
              <td>{c.latency_ms}ms</td>
              <td>{c.ok ? "성공" : c.error}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
