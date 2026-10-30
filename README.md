# StorySpine

**이야기의 뼈대를 세우고, 완결까지 함께.**

*Give your story a spine.*

StorySpine은 기획·집필·설정 검토를 연결하는 AI 소설 창작 스튜디오입니다. 이전 명칭은 Muse Novel Studio입니다.

브랜드는 **C안 · Story Architecture**로 확정했습니다. [브랜드 기획안](docs/StorySpine_브랜드_기획안.md)과 [브랜드 이미지·사용 가이드](docs/branding/README.md)를 참고하세요. 현재 이미지 자료는 채택 보드이며, 개별 배포 에셋 제작과 앱 화면 적용은 후속 작업입니다.

웹소설 연재 코파이럿. 기획→집필→추출→검증→품질→승인→커밋 파이프라인으로
장편 연재의 설정·연속성을 관리한다. 모든 AI 호출은 Meta Model API(`muse-spark-1.3`)만 사용한다.

상세 명세는 [docs/구현계획.md](docs/구현계획.md) (문서 위계 최우선).

## 구조

```text
server/app/        FastAPI 서버 (유일 AI 출구 meta_client.py, 상태머신, 기억 저장소)
server/tests/      pytest 스위트 (목/픽스처 중심, 실호출은 아래 재현 절차만)
web/               Vite + React SPA (화면 4개, 빌드 후 FastAPI가 / 에서 서빙)
samples/           자작 샘플 케이스 (주입 실험용)
evals/             실측·실험·평가 기록
docs/              기획·설계 문서
prototype/         초기 UI 목업 (단일 HTML)
data/studio.db     SQLite 저장소 (git 무시, 실행 시 자동 생성)
```

## 준비물

- Python 3.11+, Node 20+
- Meta Model API 키 (`.env`의 `MODEL_API_KEY`, 커밋 금지)

## 실행

```bash
# 1. 환경 변수
cp .env.example .env
# .env에 MODEL_API_KEY 입력

# 2. 서버 의존성 + 테스트
python -m venv .venv
.venv/bin/pip install -r server/requirements.txt
cd server && ../.venv/bin/python -m pytest -q

# 3. 서버 기동 (http://127.0.0.1:8000, health: /api/v1/health)
../.venv/bin/python -m uvicorn app.main:app --port 8000

# 4. 웹 개발 서버 (http://127.0.0.1:5173, /api 프록시)
cd web && npm install && npm run dev

# 5. 웹 프로덕션 빌드 (FastAPI가 / 에서 서빙)
cd web && npm run build
```

Windows PowerShell에서는 가상환경 경로가 `Scripts` 아래입니다:

```powershell
# 1. 환경 변수
Copy-Item .env.example .env
# .env에 MODEL_API_KEY 입력

# 2. 서버 의존성 + 테스트
python -m venv .venv
.venv\Scripts\pip install -r server/requirements.txt
cd server; ..\.venv\Scripts\python -m pytest -q

# 3. 서버 기동 (http://127.0.0.1:8000, health: /api/v1/health)
..\.venv\Scripts\python -m uvicorn app.main:app --port 8000

# 4-5. 웹 개발 서버 / 프로덕션 빌드
cd web; npm install; npm run dev
cd web; npm run build
```

## 핵심 흐름

1. 작품 생성 → 설계도 생성(10화 단위 분할)·승인 — 화면 1
2. 회차별 `advance`로 PLAN→DRAFT→VERIFY→READY 전진 — 화면 2
3. 원고·검증 카드·별명 판정 검토 후 승인+커밋 (원자적·멱등) — 화면 3
4. 엔티티 상태·이력 조회 (승인분만) — 화면 4

## 설정 (.env)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `MODEL_API_KEY` | (필수) | Meta Model API 키. 없으면 기동 거부 |
| `MODEL_BASE_URL` | `https://api.meta.ai/v1` | API 주소 |
| `MODEL_TEXT_ID` | `muse-spark-1.3` | 텍스트 모델 |
| `MODEL_MODE` | `standard` | 데이터 모드 (원고 투입 전 확정값 기록) |
| `REQUEST_TIMEOUT_S` | 120 | 호출 타임아웃 |
| `LLM_MAX_RETRY` | 3 | 네트워크·5xx·429 재시도 |
| `MAX_TOKENS_PER_CALL` | 8000 | 호출당 상한 (reasoning 포함) |
| `DAILY_TOKEN_BUDGET` | 1000000 | 일일 토큰 예산 (초과 시 작가 에스컬레이션) |
| `EPISODE_RETRY_LIMIT` | 3 | 회차 재시도 상한 |
| `ADMIN_TOKEN` | (없음) | 데모 초기화 보호 토큰 |

## 재현 절차 (서버 기동 후)

```bash
BASE=http://127.0.0.1:8000/api/v1
WID=$(curl -s -X POST $BASE/works -H 'Content-Type: application/json' \
  -d '{"title":"재현용 서점"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
DID=$(curl -s -X POST $BASE/works/$WID/designs -H 'Content-Type: application/json' \
  -d '{"keywords":"헌책방, 달빛","total_episodes":3}' | python3 -c "import sys,json;print(json.load(sys.stdin)['design_id'])")
curl -s -X POST $BASE/designs/$DID/approve
for EP in 1 2 3; do
  curl -s -X POST $BASE/episodes/$EP/advance -H 'Content-Type: application/json' \
    -d "{\"design_id\":$DID}" > /dev/null      # PLAN→DRAFT
  curl -s -X POST $BASE/episodes/$EP/advance -H 'Content-Type: application/json' \
    -d '{}' > /dev/null                          # DRAFT→VERIFY
  curl -s -X POST $BASE/episodes/$EP/advance -H 'Content-Type: application/json' \
    -d '{}'                                      # VERIFY→READY (판정 확인)
  curl -s -X POST $BASE/episodes/$EP/approve     # 승인+커밋
done
curl -s "$BASE/entities/ch_0001/state?as_of_episode=2"  # 다음화 반영 확인
```

기대 결과: 3회차 연속 COMMITTED, EP2 시점 상태에 EP1 변경 반영.
실측 기준: 약 21 호출, 약 169k 토큰, 약 9분 (`evals/결과_20261028.md`).

## 보안

- API 키는 서버 프로세스 메모리에서만 존재. UI·응답·로그에 키를 포함하지 않는다.
- 로그는 프롬프트 해시+앞 200자까지만. 원고 전문 로깅 금지.
- 제출 전 검사: `git log -p | grep -iE 'key|token|secret'` 및 `.env` 미추적 확인.

## 평가·재현

- `evals/실측_20261028.md` — 5묶음 스모크 + 20화 개요 실험 + 범위 결정
- `evals/주입실험_20261028.md` — 쿨다운 위반·동명이인·회상 3종
- `evals/결과_20261028.md` — 집계 지표 (정밀도·재현율·인용 정확도·재발률·완주율·비용)
