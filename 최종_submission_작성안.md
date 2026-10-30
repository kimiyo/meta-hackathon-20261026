# StorySpine — 최종 Submission 준비 자료와 작성안

브랜드 업데이트: 2026-10-30. 프로젝트명은 **StorySpine**, 시각 방향은 **C안 · Story Architecture**로 확정했다. 제출 설명과 발표·영상 표지에는 `StorySpine`과 `Give your story a spine.`을 사용한다. 열린 책과 중심축 심벌, 책 사이로 이어지는 길을 이미지 기준으로 삼는다. [브랜드 기획안](docs/StorySpine_브랜드_기획안.md), [채택 이미지와 사용 가이드](docs/branding/README.md)를 따른다. 현재 저장된 것은 채택 보드이며 개별 아이콘·히어로 및 발표용 완성 파일은 후속 제작 대상이다.

기준: 2026년 10월 28일 사용자가 첨부한 **Submit Project 화면**, 지원서 기록, 기존 준비 계획.

**필수 준비물은 공개 GitHub 저장소 URL, 3분 데모 영상 URL, 프로젝트 설명, Meta 제품 활용 설명, Meta 모델의 중요성 설명이다.** 트랙과 문제 주제도 선택해야 한다.

이 문서는 제출 화면에 맞춘 작성 초안이다. 실제 구현·사용 모델·측정 결과를 확인하지 않았으므로 아래 영문은 **완료된 기능만 남겨 제출한다.** `[대괄호]`는 실제 값으로 교체하고, 미구현 기능은 삭제하거나 향후 계획으로 명시한다. 첨부 화면은 제출 항목의 근거로 사용했으며, 사이트 입력·제출·저장소 공개·영상 업로드는 수행하지 않았다.

## 1. 제출 화면의 전체 항목

별표(*)가 있는 항목을 필수로 분류했다. 화면 아래쪽의 추가 항목이나 글자 수 제한은 첨부만으로 확인되지 않는다.

| 화면 항목 | 필수 여부 | 내 프로젝트에서 입력·준비할 내용 |
|---|---|---|
| Which track are you submitting to? | 필수 | **Indie Developer Track** |
| Which problem statement did you build towards? | 필수 | **Build for Your World** |
| Most Viral Track용 X post link | 해당 부문 참가 시 | 실제 프로젝트를 소개한 X 게시물 URL. 기본 제출용 필수 표시는 없음 |
| Project Description | 필수 | 작가의 문제, 해결 흐름, 실제 구현 범위, 검증 결과 |
| Public GitHub Repository | 필수 | **이번 웹소설 코파이럿의 공개 저장소 URL** |
| 3-Minute Demo Video | 필수 | **3분 데모 영상 URL** |
| Describe your use of Meta products to build the submitted project | 필수 | 실제 사용한 모델·제품과 기능별 역할, 코드·데모 증거 |
| Why are Meta's models important to your solution? | 필수 | 설정 해석·문맥 판단·집필 지원에 모델이 필요한 이유 |
| Provide feedback from your experience using Meta products | 선택 | 실제 사용 경험의 장점·문제·개선 요청 |
| Provide feedback from your experience using partner technologies | 선택 | 실제 사용한 Fireworks·Modal·Baseten 등의 경험 |

**첨부로 새로 확인된 사항:** 공개 저장소와 3분 영상이 필수다. 영상 입력란은 YouTube 주소를 예시로 보여주지만, YouTube만 허용하는지·미등록 공개가 가능한지는 화면만으로 확정할 수 없다. 별도 배포 URL·프로젝트명·팀원 입력란은 첨부 범위에서 보이지 않는다. 필요하면 프로젝트명과 배포 링크를 설명·README 첫 부분에 넣는다.

## 2. 준비해야 할 실제 자료

### A. 공개 GitHub 저장소

`speaking-kiosk`는 참가 지원 때 기술 경험을 보여준 과거 프로젝트다. **최종 제출 URL에는 이번 웹소설 코파이럿 저장소를 사용한다.**

