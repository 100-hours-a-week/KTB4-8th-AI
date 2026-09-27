"""장소 검증 서비스 (docs/1 2-4, IA_SRS AI-004).

흐름: 웹검색으로 사실 수집 → 형식에 맞춰 구조화 → 지도 API 로 좌표.

검색과 구조화를 두 번의 호출로 나눈 이유: 랭체인 with_structured_output 을 걸면
Google 검색 그라운딩이 조용히 빠진다 — 에러 없이 "성공"하지만 검색어 0개·출처
0개였다(실측, bind_tools 후 적용·invoke 때 tools 전달 둘 다). Gemini 3 자체는
그라운딩과 구조화 출력의 병용을 지원한다고 하니(Google 블로그) 랭체인 경로의
문제로 보인다. 네이티브 SDK 로 한 호출에 합칠 여지는 있으나 확인 전이다.
구조화 단계는 검색을 안 하니 그라운딩 과금은 1회만 나간다.

동일성 검증: 실존만 확인하면 "실제로 있지만 영상 속 가게가 아닌 곳"이 주소·
좌표까지 진짜라 끝까지 통과하고, 백엔드가 1번을 자동 채택해 엉뚱한 가게가
저장된다. 그래서 (1) 이름뿐 아니라 지역+특징으로도 검색하고 (2) 후보마다 요약의
특징이 그 가게에 있다는 근거(GroundedPlace.evidence)를 적게 해서, 근거를 못
대면 이름이 같아도 뺀다. 실측(2026-09-26):
  - 적용 전, 유사 상호 허용: 지어낸 가게 → 봄의정원 성수점 채택 (2회 중 1회)
  - 적용 전, 이름 일치만: 청년방앗간(범앗간 로고 오독) → 김해 제분소 채택
  - 적용 후: 지어낸 가게 3/3 exists=false, 범앗간 2/2 정답, 제분소 미출현
  표본이 작다. 거짓 양성이 다시 보이면 유사 후보에 한해 검증 검색을 1회 더
  붙인다(호출·시간 증가). 대가: 특징까지 검색해서 느려졌다(범앗간 20→28초).

TODO: 해외 주소 — 검색 결과에 뉴욕·LA 주소가 나온 적이 있다(JEONG YUK JEOM).
  네이버 지오코딩이 해외 주소를 못 바꾸면 후보가 전부 빠져 502
  map_api_error 가 나가고, 백엔드는 지도 장애로 보고 재처리를 반복한다
  (몇 번을 해도 안 풀림). 네이버의 해외 주소 처리 여부는 미확인.
"""

import asyncio
import time

from app.core.config import settings
from app.core.exceptions import AIServerError, LLMInvalidResponseError, MapAPIError
from app.core.logging import log_llm
from app.integrations.map_client import geocode
from app.models.factory import get_llm, to_domain_error
from app.prompts.loader import load_prompt
from app.schemas.verify_place import (
    GroundedPlace,
    PlaceCandidate,
    VerifyPlaceData,
    VerifyPlaceLLM,
    VerifyPlaceRequest,
)

_ENDPOINT = "verify-place"
_SEARCH_TOOL = {"google_search": {}}


def _or_none(value) -> str:
    return str(value) if value else "없음"


def _event_period(req: VerifyPlaceRequest) -> str:
    if req.event_start_date or req.event_end_date:
        return f"{req.event_start_date or '?'} ~ {req.event_end_date or '?'}"
    return "없음"


async def _search(req: VerifyPlaceRequest) -> str:
    """1단계: 그라운딩으로 웹검색. 결과는 자유 서술 텍스트."""
    prompt = load_prompt(
        "verify_place_search",
        place_name=req.place_name,
        region=_or_none(req.region),
        summary=_or_none(req.summary),
        event_period=_event_period(req),
    )
    llm = get_llm().bind_tools([_SEARCH_TOOL])

    started = time.perf_counter()
    try:
        message = await llm.ainvoke(prompt)
    except Exception as exc:
        log_llm(f"{_ENDPOINT}:search", settings.GOOGLE_MODEL,
                time.perf_counter() - started, success=False, detail=str(exc))
        raise
    log_llm(f"{_ENDPOINT}:search", settings.GOOGLE_MODEL,
            time.perf_counter() - started, success=True)
    return message.text


