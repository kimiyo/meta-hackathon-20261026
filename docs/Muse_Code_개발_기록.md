# StorySpine — Muse Code 개발 기록 (Meta 해커톤 제출용)

작성일: 2026-11-03 / 대상: Meta Global AI Developer Hackathon
트랙: Indie Developer Track / 테마: Build for Your World

이 문서는 StorySpine을 **Muse Code(에이전틱 코딩 도구)** 로 개발한 과정을
Git 이력과 저장소 실측을 근거로 정리한 것이다. 제출 폼의
"Describe your use of Meta products" 항목 중 개발 보조 부분의 근거 자료로 쓴다.

## 1. 프로젝트 한눈에 보기

- **StorySpine** — 기획·집필·설정 검토를 연결하는 AI 소설 창작 스튜디오.
  슬로건: *Give your story a spine.*
- 핵심 파이프라인: 기획 → 집필 → 추출 → 검증 → 품질 → 검토 → 확정.
  장편 연재의 설정·연속성을 "작가 승인 기억(author-approved memory)"으로 관리한다.
- 기술 스택: FastAPI 서버 + 빌드 없는 정적 프론트엔드(HTML/CSS/JS) + SQLite.
- 런타임 AI: Meta Model API의 `muse-spark-1.3` 단일 모델.
  모든 AI 호출은 `server/app/meta_client.py` 한 곳으로만 나간다(유일 AI 출구).

## 2. 개발 방식: Muse Code 에이전틱 코딩

개발은 사람이 방향을 정하고 Muse Code가 구현을 수행하는 형태로 진행했다.

- **사람(작성자)의 역할**: 제품 기획, 파이프라인·상태머신·기억 모델 설계,
  매 단계 결과 검수, 실측 평가 케이스 판정, 최종 승인·커밋·푸시.
- **Muse Code의 역할**: 설계에 따른 서버/프론트 구현, pytest 작성·실행,
  프롬프트·스키마 작성, 버그 진단·수정, UI 반복 개선, 문서 초안 작성.
- **작업 단위**: 기능 단위로 지시 → 구현 → 테스트 실행 → 검수 → 커밋의
  짧은 사이클을 반복. Git 로그의 커밋 메시지가 곧 작업 지시 단위다.
- **검증 원칙**: 모든 AI 런타임 호출은 실측으로 증명했다.
  목/픽스처 중심 pytest(221개 테스트 함수) + 실제 모델 호출 재현 절차를 병행했다.

## 3. 개발 타임라인 (Git 이력 기준, 10/28–11/3, 총 54 커밋)

| 날짜 | 단계 | 내용 (대표 커밋) |
|---|---|---|
| 10/28 | Phase 0–8, MVP 완성 | 스캐폴딩 → 기억 저장소 → 기획 → 집필 → 검증·기억 → 품질·승인 → 평가 기록 → README·재현 절차. 하루 만에 기획→확정 파이프라인과 3회차 재현(3/3 COMMITTED)을 완성 (`c57e05f`–`aed3444`) |
| 10/29 | 프론트 전환 | React 제거, 순수 HTML/CSS/JS 정적 프론트로 전환 (`a2c5fa9`) |
| 10/30 | 아이디어·브랜드 | 아이디어 채팅 P0(스키마 제시·컨텍스트 예산), StorySpine 브랜드 확정 (`6c87bbb`, `3eadd68`) |
| 10/31 | 설계 화면 | 구 프론트 제거·정본 확정, 브랜드 적용, 모델 선택, 소설 설계 화면 (`a21aedb`–`af5d2a2`) |
| 11/1 | 설정·권한 | 노드별 설정(scoped canon) CRUD·상속, 설계 트리 개선, 저자 인증·작품 소유권 (`638bb0d`–`9140ed2`) |
| 11/2 | 집필·발간 | 집필 회차 작업실, 원고 문서 API, 집필↔검토 상태 전이, 3상태(집필중/검토중/발간중) 전환 (`f5b5aa2`–`a47b104`) |
| 11/3 | 기억 고도화·마무리 | Entity 추출·일관성 개선안 구현(사실 모델·확정검사·회차 격리·평가셋), 작가검토 UX, AI 수정 흐름, 발간 단순화, 개선안 문서 (`0c09daa`–`dca8077`) |

