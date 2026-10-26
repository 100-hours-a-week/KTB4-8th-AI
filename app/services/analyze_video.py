import asyncio
import time
from urllib.parse import urlparse

from langsmith import traceable

from app.core.config import settings
from app.core.exceptions import AIServerError, InvalidRequestError
from app.core.logging import log_llm, report_handled_error
from app.models.factory import get_llm, to_domain_error
from app.prompts.analyze_video import build_batch_messages
from app.schemas.analyze_video import (
    AnalyzeVideoBatchLLM,
    AnalyzeVideosData,
    AnalyzeVideosRequest,
    VideoAnalysis,
    VideoAnalysisResult,
)
from app.schemas.category import EVENT_CATEGORIES
from app.schemas.verify_place import VerifyPlaceRequest
from app.services.verify_place import verify_place

_BATCH_ENDPOINT = "analyze-videos"
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}

# ── 배치: /v1/analyze-video ──────────────────────────────────────────

# 묶음 호출을 동시에 몇 개까지 보낼지. 서버 전체에 하나라, 배치 요청이 두 개
# 동시에 와도 Gemini 로 가는 묶음 호출은 합쳐서 이 수를 넘지 않는다(429 방지).
# 영상 분석 전용이다 — extract 와 공유하면 배치 뒤에 챗봇 요청이 줄을 선다
# (docs/6 5-5 "배치가 실시간을 굶기면 안 된다").
_BATCH_SLOTS = asyncio.Semaphore(settings.ANALYZE_VIDEO_PARALLEL)

# 장소 검증을 동시에 몇 건까지 보낼지. 분석 슬롯과 같은 이유로 서버 전체에
# 하나다 — 묶음마다 따로 두면 묶음 수만큼 곱해져 제한이 무의미해진다. 분석
# 슬롯과 나눈 건 검증이 분석 자리를 차지해 다음 묶음 분석이 밀리지 않게 하려고.
_VERIFY_SLOTS = asyncio.Semaphore(settings.VERIFY_PLACE_PARALLEL)


def _failed(url: str, code: str) -> VideoAnalysisResult:
    return VideoAnalysisResult(video_url=url, status="failed", error=code)


def _not_found(url: str) -> VideoAnalysisResult:
    # 시트 비고대로 video_url·status 말고는 전부 null 이다. 분석 값(category,
    # confidence)도 싣지 않는다 — 장소를 특정하지 못한 결과라 저장 대상이 아니다.
    return VideoAnalysisResult(video_url=url, status="not_found")


async def _analyze_group(urls: list[str]) -> list[list[VideoAnalysis] | VideoAnalysisResult]:
    """영상 최대 10개를 Gemini 호출 1번으로 분석한다.

    url 마다 한 칸씩, 그 영상의 장소 목록 또는 이미 끝난 실패 결과를 돌려준다.
    영상별로 묶어 두는 건 뒤에서 영상 단위로 판단하기 때문이다(빠뜨린 영상,
    장소 단위 not_found 를 버릴지 — _finish_video). 장소를 찾지 못한 영상은
    place_name 이 빈 항목 하나짜리 목록이다.
    실패 단위가 묶음이라는 점에 주의 — 호출이 실패하면 안의 영상이 전부
    실패로 나간다. 모델이 일부 영상을 빠뜨리면 그 영상만 실패로 표시한다.
    """
    chain = get_llm().with_structured_output(
        AnalyzeVideoBatchLLM, method="json_schema", include_raw=True
    )

    started = time.perf_counter()
    try:
        # 슬롯을 잡은 뒤에 재므로 줄 서는 시간은 빠진다 — 뒤 묶음이 대기만으로
        # 타임아웃 나지 않게.
        result = await asyncio.wait_for(
            chain.ainvoke(build_batch_messages(urls)),
            timeout=settings.TIMEOUT_ANALYZE_VIDEO,
        )
    except Exception as exc:
        log_llm(_BATCH_ENDPOINT, settings.GOOGLE_MODEL, time.perf_counter() - started,
                success=False, detail=str(exc))
        code = to_domain_error(exc).error_code
        return [_failed(u, code) for u in urls]

    latency = time.perf_counter() - started

    if result["parsing_error"] is not None:
        log_llm(_BATCH_ENDPOINT, settings.GOOGLE_MODEL, latency,
                success=False, detail=str(result["parsing_error"]))
        return [_failed(u, "llm_invalid_response") for u in urls]

    log_llm(_BATCH_ENDPOINT, settings.GOOGLE_MODEL, latency, success=True)

    # 결과 순서를 믿지 않고 video_index 로 짝짓는다. 장소가 여러 곳인 영상은
    # 같은 번호가 여러 번 온다. 번호가 아예 없는 영상은 모델이 빠뜨린 것이다 —
    # 장소가 없는 영상도 빈 항목 하나를 내라고 지시하므로 둘이 구분된다.
    by_index: dict[int, list[VideoAnalysis]] = {}
    for item in result["parsed"].results:
        by_index.setdefault(item.video_index, []).append(item.to_data())

    outcomes: list[list[VideoAnalysis] | VideoAnalysisResult] = []
    for i, url in enumerate(urls, start=1):
        if i in by_index:
            outcomes.append(by_index[i])
        else:
            # 호출은 성공했는데 결과에 이 영상이 없다. 응답에만 담으면 아무 데도
            # 안 남아서 따로 기록한다.
            report_handled_error(f"{_BATCH_ENDPOINT} video_skipped url={url}", "llm_invalid_response")
            outcomes.append(_failed(url, "llm_invalid_response"))
    return outcomes


