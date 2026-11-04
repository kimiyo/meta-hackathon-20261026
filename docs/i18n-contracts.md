# i18n Contracts (Stage-1 handoff)

한/미 지원 구현계획 v2의 stage-1 계약면을 wave-1
담당자가 파일 없이도 읽을 수 있게 모았다. 구현 위치도 함께 적는다.
(원 계획서는 `local-only/` 비공개 폴더로 이동, 본 계약면이 정본이다.)

## 1. 레지스트리 API (`server/app/locales.py`)

- `MARKETS = {"KR": {...}, "US": {...}}`, 각 항목 키:
  `default_ui_locale`, `default_writing_locale`, `preset_catalog`,
  `default_length_unit` (`KR`→`characters`, `US`→`words`).
- `UI_LOCALES = ["ko-KR", "en-US"]`.
- `validate_market(code)` / `validate_writing_locale(loc)` /
  `validate_ui_locale(loc)`: 정규화값 반환, 미등록이면 `ValueError`
  (메시지에 지원 목록 포함).
- `market_config(code)`: 읽기 전용 복사본.
- `locales_payload()`: `GET /api/v1/locales` 응답 본문.
- `can_change_locale(conn, work_id) -> (allowed, reason)`:
  `idea_threads`, `story_designs`, `manuscript_revisions`,
  내용 있는 `manuscript_docs`(초안 비어있고 revision 0이며 승인 없음은 제외),
  `episode_jobs`, `review_messages` 중 하나라도 있으면 `(False, "<kind>:<n>")`.

## 2. 로케일 API

`GET /api/v1/locales` (인증 불필요, 로그인 전 선택에 사용):

```json
{
  "markets": [
    {"code": "KR", "name_key": "market.KR",
     "default_ui_locale": "ko-KR", "default_writing_locale": "ko-KR"},
    {"code": "US", "name_key": "market.US",
     "default_ui_locale": "en-US", "default_writing_locale": "en-US"}
  ],
  "ui_locales": ["ko-KR", "en-US"]
}
```

프론트엔드 국가 선택지는 이 응답으로 렌더링한다. `name_key`는 번역 사전 참조.

## 3. 작품 API 규칙 (`server/app/routes/works.py`)

- 생성 `POST /api/v1/works`: `market`, `writing_locale`, `platform` 선택.
  생략 시 `KR`/`ko-KR`(이전 클라이언트 호환). 미등록 값은 HTTP 400
  (`UNKNOWN_MARKET` / `UNKNOWN_WRITING_LOCALE` 코드).
- 신규 US 작품 + `platform` 생략 → 빈 문자열 저장. 한국 기본값(`문피아`)을
  자동 적용하지 않는다. 신규 KR 작품 + 생략 → DB 기본값 `문피아`.
- 명시적 `platform: ""` 허용(저장). 그 외 비어있지 않은 값은 기존 목록 검증.
- 응답은 저장된 `market`/`writing_locale`/`platform`을 그대로 에코.
- 수정 `PATCH /api/v1/works/{key}`: `market`/`writing_locale` 변경은 값이
  실제로 바뀔 때만 `can_change_locale` 검사. 차단 시 HTTP 409 +
  `WORK_LOCALE_LOCKED` (`params.reason`에 차단 사유).
- 화면 국가 변경은 기존 작품을 건드리지 않는다(서버는 작품 저장값을 유지).

## 4. DB 마이그레이션 (`server/app/db.py`)

- 신규 DB: `works.market DEFAULT 'KR'`,
  `works.writing_locale DEFAULT 'ko-KR'`.
- 기존 DB: 동일 컬럼 `ALTER TABLE ... ADD COLUMN` + `try/except
  OperationalError` (기존 publish/cover 패턴과 동일, 재실행 안전).
  `NOT NULL DEFAULT`이므로 기존 행은 자동 `KR`/`ko-KR`, 기존 값 불변.
- `llm_calls` 추적 컬럼(신규 DDL + ALTER, 기본 `''`):
  `market`, `writing_locale`, `prompt_feature`, `prompt_version`,
  `preset_id`, `preset_version`. 기록 로직 연결은 후속 단계.

## 5. 프론트엔드 i18n (`frontend/js/i18n.js`)

```js
import { setLocale, getLocale, t } from "./i18n.js";
await setLocale("en-US");       // /locales/<locale>.json 로드
t("nav.ideas");                 // "Ideas"
t("work.count", { count: 3 });  // "{count}" 치환
```

- 조회 체인: 활성 로케일 → `ko-KR` 폴백 → 키 자체 + `console.error`.
- `setLocale`은 `localStorage["ui_locale"]`에 저장하고
  `locale-changed` 이벤트를 발생시킨다. `storedLocale()`로 저장값 읽기.
- `format(template, params)`: `{name}` 치환(누락된 이름은 그대로 둠).
- 복수형(최소 방식): 사전은 `{count}` 치환만, 라이브러리 없음.
  단복수 형태가 다르면 `key.one`/`key.other`처럼 명시적 키를 두고
  호출자가 `count`로 선택. ICU가 필요해지면 이 모듈 한곳에만 추가.

### 프래그먼트 규약

- 소유자별 작업 파일: `frontend/locales/_frag/<owner>.<locale>.json`
  (예: `_frag/ideas.en-US.json`). 키 충돌 금지.
