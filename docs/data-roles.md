# data-role 규칙

AI와의 대화에서 화면 요소를 정확히 지칭하기 위한 식별자 규칙.
`id`/class는 동작·스타일용으로 유지하고, `data-role`은 대화·선택자용으로 추가만 한다.

## 1. 형식

`화면.영역.요소` 3단 점 표기. 각 단은 소문자 영문 kebab-case.

- 화면: 아래 표의 화면명 사용 (section id가 아닌 의미 이름).
- 영역: 패널·섹션·다이얼로그 단위 (`chat`, `settings`, `tree`, `editor` …).
- 요소: 역할 (`panel`, `list`, `send-btn`, `item-checkbox` …).
- 세그먼트 버튼의 각 옵션은 `그룹-옵션` 평면 표기
  (예: `ideas.chat.msg-mode-all`, `design.tree.view-mode-one`).

## 2. 세부 규칙

1. 영문만 사용한다 (따옴표·인코딩·표기 흔들림 방지).
2. 정적 요소(`index.html`)의 role은 문서 내 유일해야 한다.
3. 동적·반복 요소(JS 템플릿 생성)는 같은 role을 공유한다.
   개별 지칭은 기존 식별자를 병행한다.
   예: "D004의 `ideas.chat.proposal-apply`",
   "306번 메시지의 `ideas.chat.include-checkbox`".
4. 기존 `id`/class를 바꾸지 않고 `data-role`만 추가한다.
5. 적용 범위: 화면 section, 패널/card, 주요 버튼·입력·체크박스,
   주요 목록 컨테이너. 제외: 장식 span/chip/hint, 에러·상태 텍스트,
   반복 행 내부 세부 요소.
6. 새 요소 추가 시 같은 형식을 필수로 붙이고, 이 문서의 목록을 갱신한다.

## 3. 화면명

| data-role 화면명 | section | 화면 |
|---|---|---|
| `home` | `#home` | 소설 목록 |
| `ideas` | `#ideas` | 1 아이디어 |
| `design` | `#sdesign` | 2 소설 설계 |
| `writing` | `#s2` | 3 집필 |
| `review` | `#s3` | 4 작가 검토 |
| `entities` | `#s4` | 5 설정 |
| `shell` | 없음 | 상단바·탭·전역 다이얼로그 |

## 4. 정적 목록 (`index.html`, 문서 내 유일)

### shell — 상단바·탭·전역 다이얼로그

- `shell.topbar` — 상단바
- `shell.topbar.model-chip` — 모델 표시·변경 칩
- `shell.topbar.home-btn` — 작품 목록 버튼
- `shell.topbar.work-pill` — 작품 정보 pill
- `shell.topbar.health-chip` — 서버 상태 칩
- `shell.topbar.budget-chip` — 토큰·AI 로그 칩
- `shell.topbar.user-chip` — 로그인 저자 필명 칩 (클릭 시 작가명 수정)
- `shell.auth.rename-dialog` — 작가명 수정 다이얼로그
- `shell.auth.rename-pen-name-input`, `shell.auth.rename-password-input`
- `shell.auth.rename-cancel-btn`, `shell.auth.rename-confirm-btn`
- `shell.topbar.logout-btn` — 로그아웃 버튼
- `shell.tabs` — 화면 탭 내비
- `shell.tabs.tab-ideas`, `shell.tabs.tab-design`, `shell.tabs.tab-writing`, `shell.tabs.tab-review`, `shell.tabs.tab-entities`
- `shell.tabs.export-btn` — 내보내기 버튼
- `shell.ailog.dialog` — AI 호출 로그 다이얼로그
- `shell.ailog.close-btn`, `shell.ailog.list`, `shell.ailog.detail`
- `shell.model.dialog` — 모델 변경 다이얼로그
- `shell.model.list`, `shell.model.custom-input`, `shell.model.token-input`
- `shell.model.cancel-btn`, `shell.model.apply-btn`

### home — 소설 목록

