import { useEffect, useState } from "react";
import { api } from "../api.js";

export default function Design() {
  const [works, setWorks] = useState([]);
  const [workId, setWorkId] = useState(null);
  const [design, setDesign] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [form, setForm] = useState({ keywords: "", total: 20, constraints: "" });
  const [newWork, setNewWork] = useState({ title: "", logline: "" });

  const reload = () =>
    api
      .works()
      .then((w) => {
        setWorks(w.works);
        if (!workId && w.works.length) setWorkId(w.works[0].id);
      })
      .catch((e) => setError(String(e)));

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!workId) return;
    api
      .work(workId)
      .then((w) => {
        if (w.latest_design) return api.design(w.latest_design.id).then(setDesign);
        setDesign(null);
      })
      .catch((e) => setError(String(e)));
  }, [workId]);

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

  return (
    <div>
      {error && <p style={{ color: "darkred" }}>{error}</p>}
      <h3>작품 선택</h3>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {works.map((w) => (
          <button
            key={w.id}
            onClick={() => setWorkId(w.id)}
            style={{ fontWeight: workId === w.id ? "bold" : "normal" }}
          >
            {w.title}
          </button>
        ))}
      </div>
      <div style={{ marginTop: 8, display: "flex", gap: 8 }}>
        <input
          placeholder="새 작품 제목"
          value={newWork.title}
          onChange={(e) => setNewWork({ ...newWork, title: e.target.value })}
        />
        <input
          placeholder="로그라인"
          value={newWork.logline}
          onChange={(e) => setNewWork({ ...newWork, logline: e.target.value })}
        />
        <button
          disabled={busy || !newWork.title}
          onClick={() =>
            run(async () => {
              const w = await api.createWork(newWork.title, newWork.logline);
              setNewWork({ title: "", logline: "" });
              await reload();
              setWorkId(w.id);
            })
          }
        >
          작품 만들기
        </button>
      </div>

      <h3>설계도 생성</h3>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input
          placeholder="키워드 (쉼표 구분)"
          value={form.keywords}
          onChange={(e) => setForm({ ...form, keywords: e.target.value })}
          style={{ minWidth: 240 }}
        />
        <input
          type="number"
          min={1}
          max={200}
          value={form.total}
          onChange={(e) => setForm({ ...form, total: Number(e.target.value) })}
          style={{ width: 80 }}
        />
        <input
          placeholder="제약 조건 (줄바꿈 구분)"
          value={form.constraints}
          onChange={(e) => setForm({ ...form, constraints: e.target.value })}
          style={{ minWidth: 240 }}
        />
        <button
          disabled={busy || !workId || !form.keywords}
          onClick={() =>
            run(async () => {
              const d = await api.createDesign(workId, {
                keywords: form.keywords,
                total_episodes: form.total,
                constraints: form.constraints.split("\n").filter(Boolean),
              });
              setDesign(await api.design(d.design_id));
            })
          }
        >
          {busy ? "생성 중… (취소 불가)" : "설계도 생성"}
        </button>
      </div>

      {design && (
        <div style={{ marginTop: 16 }}>
          <h3>
            설계도 v{design.version} — {design.status}
            {design.status !== "approved" && (
              <button
                style={{ marginLeft: 12 }}
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    await api.approveDesign(design.id);
                    setDesign(await api.design(design.id));
                  })
                }
              >
                설계 승인
              </button>
            )}
          </h3>
          <p>
            <b>장르/톤:</b> {design.concept.genre} / {design.concept.tone}
            <br />
            <b>주제:</b> {design.concept.theme}
            <br />
            <b>결말:</b> {design.concept.ending}
          </p>
          <h4>구간 ({design.sections.length})</h4>
          <ul>
            {design.sections.map((s, i) => (
              <li key={i}>
                <b>{s.range}</b> — {s.summary}
              </li>
            ))}
          </ul>
          <h4>회차 개요 ({design.outlines.length})</h4>
          {design.outlines.map((o) => (
            <details key={o.episode_no} style={{ marginBottom: 6 }}>
              <summary>
                EP {o.episode_no} — {o.title}
              </summary>
              <div style={{ paddingLeft: 16 }}>
                <p>목표: {o.goal}</p>
                <p>사건: {o.events.join(" / ")}</p>
                <p>인물 변화: {o.char_changes.join(" / ")}</p>
                <p>복선: {o.foreshadowing.join(" / ")}</p>
              </div>
            </details>
          ))}
        </div>
      )}
    </div>
  );
}