async def _structure(req: VerifyPlaceRequest, search_result: str) -> VerifyPlaceLLM:
    """2단계: 검색 결과를 스키마로 옮긴다. 검색 도구는 쓰지 않는다."""
    prompt = load_prompt(
        "verify_place_structure",
        place_name=req.place_name,
        region=_or_none(req.region),
        summary=_or_none(req.summary),
        search_result=search_result,
    )
    chain = get_llm().with_structured_output(
        VerifyPlaceLLM, method="json_schema", include_raw=True
    )

    started = time.perf_counter()
    try:
        result = await chain.ainvoke(prompt)
    except Exception as exc:
        log_llm(f"{_ENDPOINT}:structure", settings.GOOGLE_MODEL,
                time.perf_counter() - started, success=False, detail=str(exc))
        raise
    latency = time.perf_counter() - started

    if result["parsing_error"] is not None:
        detail = str(result["parsing_error"])
        log_llm(f"{_ENDPOINT}:structure", settings.GOOGLE_MODEL, latency,
                success=False, detail=detail)
        raise LLMInvalidResponseError(detail)

    log_llm(f"{_ENDPOINT}:structure", settings.GOOGLE_MODEL, latency, success=True)
    return result["parsed"]


def _apply_request_dates(req: VerifyPlaceRequest, place: GroundedPlace) -> GroundedPlace:
    """요청에 이미 있는 행사 날짜는 그대로 쓴다.

    docs/1 2-4 — 웹검색 보완은 "요청에 비어 있을 때"만 한다. 프롬프트로도
    알려주지만 모델이 덮어쓸 수 있으니 코드로 강제한다. 종료일이 요청에
    있으면 기간이 확정된 것이므로 unresolved 도 끈다.
    """
    update = {}
    if req.event_start_date:
        update["event_start_date"] = req.event_start_date
    if req.event_end_date:
        update["event_end_date"] = req.event_end_date
        update["event_period_unresolved"] = False
    return place.model_copy(update=update) if update else place


async def _verify(req: VerifyPlaceRequest) -> VerifyPlaceData:
    search_result = await _search(req)
    grounded = await _structure(req, search_result)

    if not grounded.exists or not grounded.candidates:
        return VerifyPlaceData(exists=False, candidates=[])

    # 근거를 못 댄 후보는 코드에서도 한 번 더 뺀다. 프롬프트로 지시하지만
    # 모델이 빈 근거로 넣는 경우를 막는다. 다 빠지면 "없는 장소"다.
    places = [
        _apply_request_dates(req, p) for p in grounded.candidates if p.evidence.strip()
    ]
    if not places:
        return VerifyPlaceData(exists=False, candidates=[])

    # 좌표는 후보마다 병렬로. 지도 API 자체가 실패하면(MapAPIError) 여기서
    # 요청 전체가 실패한다 — 벤더 장애는 백엔드가 재처리해야 하기 때문이다.
    coords = await asyncio.gather(*(geocode(p.address) for p in places))

    # 주소가 좌표로 안 바뀐 후보만 뺀다(없는 주소). 순서는 유지되므로
    # 남은 것 중 첫 번째가 자동 채택값이다.
    candidates = [
        PlaceCandidate(**p.model_dump(exclude={"evidence"}), lat=xy[0], lng=xy[1])
        for p, xy in zip(places, coords)
        if xy is not None
    ]
    if not candidates:
        # 웹검색으론 있는데 좌표를 하나도 못 얻었다. exists=false 로 돌리면
        # "없는 장소"로 오해되니, 지도 쪽 문제로 올린다.
        raise MapAPIError("검색으로 찾은 장소의 주소를 좌표로 바꾸지 못했습니다")

    return VerifyPlaceData(exists=True, candidates=candidates)


async def verify_place(req: VerifyPlaceRequest) -> VerifyPlaceData:
    try:
        # 검색·구조화·좌표 조회를 합쳐 백엔드가 기다리는 시간 안에 끝낸다
        # (docs/1 비고: verify-place 60초).
        return await asyncio.wait_for(_verify(req), timeout=settings.TIMEOUT_VERIFY_PLACE)
    except AIServerError:
        raise
    except Exception as exc:
        raise to_domain_error(exc) from exc
