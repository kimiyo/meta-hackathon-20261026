# Meta AI 기능별 설정·사용 테스트 가이드

작성: 2026-10-28 KST · 대상: Meta Global AI Developer Hackathon / 웹소설 연재 코파이럿  
상태: **실행 전 체크리스트**. 아래의 결과 칸은 실제 시험 후 채운다. 이 문서는 테스트를 수행했다는 기록이 아니다.

## 0. 이 문서의 사용법

각 절을 순서대로 진행한다. 먼저 웹 플레이그라운드에서 동작을 확인하고, 제품에 넣을 기능만 API로 재현한다. 모든 기능을 제품에 억지로 넣는 것이 목표는 아니다. 핵심 흐름은 **원고·설정 입력 → 설정 후보와 출처 추출 → 작가 승인 → 새 회차 모순 검증 → 다음 회차 플롯 → 필요하면 캐릭터 이미지**다. 음성·분할·오픈 웨이트는 가치가 입증될 때만 추가한다.

| 표시 | 의미 |
|---|---|
| `[ ]` | 아직 안 함 |
| `[x]` | 직접 수행하고 결과를 기록함 |
| `보류` | 권한·지역·시간·하드웨어 또는 제품 가치 때문에 미진행. 이유를 기록 |

작업 폴더: `E:\dev-e\hackathon\meta-hackathon-20261026-1104`. 기존 `해커톤_준비사항_및_과업목표.md`의 P0/P1 범위와 함께 사용한다. 명령은 **Windows PowerShell 7**, Python 3.9+를 기준으로 한다. `python` 명령이 없는 환경에서는 `py -3`으로 바꾼다.

### 실험 기록 양식 (매 기능마다 복사)

```text
날짜/시각(KST):
기능 / 모델 ID / Standard·Contributor 여부:
입력 자료(자작/이용 허락 여부, 원고 버전):
실행 경로(Playground/API/CLI/파트너), 소스 파일/커밋:
기대 결과:
실제 결과(출력 파일 또는 스크린샷 위치):
성공/실패/보류 및 이유:
응답 시간 / API Usage / 추정 비용:
제품에 채택할지, 다음 행동:
```

**보안과 비용:** API 키를 코드·공개 GitHub·스크린샷·브라우저 프런트엔드에 넣지 않는다. 호출은 서버에서 한다. 미공개 원고 대신 직접 만든 짧은 샘플을 먼저 사용한다. 현재 Billing에서 $150 크레딧과 카드 등록이 확인되었지만, **Spend limits는 이메일 알림이지 과금 차단 장치가 아니다.** 무료 크레딧 사용 시 데이터가 제품 개선에 사용될 수 있다는 Billing 안내가 표시되므로 민감 원고 투입 전에 조건을 다시 확인한다. API 모델별 Standard/Contributor 데이터 조건도 다르다. 실험마다 Usage와 Billing을 확인한다.

## 1. 공통 준비: 계정, 키, 기본 연결

공식 시작 문서: <https://dev.meta.ai/docs/quickstart> · 모델 목록: <https://dev.meta.ai/docs/models> · 가격: <https://dev.meta.ai/docs/pricing-rate-limits>

- [ ] <https://dev.meta.ai/>에 **크레딧을 받은 동일한 계정·팀·프로젝트**로 로그인한다. Billing의 `Free credits in use $150.00`와 결제수단을 확인한다. Billing 화면은 적용 잔액을 보여주지만 만료 조건의 근거는 아래 별도 Credit Terms다.