- `home.screen`
- `home.auth.panel` — 저자 등록·로그인 게이트 카드
- `home.auth.login-id-input`, `home.auth.pen-name-input` (등록 시만), `home.auth.password-input`
- `home.auth.submit-btn`, `home.auth.switch-btn` — 등록·로그인 전환
- `home.create.panel` — 새 소설 만들기 카드
- `home.create.start-btn`
- `home.list.panel` — 내 소설 목록 카드
- `home.list.search-input`, `home.list.grid`
- `home.novel-create.dialog` — 새 소설 다이얼로그
- `home.novel-create.title-input`, `home.novel-create.cancel-btn`, `home.novel-create.confirm-btn`
- `home.novel-delete.dialog` — 소설 삭제 다이얼로그
- `home.novel-delete.title-input`, `home.novel-delete.cancel-btn`, `home.novel-delete.confirm-btn`
- `home.novel-info.dialog` — 소설 정보 수정 다이얼로그
- `home.novel-info.title-input`, `home.novel-info.length-min-input`, `home.novel-info.length-max-input`,
  `home.novel-info.tab-general`, `home.novel-info.tab-style`, `home.novel-info.style-current`, `home.novel-info.style-name-input`,
  `home.novel-info.style-pov-input`, `home.novel-info.style-diction-input`, `home.novel-info.style-feedback-input`,
  `home.novel-info.style-save-new-btn`, `home.novel-info.style-update-btn`, `home.novel-info.style-delete-btn`,
  `home.novel-info.style-use-btn`, `home.novel-info.style-hint`, `home.novel-info.preset-select` (시스템/내 스타일 그룹, 값 `s:키`/`c:ID`), `home.novel-info.cancel-btn`, `home.novel-info.save-btn`

### ideas — 아이디어

- `ideas.screen`, `ideas.screen.locked-notice` — 읽기 전용 안내
- `ideas.screen.col-resizer` — 좌우 열 너비 조절 분할선 (드래그)
- `ideas.chat.panel` — 아이디어 대화 카드
- `ideas.chat.thread-select`, `ideas.chat.new-thread-btn`
- `ideas.chat.messages`, `ideas.chat.choices`
- `ideas.chat.msg-mode` — 포함 방식 세그먼트
- `ideas.chat.msg-mode-all`, `ideas.chat.msg-mode-none`, `ideas.chat.msg-mode-select`
- `ideas.chat.input`, `ideas.chat.send-btn`
- `ideas.synopsis.panel` — 전체 줄거리 카드
- `ideas.synopsis.include-checkbox`, `ideas.synopsis.view`
- `ideas.structure.panel` — 작품 구조 카드
- `ideas.structure.include-checkbox`, `ideas.structure.toggle-btn`
- `ideas.structure.level-select`, `ideas.structure.total-input`
- `ideas.structure.part-mode` — Part 보기 방식 세그먼트
- `ideas.structure.part-mode-one`, `ideas.structure.part-mode-flow`, `ideas.structure.part-mode-full`
- `ideas.structure.part-list`, `ideas.structure.part-add-btn`
- `ideas.settings.panel` — 설정정보 카드
- `ideas.settings.all-checkbox`
- `ideas.settings.view-mode` — 보기 방식 세그먼트
- `ideas.settings.view-mode-one`, `ideas.settings.view-mode-flow`, `ideas.settings.view-mode-full`
- `ideas.settings.list`
- `ideas.handoff.panel` — 설계로 인계 카드
- `ideas.handoff.start-btn`
- `ideas.handoff.dialog` — 인계 확인 다이얼로그
- `ideas.handoff.preview`, `ideas.handoff.cancel-btn`, `ideas.handoff.confirm-btn`

### design — 소설 설계

