"""Writing style presets: per-novel voice injected into drafting.

Each preset fills the style_policies row (pov/diction/feedback), which the
drafting prompt already follows as "문체 정책". Values stay short on purpose:
they ride along on every drafting call.
"""
from __future__ import annotations

DEFAULT_PRESET = "fusion-fantasy"

PRESETS: dict[str, dict[str, str]] = {
    "fusion-fantasy": {
        "label": "퓨전판타지 · 헌터물 (문피아형)",
        "description": "짧은 호흡·빠른 템포·사이다 전개. 모바일 연재 기본형.",
        "pov": "3인칭 관찰자 시점. 주인공의 판단과 이득 계산을 서술 중심에 둔다.",
        "diction": (
            "모바일 기준 1~3문장마다 줄을 바꾼다. 주어·서술어가 바로 이어지는 "
            "단문 위주, 동사 중심. 어려운 한자어·난해한 어휘를 피하고 쉬운 우리말로 쓴다."
        ),
        "feedback": (
            "상황 파악은 간결한 설명(Tell)으로 빠르게 넘기고 템포를 유지한다. "
            "위기는 1~2화 안에 해결하고 주인공이 즉각 보상을 챙긴다. "
            "능력치·스킬·보상은 아래 [상태창] 블록으로만 표시한다:\n"
            "[상태창]\n- 이름: ...\n- 칭호: ...\n- 스킬: ...\n- 보상: ...\n"
            "회차 끝은 다음 화가 궁금해지는 지점(보상·위기·반전)에서 끊는다."
        ),
    },
    "romance": {
        "label": "로맨스 · 로맨스판타지",
        "description": "감정선 중심. 보여주기 묘사와 완급 조절, 관계 진전형 엔딩.",
        "pov": "3인칭 밀착 시점. 주인공의 감정 변화와 상대의 반응을 섬세하게 좇는다.",
        "diction": (
            "감정이 고조되는 장면은 짧은 호흡으로, 여운이 필요한 장면은 조금 긴 호흡으로 "
            "완급을 조절한다. 직설적인 설명보다 표정·몸짓·대사의 여백으로 감정을 보여준다."
        ),
        "feedback": (
            "사건의 인과보다 인물 감정의 흐름을 우선한다. 갈등은 오해가 아닌 가치관·입장 "
            "차이에서 빚어지게 한다. 회차 끝은 관계의 진전·오해·고백 같은 감정적 고리에서 끊는다."
        ),
    },
    "mystery": {
        "label": "미스터리 · 스릴러",
        "description": "정보 통제와 복선. 짧고 빠른 호흡, 매 회차 반전 훅.",
        "pov": "3인칭 제한 시점. 독자가 아는 것을 주인공이 아는 범위로 묶는다.",
        "diction": (
            "1~2문장마다 줄을 바꿔 압박감을 유지한다. 단정적인 해설을 피하고 단서만 "
            "제시한다. 진실을 직접 말하는 문장을 쓰지 않는다."
        ),
        "feedback": (
            "매 회차에 단서 하나와 오도 하나를 배치한다. 추리는 주인공의 입으로 직접 "
            "하게 하고 서술자가 대신 풀어주지 않는다. 회차 끝은 새로운 의문이나 "
            "뒤집히는 단서에서 끊는다."
        ),
    },
    "classic": {
        "label": "정통 문학풍",
        "description": "보여주기 중심의 묘사와 문장 미학. 호흡 제한 없음.",
        "pov": "3인칭 전지적 시점. 인물의 내면과 세계를 깊이 있게 비춘다.",
        "diction": (
            "장면의 질감을 살리는 묘사를 우선하고, 필요하면 만연체도 허용한다. "
            "어휘 선택에 공을 들이되 과장된 수식은 피한다."
        ),
        "feedback": (
            "사건을 서둘러 설명하지 않고 장면을 충분히 보여준다. 인물의 선택에는 "
            "동기와 대가를 함께 그린다. 회차 끝은 여운이 남는 문장으로 맺는다."
        ),
    },
}


def preset_list() -> list[dict[str, str]]:
    """Selectable presets for the novel-info dialog (default first)."""
    keys = [DEFAULT_PRESET] + [k for k in PRESETS if k != DEFAULT_PRESET]
    return [{"key": k, "label": PRESETS[k]["label"],
             "description": PRESETS[k]["description"]} for k in keys]


def resolve_preset(key: str | None) -> tuple[str, dict[str, str]]:
    """Unknown/empty keys fall back to the default (never an error)."""
    if key in PRESETS:
        return key, PRESETS[key]
    return DEFAULT_PRESET, PRESETS[DEFAULT_PRESET]