## 4. Muse Code 수행 작업 분류

| 분야 | 수행 내용 | 산출물 (저장소 경로) |
|---|---|---|
| 서버 API | 작품·설계·회차·원고·기억·발간 REST API, 상태머신, 예산·재시도·인용 검증 | `server/app/` 25개 모듈, `routes/` 11개, `schemas/` 8개 |
| 프롬프트 | 기획·집필·추출·검증·품질·아이디어 대화용 프롬프트 14종 | `server/app/prompts/` |
| 프론트엔드 | 화면 4종+α(설계·집필·검토·발간·아이디어), 라우터·리사이저·AI 로그 패널 | `frontend/` 16개 JS 모듈, 약 6,400줄 |
| 테스트 | API·상태머신·기억·인용·일관성·발간 등 pytest 스위트 | `server/tests/` 21개 파일, 221개 테스트 함수 |
| 평가 | 스모크·주입실험·재현 파이프라인 실측과 결과 기록 | `evals/실측_20261028.md`, `evals/주입실험_20261028.md`, `evals/결과_20261028.md` |
| 버그 수정 | 커밋 누락·NaN 오류·인계 충돌·캐시 무효화 등 진단과 수정 | `7bbca40`, `2ac129f`, `5c8a5f9` 등 |
| 문서 | README·재현 절차·브랜드·개선안 문서 초안 | `README.md`, `docs/` |

규모 실측: 서버 Python 약 13,400줄(테스트 포함), 프론트 JS 약 6,400줄.

## 5. Meta 제품 활용 정리 (제출 항목 대응)

런타임 사용과 개발 보조는 구분된다.

- **런타임 (앱이 실행 중에 호출)**: Meta Model API, `muse-spark-1.3`.
  용도 = 설정 추출·모순 검증·플롯/초안 생성. 증거 = `server/app/meta_client.py`,
  `server/app/prompts/`, `evals/결과_20261028.md`.
- **개발 보조 (앱 실행과 무관)**: Muse Code.
  용도 = 위 4절의 구현·테스트·문서 작업. 증거 = 본 문서 + Git 이력 54 커밋.
- **미사용**: 이미지 생성(Muse Image), 파트너 기술(Fireworks·Modal·Baseten)은
  이번 제출물에 사용하지 않았다. 해당 폼 항목은 비우거나 미사용으로 기재한다.

## 6. 평가 결과 요약 (소표본, 조정용)

`evals/결과_20261028.md` 기준. 일반 정확도가 아닌 조정용 소표본(n≤3)이다.

- 재현 파이프라인 3회차 완주 3/3 (COMMITTED), 이전 회차 상태 반영 확인.
- 주입실험: 쿨다운 위반 탐지 1/1, 동명이인 미병합, 정상 회차 오탐 0.
- 비용 실측: 3회차 합계 21 호출, in 49,866 + out 119,041 토큰, 벽시계 537초.
- 한계: 확인용 케이스·재발률·비용 미측정. 제출 시 한계를 명시한다.

## 7. 제출 폼 붙여넣기용 영문 (Muse Code 부분)

"Describe your use of Meta products" 항목에서 개발 보조 문단으로 사용한다.
런타임 모델 설명과 합쳐서 제출한다.

```text
During development, I used Muse Code, Meta's agentic coding tool, to implement
the application from my pipeline and memory-model design. It produced the
FastAPI server, the static HTML/CSS/JS frontend, the prompt set, and a pytest
suite of 21 files with 221 test functions, and it diagnosed and fixed bugs
such as a missing commit, a NaN navigation error, and cache invalidation
issues. The full iteration history is visible as 54 commits from October 28 to
November 3. This development assistance is separate from the Meta model calls
used by the application at runtime.
```

## 8. 근거·재현

- 저장소: `https://github.com/kimiyo/meta-hackathon-20261026` (제출 시 공개 확인)
- 범위: `8408601` (initial, 10/28) → `dca8077` (11/3), 54 커밋
- 실행·재현: `README.md`의 설치·실행·재현 절차 그대로
- 보안: `.env` 미추적, 키는 서버 메모리 한정, 제출 전 비밀 검사 수행