- `design.screen`
- `design.screen.col-resizer-left` — 파트너·구조 열 분할선 (드래그)
- `design.screen.col-resizer-right` — 구조·편집 열 분할선 (드래그)
- `design.meta.panel` — 설계 정보 카드
- `design.pane-tabs` — 모바일 창 전환 버튼 행
- `design.pane-tabs-partner`, `design.pane-tabs-tree`, `design.pane-tabs-editor`
- `design.partner.panel` — 설계 파트너 카드
- `design.partner.pane-expand-btn`, `design.partner.collapse-btn`
- `design.partner.thread-select` — 노드 대화 목록
- `design.partner.new-btn` — 새 대화, `design.partner.search-btn` — 전체 검색
- `design.partner.msg-mode` — 포함 범위 세그먼트
- `design.partner.msg-mode-all`, `design.partner.msg-mode-none`, `design.partner.msg-mode-select`
- `design.partner.search-dialog` — 전체 대화 검색 모달
- `design.partner.search-input`, `design.partner.search-list`, `design.partner.search-close-btn`
- `design.partner.messages`, `design.partner.refs-input`
- `design.partner.input`, `design.partner.send-btn`
- `design.partner.expand-kind-select`, `design.partner.expand-btn`
- `design.partner.proposals`
- `design.tree.panel` — 소설 구조 카드
- `design.tree.search-input`, `design.tree.search-btn`
- `design.tree.view-mode` — 보기 방식 세그먼트
- `design.tree.view-mode-one`, `design.tree.view-mode-flow`, `design.tree.view-mode-full`
- `design.tree.expand-all-btn`, `design.tree.collapse-all-btn` — 전체 펼치기·접기
- `design.tree.show-archived-checkbox`, `design.tree.list`
- `design.tree.add-kind-select`, `design.tree.add-title-input`, `design.tree.add-btn`
- `design.editor.panel` — 설계 편집 카드
- `design.editor.title-input`, `design.editor.logline-input` — 입력 후 자동저장
- `design.editor.write-jump-btn`
- `design.editor.delete-btn` — 노드 삭제 (편집 헤더 우측)
- `design.editor.delete-dialog` — 삭제 확인 다이얼로그
- `design.editor.delete-dialog-confirm-btn`, `design.editor.delete-dialog-cancel-btn`
- `design.editor.inherited`, `design.editor.children-rollup`
- `design.editor.node-settings` — 노드 설정 영역(구분선+추가 버튼)
- `design.editor.ns-add-btn`
- `design.editor.ns-dialog` — 노드 설정 관리 다이얼로그
- `design.editor.ns-dialog-add-btn`, `design.editor.ns-dialog-list`, `design.editor.ns-dialog-inherited`, `design.editor.ns-dialog-close-btn`

### writing — 집필

- `writing.screen`
- `writing.screen.col-resizer` — 진행·상세 열 분할선 (드래그)
- `writing.screen.col-resizer-right` — 상세·대화 열 분할선 (드래그)
- `writing.progress.panel` — 회차별 진행 카드
- `writing.progress.collapse-btn`, `writing.progress.expand-btn` — 진행 열 접기/펼치기
- `writing.progress.design-id-input`, `writing.progress.load-btn`
- `writing.progress.episode-rows`
- `writing.progress.expander` — 회차 행 아래 설계 줄거리·장면 펼침
- `writing.episode.panel` — 회차 상세 카드
- `writing.episode.state-seg` — 파이프라인 상태 표시 세그먼트 (조작 불가)
- 중앙 원고는 공유 모듈(`frontend/js/manuscript.js`)이 `#ms-mount`에 마운트한다.
  버전 선택·본문은 아래 role을 유지하고, 검색·편집·저장·복원·읽기·복구 버튼은
  패널 내부 `data-ui` 계약(versions/query/search/edit/save/restore/reader/copy/diff/body/editor/recovery/quote/clear/status)을 따른다.
- `writing.chat.panel` — 수정 대화 카드
- `writing.chat.collapse-btn`, `writing.chat.expand-btn` — 대화 열 접기/펼치기
- `writing.chat.messages` — 수정 요청 내역
- `writing.chat.mode-seg`, `writing.chat.mode-partial`, `writing.chat.mode-full` — 부분/전체 방식 전환
- `writing.chat.suggest` — @ 자동완성 목록

### review — 작가 검토

- `review.screen`
- `review.screen.col-resizer` — 원고·검증 열 분할선 (드래그)
- `review.loader.episode-input`, `review.loader.load-btn`
- `review.manuscript.panel` — 원고 본문 카드
- `review.manuscript.version-select`, `review.manuscript.diff-checkbox`
- `review.manuscript.body`
- `review.change.panel` — 수정 요청 카드
- `review.change.request-input`, `review.change.send-btn`, `review.change.approve-btn`
- `review.verdicts.panel` — 검증 카드
- `review.verdicts.list`
- `review.alias.panel` — 별명·동명이인 판정 카드
- `review.alias.list`

### entities — 설정

- `entities.screen`
- `entities.screen.col-resizer` — 검색·상세 열 분할선 (드래그)
- `entities.search.panel` — 설정 검색 카드
- `entities.search.input`, `entities.search.btn`, `entities.search.results`
- `entities.detail.panel` — 설정 상태 카드
- `entities.history.panel` — 속성 이력 타임라인 카드
- `entities.history.timeline`

## 5. 동적 목록 (JS 템플릿, 인스턴스별 반복)

