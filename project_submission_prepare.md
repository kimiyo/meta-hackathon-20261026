# 📋 Meta Global AI Developer Hackathon 제출서 작성본

원본 양식(`project_submission_prepare.md` 초안)을 실제 값으로 채운 제출 직전본이다.
**복붙용 영문은 `text` 블록**, 상태 표시는 ✅ 입력완료 / ⚠️ 사용자 확인 필요 / ⬜ 사용자 직접 수행이다.
오늘(11월 4일) 밤 9시 마감 — 여유 있게 제출할 것.

---

## 1. 기본 정보 및 트랙 선택

* **Team Name** *(필수)* ⬜ — jh 기록에 팀명 없음. **1인 참가면 본인 이름/핸들로 정해서 입력.**
* **Team Members**: Jonghoon Kim / kimiyohome (폼에서 본인 계정 확인)
* **Which track are you submitting to?** *(필수)* ✅
  * `[x] Indie Developer Track` (jh 지원서와 동일)
* **Which problem statement did you build towards?** *(필수)* ✅
  * `[x] Build for Your World` (jh 지원서와 동일)
* **Most Viral Track 제출 링크** (선택) ⬜ — 참가 시 X 포스팅 후 URL 입력, 미참가 시 공란.

---

## 2. 프로젝트 핵심 내용

### Project Description *(필수)* ✅ — 아래 영문을 그대로 붙여넣기

```text
StorySpine — Give your story a spine — is an AI-assisted writing workspace for serialized web novel authors. As a story grows across chapters, authors must track character traits, relationships, timelines, and world rules; rechecking earlier chapters interrupts writing, and missed details become continuity errors readers notice.

The prototype turns published chapters into structured story memory linked to source passages. Each chapter cycles through AI drafting, entity extraction with evidence quotes, deterministic rule checks plus AI continuity review against approved memory, author-approved fixes, and publishing. The author accepts, rejects, or canonizes every change, and approved memory grounds the next chapter.

The demo runs this cycle on an original English sample story. Deterministic checks pass 12/12 paired cases; a 3-chapter instrumented run completed at about 56k tokens and 170 seconds per chapter. No evaluation with working authors has been completed, and image generation is not implemented.

Live demo: none — reproduction steps are in the repository README.
```

근거: README §3·§4·§9, `evals/결과_20261028.md`, `evals/cases.json`. 측정 안 된 수치·미구현(이미지·수백 회차·작가 평가)은 제외/명시했음.

### Public GitHub Repository *(필수)* ✅ URL 확정 + ⬜ 공개 전환 필요

```text
https://github.com/kimiyo/meta-hackathon-20261026
```

* 현재 404 = **비공개 상태**이므로 제출 전 Settings → Public 전환 필수, 전환 후 로그아웃 상태에서 접속 확인.
* 저장소 내용 점검 완료: 실행 코드·의존성·README(10항목)·`.env.example`·자작 샘플·모순/정상 짝·평가 기록 포함. `.env`는 이력 포함 미커밋 확인.
* ⚠️ **LICENSE 파일 없음** — 공개 전 추가 권장(미정. MIT 권장, 결정 필요).

### 3-Minute Demo Video *(필수)* ⬜ URL 필요

* 3분 영상 파일은 준비됨(저장소에 `demo-video-3min.mp4` 커밋됨).
* 폼은 **URL 입력**이므로 YouTube 등에 업로드 후 외부 재생 확인하고 아래에 기입:

```text
[업로드 후 영상 URL]
```

---

## 3. 메타 제품 활용 내역

### Describe your use of Meta products… ✅ — 아래 영문을 그대로 붙여넣기