async def _verify(url: str, analysis: VideoAnalysis) -> VideoAnalysisResult:
    """분석한 장소 하나를 장소 검증에 넘겨 최종 결과로 만든다.

    not_found: 장소명을 못 읽었거나(검색하지 않는다), 검증에서 장소를 확인하지
    못했을 때. failed: 검증 단계 자체가 실패했을 때(LLM·지도 API 오류). 한
    장소의 실패가 다른 장소에 번지지 않게 여기서 다 잡는다.
    """
    if not analysis.place_name:
        return _not_found(url)

    req = VerifyPlaceRequest(
        place_name=analysis.place_name,
        region=analysis.region,
        summary=analysis.summary,
        event_start_date=analysis.event_start_date,
        event_end_date=analysis.event_end_date,
    )
    try:
        async with _VERIFY_SLOTS:
            verified = await verify_place(req)
    except Exception as exc:
        # verify_place 는 이미 우리 예외(map_api_error·llm_timeout 등)로 바꿔 던진다.
        # to_domain_error 에 다시 넣으면 타임아웃·429 외엔 전부
        # internal_server_error 로 뭉개지므로 우리 예외면 코드를 그대로 쓴다.
        code = exc.error_code if isinstance(exc, AIServerError) else to_domain_error(exc).error_code
        # 영상별 failed 로 응답에만 담기면 아무 데도 안 남는다(검증 타임아웃은
        # LLM 호출이 취소돼 log_llm 도 안 찍힌다). 그래서 따로 기록한다.
        report_handled_error(f"{_BATCH_ENDPOINT} verify_failed place={analysis.place_name} url={url}", code, exc)
        return _failed(url, code)

    if not verified.exists or not verified.candidates:
        return _not_found(url)

    # 후보는 적합도 순이라 첫 번째를 쓴다(시트: 동명 장소면 1곳만).
    # 장소를 확정하는 값(이름·주소·좌표·영업시간·행사 기간)은 검증 결과를,
    # 영상에서만 알 수 있는 값(카테고리·신뢰도·요약)은 분석 결과를 쓴다.
    # 행사 기간은 verify_place 가 이미 "영상 값 우선, 비면 검색 값"으로 합쳤다.
    place = verified.candidates[0]

    # 배포 회고: business_hours 필수. 영업시간을 못 찾은 장소는 내보내지 않는다.
    # 영업시간이 있는 다음 후보로 넘어가지 않는 건, 후보 순서가 곧 "영상 속
    # 가게일 가능성" 순이라 영업시간 때문에 다른 가게를 고르면 안 되기 때문이다.
    # 임시 결정이다 — 실측에서 success 의 7~24%(공원·사찰, 검색에 영업시간이 없는
    # 맛집)가 이 규칙으로 빠진다. 영업시간 추정(주변 최빈값)을 붙인 뒤 다시 본다.
    if place.business_hours is None:
        return _not_found(url)
    return VideoAnalysisResult(
        video_url=url,
        status="success",
        place_name=place.place_name,
        address=place.address,
        # 검색 결과가 건물명·층을 더 정확히 알고, 없을 때만 영상에서 본 위치를 쓴다.
        location_detail=place.location_detail or analysis.location_detail,
        category=analysis.category,
        business_hours=place.business_hours,
        event_start_date=place.event_start_date,
        event_end_date=place.event_end_date,
        event_period_unresolved=place.event_period_unresolved,
        place_type="이벤트" if analysis.category in EVENT_CATEGORIES else "장소",
        lat=place.lat,
        lng=place.lng,
        confidence=analysis.confidence,
        summary=analysis.summary,
    )