권장 준비 내용:

- [ ] 이번 제품의 실행 가능한 소스 코드와 의존성 정보
- [ ] README: 문제·해결 방식·핵심 기능·설치·실행·Meta 활용·데모 링크
- [ ] `.env.example`: 환경변수 이름과 설명만 포함
- [ ] 직접 작성했거나 공유 권한이 있는 샘플 원고·설정집
- [ ] 모순이 있는 회차와 정상 회차, 기대 결과·근거
- [ ] 실제 평가 결과와 실행 환경·모델·입력 분량·한계
- [ ] 적용한 라이선스와 외부 코드·데이터·이미지의 출처
- [ ] API 키·실제 환경설정·개인정보·비공개 원고가 현재 파일과 Git 이력에 없는지 확인
- [ ] 로그아웃 상태에서 저장소가 열리는지 확인

권장 README 순서:

```text
1. Project name + one-sentence description
2. Demo video / Live demo if available
3. Problem and target users
4. What works in this submission
5. How Meta products are used
6. Architecture and author-approved memory flow
7. Quick start and environment variables
8. Sample story and reproduction steps
9. Evaluation results and limitations
10. Credits, licenses, and reused components
```

배포 환경이 없다면 깨끗한 환경에서 실행할 수 있는 설치 절차와 샘플을 제공한다. 배포가 있다면 심사자가 접근할 수 있는 경로·계정·초기화 방법을 안내한다.

### B. 3분 데모 영상

**한 작가가 기존 설정을 확인하고 새 회차의 모순을 수정한 뒤 다음 회차를 준비하는 흐름**을 보여준다. 기능 소개 화면만 나열하기보다 입력·모델 결과·작가의 선택을 실제 제품에서 연결한다.

| 시간 | 내용 | 화면에서 보여줄 증거 |
|---|---|---|
| 0:00–0:20 | 문제와 대상 | 연재 작가가 회차마다 설정을 찾아야 하는 상황 |
| 0:20–0:45 | 설정 기억 | 기존 회차 입력, 설정 추출, 출처, 작가 승인 |
| 0:45–1:25 | 핵심 시연: 모순 탐지 | 새 회차 검증, 충돌한 문장과 기존 근거 대조 |
| 1:25–1:55 | 수정·승인·기억 반영 | 작가 판단, 수정본 저장, 갱신된 설정 |
| 1:55–2:20 | 다음 회차 지원 | 확정 설정을 참조한 플롯·짧은 장면 생성 |
| 2:20–2:35 | 이미지 또는 기술 증거 | 구현했다면 설정 기반 이미지, 아니면 Meta 호출·구조 설명 |
| 2:35–3:00 | 검증 결과와 마무리 | 실제 결과·한계, 저장소·데모 접속 정보 |

이미지 기능이 없으면 해당 구간을 삭제하고 핵심 검증·결과 설명에 배분한다. 영문 자막·내레이션은 권장안이며 첨부에서 언어 규정은 확인되지 않는다. 녹화에서 편집으로 줄인 대기 시간이나 사전 생성 자료는 실제 실시간 처리와 혼동되지 않게 표시한다.

**시연용 가상 사례 — 직접 작성해 사용할 예시:**

- 1화 확정 설정: “서윤은 마법을 사용한 뒤 반드시 24시간 동안 다시 사용할 수 없다.”
- 새 회차: “한 시간 뒤, 서윤은 같은 마법을 다시 사용했다.”
- 기대 결과: 쿨다운 규칙과 충돌하는 두 문장을 제시한다.
- 작가의 선택: 시간을 다음 날로 바꾸거나, 예외를 설명하는 설정을 명시적으로 추가한다.
- 후속 확인: 재검증하고 다음 회차 플롯에 확정된 규칙이 반영되는지 보여준다.

이 사례는 설명용 창작 예시다. 현재 제품이 이 기능을 구현했다고 주장하는 증거는 아니다.

