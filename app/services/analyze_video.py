import asyncio
import time
from urllib.parse import urlparse

from langsmith import traceable

from app.core.config import settings
from app.core.exceptions import AIServerError, InvalidRequestError
from app.core.logging import log_llm, report_handled_error
from app.integrations.jev_client import classify_category
from app.models.factory import get_gpu_llm, get_llm, to_domain_error, wake_gpu
from app.prompts.analyze_video import build_extract_messages, build_observe_messages
from app.schemas.analyze_video import (
    AnalyzeVideosData,
    AnalyzeVideosRequest,
    PlaceExtractionLLM,
    VideoAnalysis,
    VideoAnalysisResult,
    VideoObservationBatchLLM,
    VideoObservationLLM,
)
from app.schemas.business_hours import BusinessHours, TimeRange, WeeklyHours
from app.schemas.category import ALWAYS_OPEN_CATEGORIES, EVENT_CATEGORIES
from app.schemas.verify_place import VerifyPlaceData, VerifyPlaceRequest
from app.services.verify_place import verify_place

_BATCH_ENDPOINT = "analyze-videos"
_GPU_ENDPOINT = "analyze-videos:extract"
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

# 영상마다 부르는 Gemma 추출을 동시에 몇 건까지 보낼지. 서버 전체에 하나다(config 주석).
_GPU_SLOTS = asyncio.Semaphore(settings.ANALYZE_VIDEO_GPU_PARALLEL)

# Gemma 추출은 실패하면 1번 더 부른다. 그래도 실패하면 영상 전체가 failed 다(Gemini 대체 없음).
_GPU_ATTEMPTS = 2

# create_task 로 띄운 깨우기 요청을 붙잡아 둔다 — 참조가 없으면 중간에 수거될 수 있다.
_background: set[asyncio.Task] = set()


def _failed(url: str, code: str) -> VideoAnalysisResult:
    return VideoAnalysisResult(video_url=url, status="failed", error=code)


def _always_open() -> BusinessHours:
    # 24시간 영업은 00:00~23:59 다(verify_place_search.yaml 과 같은 약속).
    # 검색으로 확인한 값이 아니라서 estimated 를 켠다.
    day = [TimeRange(open_time="00:00", close_time="23:59")]
    week = WeeklyHours(**{key: day for key in WeeklyHours.model_fields})
    return BusinessHours(weekly_hours=week, estimated=True)


def _not_found(url: str) -> VideoAnalysisResult:
    # 시트 비고대로 video_url·status 말고는 전부 null 이다. 분석 값(category,
    # confidence)도 싣지 않는다 — 장소를 특정하지 못한 결과라 저장 대상이 아니다.
    return VideoAnalysisResult(video_url=url, status="not_found")


