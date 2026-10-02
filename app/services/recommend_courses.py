"""코스 추천 서비스 (API 시트 "코스 추천", docs/4 3-2, IA_SRS AI-009~012).

흐름: 후보 압축(취향 벡터 + 벡터 검색) → 재순위(LLM) → 코스 조합(LLM)
      → 방문 순서·이동시간·도착 시각 계산 → 외출 가능 시간 검증(코드).

챗봇 화면의 동기 경로라 전체 15초 deadline 안에서 구간마다 예산을 두고
(docs/6 5-3), 예산을 넘기거나 실패한 구간은 LLM 을 쓰지 않는 폴백으로 넘긴다
(docs/6 5-6):
  - 후보 압축 실패 → 백엔드가 준 순서 그대로
  - 재순위 실패    → 압축 단계의 유사도 순서 그대로
  - 코스 조합 실패 → 거리 기준 규칙 조합 + 템플릿 제목
지도 API 장애(MapAPIError)만은 폴백하지 않고 map_api_error(502)로 올린다 —
verify-place 와 같은 기준이고, API 시트에 이 엔드포인트의 502 가 명시돼 있다.

영업시간·휴무일 판단은 LLM 이 한다(재순위 프롬프트). business_hours 가 자유
문자열이라 코드로 파싱할 수 없다(docs/1 2-6).
"""

import asyncio
import math
import time
from collections.abc import Awaitable

from pydantic import BaseModel

from app.core.config import settings
from app.core.exceptions import AIServerError, LLMInvalidResponseError, LLMTimeoutError
from app.core.logging import log_llm, logger
from app.integrations.map_client import driving_minutes
from app.models.factory import get_llm, to_domain_error
from app.prompts.loader import load_prompt
from app.retrieval.chroma_store import get_embeddings, search_places
from app.retrieval.embedder import get_embedder
from app.schemas.recommend_courses import (
    ComposeLLM,
    Course,
    RecommendCandidate,
    RecommendCoursesData,
    RecommendCoursesRequest,
    RerankLLM,
)
from app.services import course_builder as cb

_ENDPOINT = "recommend-courses"

# 이번 대화 조건과 장기 취향을 섞는 비율. 조건이 우선이다 — "오늘은 조용한 데"
# 라고 했는데 평소 취향인 핫플이 앞서면 요청을 무시한 추천이 된다.
_QUERY_WEIGHT = 0.7

# 전체 deadline 에서 직렬화·네트워크 몫으로 남겨두는 시간(docs/6 5-3 "여유")
_SAFETY_SECONDS = 1.0
# 조합 단계가 지도 API 몫으로 남겨두는 최소 시간. 모자라면 추정치를 쓰므로 짧아도 된다.
_MAP_RESERVE_SECONDS = 1.0


class _Deadline:
    """요청 하나의 남은 시간. 구간 예산을 고정으로 나누지 않고 여기서 떼어 쓴다."""

    def __init__(self, seconds: float):
        self._until = time.monotonic() + seconds

    def left(self) -> float:
        return self._until - time.monotonic()


# ── 후보 압축 ──────────────────────────────────────────────────────────


def _normalize(v: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v] if norm else v


def _mean(vectors: list[list[float]]) -> list[float]:
    return [sum(xs) / len(vectors) for xs in zip(*vectors)]


def _combine(query_vec: list[float] | None, taste_vec: list[float] | None) -> list[float] | None:
    if query_vec is None or taste_vec is None:
        return query_vec or taste_vec
    q, t = _normalize(query_vec), _normalize(taste_vec)
    return [_QUERY_WEIGHT * a + (1 - _QUERY_WEIGHT) * b for a, b in zip(q, t)]


