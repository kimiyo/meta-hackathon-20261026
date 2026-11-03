# i18n 용어집 (KO-EN)

Stage-1 계약 문서. 모든 wave-1 화면 담당자는 이 표의 번역을 따른다.
새 용어 추가 시 양쪽 열을 함께 채운다 (빈칸 금지).

## 핵심 UI 용어

| ko-KR | en-US | 비고 |
|---|---|---|
| 스토리 스파인 | StorySpine | 브랜드명, 번역 키 `common.appName`은 양쪽 모두 `StorySpine` |
| 한국 | Korea | `market.KR` |
| 미국 | United States | `market.US` |
| 확인 | OK | `common.ok` / `common.confirm` |
| 취소 | Cancel | `common.cancel` |
| 닫기 | Close | `common.close` |
| 저장 | Save | `common.save` |
| 오류 | Error | `common.error` |
| 불러오는 중… | Loading… | `common.loading` |
| 다시 시도 | Retry | `common.retry` |
| 작품 | Novel | 작품 단위 |
| 아이디어 | Ideas | 화면명 |
| 설계 | Story Design | 화면명 |
| 집필 | Writing | 화면명 |
| 검토 | Review | 화면명 |
| 발간 | Publish | 화면명 |
| 회차 | Episode | 회차 번호 단위 |
| 원고 | Manuscript | 원고 본문 |
| 문체 | Style | 문체 프리셋/정책 |
| 장르 | Genre | 프리셋 장르 |
| 등장인물 | Characters | 엔티티 인물 |
| 일관성 | Consistency | 일관성 검사 |
| 소개글 | Blurb | 발간 소개글 |
| 표지 | Cover | 표지 이미지 |
| 연재 플랫폼 | Platform | 발간 플랫폼 |

## 오류 코드 (`error.<CODE>`)

| 코드 | ko-KR | en-US |
|---|---|---|
| `WORK_TITLE_REQUIRED` | 제목을 입력하세요. | Enter a title. |
| `UNKNOWN_MARKET` | 지원하지 않는 시장입니다: {value} | Unsupported market: {value} |
| `UNKNOWN_WRITING_LOCALE` | 지원하지 않는 집필 언어입니다: {value} | Unsupported writing locale: {value} |
| `UNKNOWN_UI_LOCALE` | 지원하지 않는 화면 언어입니다: {value} | Unsupported UI locale: {value} |
| `UNKNOWN_PLATFORM` | 지원하지 않는 플랫폼입니다: {value} | Unsupported platform: {value} |
| `WORK_LOCALE_LOCKED` | 이미 생성된 내용이 있어 시장·집필 언어를 변경할 수 없습니다. ({reason}) | Market/writing locale can't be changed after content exists. ({reason}) |

`{value}`, `{reason}`은 서버 `params`로 치환한다. 번역에 따옴표·괄호 스타일을
임의로 바꾸지 않는다.