### C. Meta 활용 증거와 결과 기록

| 준비 자료 | 기록할 값 |
|---|---|
| 텍스트 모델 | 실제 모델 ID·버전·사용 모드 |
| 기능 연결 | 설정 추출 / 모순 검증 / 플롯·장면 생성 중 실제 구현 항목 |
| 이미지 | 실제 모델 ID, 입력 설정·프롬프트·출력. 미사용이면 제외 |
| 개발 보조 | Muse Code를 실제 썼다면 수행 작업. 미사용이면 제외 |
| 코드 증거 | API 호출 부분, 출력 검증·저장 부분의 저장소 경로 |
| 평가 | 테스트 회차 수·분량·사례 수·탐지·오탐·누락·지연·사용량 |
| 사용자 경험 | 실제 사용자 수·수행 과업·관찰·개선 사항. 미실시라면 명시 |

**$150 크레딧 활성화는 앞서 제공한 Billing 화면으로 확인되지만, API 호출·모델 활용·기능 완성의 증거는 별도로 필요하다.** 이전 준비 문서의 정확도·응답 시간은 목표치이므로 측정 결과로 그대로 옮기지 않는다.

## 3. 항목별 작성안

아래 영어 문장은 폼에 활용할 초안이다. 글자 수 제한은 첨부에 없으므로 저장 과정에서 확인하고 줄인다. 각 항목의 권장 분량은 공식 제한이 아니다.

### 3-1. Track / Problem statement

```text
Indie Developer Track
Build for Your World
```

지원서와 같은 선택이다. Most Viral 항목은 기본 트랙 선택과 별도로 보이므로 Indie 선택을 바꾸는 것으로 해석하지 않는다.

### 3-2. Project Description — 필수

작성 순서: **대상과 문제 → 실제 해결 흐름 → 작가의 통제 → 데모·평가 범위**. 권장 분량은 영문 약 150~220단어다.

**아래는 기억·근거 제시·작가 승인·플롯 지원이 모두 구현된 경우의 초안이다. 해당되지 않는 문장은 삭제·수정한다.**

```text
Web Novel Copilot is an AI-assisted writing workspace for serialized web novel authors. As a story grows, authors must keep track of character traits, relationships, timelines, and world-building rules across many chapters. Rechecking earlier chapters interrupts writing, while missed details can create continuity errors.

The submitted prototype turns existing chapters into structured story memory linked to source passages. When an author reviews a new chapter, it flags potential contradictions and presents the relevant earlier evidence. The author can accept a correction, reject a suggestion, or explicitly approve a change to the story's canon. Approved memory then informs the next chapter's outline and scene drafting.

The goal is to reduce continuity-checking effort while keeping creative decisions with the author. This fits Build for Your World by addressing the recurring work of maintaining a serialized fictional world.

The demo covers [implemented workflow] using [number] original sample chapters. Evaluation on [number] labeled cases found [actual results]. Current limitations include [observed limitations].

Live demo, if available: [URL]
```

표본 평가를 하지 않았다면 평가 문장을 삭제하고 다음과 같이 바꾼다.

```text
This submission demonstrates the workflow on an original sample story. A broader evaluation with working authors has not yet been completed.
```

이미지를 실제 구현했다면 관련 문장을 추가한다.

```text
It also generates character or scene visuals from approved story details, connecting visual references to the same story memory used for writing.
```

프로젝트명은 가칭이다. 실제 이름이 정해지면 저장소·영상·본문에서 통일한다. 이미지 미구현, 수백 회차 미검증, 사용자 평가 미실시는 숨기지 말고 제출 범위를 구체적으로 적는다.

### 3-3. Public GitHub Repository — 필수

```text
[이번 웹소설 코파이럿의 실제 공개 GitHub 저장소 URL]
```

설명 문장 대신 저장소 URL을 입력한다. 저장소 이름·주소는 아직 확인되지 않았으므로 임의로 만들지 않았다.

