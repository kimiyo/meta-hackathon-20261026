# 평가셋 (§9)

`cases.json`은 모순/정상 짝짓기 케이스다. 각 케이스는 확정 검사(checks.py)가
잡아야 할 위반과 잡지 말아야 할 정상을 쌍으로 둔다. 러너는
`server/tests/test_eval_quality.py`이며 LLM 호출 없이 실행된다.

```sh
.venv/Scripts/python.exe -m pytest server/tests/test_eval_quality.py -q
```

케이스 형식:

- `committed`: EP1에 확정 저장되는 사실 목록
- `pending_items` / `pending_events`: EP2 임시안
- `expect`: `{check, count, verdict?}` — 해당 확정 검사의 후보 수와 판정
- 모든 `evidence_quote`는 해당 회차 원고의 부분문자열이어야 하며,
  러너가 인용 정직성을 함께 검증한다.

현재 12 케이스: 횟수 제한(위반/정상/불확실/새날짜·점표기·무관 술어),
사망 후 행동(위반/회상/꿈), 배타 위치(위반/이동/단일).

같은 파일에 파이프라인 동작 테스트도 있다: 안정 ID·동명이인 분리·
작품 격리·비상태 모드·관계 확정/종료·기준 버전 차단·서술 단위.
