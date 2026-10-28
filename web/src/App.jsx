import { useEffect, useState } from "react";
import { api } from "./api.js";
import Design from "./pages/Design.jsx";
import Monitor from "./pages/Monitor.jsx";
import Review from "./pages/Review.jsx";
import Entities from "./pages/Entities.jsx";

const TABS = ["작품 · 설계", "집필 모니터", "작가 검토", "엔티티 브라우저"];

export default function App() {
  const [tab, setTab] = useState(0);
  const [health, setHealth] = useState({ state: "loading" });

  useEffect(() => {
    api
      .health()
      .then((h) => setHealth({ state: "ok", ...h }))
      .catch((e) => setHealth({ state: "error", message: String(e) }));
  }, []);

  return (
    <div style={{ fontFamily: "sans-serif", maxWidth: 960, margin: "0 auto", padding: 24 }}>
      <h1>Muse Novel Studio</h1>
      <p>
        서버 상태:{" "}
        {health.state === "loading" && "확인 중…"}
        {health.state === "ok" && `정상 (모델: ${health.model}, DB: ${health.db})`}
        {health.state === "error" && `연결 실패: ${health.message}`}
      </p>
      <nav style={{ display: "flex", gap: 8, margin: "16px 0" }}>
        {TABS.map((t, i) => (
          <button
            key={t}
            onClick={() => setTab(i)}
            style={{ fontWeight: tab === i ? "bold" : "normal" }}
          >
            {i + 1}. {t}
          </button>
        ))}
      </nav>
      <main>
        {tab === 0 && <Design />}
        {tab === 1 && <Monitor />}
        {tab === 2 && <Review />}
        {tab === 3 && <Entities />}
      </main>
    </div>
  );
}