### 3-4. 3-Minute Demo Video — 필수

```text
[업로드를 완료하고 외부 재생을 확인한 3분 데모 영상 URL]
```

입력란에는 동영상 파일 자체가 아니라 URL을 넣는 형태다. 영상 처리 완료·음성·자막·접근 권한을 확인한다. 플랫폼·공개 범위에 추가 조건이 있는지는 실제 폼·공지에서 확인한다.

### 3-5. Describe your use of Meta products… — 필수

이 항목은 **무슨 제품을 어디에 어떻게 사용했는가**를 설명한다. 권장 분량은 영문 약 120~180단어다.

**실제 텍스트 모델 통합과 승인 흐름을 구현한 뒤 사용하는 초안:**

```text
The submitted application uses Meta Model API with [exact text model ID] for [implemented tasks: story-fact extraction, continuity review, and/or chapter planning].

For extraction, the application sends chapter text and requests structured facts, including characters, relationships, rules, and supporting passages. For continuity review, it supplies the new draft together with relevant approved facts and source excerpts. The model returns potential conflicts for author review. Approved story memory also provides context for [implemented outlining or scene-drafting feature].

The application handles persistence, source linking, output validation, and author approvals. The model proposes interpretations and suggestions; it does not independently define the story's canon.

The integration is documented in [repository paths]. The demo shows [specific visible Meta-powered steps].
```

기능별로 실제 구현에 맞춰 줄인다. 설정 검색이 미구현이라면 관련 설정을 검색해 전달하는 것처럼 쓰지 않고, 전체 설정을 전송하는 등 실제 방식을 적는다. 일반 텍스트 출력을 파싱하는 경우에는 API의 특정 구조화 출력 기능을 사용한 것으로 오해하지 않게 설명한다.

이미지 생성을 구현한 경우에만 추가한다.

```text
For visuals, [exact image model ID] generates [character or scene images] from approved character descriptions and scene details.
```

Muse Code를 실제로 개발 보조에 사용한 경우에만 추가한다.

```text
During development, I used Muse Code for [specific tasks]. This development assistance is separate from the Meta model calls used by the application at runtime.
```

### 3-6. Why are Meta's models important… — 필수

이 항목은 **왜 모델의 능력이 이 문제 해결에 중요한가**를 설명한다. 앞 항목의 API 목록을 반복하기보다 설정 관리의 어려움과 모델의 역할을 연결한다. 권장 분량은 영문 약 100~160단어다.

**실제 구현에 맞춰 조정할 영문 초안:**

```text
Continuity checking in fiction requires interpreting meaning across differently worded passages. A character may be referred to by a title, a rule may be implied rather than repeated, and an apparent contradiction may be a deliberate change in the story. Keyword matching alone cannot reliably distinguish these situations.

In this prototype, Meta's [actual model] supports [implemented interpretation and generation tasks] using the story context supplied by the application. It helps connect existing facts with a new draft and turn relevant context into actionable suggestions for the author.

The model's role is combined with source evidence, persistent memory, and author approval. This makes AI assistance part of a reviewable writing workflow, while the author remains responsible for creative decisions and final accuracy.
```

「Meta만 가능하다」「다른 모델보다 우수하다」「한국어에서 가장 정확하다」처럼 비교 평가 없는 주장은 넣지 않는다. 실제로 비교 실험했다면 조건·결과를 별도 한 문장으로 제시한다.

### 3-7. Feedback on Meta products — 선택

**실제 경험만 작성한다.** 크레딧을 받았다는 사실만으로 문서·지연·출력 품질에 대한 사용 후기를 만들지 않는다. 아직 평가하지 않았다면 비워둘 수 있다.

```text
I used [product/model] for [task]. In [specific test or workflow], [observed positive behavior] helped with [concrete benefit].

The main friction was [specific observed issue], which occurred under [conditions]. I handled it by [actual workaround].

My highest-priority improvement request is [specific change], because it would help developers building [relevant workflow].
```

