import { useEffect, useState } from "react";
import { api } from "../api.js";

// 작은 단어 단위 diff (삽입/삭제만 표시)
function diffWords(a, b) {
  const aw = a.split(/(\s+)/);
  const bw = b.split(/(\s+)/);
  const m = aw.length;
  const n = bw.length;
  const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--)
    for (let j = n - 1; j >= 0; j--)
      dp[i][j] = aw[i] === bw[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out = [];
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (aw[i] === bw[j]) {
      out.push({ t: "same", s: aw[i] });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ t: "del", s: aw[i++] });
    } else {
      out.push({ t: "add", s: bw[j++] });
    }
  }
  while (i < m) out.push({ t: "del", s: aw[i++] });
  while (j < n) out.push({ t: "add", s: bw[j++] });
  return out;
}

export default function Review() {
  const [ep, setEp] = useState("1");
  const [job, setJob] = useState(null);
  const [versions, setVersions] = useState([]);
  const [version, setVersion] = useState(null);
  const [text, setText] = useState("");
  const [compare, setCompare] = useState(false);
  const [prevText, setPrevText] = useState("");
  const [items, setItems] = useState([]);
  const [aliases, setAliases] = useState([]);
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [entName, setEntName] = useState({});

  const load = async (epNo) => {
    const j = await api.episode(epNo);
    setJob(j);
    setVersions(j.revisions);
    const latest = j.revisions[j.revisions.length - 1];
    setVersion(latest.version);
    setText((await api.manuscript(epNo, latest.version)).text);
    if (j.revisions.length > 1) {
      setPrevText((await api.manuscript(epNo, j.revisions[j.revisions.length - 2].version)).text);
    } else {
      setPrevText("");
    }
    const prop = await api.proposals(epNo);
    setItems(prop.items.filter((i) => j.proposal_set_id == null || i.proposal_set_id === j.proposal_set_id));
    setAliases((await api.aliases("candidate")).aliases);
  };

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

  useEffect(() => {
    run(() => load(1)).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const changeVersion = (v) =>
    run(async () => {
      setVersion(v);
      setText((await api.manuscript(ep, v)).text);
    });

  return (
    <div>
      {error && <p style={{ color: "darkred" }}>{error}</p>}
      <p>
        회차: <input value={ep} onChange={(e) => setEp(e.target.value)} style={{ width: 60 }} />
        <button disabled={busy} onClick={() => run(() => load(Number(ep)))} style={{ marginLeft: 8 }}>
          불러오기
        </button>
        {job && (
          <span style={{ marginLeft: 12 }}>
            상태: <b>{job.state}</b> · 시도 {job.attempt}
          </span>
        )}
      </p>
      {job && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          <div>
            <h4>
              원고{" "}
              <select value={version ?? ""} onChange={(e) => changeVersion(Number(e.target.value))}>
                {versions.map((r) => (
                  <option key={r.version} value={r.version}>
                    v{r.version} ({r.approval_status})
                  </option>
                ))}
              </select>{" "}
              <label>
                <input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} /> 이전 버전과 비교
              </label>
            </h4>
            <div
              style={{
                border: "1px solid #ccc",
                padding: 12,
                maxHeight: 420,
                overflowY: "auto",
                whiteSpace: "pre-wrap",
                fontFamily: "serif",
              }}
            >
              {compare && prevText
                ? diffWords(prevText, text).map((d, i) => (
                    <span
                      key={i}
                      style={
                        d.t === "del"
                          ? { background: "#f9e6e1", textDecoration: "line-through" }
                          : d.t === "add"
                            ? { background: "#e3f1e6" }
                            : {}
                      }
                    >
                      {d.s}
                    </span>
                  ))
                : text}
            </div>
            <h4>수정 요청 (→ DRAFT 복귀, 원고·입력 보존)</h4>
            <textarea value={instruction} onChange={(e) => setInstruction(e.target.value)} rows={3} style={{ width: "100%" }} />
            <div style={{ marginTop: 6, display: "flex", gap: 8 }}>
              <button
                disabled={busy || !instruction}
                onClick={() => run(async () => {
                  await api.requestChange(Number(ep), instruction);
                  setInstruction("");
                  await load(Number(ep));
                })}
              >
                수정 요청 보내기
              </button>
              <button
                disabled={busy || job.state !== "READY_FOR_REVIEW"}
                onClick={() => run(async () => {
                  const r = await api.approveEpisode(Number(ep));
                  alert(`커밋 완료: ${r.commit.applied}건 반영`);
                  await load(Number(ep));
                })}
              >
                승인 + 커밋
              </button>
            </div>
          </div>
          <div>
            <h4>검증 카드 ({items.length})</h4>
            {items.map((i) => (
              <div key={i.item_id} style={{ border: "1px solid #ccc", padding: 8, marginBottom: 8 }}>
                <p>
                  <b>{i.validation}</b> · {i.operation} · {i.entity_id || i.candidate_name} · {i.attribute_path}
                </p>
                <p style={{ background: "#f5f5f5", padding: 6 }}>신규: {i.evidence_quote || "(인용 없음)"}</p>
                {i.counter_quote && <p style={{ background: "#f5f5f5", padding: 6 }}>기존 근거: {i.counter_quote}</p>}
                {i.reason && <p>사유: {i.reason}</p>}
                {i.validation === "deferred" && !i.evidence_start && <p style={{ color: "#b7791f" }}>근거 미확인 — 충돌 증거로 사용하지 않음</p>}
              </div>
            ))}
            <h4>별명 · 동명이인 판정</h4>
            {aliases.map((a) => (
              <div key={a.alias_id} style={{ border: "1px solid #dcc489", background: "#faf0d7", padding: 8, marginBottom: 8 }}>
                <p>
                  <b>{a.alias}</b> — {a.basis} (현재 연결: {a.canonical_name ?? "없음"})
                </p>
                <div style={{ display: "flex", gap: 6 }}>
                  <input
                    placeholder="연결할 entity_id"
                    value={entName[a.alias_id] ?? ""}
                    onChange={(e) => setEntName({ ...entName, [a.alias_id]: e.target.value })}
                    style={{ width: 120 }}
                  />
                  <button
                    disabled={busy}
                    onClick={() => run(async () => {
                      await api.resolveAlias(a.alias_id, { entity_id: entName[a.alias_id] || null, basis: "작가 판정" });
                      setAliases((await api.aliases("candidate")).aliases);
                    })}
                  >
                    확정(빈칸=새 인물)
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => run(async () => {
                      await api.resolveAlias(a.alias_id, { reject: true });
                      setAliases((await api.aliases("candidate")).aliases);
                    })}
                  >
                    기각
                  </button>
                </div>
              </div>
            ))}
            {aliases.length === 0 && <p>대기 중인 별명 후보 없음</p>}
          </div>
        </div>
      )}
    </div>
  );
}