- 병합은 런타임에 수행한다(정적 서빙이라 별도 빌드 단계가 없음):
  `frontend/js/i18n.js`의 `loadLocale`이 베이스 사전(`<locale>.json`)을
  읽은 뒤 `_frag/manifest.json`의 `fragments` 순서대로 병합한다.
  베이스가 우선하고, 중복 키·읽기 실패는 `console.error`로 기록한 뒤
  나머지 사전을 계속 사용한다(운영 화면이 비지 않음). 중복·누락은
  테스트(`server/tests/test_i18n_merged.py`)에서 실패로 탐지한다.
- `_frag/manifest.json`에 등록되지 않은 프래그먼트는 로드되지 않는다.
- 소유자별 키 접두사:

| 소유자 | 접두사 |
|---|---|
| shell | `common.` `nav.` `error.` `home.` `model.` `monitor.` `manual.` |
| ideas | `ideas.` |
| design | `design.` |
| writing | `work.` `manuscript.` `ailog.` |
| review | `review.` `entities.` |
| publish | `publish.` |
| integration | `error.BLURB_EMPTY` (단일 키, 아래 §7 참조) |

- `market.*`는 stage-1 스켈레톤 소유(수정 시 shell과 협의).
- 번역 키는 화면 문구가 아니라 기능 이름. 하드코딩 문자열 추가 금지.

### 브라우저 저장 키 (plan §4.2)

`preferred_market`, `ui_locale` — 모두 `localStorage`, 브라우저별.
작품 설정은 서버 저장(기기 간 유지).

## 6. 프롬프트 로더 (`server/app/prompt_loader.py`)

`load(feature, writing_locale, market, base_dir=None) -> PromptBundle`:

1. 필수: `prompts/<writing_locale>/<feature>.txt`
2. 선택 오버레이: `prompts/markets/<market>/<feature>.txt`
3. 병합 규칙: 오버레이가 비어있지 않으면 로케일 본문 뒤에 빈 줄 하나로
   이어붙임 (`locale_text.rstrip + "\n\n" + overlay.lstrip`).
   언어는 로케일 파일 소유, 시장별 창작 지침은 오버레이 소유.
4. 레거시 평면 `prompts/<feature>.txt`는 `writing_locale == "ko-KR"`이고
   `ko-KR` 파일이 없을 때만 사용(이전 과정 파일).
5. 필수 파일 누락 → `FileNotFoundError` (feature+locale 명시).
   다른 언어로 조용한 대체 금지.
6. `feature`는 `[A-Za-z0-9_-]+`만 허용(경로 탈출 차단).

`PromptBundle(text, feature, writing_locale, market, locale_path,
overlay_path, legacy_fallback)` — `locale_path`/`overlay_path`는
`llm_calls` 기록용 provenance. 호출부 재배선은 후속 단계에서 수행.

## 7. 오류 형태 (`server/app/errors.py`, plan §5.4)

신규 코드형 detail:

```json
{"detail": {"code": "WORK_TITLE_REQUIRED", "params": {"max": 80}}}
```

- `error_detail(code, params, extra)` / `coded_error(status, code, ...)` /
  `is_coded_detail(detail)` 헬퍼. 기존 문자열 detail과 병용(하위 호환).
- `latestRevision` 같은 추가 키는 `extra`로 그대로 전달(원고 충돌 경로 유지).
- 프론트엔드 매핑 규칙: `code` → `error.<CODE>` 키 조회 +
  `params` 치환 → `t()` 폴백 체인 적용. 문자열 detail이면 그대로 표시.
- 카탈로그: `WORK_TITLE_REQUIRED`, `UNKNOWN_MARKET`,
  `UNKNOWN_WRITING_LOCALE`, `UNKNOWN_UI_LOCALE`, `UNKNOWN_PLATFORM`,
  `WORK_LOCALE_LOCKED`. 문구는 `docs/i18n-glossary.md`.
- `BLURB_EMPTY`는 `server/app/publishing.py`에 정의하고
  `integration` 프래그먼트의 `error.BLURB_EMPTY`로 렌더링한다.
  (`errors.py`·용어집 오류표는 shell 테스트가 값 일치를 단언하므로
  통합 단계에서 건드리지 않는다.)

## 8. 단어 수 정의 (구현은 후속 담당)

영어 분량의 유일한 정의. 서버·화면 동일 결과:

```python
# server
import re
words = len(re.findall(r"\S+", text))
```

```js
// frontend
const words = (text.match(/\S+/g) || []).length;
```

빈 문자열·공백만 → 0. `None`/`null`은 호출 전 `""`로 정규화.

## 9. 프리셋 카탈로그 스키마 (plan §7.1, US 구현은 후속 담당)

시장별 카탈로그(`MARKETS[m].preset_catalog`: `kr`/`us`)의 항목 스키마:

```text
id            안정 ID (KR 기존 키 유지: fusion-fantasy, romance, ...;
              US는 충돌 없는 신규 ID)
market        KR | US
version       프리셋 내용 버전 (스냅샷 이후 변경 추적용)
label         화면 표시명 (UI 언어가 아니라 카탈로그 언어)
description   화면 설명
pov           AI용 시점 지시
diction       AI용 문장/어휘 지시
feedback      AI용 전개/피드백 지시
```

- `label`/`description`(화면용)과 `pov`/`diction`/`feedback`(AI용) 분리.
- 적용 시 작품에 값 스냅샷 저장(현행 유지). 이후 카탈로그 변경이
  기존 작품 스냅샷·사용자 문체를 덮어쓰지 않음. 사용자 문체 임의 번역 금지.
- 작품 시장과 같은 카탈로그의 목록을 기본 제공.