async def _finish_video(
    url: str, outcome: list[VideoAnalysis] | VideoAnalysisResult
) -> list[VideoAnalysisResult]:
    """영상 하나의 장소를 모두 검증해 응답 항목들로 만든다.

    - 장소 하나라도 failed(처리 오류)면 성공한 장소까지 버리고 영상당 failed
      한 항목만 낸다. 백엔드는 그 영상을 통째로 다시 요청하고, 앞서 저장한
      장소가 없으니 재처리해도 중복이 생기지 않는다.
    - 장소 단위 not_found 는 내보내지 않는다 — 다시 해도 확인되지 않는 경우
      (종료된 팝업 등)라 failed 로 올리면 재처리가 끝나지 않는다. 확인된
      장소만 낸다.
    - 확인된 장소가 하나도 없으면 영상당 not_found 한 항목.
    """
    if isinstance(outcome, VideoAnalysisResult):
        return [outcome]
    verified = await asyncio.gather(*(_verify(url, place) for place in outcome))
    failed = [r for r in verified if r.status == "failed"]
    if failed:
        # 원인이 여럿이어도 한 항목에 하나만 담을 수 있어 처음 실패한 장소의 코드를 쓴다.
        return [_failed(url, failed[0].error)]
    succeeded = [r for r in verified if r.status == "success"]
    return succeeded or [_not_found(url)]


@traceable(name="analyze-videos:group", run_type="chain")
async def _process_group(urls: list[str]) -> list[VideoAnalysisResult]:
    """묶음 하나를 분석하고, 끝나는 대로 그 묶음의 검증을 시작한다.

    분석 슬롯은 분석 동안만 잡는다 — 검증까지 잡고 있으면 다음 묶음 분석이
    검증 시간(20~28초)만큼 밀린다. 묶음이 끝나는 대로 검증으로 넘어가므로
    먼저 끝난 묶음이 나머지 묶음의 분석을 기다리지 않는다. 결과는 영상 순서를
    지키고, 같은 영상의 장소는 연속으로 놓는다.
    """
    async with _BATCH_SLOTS:
        analyzed = await _analyze_group(urls)

    per_video = await asyncio.gather(*(_finish_video(u, o) for u, o in zip(urls, analyzed)))
    return [item for items in per_video for item in items]


@traceable(name="analyze-videos", run_type="chain")
async def analyze_videos(req: AnalyzeVideosRequest) -> AnalyzeVideosData:
    size = settings.ANALYZE_VIDEO_BATCH_SIZE
    urls = req.video_urls
    groups = [urls[i:i + size] for i in range(0, len(urls), size)]

    # _process_group 이 예외를 다 잡지만, 예상 못 한 버그 하나가 다른 묶음까지
    # 날리지 않도록 한 번 더 격리한다(스프린트 d2-4).
    outcomes = await asyncio.gather(*(_process_group(g) for g in groups), return_exceptions=True)

    results: list[VideoAnalysisResult] = []
    for group, outcome in zip(groups, outcomes):
        if isinstance(outcome, BaseException):
            code = to_domain_error(outcome).error_code
            results.extend(_failed(u, code) for u in group)
        else:
            results.extend(outcome)

    return AnalyzeVideosData(results=results)
