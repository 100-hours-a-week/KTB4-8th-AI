"""장소 검증 서비스 (docs/1 2-4, IA_SRS AI-004).

흐름: 웹검색(고정 형식 텍스트) → 코드로 파싱 → 지도 API 로 좌표
      → 네이버 지역 검색으로 지점명 확인·폐점 표시(첫 번째 후보만).

검색 호출에 구조화 출력을 걸지 않는 이유: 랭체인 with_structured_output 을 걸면
Google 검색 그라운딩이 조용히 빠진다 — 에러 없이 "성공"하지만 검색어 0개·출처
0개였다(실측, bind_tools 후 적용·invoke 때 tools 전달 둘 다). 그래서 예전엔 구조화를
Gemini 호출 하나로 따로 했는데(11/3 까지), 검색 프롬프트가 정해진 형식으로 쓰게 하고
코드(verify_place_parser)가 읽도록 바꿨다. 같은 50곳 실측: 장소당 Gemini 토큰 비용
−32%, 시간은 비슷, 파싱 50/50(~/keepgo-bench/split_compare/search_format).

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
from datetime import datetime
from zoneinfo import ZoneInfo

from langsmith import traceable

from app.core.config import settings
from app.core.exceptions import AIServerError, LLMInvalidResponseError
from app.core.logging import log_llm, logger, report_handled_error_throttled
from app.integrations.map_client import geocode
from app.integrations.search_client import NaverSearchError, search_local
from app.models.factory import get_llm, to_domain_error
from app.prompts.loader import load_prompt
from app.schemas.business_hours import BusinessHours
from app.schemas.verify_place import (
    BusinessHoursParsed,
    GroundedPlace,
    PlaceCandidate,
    VerifyPlaceData,
    VerifyPlaceParsed,
    VerifyPlaceRequest,
)
from app.services.course_builder import distance_m
from app.services.place_name import brand_name
from app.services.verify_place_parser import ParseError, parse

_ENDPOINT = "verify-place"
_SEARCH_TOOL = {"google_search": {}}
# 형식을 못 읽으면 검색을 한 번 더 한다. 그래도 못 읽으면 llm_invalid_response(영상 failed).
# 검색이 20초 안팎이라 두 번째 시도는 verify-place 60초 안에 못 끝날 수 있다 — 그러면 llm_timeout.
_SEARCH_ATTEMPTS = 2
# 영업시간 데이터가 Asia/Seoul 기준이다. 서버(컨테이너)는 UTC 일 수 있어서
# date.today() 를 쓰면 한국 자정~오전 9시에 하루 어긋난다.
_KST = ZoneInfo("Asia/Seoul")
# 네이버 결과가 이 거리 안이면 같은 자리로 본다. 지오코딩은 건물 주소, 네이버는 가게
# 위치라 같은 가게도 조금 어긋날 수 있어 넉넉히 둔다(추정, 실측 전).
_SAME_PLACE_METERS = 100


def _or_none(value) -> str:
    return str(value) if value else "없음"


def _event_period(req: VerifyPlaceRequest) -> str:
    if req.event_start_date or req.event_end_date:
        return f"{req.event_start_date or '?'} ~ {req.event_end_date or '?'}"
    return "없음"


@traceable(name="verify-place:search", run_type="chain")
async def _search(req: VerifyPlaceRequest) -> str:
    """그라운딩으로 웹검색. 결과는 고정 형식 텍스트(verify_place_search.yaml)."""
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


@traceable(name="verify-place:parse", run_type="parser")
def _parse(search_result: str) -> VerifyPlaceParsed:
    parsed, _stats = parse(search_result)  # 통계(버린 후보 수)는 측정용이라 여기선 쓰지 않는다
    return parsed


async def _search_and_parse(req: VerifyPlaceRequest) -> VerifyPlaceParsed:
    last: ParseError | None = None
    for attempt in range(1, _SEARCH_ATTEMPTS + 1):
        search_result = await _search(req)
        try:
            return _parse(search_result)
        except ParseError as exc:
            # 원문 앞부분을 남긴다 — 무엇이 형식을 깼는지 봐야 프롬프트나 파서를 고칠 수 있다.
            logger.warning("verify-place parse_failed attempt=%d place=%s reason=%s head=%r",
                           attempt, req.place_name, exc, search_result[:300])
            last = exc
    raise LLMInvalidResponseError(f"검색 결과 형식을 읽지 못함: {last}")


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


def _to_business_hours(hours: BusinessHoursParsed | None) -> BusinessHours | None:
    """모델이 적은 영업시간을 응답 형식으로 바꾼다.

    모델은 옮겨 적기만 하고, 정리는 여기서 한다 — 예외일 목록을 날짜 dict 로
    바꾸고, 오늘(한국 시간) 이전 날짜는 버린다. 같은 날짜가 두 번 오면 뒤의
    것을 쓴다. timezone·estimated 는 기본값(Asia/Seoul, false)이다.
    """
    if hours is None:
        return None
    today = datetime.now(_KST).date()
    exceptions = {day.date: day.hours for day in hours.exceptions if day.date >= today}
    return BusinessHours(weekly_hours=hours.weekly_hours, exceptions=exceptions)


async def _check_naver(place: PlaceCandidate) -> PlaceCandidate:
    """네이버 지역 검색으로 지점명을 확인하고, 안 나오면 폐점으로 표시한다.

    구글 검색에는 폐업한 가게의 옛 글도 나와서 Gemini 가 운영 중으로 볼 수 있다.
    검증 상호로 검색해 결과마다 이름(지점명·공백 뺀 상호로 시작하는지)과 위치를 본다.
    - 둘 다 맞으면 네이버 상호로 바꾼다 — 지점명 표기가 하나로 맞춰진다.
    - 하나만 맞으면 영업 중으로 보고 검증 상호를 그대로 쓴다(영문 표기, 다른 지점 등).
    - 하나도 안 맞으면 closed 로 표시한다.
    - 검색이 실패하면 확인을 못 한 것이라 그대로 둔다.
    """
    try:
        results = await search_local(place.place_name)
    except NaverSearchError as exc:
        # 네이버가 고장 나면 요청마다 같은 실패가 나서 5분에 한 번만 보고한다.
        report_handled_error_throttled(f"{_ENDPOINT} naver_search_failed", "naver_search_error", exc)
        return place
    want = brand_name(place.place_name)
    here = (place.lat, place.lng)
    loose = False
    for r in results:
        name_ok = "".join(r.name.split()).startswith(want)
        near = distance_m(here, (r.lat, r.lng)) <= _SAME_PLACE_METERS
        if name_ok and near:
            return place.model_copy(update={"place_name": r.name})
        loose = loose or name_ok or near
    return place if loose else place.model_copy(update={"closed": True})


async def _verify(req: VerifyPlaceRequest) -> VerifyPlaceData:
    grounded = await _search_and_parse(req)

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
        PlaceCandidate(
            **p.model_dump(exclude={"evidence", "business_hours"}),
            business_hours=_to_business_hours(p.business_hours),
            lat=xy[0],
            lng=xy[1],
        )
        for p, xy in zip(places, coords)
        if xy is not None
    ]
    if not candidates:
        # 웹검색으론 있는데 좌표를 하나도 못 얻었다(해외 주소 등). 지도 API
        # 장애였다면 geocode 가 MapAPIError 를 이미 던졌으니, 여기 온 건 주소
        # 자체가 안 바뀐 경우다. 예전엔 MapAPIError 로 올렸는데, 백엔드가 장애로
        # 보고 재처리를 반복해도 풀리지 않아서 "확인 못 함"(not_found)으로 돌린다.
        # 얼마나 자주 나오는지 셀 수 있게 로그를 남긴다.
        logger.warning(
            "verify-place address_unresolved place=%s addresses=%s",
            req.place_name,
            [p.address for p in places],
        )
        return VerifyPlaceData(exists=False, candidates=[])

    # 영상 분석은 첫 번째 후보만 쓴다 — 나머지까지 검색하지 않는다.
    candidates[0] = await _check_naver(candidates[0])
    return VerifyPlaceData(exists=True, candidates=candidates)


@traceable(name="verify-place", run_type="chain")
async def verify_place(req: VerifyPlaceRequest) -> VerifyPlaceData:
    try:
        # 검색·파싱·좌표 조회를 합쳐 백엔드가 기다리는 시간 안에 끝낸다
        # (docs/1 비고: verify-place 60초).
        return await asyncio.wait_for(_verify(req), timeout=settings.TIMEOUT_VERIFY_PLACE)
    except AIServerError:
        raise
    except Exception as exc:
        raise to_domain_error(exc) from exc