**해커톤 크레딧 조건 확인(2026-10-28):** Meta의 *Global AI Developer Hackathon Credit Terms* §2.2에 따른 **신청 마감은 2026-11-04 21:00 미국 동부시간**이다. 이는 **이미 적용된 크레딧의 만료일이 아니다**. §3.5에 따르면 적용된 $150 크레딧은 사용하거나 계정이 종료될 때까지 만료되지 않는다. §3.3에 따라 API 약관·허용 사용 정책이 허용하는 서비스 사용에 쓸 수 있으며 해커톤 외 사용도 포함된다. §3.6에 따라 소진 후 추가 API 사용은 계정의 결제수단에 표준 요금으로 청구된다. §4.1은 자격 미충족·허위 정보·중복 계정·약관 위반 등에 따른 취소 가능성을 별도로 규정한다. 출처: [해커톤 Waiver에서 연결된 Credit Terms PDF](https://cerebralvalley.ai/e/meta-ai-developer-hackathon/waiver) (§2.2, §3.3~3.6, §4.1). 실제 계정에 적용된 금액과 사용량은 Billing/Usage에서 계속 확인한다.
- [ ] `Models`에서 각 모델의 표시, `Try in playground` 버튼, 가격·접근 제한을 본다. 목록에 나타나는 것과 실제 호출 성공은 다르다.
- [ ] `API keys → Create API key`로 **개발용 키**를 만든다. 키는 생성 순간에 안전하게 보관한다. 사용하지 않는 키는 나중에 폐기한다.
- [ ] PowerShell에서 이번 세션에만 키를 설정한다. 입력값이 화면·명령 기록에 남지 않게 한다:

```powershell
$env:MODEL_API_KEY = (Read-Host 'Meta Model API key' -AsSecureString | ConvertFrom-SecureString -AsPlainText)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade openai requests
```

PowerShell 실행 정책 때문에 활성화가 막히면 `.\.venv\Scripts\python.exe -m pip install openai requests`와 `.\.venv\Scripts\python.exe`을 직접 사용한다. 키의 **값**을 출력하거나 파일에 기록하지 않는다. 새 터미널에서는 환경변수를 다시 설정한다.

- [ ] 아래 짧은 코드를 `smoke_meta.py`로 저장해 실행한다. 응답과 모델 목록만 출력한다. `muse-spark-1.3`이 실패하면 오류 코드·메시지를 기록하고 [문제 해결](#11-문제-해결)로 간다.

```python
import os
from openai import OpenAI

client = OpenAI(base_url="https://api.meta.ai/v1", api_key=os.environ["MODEL_API_KEY"])
print("Available API model IDs:", [m.id for m in client.models.list().data])
r = client.responses.create(model="muse-spark-1.3", input="한국어로 한 문장만 답해 주세요: 준비 완료")
print("Response ID:", r.id)
print("Text:", r.output_text)
print("Usage:", r.usage)
```

```powershell
python .\smoke_meta.py
```

- [ ] `Usage`에 호출이 기록되는지 확인한다. 반영이 늦을 수 있으므로 응답의 `usage`도 보관한다. 크레딧 소진 후 카드 과금 가능성을 고려해 작은 입력으로 시작한다.

**통과 기준:** 모델 목록 조회, 한국어 응답, `response.id`와 `usage` 확인. **현재 상태:** 미실행.

## 2. Muse Spark: 핵심 텍스트·멀티모달·에이전트

모델: `muse-spark-1.3` (신규 작업 권장). Chat Playground는 Model API 왼쪽 `Chat`, 또는 `Models → Muse Spark 1.3 → Try in playground`. 공식 문서: <https://dev.meta.ai/docs/models#muse-spark>.

### 2-A. 설정 추출과 한국어 품질

- [ ] 다음 **자작 샘플**을 Playground에 입력한다. 실제 원고는 사용하지 않는다.

```text
[1화 확정 설정]
주인공 이서윤은 오른손에만 은빛 흉터가 있다. 마력은 하루에 두 번만 사용할 수 있다.
[새 회차]
서윤은 왼손의 은빛 흉터를 보며 세 번째 마법을 썼다.

확정 설정, 새 회차의 충돌 후보, 각각의 원문 근거를 JSON 형태로 제시해 줘.
원문에 없는 설정을 만들지 말고, 확신이 없으면 '미확정'이라고 써.
```

- [ ] 기대: **흉터 위치**와 **하루 마법 사용 횟수**의 두 충돌 후보. 각 근거 문장이 실제 입력에 존재하는지 사람이 대조한다. 모델의 판단을 확정 설정으로 자동 저장하지 않는다.
- [ ] API에서도 같은 입력을 `client.responses.create(model="muse-spark-1.3", input=...)`로 보내고 `r.output_text`, `r.usage`, 소요 시간을 기록한다. 첫 테스트에서는 JSON 문자열의 유효성과 근거 정확성을 확인한다. 제품 구현에서는 <https://dev.meta.ai/docs/structured-output>의 스키마/구조화 출력 기능을 적용하고 **파싱 실패**도 처리한다. Responses API에서는 `text.format`, Chat Completions에서는 `response_format`을 사용한다.
- [ ] 정상 변화 사례도 추가한다: “이후 치료로 오른손 흉터가 사라졌다.” 이것을 무조건 모순으로 단정하지 않는지 확인한다.

### 2-B. 이미지·문서 이해 및 웹 검색

- [ ] Chat Playground에 **자작 캐릭터 스케치/설정표 이미지**를 올려 외형 설명을 받고, 원본과 대조한다. 이미지 이해 문서: <https://dev.meta.ai/docs/image-understanding>.
- [ ] PDF를 쓸 경우 짧은 자작 설정집으로 시험하고, 페이지/문장 근거를 대조한다. PDF 입력·처리 문서: <https://dev.meta.ai/docs/file-handling#pdf-handling>. 화면과 모델 버전에 따른 입력 허용 여부를 먼저 확인한다.
- [ ] 실시간 검색이 필요한 경우에만 `web_search`를 켠다. 예를 들어 공개 장르 자료의 최신 정보 질문으로 시험한다. 결과에 `web_search_call`과 출처 URL이 있는지 확인한다. 검색은 추가 과금 항목이며, 출처를 검증한다.

```python
r = client.responses.create(
    model="muse-spark-1.3",
    input="Meta Model API의 현재 공개 문서에서 Muse Spark 1.3의 특징을 찾아 출처와 함께 요약해 줘.",
    tools=[{"type": "web_search"}],
)
print(r.model_dump_json(indent=2))  # 출력에 키나 민감한 원고가 없는지 확인 후 공유
```

검색 문서: <https://dev.meta.ai/docs/search-grounding>. 제품의 **자체 원고 검색**은 웹 검색이 아니라 자체 저장된 회차·설정에서 해야 한다.

### 2-C. 도구 호출/에이전트는 별도 통과 기준

- [ ] `get_lore(character, chapter)`와 `propose_change(...)` 같은 **읽기/후보 작성 도구**를 설계한다. 모델이 도구를 호출해 관련 원문을 찾고 근거를 제시하는지 시험한다. <https://dev.meta.ai/docs/tool-calling>.
- [ ] `approve_change`는 모델이 자율 호출해 확정하지 않도록 한다. **작가의 UI 승인** 후 애플리케이션이 기록한다. 도구 입력 검사, 호출 횟수 상한, 잘못된 인용·빈 결과 처리도 기록한다.

**통과 기준:** 한국어 두 모순 후보와 정확한 원문 근거, 정상 변화의 구별, 구조화 결과 파싱, 사용량 기록. **현재 상태:** 미실행.

## 3. Muse Image: 캐릭터·장면 이미지 생성과 수정

모델: `muse-image-1.0`. 왼쪽 `Image` 또는 Models의 Playground. 문서: <https://dev.meta.ai/docs/image-generation>. 이미지당 과금이며, 내장 웹/이미지 검색은 이미지 가격에 포함된다.

- [ ] Playground에서 다음 자작 설정으로 **한 장** 생성한다: `한국 판타지 웹소설 캐릭터 설정화. 주인공 이서윤, 오른손의 은빛 흉터, 짙은 남색 망토. 정면 전신, 단색 배경. 왼손에는 흉터가 없다.`
- [ ] 결과에서 **오른손/왼손 방향**과 설정 일치 여부를 직접 확인한다. 좌우 뒤바뀜은 실패 또는 수정 필요로 기록한다. 이미지 생성이 설정의 진실을 증명하는 것은 아니다.
- [ ] 생성 이미지를 입력해 `배경만 바꾸고 캐릭터 얼굴·망토·오른손 흉터는 유지해 줘`로 편집한다. 원본과 수정본을 함께 저장하고 일관성을 평가한다. 타인 저작물/실존 인물 이미지는 허락 없이 사용하지 않는다.
- [ ] API 단발 생성도 시험한다. 이미지는 기본적으로 base64로 돌아오므로 디코딩해 파일로 저장한다.

```python
import base64, os
from openai import OpenAI
client = OpenAI(base_url="https://api.meta.ai/v1", api_key=os.environ["MODEL_API_KEY"])
r = client.images.generate(model="muse-image-1.0", n=1,
    prompt="Original fantasy character concept art, silver scar on the RIGHT hand, navy cloak, plain background")
with open("muse-image-test.webp", "wb") as f:
    f.write(base64.b64decode(r.data[0].b64_json))
print("Saved muse-image-test.webp")
```

- [ ] 여러 차례 수정할 계획이면 Responses API의 `previous_response_id` 방식도 시험한다. 단발 `images.generate`와 상태 유지 방식은 다르다. 웹 검색이 내장되어 있으므로 **Muse Image 요청에 별도의 `web_search` 도구를 넣지 않는다**.

**통과 기준:** 생성·편집 성공, 설정 일치 여부와 실패 사례 기록, 이미지 파일 및 비용 확인. **제품 연결:** 승인된 외형 설정 → 이미지 프롬프트 → 결과와 설정 버전 연결. **현재 상태:** 미실행.

## 4. Muse Voice Transcribe: 원고 구술 입력 (선택)

모델: `muse-voice-transcribe-1.0`. 왼쪽 `Audio` Playground. 공식 문서: <https://dev.meta.ai/docs/speech-to-text>. **음성→텍스트**이며 음성 생성이나 실시간 음성 대화 모델이 아니다. 한국어가 지원 언어에 포함된다.

- [ ] 10~20초 동안 자작 문장을 한국어로 녹음한다: `이서윤의 오른손에는 은빛 흉터가 있고 마력은 하루 두 번만 사용한다.` 다른 사람의 목소리는 동의 없이 녹음하지 않는다.
- [ ] Playground에 업로드하고 `이서윤`, `마력`, `두 번` 인식 정확도를 원음과 비교한다.
- [ ] API 파일 시험은 **mono, 16-bit PCM WAV, 16 또는 24 kHz**로 변환한다. `ffmpeg`가 설치된 경우:

```powershell
ffmpeg -i .\input.m4a -ac 1 -ar 24000 -c:a pcm_s16le -map_metadata -1 .\voice-test.wav
```

- [ ] `POST /v1/asr/transcribe`에 multipart `request` JSON과 `audio` WAV를 보낸다. 다음 Python 예시를 `voice_test.py`에 저장한다.

```python
import json, os, requests
url = "https://api.meta.ai/v1/asr/transcribe"
settings = {"mode": "PUSH_TO_TALK", "model": "muse-voice-transcribe-1.0", "audioEncoding": "WAV",
            "languageBias": ["Korean"], "keywords": ["이서윤"]}
with open("voice-test.wav", "rb") as audio:
    files = {"request": (None, json.dumps(settings), "application/json"),
             "audio": ("voice-test.wav", audio, "audio/wav")}
    r = requests.post(url, headers={"Authorization": "Bearer " + os.environ["MODEL_API_KEY"]},
                      files=files, timeout=120)
r.raise_for_status()
print(r.json())
```

- [ ] 여러 화자의 대화는 `mode: DIARIZATION`으로 별도 시험하고 `turns`, `speaker`를 확인한다. 실시간 마이크는 WebSocket의 첫 handshake에 인증을 넣어야 하며 HTTP Authorization 헤더만으로는 안 된다. 실시간 시험은 파일 시험을 통과한 뒤 진행한다.

**통과 기준:** 한국어 전사·고유명사·음성 길이 확인. 파일 제한은 10분/32MB이며 지원 WAV 형식이 아니면 변환한다. **제품 연결:** 작가의 구술 메모 → 초안 텍스트 → Spark가 설정 후보 추출. **현재 상태:** 미실행/선택.

## 5. SAM 3.1: 이미지·영상 속 물체 분할 (선택)

모델: `sam-3.1`. 왼쪽 `Segment` Playground. <https://dev.meta.ai/docs/media-segmentation> · <https://dev.meta.ai/docs/sam/overview>.

- [ ] 직접 만든 장면 이미지에서 **하나의 구체적인 물체**를 선택한다(예: `navy cloak`). 짧은 명사구로 요청하고 마스크가 정확히 해당 물체를 덮는지 확인한다. 여러 물체 유형은 각각 요청한다.
- [ ] 결과의 박스·마스크를 원본 이미지 위에 겹쳐 눈으로 검증한다. 대상이 없을 때 빈 결과가 나올 수 있다.
- [ ] API 시험은 아래처럼 이미지 URL을 넣는다. **자작·공개 접근 가능한** 이미지 URL로 교체한다. 로컬 파일은 공식 Files API 또는 base64 data URL 형식을 검토한다.

```python
import os
from openai import OpenAI
client = OpenAI(base_url="https://api.meta.ai/v1", api_key=os.environ["MODEL_API_KEY"])
with client.responses.stream(
    model="sam-3.1",
    input=[{"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": "navy cloak"},
        {"type": "input_image", "image_url": "https://YOUR-OWN-IMAGE-URL/example.png"},
    ]}],
) as stream:
    for event in stream:
        if event.type == "response.output_text.delta":
            print(event.delta, end="")
print()
```

- [ ] 반환값은 보통 자연어 설명이 아니라 특수 토큰 형태의 박스·마스크 데이터다. 실제 UI 오버레이 구현은 공식 `@meta-sam/parser`와 `decodeMaskToRaster` 사용법을 따른다: <https://dev.meta.ai/docs/sam/client-libraries>. 긴 영상 처리보다 정지 이미지 한 장부터 시작한다.

**통과 기준:** 대상 물체가 있는 경우 마스크와 박스를 시각화하고 정확성 기록. **제품 연결 후보:** 캐릭터 이미지의 의상·소품만 선택해 편집하는 UI. 핵심 원고 검증과 무관하면 구현은 보류한다. **현재 상태:** 미실행/선택.

## 6. Muse Code: 개발용 코딩 에이전트

문서: <https://dev.meta.ai/docs/muse-code> · 인증/과금: <https://dev.meta.ai/docs/muse-code/auth> · 구독: <https://dev.meta.ai/docs/muse-code/subscriptions>.

- [ ] 공식 설치 페이지 <https://dev.meta.ai/lp/muse-code>의 Windows 명령을 확인한다. 원격 스크립트를 실행하기 전에 URL과 내용을 검토한다. 공식 예시는 다음과 같다:

```powershell
irm https://dev.meta.ai/install.ps1 | iex
muse --version
```

- [ ] 테스트용 Git 브랜치/복사본에서 `muse`를 실행한다. 첫 실행 때 작업공간 신뢰 여부와 인증을 묻는다. **브라우저 로그인 또는 Meta API 키**를 고른다. API 키를 사용하면 종량제이며, 크레딧 적용은 Usage/Billing으로 확인한다. 구독은 별도 월 결제이므로 **확인 없이 구매하지 않는다**.
- [ ] 첫 프롬프트는 작은 작업으로 제한한다: `이 저장소를 읽고 웹소설 설정 추출 모듈의 테스트 계획만 작성해 줘. 파일 수정 전에는 승인 요청.` 생성·수정 파일과 실행 명령을 검토한다. 실제 제품 로직을 맡기기 전에 테스트 통과 여부를 확인한다.
- [ ] `/settings`에서 모델/설정과 과금 방식을 확인한다. 구독은 Contributor 모드가 기본일 수 있고, 종량제와 조건이 다르다. 프로젝트 비밀값·미공개 원고를 그대로 전달하지 않는다.
- [ ] Muse Code는 **개발 생산성 도구**다. 심사용 제품에 Meta 모델을 실제 통합한 증거와 구분해 기록한다.

**통과 기준:** CLI 버전 확인, 인증, 작은 작업의 계획/변경 검토, Usage 또는 구독 상태 확인. **현재 상태:** 미실행.

## 7. 오픈 웨이트: Muse Glimmer와 Muse Spark Open

둘 다 **Meta Model API의 $150 크레딧으로 호출하는 모델이 아니다.** 자체 하드웨어 또는 별도 공급자 계정·비용이 필요하다. 모델과 라이선스는 별도 확인한다. <https://dev.meta.ai/docs/models#muse-glimmer> · <https://dev.meta.ai/docs/muse-spark-open>.

### 7-A. Muse Glimmer 30B: 로컬 또는 호스팅

- [ ] 하드웨어 여유가 있으면 <https://ollama.com/library/muse-glimmer>의 공식 모델 페이지와 설치 안내를 확인하고 `ollama run muse-glimmer`로 짧은 한국어 설정 추출을 시험한다. 다운로드 크기는 약 **18GB**이므로 디스크·RAM/VRAM·다운로드 시간을 먼저 점검한다. bf16 원본은 더 큰 자원이 필요하다. 로컬 실행이 느리거나 불가능하면 보류하고 호스팅 경로를 택한다.
- [ ] 같은 자작 입력을 Spark 1.3과 Glimmer에 주고 **모순 검출/근거/속도/비용/데이터 경로**를 비교한다. 서로 다른 모델의 결과를 같은 정답 데이터로 평가한다.
- [ ] 모델 페이지의 Apache 2.0 및 사용 정책을 읽고, 공개 저장소에 가중치를 무단 포함하지 않는다.

### 7-B. Muse Spark Open: 호스팅 우선

- [ ] 자체 호스팅은 816B 전체 파라미터 때문에 일반 개인 PC용으로 가정하지 않는다. Meta 문서는 BF16 가중치 약 1.65TB, NVFP4 약 537GB라고 안내한다. <https://dev.meta.ai/docs/muse-spark-open>.
- [ ] 참가자 가이드 <https://cerebralvalley.ai/e/meta-ai-developer-hackathon/details>의 **6. Inference Partner Provided Resources**에서 Baseten, Modal, Fireworks의 각 신청 링크와 조건을 확인한다. 크레딧은 **별도 신청·선정**, 자동 지급이 아니다. 계정을 만들고 유료 사용을 시작하기 전에 실제 혜택 적용과 과금 설정을 확인한다.
- [ ] Baseten을 이용한다면 가이드의 해커톤 지정 모델 ID `meta-models/muse-spark-open-1026`을 사용한다. 다른 공급자는 해당 서비스의 공식 모델 ID·API 예시에 따른다. 간단한 한국어 설정 추출 요청이 성공하면 응답·공급자 사용량·비용을 기록한다.
- [ ] 보너스상 `Best Build on Meta Open Weight Models`은 Glimmer 또는 Spark Open 활용이 해당된다. **단순 이름 표기가 아니라 실제 모델 구동과 통합**을 증명할 수 있어야 한다. 기본 제품을 해치며 이 상을 좇지는 않는다.

**통과 기준:** 선택한 실행 경로, 정확한 모델 ID, 실제 응답과 비용, 권리/라이선스 검토. **현재 상태:** 미실행/선택.

## 8. Muse Wonder / Wonder Full: 지역 제한 확인

- [ ] Models 페이지에서 Muse Wonder가 보이더라도 **Meta 공식 모델 문서는 현재 한국에서 Muse Wonder와 Wonder Full을 이용할 수 없다고 명시**한다. `muse-wonder-1.0`을 제품의 필수 의존성으로 두지 않는다. <https://dev.meta.ai/docs/models#muse-wonder-availability>.
- [ ] Wonder Full은 일반 Model API가 아니라 Muse Code의 **Power/Maximum 구독** 대상이다. $150 API 크레딧만으로 열리는 기능이 아니다. 지역 제한을 우회하지 않는다.
- [ ] 실제 계정에서 허용된 플레이그라운드 호출이 가능한지 확인할 수 있으나, 제한 오류가 나오면 코드 오류로 오인하지 말고 결과를 기록하고 Spark로 돌아간다. 지원팀/Discord 오피스 아워에서 변경 여부를 확인할 수 있다.

**통과 기준:** 제공 상태를 확인하고 `사용 가능` 또는 `지역 제한/보류`로 명확히 기록. **현재 판단:** 한국 기준 필수 경로에서 제외.

## 9. 기능 결합 실험: 웹소설 코파이럿의 최소 시연

개별 시험이 끝났으면 다음 하나의 시나리오만 먼저 연결한다. 각 단계의 입력·출력과 출처를 저장한다.

1. **원고:** 자작 1화 설정 2개와 의도적인 모순이 있는 새 회차를 넣는다.
2. **Spark:** 설정 후보와 원문 위치를 추출한다. 검증 UI는 ‘모델 후보’로 표시한다.
3. **작가 승인:** 확정 설정만 데이터베이스에 반영한다. 거절·보류는 확정하지 않는다.
4. **Spark:** 새 회차를 기존 설정과 비교해 두 근거 문장을 나란히 보여준다. 정상적인 상태 변화는 오탐으로 단정하지 않는다.
5. **수정·후속:** 작가의 수정 이후 다시 검증하고, 승인한 설정으로 다음 회차 플롯을 제안한다.
6. **Image(P1):** 승인된 외형 설정에서 이미지 생성 및 수정, 사용한 설정 버전을 연결한다.
7. **선택:** 구술 메모에는 Voice Transcribe, 이미지 부분 편집에는 SAM, 로컬/파트너 모델 비교에는 Glimmer/Spark Open을 사용한다.

**평가표:** 모순 10개와 정상 10개를 직접 작성해 정답·근거를 보관한다. 탐지 수, 오탐 수, 존재하지 않는 인용 수, 응답 시간, 토큰/이미지/음성 사용량을 적는다. 작은 샘플 결과를 일반적인 정확도라고 주장하지 않는다. 공개 GitHub 저장소에는 API 키·실제 미공개 원고·타인의 권리 있는 자료가 포함되지 않게 한다.

## 10. 권장 실행 순서와 상태판

| 순서 | 테스트 | 결과/날짜 | 제품 우선순위 |
|---:|---|---|---|
| 1 | 계정·크레딧·키·Spark 최소 호출 | `[ ]` | 필수 |
| 2 | 한국어 설정 추출·모순·정상 변화·근거 검증 | `[ ]` | P0 |
| 3 | 구조화 출력·도구 호출·Usage/비용 기록 | `[ ]` | P0 |
| 4 | Muse Image 생성·편집·설정 일치 | `[ ]` | P1 |
| 5 | Muse Code 설치·종량제 작은 작업 | `[ ]` | 개발 보조 |
| 6 | Voice Transcribe 파일·실시간 시험 | `[ ]` | 선택 |
| 7 | SAM 이미지·마스크 시각화 | `[ ]` | 선택 |
| 8 | Glimmer 로컬/호스팅·Spark Open 파트너 | `[ ]` | 선택/보너스 |
| 9 | 기능 결합 시연 및 3분 데모 녹화 | `[ ]` | 제출 필수 |

**중단 기준:** 1~3번이 불안정하면 선택 기능은 추가하지 않는다. 11월 3일까지 작동 데모·공개 저장소·3분 영상을 우선 준비한다. 공식 제출 마감은 **2026-11-05 11:00 KST**(11월 4일 21:00 미국 동부 표준시)이며 실제 포털 공지를 재확인한다.

## 11. 문제 해결

| 증상 | 먼저 확인할 것 |
|---|---|
| `401 invalid_api_key` | 키가 현재 터미널에 설정되어 있는지, 키를 만든 팀/프로젝트가 맞는지. 키 값을 로그에 출력하지 않는다. |
| `404 model_not_found` | 정확한 모델 ID, 계정의 모델 목록, 지역 가용성. Wonder 한국 제한을 우회하지 않는다. |
| `429` | 모델별 팀 단위 제한. `Retry-After`와 지수 백오프를 사용하고 호출 루프를 멈춘다. |
| `400` 이미지/음성 | 이미지 입력 형식, 음성의 mono PCM WAV 16/24kHz, 파일 10분/32MB 제한. |
| SAM 결과 비어 있음 | 해당 물체가 실제 이미지에 있는지, 짧고 구체적인 **한 종류**의 명사구인지 확인. |
| Muse Code가 다른 방식으로 과금 | CLI 인증 우선순위: 환경 `META_API_KEY` → 저장된 키 → 브라우저 세션. 구독과 일반 API 키를 혼동하지 않는다. |
| Usage가 바로 안 보임 | 응답의 `usage`·ID를 기록하고 대시보드 반영을 기다린다. Billing에는 지연이 있을 수 있다. |

## 12. 공식 링크 모음

- [해커톤 참가자 가이드·파트너 혜택·심사](https://cerebralvalley.ai/e/meta-ai-developer-hackathon/details) · [Waiver의 Credit Terms PDF](https://cerebralvalley.ai/e/meta-ai-developer-hackathon/waiver)
- [Model API Quickstart](https://dev.meta.ai/docs/quickstart) · [Models](https://dev.meta.ai/docs/models) · [Pricing](https://dev.meta.ai/docs/pricing-rate-limits)
- [Muse Code](https://dev.meta.ai/docs/muse-code) · [구독과 종량제](https://dev.meta.ai/docs/muse-code/subscriptions)
- [Muse Image](https://dev.meta.ai/docs/image-generation) · [Voice Transcribe](https://dev.meta.ai/docs/speech-to-text) · [SAM 3.1](https://dev.meta.ai/docs/sam/overview)
- [Muse Glimmer](https://dev.meta.ai/docs/muse-glimmer) · [Muse Spark Open](https://dev.meta.ai/docs/muse-spark-open)
- [공식 API 예제 저장소](https://github.com/meta-models/meta-model-cookbook) · [오픈 모델 예제](https://github.com/meta-models/meta-oss-cookbook)

> **확인 범위:** 2026-10-28에 공식 페이지와 현재 계정의 Billing/Models 화면을 확인했다. 이 문서의 체크리스트에 적힌 실제 API·CLI·로컬 모델 테스트는 아직 실행하지 않았다. 가격·지역·모델 가용성·혜택은 바뀔 수 있으므로 실행 직전에 위 공식 페이지와 계정 화면을 다시 확인한다.