같은 role이 여러 인스턴스에 나타나며, 개별 지칭 시 기존 식별자
(메시지 id, 설정 코드 D번호, 노드 id 등)를 병행한다.
(단일) 표시가 없는 항목은 인스턴스별 반복이다.

### ideas.js

- `ideas.chat.include-checkbox` — 메시지별 포함 체크박스
- `ideas.chat.choice-btn` — 대화 이어가기 버튼
- `ideas.chat.proposal-apply` — 설정 제안 반영 버튼
- `ideas.chat.proposal-synopsis` — 줄거리 변경 반영 버튼
- `ideas.chat.proposal-structure` — 구조 반영 버튼
- `ideas.chat.proposal-merge` — 통합 원본 삭제 버튼
- `ideas.chat.proposal-apply-all` — 모두 반영 버튼
- `ideas.settings.item-checkbox` — 설정 항목 포함 체크박스
- `ideas.settings.edit-btn`, `ideas.settings.delete-btn`, `ideas.settings.collapse-btn`
- `ideas.settings.edit-category-select`, `ideas.settings.edit-title-input`
- `ideas.settings.edit-summary-input`, `ideas.settings.edit-detail-input`
- `ideas.settings.edit-save-btn`, `ideas.settings.edit-cancel-btn`
- `ideas.structure.part-title-input`, `ideas.structure.part-summary-input`
- `ideas.structure.part-delete-btn`, `ideas.structure.part-collapse-btn`
- `ideas.synopsis.edit-input` — 줄거리 편집 textarea (`dataset.role`로 부여)

### storydesign.js

- `design.tree.seed-root-btn` — 루트 노드 만들기 (빈 상태, 단일)
- `design.tree.seed-outlines-btn` — 기존 개요에서 Episode 생성 (빈 상태, 단일)
- `design.partner.apply-patch-btn` — 선택 노드 수정안 적용 (단일)
- `design.partner.apply-all-btn` — 하위 설계안 전체 적용 (단일)
- `design.partner.apply-child-btn` — 하위 설계안 개별 추가
- `design.partner.typing` — AI 답변 생성 중 애니메이션 (단일)
- `design.editor.ns-edit-btn`, `design.editor.ns-delete-btn` — 설정 행 편집·삭제 (다이얼로그)
- `design.editor.ns-override-btn` — 물려받은 설정 덮어쓰기 (다이얼로그)
- `design.editor.ns-form-category`, `design.editor.ns-form-title` (다이얼로그)
- `design.editor.ns-form-summary`, `design.editor.ns-form-detail` (다이얼로그)
- `design.editor.ns-form-save`, `design.editor.ns-form-cancel` (다이얼로그)
- `design.editor.rollup-mode-one`, `design.editor.rollup-mode-flow` — 하위 줄거리 보기 전환

### monitor.js (writing)

- `writing.progress.row-detail-btn` — 회차 행 상세 버튼
- `writing.progress.row-advance-btn` — 회차 행 전진 버튼
- `writing.episode.advance-btn` — 다음 단계로 전진 버튼 (단일)
- `writing.episode.start-btn` — 이 화 집필 시작 버튼 (단일)
- `writing.episode.version-select` — 원고 버전 선택: 작업 초안 + 최신순 저장 버전 (단일)
- `writing.episode.manuscript` — 원고 본문: 줄 번호·차이 강조·검색 강조 (단일)
- `writing.episode.change-input`, `writing.episode.change-btn` — 수정 대화 입력·요청 (우측 패널, 단일)
- `writing.episode.review-link` — 작가 검토로 이동 버튼 (단일)

### review.js

- `review.alias.entity-input` — 연결할 인물 ID 입력
- `review.alias.confirm-btn` — 별명 판정 확정 버튼
- `review.alias.reject-btn` — 별명 판정 기각 버튼

### home.js

- `home.create.panel`, `home.list.panel` — 게이트 표시 시 숨김 참조 (정적 카드)
- `home.list.enter-btn` — 소설 이어 쓰기 버튼
- `home.list.delete-btn` — 소설 삭제 버튼
- `home.list.clear-search-btn` — 전체 소설 보기 (검색 결과 없음, 단일)
- `home.list.retry-btn` — 목록 재시도 (오류 시, 단일)

### entities.js

- `entities.detail.asof-input` — 회차 지정 상태 입력 (단일)
- `entities.detail.asof-btn` — 회차 지정 조회 버튼 (단일)

### model.js

- `shell.model.pick-radio` — 모델 선택 라디오 (프리셋별 반복)
