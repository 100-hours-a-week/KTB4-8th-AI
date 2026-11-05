"""TypeSafe Jev(결정 모델) 클라이언트 — 카테고리 분류, 코스 추천 재순위.

글을 생성하지 않고, 상태(state)와 선택지를 주면 선택지별 확률을 돌려준다. 외부 연결은
integrations/ 에만 둔다(docs/6 6-3).

API: POST {JEV_URL}  헤더 Authorization: Bearer <TYPESAFE_API_KEY>
     본문 {state, model, questions: {category: {type: "choice", instructions, criteria}}}
     응답 answers.category.choice
"""

import httpx
from langsmith import traceable

from app.core.config import settings
from app.prompts import jev_rerank
from app.prompts.jev_category import CRITERIA, INSTRUCTIONS
from app.schemas.category import PlaceCategory

# 실패하면 2번 더 부른다(총 3번). 호출이 0.2초라 다시 불러도 싸고, 여기서 실패하면 영상 전체가
# failed 가 돼 백엔드 재요청으로 Gemini 영상 분석부터 다시 돈다.
_ATTEMPTS = 3


async def _ask(body: dict, read) -> object:
    """body 를 보내고 answers 를 read 로 읽는다. 세 번 다 실패하면 마지막 예외를 올린다."""
    if not (settings.TYPESAFE_API_KEY or "").strip():
        raise ValueError("TYPESAFE_API_KEY 가 비어 있음")
    headers = {"Authorization": f"Bearer {settings.TYPESAFE_API_KEY}"}
    last: Exception | None = None
    for _ in range(_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=settings.TIMEOUT_JEV) as client:
                response = await client.post(settings.JEV_URL, json=body, headers=headers)
            response.raise_for_status()
            return read(response.json()["answers"])
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            last = exc
    raise last


@traceable(name="jev-category", run_type="tool")
async def classify_category(place_name: str, observations: list[str]) -> PlaceCategory:
    """장소 하나의 카테고리를 고른다. 입력은 장소명과 영상 전체 관찰 기록(11/3 벤치와 같다).

    세 번 다 실패하면 마지막 예외를 그대로 올린다 — 부르는 쪽이 to_domain_error 로 바꾼다
    (타임아웃은 llm_timeout, 나머지는 internal_server_error).
    """
    body = {
        "state": f"장소명: {place_name}\n관찰 기록:\n" + "\n".join(observations),
        "model": settings.JEV_MODEL,
        "questions": {"category": {"type": "choice", "instructions": INSTRUCTIONS, "criteria": CRITERIA}},
    }

    def read(answers: dict) -> PlaceCategory:
        choice = answers["category"]["choice"]
        if choice not in CRITERIA:
            raise ValueError(f"선택지에 없는 답: {choice!r}")
        return choice

    return await _ask(body, read)


@traceable(name="jev-rerank", run_type="tool")
async def score_places(query: str, category: str, places: list[tuple[int, str, str]]) -> dict[int, float]:
    """(place_id, 장소명, 요약) 후보마다 요청에 맞을 확률을 받는다. 한 요청으로 보낸다 — 공유 state 는 한 번만 과금된다."""
    body = {
        "state": f"정성 조건: {query}\n카테고리: {category}",
        "model": settings.JEV_MODEL,
        "questions": {
            str(place_id): {
                "type": "noul",
                "instructions": jev_rerank.INSTRUCTIONS.format(place_name=name, summary=summary),
                "criteria": jev_rerank.CRITERIA,
            }
            for place_id, name, summary in places
        },
    }
    return await _ask(body, lambda answers: {place_id: answers[str(place_id)]["noul"] for place_id, _, _ in places})