async def _search(req: RecommendCoursesRequest, candidates: list[RecommendCandidate]) -> list[RecommendCandidate]:
    # 취향 벡터는 저장하지 않고 매번 계산한다 — 장소를 지우면 다음 추천에 바로
    # 반영된다(docs/5 3-3). 이력이 아직 임베딩되지 않았으면 조용히 빠진다.
    history_ids = [h.place_id for h in req.history_place_ids]
    history_vecs = await asyncio.to_thread(get_embeddings, history_ids)
    taste_vec = _mean(history_vecs) if history_vecs else None

    # 검색용은 embed_query — 저장용 embed_documents 와 벡터가 다르다(embedder.py).
    query_vec = await get_embedder().aembed_query(req.query) if req.query.strip() else None

    vector = _combine(query_vec, taste_vec)
    if vector is None:
        return candidates

    by_id = {c.place_id: c for c in candidates}
    found = await asyncio.to_thread(
        search_places, vector, list(by_id), min(settings.RECOMMEND_TOP_K, len(by_id))
    )
    ranked = [by_id[pid] for pid in found if pid in by_id]

    # 아직 임베딩되지 않은 후보(저장 직후 등)는 검색에 안 잡힌다. 버리지 않고
    # 뒤에 붙여 LLM 이 판단하게 한다 — 남는 자리가 있을 때만.
    missing = [c for c in candidates if c.place_id not in set(found)]
    return (ranked + missing)[: settings.RECOMMEND_TOP_K]


async def _compress(
    req: RecommendCoursesRequest, candidates: list[RecommendCandidate], deadline: _Deadline
) -> list[RecommendCandidate]:
    budget = min(settings.RECOMMEND_BUDGET_RETRIEVAL, deadline.left())
    try:
        return await asyncio.wait_for(_search(req, candidates), timeout=budget)
    except Exception as exc:
        logger.warning("%s 후보 압축 실패, 요청 순서로 대체: %r", _ENDPOINT, exc)
        return candidates[: settings.RECOMMEND_TOP_K]


# ── LLM 호출 ───────────────────────────────────────────────────────────


def _or_unknown(value) -> str:
    return str(value) if value else "모름"


def _categories(req: RecommendCoursesRequest) -> str:
    """프롬프트·템플릿 제목용. 빈 목록([])은 "상관없음"이라 모름과 같게 다룬다."""
    return ", ".join(req.category) if req.category else ""


async def _structured(stage: str, prompt: str, schema: type[BaseModel], budget: float) -> BaseModel:
    # factory 의 공용 인스턴스는 그대로 두고 이 엔드포인트에서만 thinking 을 줄인다.
    llm = get_llm().model_copy(update={"thinking_budget": settings.RECOMMEND_THINKING_BUDGET})
    chain = llm.with_structured_output(schema, method="json_schema", include_raw=True)
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(chain.ainvoke(prompt), timeout=budget)
    except Exception as exc:
        log_llm(f"{_ENDPOINT}:{stage}", settings.GOOGLE_MODEL,
                time.perf_counter() - started, success=False, detail=repr(exc))
        raise
    latency = time.perf_counter() - started

    if result["parsing_error"] is not None:
        detail = str(result["parsing_error"])
        log_llm(f"{_ENDPOINT}:{stage}", settings.GOOGLE_MODEL, latency, success=False, detail=detail)
        raise LLMInvalidResponseError(detail)

    log_llm(f"{_ENDPOINT}:{stage}", settings.GOOGLE_MODEL, latency, success=True)
    return result["parsed"]


async def _rerank(
    req: RecommendCoursesRequest, candidates: list[RecommendCandidate], deadline: _Deadline
) -> list[RecommendCandidate]:
    # 조합 단계가 최소한 한 번은 돌 수 있게 그 몫을 남기고 쓴다.
    budget = min(
        settings.RECOMMEND_BUDGET_RERANK,
        deadline.left() - settings.RECOMMEND_MIN_LLM_SECONDS - _MAP_RESERVE_SECONDS,
    )
    if budget < settings.RECOMMEND_MIN_LLM_SECONDS:
        logger.warning("%s 남은 시간 %.1fs, 재순위 생략", _ENDPOINT, deadline.left())
        return candidates

    prompt = load_prompt(
        "recommend_rerank",
        query=req.query or "없음",
        category=_categories(req) or "모름",
        datetime=_or_unknown(req.datetime),
        candidates="\n".join(
            f"- {c.place_id} | {c.place_name} | {c.business_hours} | {c.summary}" for c in candidates
        ),
    )
    try:
        parsed: RerankLLM = await _structured("rerank", prompt, RerankLLM, budget)
    except Exception as exc:
        logger.warning("%s 재순위 실패, 유사도 순서로 대체: %r", _ENDPOINT, exc)
        return candidates

    # 모델이 낸 id 가 후보에 실제로 있는지 확인하고, 없거나 중복된 건 버린다
    # (IA_SRS AI-010). 빈 결과는 "영업 중이고 조건에 맞는 곳이 없다"는 판단이다.
    by_id = {c.place_id: c for c in candidates}
    seen: set[str] = set()
    ranked = []
    for pid in parsed.place_ids:
        if pid in by_id and pid not in seen:
            seen.add(pid)
            ranked.append(by_id[pid])
    return ranked


