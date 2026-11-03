"""Writing style presets: per-novel voice injected into drafting.

Market catalogs (plan §7). The KR catalog keeps the original keys
(fusion-fantasy, romance, mystery, classic); the US catalog lives in a
separate namespace so short IDs like "romance" never collide across
markets. Each entry follows the contracts schema
(docs/i18n-contracts.md §9): id/market/version/label/description plus
the AI voice (pov/diction/feedback).

Application snapshots the voice into style_policies (see drafting):
later catalog edits never rewrite works already using a preset.
"""
from __future__ import annotations

from . import locales

DEFAULT_PRESET = "fusion-fantasy"

PRESETS: dict[str, dict[str, object]] = {
    "fusion-fantasy": {
        "id": "fusion-fantasy",
        "market": "KR",
        "version": 1,
        "label": "퓨전판타지 · 헌터물 (문피아형)",
        "description": "짧은 호흡·빠른 템포·사이다 전개. 모바일 연재 기본형.",
        "pov": "3인칭 관찰자 시점. 주인공의 판단과 이득 계산을 서술 중심에 둔다.",
        "diction": (
            "모바일 기준 1~3문장마다 줄을 바꾼다. 주어·서술어가 바로 이어지는 "
            "단문 위주, 동사 중심. 어려운 한자어·난해한 어휘를 피하고 쉬운 우리말로 쓴다. "
            "하나의 호흡이 끝나면 빈 줄을 넣어 문단을 나누고, 빈 줄 없이 줄만 바꿔 나열하지 않는다."
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
        "id": "romance",
        "market": "KR",
        "version": 1,
        "label": "로맨스 · 로맨스판타지",
        "description": "감정선 중심. 보여주기 묘사와 완급 조절, 관계 진전형 엔딩.",
        "pov": "3인칭 밀착 시점. 주인공의 감정 변화와 상대의 반응을 섬세하게 좇는다.",
        "diction": (
            "감정이 고조되는 장면은 짧은 호흡으로, 여운이 필요한 장면은 조금 긴 호흡으로 "
            "완급을 조절한다. 직설적인 설명보다 표정·몸짓·대사의 여백으로 감정을 보여준다. "
            "호흡 단위가 바뀌면 빈 줄을 넣어 문단을 나누고 숨 쉴 틈을 준다."
        ),
        "feedback": (
            "사건의 인과보다 인물 감정의 흐름을 우선한다. 갈등은 오해가 아닌 가치관·입장 "
            "차이에서 빚어지게 한다. 회차 끝은 관계의 진전·오해·고백 같은 감정적 고리에서 끊는다."
        ),
    },
    "mystery": {
        "id": "mystery",
        "market": "KR",
        "version": 1,
        "label": "미스터리 · 스릴러",
        "description": "정보 통제와 복선. 짧고 빠른 호흡, 매 회차 반전 훅.",
        "pov": "3인칭 제한 시점. 독자가 아는 것을 주인공이 아는 범위로 묶는다.",
        "diction": (
            "1~2문장마다 줄을 바꿔 압박감을 유지한다. 단정적인 해설을 피하고 단서만 "
            "제시한다. 진실을 직접 말하는 문장을 쓰지 않는다. "
            "하나의 호흡이 끝나면 빈 줄을 넣어 문단을 나누고, 빈 줄 없이 줄만 바꿔 나열하지 않는다."
        ),
        "feedback": (
            "매 회차에 단서 하나와 오도 하나를 배치한다. 추리는 주인공의 입으로 직접 "
            "하게 하고 서술자가 대신 풀어주지 않는다. 회차 끝은 새로운 의문이나 "
            "뒤집히는 단서에서 끊는다."
        ),
    },
    "classic": {
        "id": "classic",
        "market": "KR",
        "version": 1,
        "label": "정통 문학풍",
        "description": "보여주기 중심의 묘사와 문장 미학. 호흡 제한 없음.",
        "pov": "3인칭 전지적 시점. 인물의 내면과 세계를 깊이 있게 비춘다.",
        "diction": (
            "장면의 질감을 살리는 묘사를 우선하고, 필요하면 만연체도 허용한다. "
            "어휘 선택에 공을 들이되 과장된 수식은 피한다. "
            "문단이 바뀌면 빈 줄로 띄워 읽기 리듬을 살린다."
        ),
        "feedback": (
            "사건을 서둘러 설명하지 않고 장면을 충분히 보여준다. 인물의 선택에는 "
            "동기와 대가를 함께 그린다. 회차 끝은 여운이 남는 문장으로 맺는다."
        ),
    },
}

# US catalog (plan §7.2). Written in English — the catalog language, not
# the UI language. Values stay short: they ride on every drafting call.
US_PRESETS: dict[str, dict[str, object]] = {
    "fantasy": {
        "id": "fantasy",
        "market": "US",
        "version": 1,
        "label": "Fantasy",
        "description": "Otherworldly stakes, clear magic rules, propulsive quests.",
        "pov": ("Third-person limited POV. Stay close to the protagonist's "
                "judgment and desires."),
        "diction": (
            "Concrete sensory detail and plain strong verbs. Name each "
            "magic's cost plainly. Start a new paragraph, separated by a "
            "blank line, whenever the beat or focus shifts; never stack "
            "lines without a blank line between paragraphs."
        ),
        "feedback": (
            "Resolve each episode's crisis within one or two episodes and "
            "pay off setup quickly. End on a hook: reward, danger, or "
            "reversal."
        ),
    },
    "romance": {
        "id": "romance",
        "market": "US",
        "version": 1,
        "label": "Romance",
        "description": "Emotion-first arcs, earned intimacy, relationship momentum.",
        "pov": ("Third-person close POV. Track the protagonist's shifting "
                "feelings and the love interest's reactions."),
        "diction": (
            "Show emotion through expression, gesture, and dialogue subtext "
            "more than direct statement. Vary rhythm: short bursts for "
            "intensity, longer lines for tenderness. Separate paragraphs "
            "with a blank line."
        ),
        "feedback": (
            "Build conflict from values and circumstances, not "
            "misunderstandings. End each episode on an emotional beat: "
            "progress, tension, or confession."
        ),
    },
    "mystery-thriller": {
        "id": "mystery-thriller",
        "market": "US",
        "version": 1,
        "label": "Mystery / Thriller",
        "description": "Controlled information, planted clues, a twist every episode.",
        "pov": ("Third-person limited POV. The reader knows only what the "
                "protagonist knows."),
        "diction": (
            "Short paragraphs for pressure. Present clues without "
            "explaining them; never state the truth outright. Separate "
            "paragraphs with a blank line."
        ),
        "feedback": (
            "Plant one clue and one misdirect per episode. Let the "
            "protagonist voice deductions; the narrator never solves it "
            "for them. End on a new question or an overturned clue."
        ),
    },
    "science-fiction": {
        "id": "science-fiction",
        "market": "US",
        "version": 1,
        "label": "Science Fiction",
        "description": "Plausible tech, idea-driven wonder, human consequences.",
        "pov": ("Third-person limited POV. Filter strangeness through one "
                "character's grounded viewpoint."),
        "diction": (
            "Explain new technology through use and consequence, not "
            "lectures. Keep jargon light and consistent. Separate "
            "paragraphs with a blank line."
        ),
        "feedback": (
            "Give every wonder a cost and every choice a consequence. End "
            "episodes on implication: what the new knowledge demands next."
        ),
    },
    "literary-fiction": {
        "id": "literary-fiction",
        "market": "US",
        "version": 1,
        "label": "Literary Fiction",
        "description": "Image-driven prose, interior depth, resonant endings.",
        "pov": ("Third-person close POV. Render inner life and world with "
                "equal care."),
        "diction": (
            "Prefer showing scenes fully over summarizing events. Long "
            "sentences are allowed when the rhythm earns them; cut "
            "ornament that adds nothing. Separate paragraphs with a "
            "blank line."
        ),
        "feedback": (
            "Motivate choices and show their costs. Close episodes on a "
            "resonant image or line rather than a plot hook."
        ),
    },
}

# preset_catalog name (locales.MARKETS[m]["preset_catalog"]) -> entries.
CATALOGS: dict[str, dict[str, dict[str, object]]] = {
    "kr": PRESETS,
    "us": US_PRESETS,
}

DEFAULT_PRESETS: dict[str, str] = {
    "KR": "fusion-fantasy",
    "US": "fantasy",
}


def catalog_for_market(market: str) -> dict[str, dict[str, object]]:
    """Preset catalog for a market code (validates via the registry)."""
    cfg = locales.market_config(market)  # raises ValueError when unknown
    return CATALOGS[cfg["preset_catalog"]]


def default_preset_for_market(market: str) -> str:
    """Default preset key for a market (KR keeps the legacy default)."""
    catalog_for_market(market)  # validate
    return DEFAULT_PRESETS[locales.validate_market(market)]


def _listed(catalog: dict[str, dict[str, object]],
            keys: list[str]) -> list[dict[str, object]]:
    return [{"key": k, "id": catalog[k]["id"], "market": catalog[k]["market"],
             "version": catalog[k]["version"],
             "label": catalog[k]["label"],
             "description": catalog[k]["description"],
             "pov": catalog[k]["pov"], "diction": catalog[k]["diction"],
             "feedback": catalog[k]["feedback"]} for k in keys]


def preset_list(market: str | None = None) -> list[dict[str, object]]:
    """Selectable presets for the novel-info dialog (default first).

    market=None preserves the legacy KR-only listing shape; pass a
    market code for that market's catalog.
    """
    if market is None:
        keys = [DEFAULT_PRESET] + [k for k in PRESETS
                                   if k != DEFAULT_PRESET]
        return _listed(PRESETS, keys)
    catalog = catalog_for_market(market)
    default = default_preset_for_market(market)
    keys = [default] + [k for k in catalog if k != default]
    return _listed(catalog, keys)


def resolve_preset(key: str | None,
                   market: str = "KR") -> tuple[str, dict[str, object]]:
    """Unknown/empty keys fall back to the market default (never an error)."""
    catalog = catalog_for_market(market)
    if key in catalog:
        return key, catalog[key]
    default = default_preset_for_market(market)
    return default, catalog[default]