async def _observe_group(urls: list[str]) -> list[VideoObservationLLM | VideoAnalysisResult]:
    """영상 최대 10개를 Gemini 호출 1번으로 읽는다 — 판정과 관찰 기록만(11/3 분리).

    url 마다 한 칸씩, 그 영상의 관찰 결과 또는 이미 끝난 실패 결과를 돌려준다.
    실패 단위가 묶음이라는 점에 주의 — 호출이 실패하면 안의 영상이 전부
    실패로 나간다. 모델이 일부 영상을 빠뜨리면 그 영상만 실패로 표시한다.
    """
    chain = get_llm().with_structured_output(
        VideoObservationBatchLLM, method="json_schema", include_raw=True
    )

    started = time.perf_counter()
    try:
        # 슬롯을 잡은 뒤에 재므로 줄 서는 시간은 빠진다 — 뒤 묶음이 대기만으로
        # 타임아웃 나지 않게.
        result = await asyncio.wait_for(
            chain.ainvoke(build_observe_messages(urls)),
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

    # 결과 순서를 믿지 않고 video_index 로 짝짓는다. 영상마다 한 항목이라 같은 번호가
    # 두 번 오면 앞의 것을 쓴다. 번호가 아예 없는 영상은 모델이 빠뜨린 것이다.
    by_index: dict[int, VideoObservationLLM] = {}
    for item in result["parsed"].results:
        by_index.setdefault(item.video_index, item)

    outcomes: list[VideoObservationLLM | VideoAnalysisResult] = []
    for i, url in enumerate(urls, start=1):
        if i in by_index:
            outcomes.append(by_index[i])
        else:
            # 호출은 성공했는데 결과에 이 영상이 없다. 응답에만 담으면 아무 데도
            # 안 남아서 따로 기록한다.
            report_handled_error(f"{_BATCH_ENDPOINT} video_skipped url={url}", "llm_invalid_response")
            outcomes.append(_failed(url, "llm_invalid_response"))
    return outcomes


async def _extract(url: str, observed: VideoObservationLLM) -> list[VideoAnalysis] | VideoAnalysisResult:
    """관찰 기록에서 장소 정보를 뽑는다. 영상 하나에 Gemma 호출 1번(실패하면 1번 더).

    두 번 다 실패하면 영상 failed — 백엔드가 영상을 다시 요청한다(11/3 결정, Gemini 대체 없음).
    GPU 가 꺼져 있거나 설정이 비면 부를 수 없으니 바로 failed 다.
    """
    gpu = get_gpu_llm()
    if gpu is None:
        report_handled_error(f"{_BATCH_ENDPOINT} gpu_unavailable url={url}", "internal_server_error")
        return _failed(url, "internal_server_error")
    # 장소가 7곳쯤인 영상은 출력이 1024 를 넘어 공용 상한으로는 잘린다(config 주석).
    chain = gpu.model_copy(update={"max_tokens": settings.ANALYZE_VIDEO_GPU_MAX_TOKENS}).with_structured_output(
        PlaceExtractionLLM, method="json_schema", include_raw=True
    )
    messages = build_extract_messages(observed.observations, observed.upload_date)
    last: BaseException | None = None
    code = "internal_server_error"
    for _ in range(_GPU_ATTEMPTS):
        started = time.perf_counter()
        try:
            # 자리를 잡은 뒤에 잰다 — 줄 서는 시간으로 타임아웃 나지 않게(Gemini 와 같다).
            async with _GPU_SLOTS:
                result = await asyncio.wait_for(chain.ainvoke(messages), timeout=settings.TIMEOUT_ANALYZE_VIDEO_GPU)
        except Exception as exc:
            log_llm(_GPU_ENDPOINT, settings.GPU_MODEL, time.perf_counter() - started, success=False, detail=str(exc))
            last, code = exc, to_domain_error(exc).error_code
            continue
        if result["parsing_error"] is not None:
            log_llm(_GPU_ENDPOINT, settings.GPU_MODEL, time.perf_counter() - started,
                    success=False, detail=str(result["parsing_error"]))
            last, code = result["parsing_error"], "llm_invalid_response"
            continue
        log_llm(_GPU_ENDPOINT, settings.GPU_MODEL, time.perf_counter() - started, success=True)
        # 장소를 하나도 안 내면 장소명을 비운 항목과 같게 본다(→ not_found).
        return result["parsed"].places or [VideoAnalysis(confidence=0.0)]
    # 응답에만 담기면 아무 데도 안 남아서 따로 기록한다(검증 실패와 같다).
    report_handled_error(f"{_BATCH_ENDPOINT} extract_failed url={url}", code, last)
    return _failed(url, code)


async def _verify_in_slot(req: VerifyPlaceRequest) -> VerifyPlaceData:
    async with _VERIFY_SLOTS:
        return await verify_place(req)


async def _verify(url: str, analysis: VideoAnalysis, observations: list[str]) -> VideoAnalysisResult:
    """분석한 장소 하나를 장소 검증에 넘기고, 카테고리를 골라 최종 결과로 만든다.

    not_found: 장소명을 못 읽었거나(검색하지 않는다), 검증에서 장소를 확인하지
    못했을 때. failed: 검증이나 카테고리 분류 자체가 실패했을 때(LLM·지도 API·Jev 오류).
    한 장소의 실패가 다른 장소에 번지지 않게 여기서 다 잡는다.
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
    # 카테고리(Jev, 0.2초)는 검증(20~28초)과 동시에 부른다 — 검증은 카테고리를 쓰지 않는다.
    # 하나가 실패하면 다른 하나는 취소한다 — 어차피 영상 전체가 failed 라 결과를 못 쓴다.
    verify_task = asyncio.create_task(_verify_in_slot(req))
    category_task = asyncio.create_task(classify_category(analysis.place_name, observations))
    done, _ = await asyncio.wait((verify_task, category_task), return_when=asyncio.FIRST_EXCEPTION)
    failed = next((t for t in done if t.exception() is not None), None)
    if failed is not None:
        verify_task.cancel()
        category_task.cancel()
        exc = failed.exception()
        # verify_place 는 이미 우리 예외(map_api_error·llm_timeout 등)로 바꿔 던진다.
        # to_domain_error 에 다시 넣으면 타임아웃·429 외엔 전부
        # internal_server_error 로 뭉개지므로 우리 예외면 코드를 그대로 쓴다.
        code = exc.error_code if isinstance(exc, AIServerError) else to_domain_error(exc).error_code
        # 영상별 failed 로 응답에만 담기면 아무 데도 안 남는다(검증 타임아웃은
        # LLM 호출이 취소돼 log_llm 도 안 찍힌다). 그래서 따로 기록한다.
        where = "verify_failed" if failed is verify_task else "category_failed"
        report_handled_error(f"{_BATCH_ENDPOINT} {where} place={analysis.place_name} url={url}", code, exc)
        return _failed(url, code)
    verified, category = verify_task.result(), category_task.result()

    if not verified.exists or not verified.candidates:
        return _not_found(url)

    # 후보는 적합도 순이라 첫 번째를 쓴다(시트: 동명 장소면 1곳만).
    # 장소를 확정하는 값(이름·주소·좌표·영업시간·행사 기간)은 검증 결과를,
    # 영상에서만 알 수 있는 값(신뢰도·요약)은 분석 결과를, 카테고리는 Jev 결과를 쓴다.
    # 행사 기간은 verify_place 가 이미 "영상 값 우선, 비면 검색 값"으로 합쳤다.
    place = verified.candidates[0]

    # 배포 회고: business_hours 필수. 영업시간을 못 찾은 장소는 내보내지 않는다.
    # 영업시간이 있는 다음 후보로 넘어가지 않는 건, 후보 순서가 곧 "영상 속
    # 가게일 가능성" 순이라 영업시간 때문에 다른 가게를 고르면 안 되기 때문이다.
    # 임시 결정이다 — 실측에서 success 의 7~24%(공원·사찰, 검색에 영업시간이 없는
    # 맛집)가 이 규칙으로 빠진다. 영업시간 추정(주변 최빈값)을 붙인 뒤 다시 본다.
    # 산책·명소는 예외로 24시간 영업으로 본다(10/26 결정).
    business_hours = place.business_hours
    if business_hours is None and category in ALWAYS_OPEN_CATEGORIES:
        business_hours = _always_open()
    if business_hours is None:
        return _not_found(url)
    return VideoAnalysisResult(
        video_url=url,
        status="success",
        place_name=place.place_name,
        address=place.address,
        # 검색 결과가 건물명·층을 더 정확히 알고, 없을 때만 영상에서 본 위치를 쓴다.
        location_detail=place.location_detail or analysis.location_detail,
        category=category,
        business_hours=business_hours,
        event_start_date=place.event_start_date,
        event_end_date=place.event_end_date,
        event_period_unresolved=place.event_period_unresolved,
        place_type="이벤트" if category in EVENT_CATEGORIES else "장소",
        lat=place.lat,
        lng=place.lng,
        confidence=analysis.confidence,
        summary=analysis.summary,
    )


async def _finish_video(
    url: str, outcome: VideoObservationLLM | VideoAnalysisResult
) -> list[VideoAnalysisResult]:
    """영상 하나의 장소를 뽑고(Gemma) 모두 검증해 응답 항목들로 만든다.

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
    # 롱폼·해외·장소 아님은 예전에도 장소명을 비운 결과 → not_found 였다. 같은 응답이다.
    # 기록이 비었으면 Gemma 를 부르지 않는다 — 뽑을 근거가 없다.
    if outcome.verdict != "장소" or not outcome.observations:
        return [_not_found(url)]
    places = await _extract(url, outcome)
    if isinstance(places, VideoAnalysisResult):
        return [places]
    verified = await asyncio.gather(*(_verify(url, place, outcome.observations) for place in places))
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
        observed = await _observe_group(urls)

    per_video = await asyncio.gather(*(_finish_video(u, o) for u, o in zip(urls, observed)))
    return [item for items in per_video for item in items]


@traceable(name="analyze-videos", run_type="chain")
async def analyze_videos(req: AnalyzeVideosRequest) -> AnalyzeVideosData:
    # Gemini 분석(40~70초) 동안 GPU 콜드 스타트를 먼저 치른다. 기다리지 않는다.
    task = asyncio.create_task(wake_gpu())
    _background.add(task)
    task.add_done_callback(_background.discard)

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