async def _compose(
    req: RecommendCoursesRequest, ranked: list[RecommendCandidate], deadline: _Deadline
) -> list[tuple[str, list[cb.Pick]]]:
    budget = deadline.left() - _MAP_RESERVE_SECONDS
    if budget < settings.RECOMMEND_MIN_LLM_SECONDS:
        # 재시도하듯 시간을 끝까지 채우면 사용자가 떠난 뒤에 응답이 간다(docs/6 5-3).
        logger.warning("%s 남은 시간 %.1fs, 규칙 기반 조합으로 대체", _ENDPOINT, deadline.left())
        return _rule_based(req, ranked)

    prompt = load_prompt(
        "recommend_compose",
        query=req.query or "없음",
        category=_categories(req) or "모름",
        datetime=_or_unknown(req.datetime),
        available_time=f"{req.available_time}분" if req.available_time else "제한 없음",
        max_courses=cb.MAX_COURSES,
        max_places=cb.MAX_PLACES_PER_COURSE,
        candidates="\n".join(
            f"- {c.place_id} | {c.place_name} | {c.business_hours} | {c.summary} | ({c.lat:.5f}, {c.lng:.5f})"
            for c in ranked
        ),
    )
    try:
        parsed: ComposeLLM = await _structured("compose", prompt, ComposeLLM, budget)
    except Exception as exc:
        logger.warning("%s 코스 조합 실패, 규칙 기반 조합으로 대체: %r", _ENDPOINT, exc)
        return _rule_based(req, ranked)

    # 개수는 여기서 자르지 않는다 — 시간 초과로 잘라낸 뒤 서로 같아진 코스를
    # 빼고 나서 3개를 채워야 하므로, 여분은 _recommend 끝에서 자른다.
    by_id = {c.place_id: c for c in ranked}
    drafts = []
    for course in parsed.courses:
        picks, seen = [], set()
        for p in course.places:
            if p.place_id in by_id and p.place_id not in seen:
                seen.add(p.place_id)
                stay = p.stay_minutes if p.stay_minutes > 0 else cb.default_stay(req.category)
                picks.append(cb.Pick(by_id[p.place_id], stay, p.reason))
        if picks:
            drafts.append((course.title, picks[: cb.MAX_PLACES_PER_COURSE]))

    # 모델이 유효한 장소를 하나도 못 냈으면 조합이 실패한 것이다.
    return drafts or _rule_based(req, ranked)


def _rule_based(req: RecommendCoursesRequest, ranked: list[RecommendCandidate]) -> list[tuple[str, list[cb.Pick]]]:
    stay = cb.default_stay(req.category)
    groups = cb.rule_based_groups(ranked, stay, _available_minutes(req))
    return [
        (
            cb.template_title(group[0].place_name, req.query, _categories(req)),
            [cb.Pick(c, stay, "요청 조건과 유사도가 높은 장소") for c in group],
        )
        for group in groups
    ]


# ── 이동 시간 ──────────────────────────────────────────────────────────


async def _travel_times(
    legs: set[tuple[cb.LatLng, cb.LatLng]], deadline: _Deadline
) -> dict[tuple[cb.LatLng, cb.LatLng], int]:
    """구간별 이동 시간(분). 먼 구간만 지도 API 를 병렬로 부른다.

    예산(최대 3초, 남은 시간이 적으면 그만큼) 안에 안 끝난 구간은 추정치를 쓴다 — 기다리다 전체 deadline 을
    넘기는 것보다 낫다(docs/6 5-3). 지도 API 장애는 그대로 올린다.
    """
    times = {leg: cb.estimate_minutes(*leg) for leg in legs}
    remote = [leg for leg in legs if cb.needs_map_api(*leg)]
    budget = min(settings.RECOMMEND_BUDGET_MAP, deadline.left())
    if not remote or budget <= 0:
        return times

    tasks = {asyncio.create_task(driving_minutes(*leg)): leg for leg in remote}
    done, pending = await asyncio.wait(tasks, timeout=budget)
    for task in pending:
        task.cancel()
    if pending:
        logger.warning("%s 지도 API %d/%d 구간 시간 초과, 추정치 사용", _ENDPOINT, len(pending), len(tasks))

    for task in done:
        minutes = task.result()  # MapAPIError 는 여기서 그대로 올라간다
        if minutes is not None:
            times[tasks[task]] = minutes
    return times