개발 중 기록할 후보는 한국어 고유명사 유지, 인용 정확성, 출력 형식의 안정성, 긴 입력의 비용·지연, 모델 접근·오류 안내다. 문제가 실제 발생했을 때 입력 조건과 현상을 적고, 발생하지 않은 불만을 만들어 넣지 않는다.

### 3-8. Feedback on partner technologies — 선택

Fireworks·Modal·Baseten 등을 실제 사용했다면 **어떤 서비스로 어떤 모델·작업을 실행했는지**와 경험을 적는다.

```text
I used [partner service] to [actual workload]. [Observed benefit] helped with [specific need]. The main issue was [actual issue, if any], and I would suggest [concrete improvement].
```

사용하지 않았다면 비워두거나 다음과 같이 적는다.

```text
I did not use partner technologies in the submitted prototype.
```

### 3-9. Most Viral용 X 게시물 — 해당 시 선택

해당 부문에 참여하려면 실제 X 게시물을 작성·게시한 뒤 링크를 넣어야 한다. 게시 자체는 아직 수행하지 않는다. 해시태그·집계 기준·마감·공개 조건은 별도 공지를 확인한다.

아래 문장은 실제로 구현·시연한 범위에 맞춰 사용할 게시물 초안이다.

```text
I'm building Web Novel Copilot for serialized fiction authors: source-linked story memory, continuity checks, and author-approved updates that guide the next chapter. Built with Meta [actual model/product]. Demo: [link]
```

## 4. 제출 전에 채워야 할 사실 목록

| 확인 항목 | 현재 상태 | 입력할 실제 값 |
|---|---|---|
| 최종 제품명 | Web Novel Copilot 가칭 | [확정 이름] |
| 트랙·테마 | 지원서·첨부 기준 확인 | Indie Developer / Build for Your World |
| 공개 저장소 | 미확인 | [실제 URL] |
| 3분 영상 | 미확인 | [실제 URL] |
| 배포·실행 방식 | 미확인 | [URL 또는 재현 절차] |
| 텍스트 모델·모드 | 이전 문서는 추천안 | [실제 ID·모드] |
| 이미지 모델·기능 | 계획만 확인 | [구현 여부·실제 ID] |
| 기억·근거·승인·플롯 | 계획만 확인 | [완료 기능] |
| 샘플 회차·분량 | 미측정 | [실제 수·분량] |
| 평가 결과·응답 시간 | 미측정 | [실제 결과 또는 미실시] |
| Meta·파트너 후기 | 사용 경험 미확인 | [실제 관찰 또는 생략] |

## 5. 최종 제출 체크리스트

- [ ] Indie Developer Track / Build for Your World 선택
- [ ] 설명이 실제 구현·영상·README와 일치
- [ ] 템플릿의 대괄호와 미사용 선택 문장 제거
- [ ] 정확도·시간 절감·지원 회차 수는 실제 측정값만 사용
- [ ] 이번 프로젝트의 공개 GitHub URL 입력, 로그아웃 상태 접근 확인
- [ ] 3분 영상 URL 입력, 외부 재생·소리·글씨 확인
- [ ] 실제 Meta 제품·모델·역할과 코드 경로를 정확히 작성
- [ ] 이미지·파트너·Muse Code는 실제 사용한 경우만 기재
- [ ] 미구현 기능·평가 한계를 명시
- [ ] 공유 원고·소스·이미지의 권리와 API 키 노출 여부 확인
- [ ] 첨부 아래쪽의 추가 필드·필수 동의·글자 수 제한 확인
- [ ] 최종 제출 후 완료 화면·제출 시각·링크·버전 보관

제출 경로: [Cerebral Valley Submit Project](https://cerebralvalley.ai/e/meta-ai-developer-hackathon/hackathon/submit). 본 문서는 사용자가 제공한 화면을 근거로 작성했으며, 폼 입력·외부 공개·실제 제출을 대신 수행한 결과는 아니다.