```text
The application uses Meta Model API with muse-spark-1.3 (default; muse-wonder-1.0 as an optional preset) via chat completions with structured JSON outputs. One module, server/app/meta_client.py, is the sole AI exit; locale-specific prompts live in server/app/prompts/, and every call records model, preset, tokens, latency, and prompts for the in-app AI call log.

Implemented Meta-powered tasks: idea chat, design planning, episode drafting, entity extraction with evidence quotes, proposal verification, consistency review, manuscript chat edits, AI contradiction fixes, quality review, and blurb generation. The application handles persistence, source linking, output validation, and author approvals — the model proposes interpretations, never the canon.

The demo shows extraction, continuity review with evidence episodes, and AI fixes. No partner inference, open-weight models, or image/voice models are used. During development I used Muse Code for implementation, tests, and docs; that assistance is separate from runtime model calls.
```

### Why are Meta's models important… ✅ — 아래 영문을 그대로 붙여넣기

```text
Continuity checking in fiction requires interpreting meaning across differently worded passages. A character may be called by a title, a rule may be implied rather than repeated, and an apparent contradiction may be a deliberate story change. Keyword matching cannot reliably distinguish these cases.

Here Meta's models interpret supplied story context: connecting approved facts with a new draft, judging whether a mismatch is a real conflict, and drafting fixes the author reviews. Without this interpretation layer the workflow would be search plus manual comparison.

Model output is always paired with source evidence, persistent memory, and author approval, so assistance stays inside a reviewable workflow and creative responsibility remains with the author.
```

비교 주장 없음(비교 실험 미수행과 정합).

### Partner technologies feedback ✅

```text
I did not use partner technologies in the submitted prototype.
```

---

## 4. 만족도 및 평가 문항 — ⚠️ 전 항목 사용자 주관 답변. 제안값이므로 확정 필요

* **How likely are you to build with Meta Model API?** 제안: `Very likely` (전 기능이 이 API 기반)
* **Overall opinion of Meta Model API?** 제안: `Favorable` (동작은 안정적이나 장문 생성 지연·잘림 경험 있음)
* **Recommend Meta Model API to another developer? (0~5)** 제안: `4`
* **Recommend Muse Code to another developer? (0~5)** 제안: `5` (구현·테스트·문서 전 과정 사용)
* **Biggest obstacle?** 제안: `응답 속도` — 장문 생성 시 수 분 소요·읽기 타임아웃·출력 잘림이 실제 최대 장애. 폼 선택지에 가장 가까운 항목 선택.

## 5. 비교 및 도구 선택 — ⚠️ 전 항목 사용자 주관. 특히 첫 항목 주의

* **Compared with other AI models…?** ⚠️ **비교 실험을 하지 않았으므로 우호적 비교 선택 금지.** `비교 안 함/비슷함` 계열의 중립 선택지가 있으면 그것을, 없으면 가장 덜 강한 표현을 선택.
* **What matters most when choosing a model/tool?** 제안: `답변/코드 품질` (구조화 JSON 안정성이 이 프로젝트의 핵심 의존)
* **Hardest to switch models/tools?** 제안: `프롬프트 튜닝 종속성` (기능·로케일별 프롬프트 자산이 전환 비용)
* **What would stop production use of Meta's models?** 제안: `가격/비용 예측 어려움` (토큰량은 기록했으나 통화 비용 미산정 — 정직한 선택)

## 6. 약관 동의 및 최종 제출 ⬜ 사용자 직접 수행

* `[ ]` Meta·Cerebral Valley 이용·게시·공유 허가 문구 *(필수 체크)*
* **[Submit]** 클릭 → 완료 화면·제출 시각·링크 보관

---

## 💡 제출 전 체크리스트

* [ ] GitHub 저장소가 **Public**인지 확인 (현재 비공개 → 전환 필요)
* [ ] LICENSE 파일 추가 여부 결정 (없으면 공개 전 추가 권장)
* [ ] 3분 영상 업로드 + URL 외부 재생·소리·자막 확인
* [ ] Team Name 결정
* [ ] §4·§5 주관 문항 확정 (제안값 검토)
* [ ] 마감(오늘 밤 9시) 전 여유 있게 제출, 완료 화면 보관