# ── 조립 ───────────────────────────────────────────────────────────────


def _available_minutes(req: RecommendCoursesRequest) -> int | None:
    return int(req.available_time) if req.available_time else None


def _origin(req: RecommendCoursesRequest) -> cb.LatLng | None:
    return (req.origin.lat, req.origin.lng) if req.origin else None


async def _recommend(req: RecommendCoursesRequest) -> RecommendCoursesData:
    # 같은 place_id 가 두 번 오면 앞의 것만 쓴다.
    candidates = list({c.place_id: c for c in reversed(req.candidates)}.values())[::-1]
    if not candidates:
        return RecommendCoursesData(courses=[])

    deadline = _Deadline(settings.TIMEOUT_RECOMMEND_COURSES - _SAFETY_SECONDS)
    compressed = await _compress(req, candidates, deadline)
    ranked = await _rerank(req, compressed, deadline)
    if not ranked:
        return RecommendCoursesData(courses=[])

    drafts = await _compose(req, ranked, deadline)

    origin = _origin(req)
    ordered = [(title, cb.order_by_distance(picks, origin)) for title, picks in drafts]
    course_legs = [cb.legs(picks, origin) for _, picks in ordered]
    times = await _travel_times({leg for legs in course_legs for leg in legs if leg is not None}, deadline)

    courses: list[Course] = []
    seen_sets: set[frozenset[str]] = set()
    for (title, picks), legs in zip(ordered, course_legs):
        travel = [0 if leg is None else times[leg] for leg in legs]
        course = cb.build_course(title, picks, travel, req.datetime, _available_minutes(req))
        if course is None:
            continue
        # 시간 초과로 뒤를 잘라내다 보면 코스끼리 같아질 수 있고, 후보가 적으면
        # 모델이 앞 코스의 일부만 떼어 코스 수를 채운다. 이미 고른 코스에 다
        # 들어 있는 코스는 새 선택지가 아니라 뺀다.
        key = frozenset(p.place_id for p in course.places)
        if any(key <= seen for seen in seen_sets):
            continue
        seen_sets.add(key)
        courses.append(course)

    return RecommendCoursesData(courses=courses[: cb.MAX_COURSES])


async def recommend_courses(req: RecommendCoursesRequest) -> RecommendCoursesData:
    try:
        return await asyncio.wait_for(_recommend(req), timeout=settings.TIMEOUT_RECOMMEND_COURSES)
    except asyncio.TimeoutError as exc:
        raise LLMTimeoutError("코스 추천이 전체 제한 시간 내에 끝나지 않았습니다") from exc
    except AIServerError:
        raise
    except Exception as exc:
        raise to_domain_error(exc) from exc


# ── 중단 ───────────────────────────────────────────────────────────────

# 진행 중인 요청. 임베디드 Chroma 때문에 프로세스가 하나라(chroma_store.py)
# 메모리에 둬도 된다 — 워커를 늘리면 Redis 등으로 옮겨야 한다.
_running: dict[str, asyncio.Task] = {}


async def run_cancellable(request_id: str | None, work: Awaitable[RecommendCoursesData]) -> RecommendCoursesData | None:
    """중단 API 로 취소할 수 있게 실행한다. 중단되면 None.

    API 시트의 요청 body 에는 request_id 가 없어서, 백엔드가 보낸
    X-Request-Id 헤더로 요청을 식별한다. 헤더가 없으면 중단할 수 없다.
    """
    if request_id is None:
        return await work

    task = asyncio.ensure_future(work)
    _running[request_id] = task
    try:
        return await task
    except asyncio.CancelledError:
        # 이 코루틴 자체가 취소된 거면(클라이언트 끊김 등) 그대로 올린다.
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise
        logger.info("%s request_id=%s 중단됨", _ENDPOINT, request_id)
        return None
    finally:
        if _running.get(request_id) is task:
            del _running[request_id]


def cancel(request_id: str) -> None:
    """진행 중이면 취소한다. 이미 끝났거나 모르는 id 여도 성공으로 본다 —
    백엔드가 중단 요청을 여러 번 보내도 결과가 같게(멱등)."""
    task = _running.get(request_id)
    if task is not None and not task.done():
        task.cancel()
