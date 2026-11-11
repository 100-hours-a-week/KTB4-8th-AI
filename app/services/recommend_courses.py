"""코스 추천 서비스 (API 시트 "코스 추천", docs/4 3-2, IA_SRS AI-009~012).

흐름: 후보 압축(취향 벡터 + 벡터 검색) → 영업 필터(코드) → 재순위(Jev) → 코스 조합(LLM)
      → 방문 순서·이동시간·도착 시각 계산 → 외출 가능 시간 검증(코드).

챗봇 화면의 경로라 설정된 전체 deadline 안에서 구간마다 예산을 두고
(docs/6 5-3), 예산을 넘기거나 실패한 구간은 LLM 을 쓰지 않는 폴백으로 넘긴다
(docs/6 5-6):
  - 후보 압축 실패 → 백엔드가 준 순서 그대로
  - 재순위 실패    → 영업 필터를 지난 후보를 유사도 순서 그대로
  - 코스 조합 실패 → 거리 기준 규칙 조합 + 템플릿 제목
지도 API 장애(MapAPIError)만은 폴백하지 않고 map_api_error(502)로 올린다 —
verify-place 와 같은 기준이고, API 시트에 이 엔드포인트의 502 가 명시돼 있다.

재순위 전 영업 판단은 코드가 한다(_is_closed). 조합 프롬프트에는 사람이 읽는 문장으로
바꿔 넣는다(_hours_text).

재순위는 Jev 조건 평가와 별도로 보존한 취향 유사도를 결합한다. 결합 점수를 조합
프롬프트에도 전달하고, 최종 코스 추천도(match_score)는 남은 장소들의 결합 점수 평균이다.
Jev 실패 시에는 취향이 이미 포함된 검색 점수만 쓰며, 취향을 다시 더하지 않는다.
"""

import asyncio
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from app.core.config import settings
from app.core.exceptions import AIServerError, LLMInvalidResponseError, LLMTimeoutError
from app.core.logging import log_llm, logger
from app.integrations.jev_client import score_places
from app.integrations.map_client import driving_minutes
from app.models.factory import get_gpu_llm, get_llm, invoke_gpu_first, to_domain_error
from app.prompts.loader import load_prompt
from app.retrieval.chroma_store import get_embeddings, get_embeddings_by_id, search_places
from app.retrieval.embedder import get_embedder
from app.schemas.business_hours import BusinessHours
from app.schemas.category import CATEGORY_DESCRIPTIONS
from app.schemas.extract import has_visit_time
from app.schemas.recommend_courses import (
    ComposeLLM,
    Course,
    RecommendCandidate,
    RecommendCoursesData,
    RecommendCoursesRequest,
)
from app.services import course_builder as cb
from app.services.place_name import brand_name

_ENDPOINT = "recommend-courses"

# 스트리밍(/v3/recommend-course/stream)이 status 이벤트로 내보내는 단계. 단계를 시작할 때 알린다.
Stage = Literal["retrieving", "reranking", "composing", "routing"]
Report = Callable[[Stage], None]


def _no_report(stage: Stage) -> None:
    """스트리밍이 아닌 요청(/v1)은 단계를 알릴 곳이 없다."""

# 압축 기준 벡터의 재료별 비율. 이번 대화 조건이 우선이다 — "오늘은 조용한 데"
# 라고 했는데 평소 취향인 핫플이 앞서면 요청을 무시한 추천이 된다. 카테고리도
# 요청 조건이라 취향보다 높게 둔다. 없는 재료는 빼고 남은 것끼리 비율을 다시
# 나눈다(query 없이 카테고리·취향만 오면 0.6 / 0.4). 임의 초기값이다 — 벤치로
# 0.4/0.4/0.2 와 비교해 조정한다.
_WEIGHTS = {"query": 0.5, "category": 0.3, "taste": 0.2}

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


def _combine(parts: dict[str, list[float] | None]) -> list[float] | None:
    present = {name: _normalize(v) for name, v in parts.items() if v is not None}
    if not present:
        return None
    total = sum(_WEIGHTS[name] for name in present)
    weighted = [[_WEIGHTS[name] / total * x for x in v] for name, v in present.items()]
    return [sum(xs) for xs in zip(*weighted)]


# 카테고리 설명문은 고정 문장이라 벡터도 바뀌지 않는다. 처음 쓰일 때 임베딩해
# 프로세스 메모리에 둔다. 실패한 카테고리는 넣지 않아 다음 요청에서 다시 부른다.
_category_vecs: dict[str, list[float]] = {}


async def _category_vec(categories: list[str] | None) -> list[float] | None:
    """요청 카테고리 설명문 벡터의 평균. [] 와 null("아무거나")·"기타"만이면 None.

    실패하면 카테고리 없이 진행한다 — 이것 때문에 압축 전체를 버리면 추천도가
    전부 null 이 된다.
    """
    names = [c for c in dict.fromkeys(categories or []) if c in CATEGORY_DESCRIPTIONS]
    if not names:
        return None
    todo = [n for n in names if n not in _category_vecs]
    if todo:
        embedder = get_embedder()
        try:
            vecs = await asyncio.gather(*(embedder.aembed_query(CATEGORY_DESCRIPTIONS[n]) for n in todo))
        except Exception as exc:
            logger.warning("%s 카테고리 벡터 실패, 카테고리 없이 진행: %r", _ENDPOINT, exc)
            return None
        _category_vecs.update(zip(todo, vecs))
    return _mean([_normalize(_category_vecs[n]) for n in names])


async def _query_vec(query: str) -> list[float] | None:
    # 검색용은 embed_query — 저장용 embed_documents 와 벡터가 다르다(embedder.py).
    return await get_embedder().aembed_query(query) if query.strip() else None


def _similarity_score(similarity: float | None) -> float | None:
    """기존 고정 구간으로 유사도를 0~1 에 맞춘다. 확률 보정은 아니다."""
    if similarity is None or not math.isfinite(similarity):
        return None
    lo, hi = settings.RECOMMEND_SCORE_SIM_MIN, settings.RECOMMEND_SCORE_SIM_MAX
    if hi <= lo:
        return None
    return min(1.0, max(0.0, (similarity - lo) / (hi - lo)))


def _cosine(a: list[float], b: list[float]) -> float | None:
    if not a or len(a) != len(b):
        return None
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
    if not norm or not math.isfinite(norm):
        return None
    value = sum(x * y for x, y in zip(a, b)) / norm
    return min(1.0, max(-1.0, value)) if math.isfinite(value) else None


@dataclass
class PlaceScores:
    """단계별 평가를 보존한다. 계산하지 못한 값은 0점과 구분한다."""

    search_similarity: float | None = None
    taste: float | None = None  # 저장 이력만의 유사도를 0~1로 환산한 값
    condition: float | None = None  # Jev noul. 검색 점수와 섞어 조건을 중복 반영하지 않는다.
    combined: float | None = None


Scores = dict[int, PlaceScores]


async def _search(
    req: RecommendCoursesRequest, candidates: list[RecommendCandidate]
) -> tuple[list[RecommendCandidate], Scores]:
    # 취향 벡터는 저장하지 않고 매번 계산한다 — 장소를 지우면 다음 추천에 바로
    # 반영된다(docs/5 3-3). 이력이 아직 임베딩되지 않았으면 조용히 빠진다.
    history_ids = [h.place_id for h in req.history_place_ids]
    history_vecs = await asyncio.to_thread(get_embeddings, history_ids)
    taste_vec = _mean(history_vecs) if history_vecs else None

    # 둘 다 임베딩 API 를 부를 수 있어 동시에 — 걸리는 시간은 느린 쪽 하나만큼.
    query_vec, category_vec = await asyncio.gather(_query_vec(req.query), _category_vec(req.category))

    vector = _combine({"query": query_vec, "category": category_vec, "taste": taste_vec})
    if vector is None:
        return candidates, {}

    by_id = {c.place_id: c for c in candidates}
    found = await asyncio.to_thread(
        search_places, vector, list(by_id), min(settings.RECOMMEND_TOP_K, len(by_id))
    )
    sims = {pid: sim for pid, sim in found if pid in by_id}
    ranked = [by_id[pid] for pid in sims]

    # 아직 임베딩되지 않은 후보(저장 직후 등)는 검색에 안 잡힌다. 버리지 않고
    # 뒤에 붙여 LLM 이 판단하게 한다 — 남는 자리가 있을 때만. 검색 유사도와
    # 취향은 미상이지만, Jev 평가가 성공하면 조건 평가로 추천도를 계산할 수 있다.
    missing = [c for c in candidates if c.place_id not in sims]
    compressed = (ranked + missing)[: settings.RECOMMEND_TOP_K]
    scores = {
        c.place_id: PlaceScores(
            search_similarity=sims.get(c.place_id), combined=_similarity_score(sims.get(c.place_id))
        )
        for c in compressed
    }
    if taste_vec is not None:
        try:
            embeddings = await asyncio.to_thread(get_embeddings_by_id, [c.place_id for c in compressed])
            for pid, vector in embeddings.items():
                if pid in scores:
                    scores[pid].taste = _similarity_score(_cosine(taste_vec, vector))
        except Exception as exc:
            # 개인화 부가 조회만 실패했으면 이미 구한 검색 결과는 유지한다.
            logger.warning("%s 취향 점수 조회 실패, 검색 결과 유지: %r", _ENDPOINT, exc)
    return compressed, scores


async def _compress(
    req: RecommendCoursesRequest, candidates: list[RecommendCandidate], deadline: _Deadline
) -> tuple[list[RecommendCandidate], Scores]:
    budget = min(settings.RECOMMEND_BUDGET_RETRIEVAL, deadline.left())
    try:
        return await asyncio.wait_for(_search(req, candidates), timeout=budget)
    except Exception as exc:
        logger.warning("%s 후보 압축 실패, 요청 순서로 대체: %r", _ENDPOINT, exc)
        return candidates[: settings.RECOMMEND_TOP_K], {}


def _match_score(place_ids: list[int], scores: Scores) -> int | None:
    """최종 코스에 남은 장소들의 결합 점수 평균(0~100). 만족 확률이 아니다."""
    values = [
        scores[pid].combined for pid in place_ids
        if pid in scores and scores[pid].combined is not None
    ]
    if not values:
        return None
    return round(sum(values) / len(values) * 100)


_WEEKDAYS = [("MONDAY", "월"), ("TUESDAY", "화"), ("WEDNESDAY", "수"), ("THURSDAY", "목"),
             ("FRIDAY", "금"), ("SATURDAY", "토"), ("SUNDAY", "일")]


def _ranges_text(ranges) -> str:
    if ranges is None:
        return "모름"
    if not ranges:
        return "휴무"
    return ",".join(f"{r.open_time}-{r.close_time}" for r in ranges)


def _hours_text(hours: BusinessHours) -> str:
    """프롬프트용 영업시간 문장. 객체(JSON)를 그대로 넣으면 후보 줄이 길어져서
    재순위 시간이 늘어난다. 7요일이 같으면 "매일"로 줄인다."""
    days = [_ranges_text(getattr(hours.weekly_hours, key)) for key, _ in _WEEKDAYS]
    if len(set(days)) == 1:
        text = f"매일 {days[0]}"
    else:
        text = " ".join(f"{label} {d}" for (_, label), d in zip(_WEEKDAYS, days))
    if hours.exceptions:
        extra = " ".join(f"{day} {_ranges_text(r)}" for day, r in sorted(hours.exceptions.items()))
        text += f" (임시: {extra})"
    if hours.estimated:
        text += " (추정)"
    return text


def _day_ranges(hours: BusinessHours, day: date):
    if day in hours.exceptions:
        return hours.exceptions[day]
    return getattr(hours.weekly_hours, _WEEKDAYS[day.weekday()][0])


def _minutes(hhmm: str) -> int:
    hour, minute = hhmm.split(":")
    return int(hour) * 60 + int(minute)


def _is_closed(hours: BusinessHours, when: datetime | None) -> bool:
    """방문 일시에 닫혀 있는지. 모르면(방문 일시·그날 영업시간이 없음) 닫지 않은 것으로 본다."""
    if when is None:
        return False
    today = _day_ranges(hours, when.date())
    if not has_visit_time(when):  # 시각을 모르면 휴무일만 뺀다
        return today == []
    if today is None:
        return False
    now = when.hour * 60 + when.minute
    for r in today:
        start, end = _minutes(r.open_time), _minutes(r.close_time)
        if end == 0 or r.close_time == "23:59":  # 자정 마감·24시간 영업
            end = 24 * 60
        if (start <= now < end) if end > start else now >= start:
            return False
    # 전날 시작해 자정을 넘긴 영업
    for r in _day_ranges(hours, when.date() - timedelta(days=1)) or []:
        start, end = _minutes(r.open_time), _minutes(r.close_time)
        if 0 < end < start and now < end:
            return False
    return True


# ── LLM 호출 ───────────────────────────────────────────────────────────


def _or_unknown(value) -> str:
    return str(value) if value else "모름"


def _visit_text(value) -> str:
    """프롬프트용 방문 일시. 00:00 은 "시간 모름" 약속이라 날짜만 알려 준다 —
    그대로 넘기면 모델이 자정 방문으로 보고 영업시간을 판단한다."""
    if value is None:
        return "모름"
    if not has_visit_time(value):
        return f"{value:%Y-%m-%d} (시간 모름)"
    return f"{value:%Y-%m-%d %H:%M}"


def _categories(req: RecommendCoursesRequest) -> str:
    """프롬프트·템플릿 제목용. 빈 목록([])은 "상관없음"이라 모름과 같게 다룬다."""
    return ", ".join(req.category) if req.category else ""


async def _structured(stage: str, prompt: list[BaseMessage], schema: type[BaseModel], budget: float) -> BaseModel:
    # factory 의 공용 인스턴스는 그대로 두고 이 엔드포인트에서만 thinking 을 줄인다.
    llm = get_llm().model_copy(update={"thinking_budget": settings.RECOMMEND_THINKING_BUDGET})
    chain = llm.with_structured_output(schema, method="json_schema", include_raw=True)
    gpu = get_gpu_llm()
    gpu_chain = gpu.with_structured_output(schema, method="json_schema", include_raw=True) if gpu else None
    started = time.perf_counter()
    try:
        result, model = await invoke_gpu_first(
            f"{_ENDPOINT}:{stage}", prompt, gpu_chain, chain,
            total=budget, gpu_timeout=budget - settings.RECOMMEND_GEMINI_RESERVE,
        )
    except Exception as exc:
        log_llm(f"{_ENDPOINT}:{stage}", settings.GOOGLE_MODEL,
                time.perf_counter() - started, success=False, detail=repr(exc))
        raise
    latency = time.perf_counter() - started

    if result["parsing_error"] is not None:
        detail = str(result["parsing_error"])
        log_llm(f"{_ENDPOINT}:{stage}", model, latency, success=False, detail=detail)
        raise LLMInvalidResponseError(detail)

    log_llm(f"{_ENDPOINT}:{stage}", model, latency, success=True)
    return result["parsed"]


async def _rerank(
    req: RecommendCoursesRequest, candidates: list[RecommendCandidate], deadline: _Deadline,
    scores: Scores | None = None,
) -> list[RecommendCandidate]:
    opened = [c for c in candidates if not _is_closed(c.business_hours, req.datetime)]
    if not opened:
        return []

    # 조합 단계가 최소한 한 번은 돌 수 있게 그 몫을 남기고 쓴다.
    budget = min(
        settings.RECOMMEND_BUDGET_RERANK,
        deadline.left() - settings.RECOMMEND_MIN_LLM_SECONDS - _MAP_RESERVE_SECONDS,
    )
    if budget < settings.RECOMMEND_MIN_LLM_SECONDS:
        logger.warning("%s 남은 시간 %.1fs, 재순위 생략", _ENDPOINT, deadline.left())
        return opened

    started = time.perf_counter()
    try:
        condition_scores = await asyncio.wait_for(
            score_places(
                req.query or "없음",
                _categories(req) or "모름",
                [(c.place_id, c.place_name, c.summary) for c in opened],
            ),
            timeout=budget,
        )
        if any(
            not isinstance(condition_scores.get(c.place_id), (int, float))
            or not math.isfinite(condition_scores[c.place_id])
            or not 0 <= condition_scores[c.place_id] <= 1
            for c in opened
        ):
            raise ValueError("Jev 조건 평가가 누락되었거나 0~1 범위 밖입니다")
    except Exception as exc:
        log_llm(f"{_ENDPOINT}:rerank", settings.JEV_MODEL, time.perf_counter() - started, success=False, detail=repr(exc))
        logger.warning("%s 재순위 실패, 유사도 순서로 대체: %r", _ENDPOINT, exc)
        return opened
    log_llm(f"{_ENDPOINT}:rerank", settings.JEV_MODEL, time.perf_counter() - started, success=True)

    scores = scores if scores is not None else {}
    for c in opened:
        item = scores.setdefault(c.place_id, PlaceScores())
        item.condition = condition_scores[c.place_id]
        weight = settings.RECOMMEND_TASTE_WEIGHT if item.taste is not None else 0.0
        item.combined = (
            (1.0 - weight) * item.condition
            + weight * (item.taste if item.taste is not None else 0.0)
        )

    # 조건 미달 여부는 결합 전 Jev 값으로 판단한다. 취향에 맞아도 미달 후보는 살리지 않는다.
    kept = [c for c in opened if condition_scores[c.place_id] >= settings.RECOMMEND_JEV_MIN_SCORE]
    return sorted(kept, key=lambda c: -scores[c.place_id].combined)


async def _compose(
    req: RecommendCoursesRequest, ranked: list[RecommendCandidate], deadline: _Deadline,
    scores: Scores | None = None,
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
        datetime=_visit_text(req.datetime),
        available_time=f"{req.available_time}분" if req.available_time else "제한 없음",
        max_courses=cb.MAX_COURSES,
        max_places=cb.MAX_PLACES_PER_COURSE,
        candidates="\n".join(
            f"- {c.place_id} | {c.place_name} | {_hours_text(c.business_hours)} | {c.summary} | ({c.lat:.5f}, {c.lng:.5f})"
            f" | {_scores_text((scores or {}).get(c.place_id))}"
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
                picks.append(cb.Pick(by_id[p.place_id], stay))
        if picks:
            drafts.append((course.title, picks[: cb.MAX_PLACES_PER_COURSE]))

    # 모델이 유효한 장소를 하나도 못 냈으면 조합이 실패한 것이다.
    return drafts or _rule_based(req, ranked)


def _scores_text(scores: PlaceScores | None) -> str:
    def number(value: float | None) -> str:
        return "모름" if value is None else f"{value:.3f}"

    item = scores or PlaceScores()
    return f"조건평가={number(item.condition)}, 취향점수={number(item.taste)}, 종합점수={number(item.combined)}"


def _rule_based(req: RecommendCoursesRequest, ranked: list[RecommendCandidate]) -> list[tuple[str, list[cb.Pick]]]:
    stay = cb.default_stay(req.category)
    groups = cb.rule_based_groups(ranked, stay, _available_minutes(req))
    return [
        (
            cb.template_title(group[0].place_name, req.query, _categories(req)),
            [cb.Pick(c, stay) for c in group],
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


def _arrivals(course: Course, visit: datetime) -> list[datetime]:
    """코스 장소별 도착 일시. 자정을 넘기면 다음 날로 센다."""
    out, day, last = [], visit.date(), visit.hour * 60 + visit.minute
    for p in course.places:
        h, m = map(int, p.arrival_time.split(":"))
        if h * 60 + m < last:
            day += timedelta(days=1)
        last = h * 60 + m
        out.append(datetime.combine(day, datetime.min.time()).replace(hour=h, minute=m))
    return out


def _build_open(
    title: str,
    picks: list[cb.Pick],
    origin: cb.LatLng | None,
    times: dict[tuple[cb.LatLng, cb.LatLng], int],
    req: RecommendCoursesRequest,
) -> Course | None:
    """코스를 만들고, 도착 시각에 닫혀 있는 장소를 뺀다(11/10, RAG 개선 #4).

    영업 필터(_rerank)는 출발 시각만 본다. 코스 뒤쪽 장소는 도착할 때 닫혀 있을 수 있어서 여기서 다시 본다.
    빼면 도착 시각이 바뀌므로 닫힌 곳이 없어질 때까지 반복한다. 새로 생긴 구간은 지도 API 를 다시 부르지
    않고 추정치를 쓴다(15초 안에 다시 부를 시간이 없다). 2곳 이상이던 코스가 1곳 이하로 줄면 코스로 보지
    않는다. 방문 시각을 모르면(00:00) 도착 시각도 추정이라 보지 않는다.
    """
    dropped = False
    while picks:
        legs = cb.legs(picks, origin)
        travel = [0 if leg is None else times.get(leg) or cb.estimate_minutes(*leg) for leg in legs]
        course = cb.build_course(title, picks, travel, req.datetime, _available_minutes(req))
        if course is None or req.datetime is None or not has_visit_time(req.datetime):
            return course
        hours = {p.candidate.place_id: p.candidate.business_hours for p in picks}
        closed = {
            place.place_id
            for place, at in zip(course.places, _arrivals(course, req.datetime))
            if _is_closed(hours[place.place_id], at)
        }
        if not closed:
            return None if dropped and len(course.places) < 2 else course
        logger.info("%s 도착 때 닫힌 장소 %d곳 제외: %s", _ENDPOINT, len(closed), sorted(closed))
        picks = [p for p in picks if p.candidate.place_id not in closed]
        dropped = True
    return None


async def _recommend(req: RecommendCoursesRequest, report: Report) -> RecommendCoursesData:
    # 같은 place_id 가 두 번 오면 앞의 것만 쓴다.
    candidates = list({c.place_id: c for c in reversed(req.candidates)}.values())[::-1]
    # 같은 가게가 다른 place_id 로 올 때가 있다(summary 만 다르거나 지점명 표기만 다름).
    # 끝의 지점명(~점)과 공백을 뺀 이름, 좌표가 모두 같으면 앞의 것만 쓴다.
    # 좌표는 반올림돼 와서 다른 가게도 같을 수 있어 좌표만으로는 묶지 않는다.
    candidates = list({(brand_name(c.place_name), c.lat, c.lng): c for c in reversed(candidates)}.values())[::-1]
    if not candidates:
        return RecommendCoursesData(courses=[])

    deadline = _Deadline(settings.TIMEOUT_RECOMMEND_COURSES - _SAFETY_SECONDS)
    report("retrieving")
    compressed, scores = await _compress(req, candidates, deadline)
    report("reranking")
    ranked = await _rerank(req, compressed, deadline, scores)
    if not ranked:
        return RecommendCoursesData(courses=[])

    report("composing")
    drafts = await _compose(req, ranked, deadline, scores)

    origin = _origin(req)
    ordered = [(title, cb.order_by_distance(picks, origin)) for title, picks in drafts]
    course_legs = [cb.legs(picks, origin) for _, picks in ordered]
    report("routing")
    times = await _travel_times({leg for legs in course_legs for leg in legs if leg is not None}, deadline)

    courses: list[Course] = []
    seen_sets: set[frozenset[int]] = set()
    built = [_build_open(title, picks, origin, times, req) for title, picks in ordered]
    if drafts and not any(built):
        # 도착 때 닫힌 곳을 빼다 보니 코스가 하나도 남지 않았다. 지도 API 를 다시 부를 시간은
        # 없어서 규칙 기반 조합을 추정 이동 시간으로 만든다.
        built = [_build_open(t, cb.order_by_distance(p, origin), origin, times, req) for t, p in _rule_based(req, ranked)]
    for course in built:
        if course is None:
            continue
        # 시간 초과로 뒤를 잘라내다 보면 코스끼리 같아질 수 있고, 후보가 적으면
        # 모델이 앞 코스의 일부만 떼어 코스 수를 채운다. 이미 고른 코스에 다
        # 들어 있는 코스는 새 선택지가 아니라 뺀다.
        key = frozenset(p.place_id for p in course.places)
        if any(key <= seen for seen in seen_sets):
            continue
        seen_sets.add(key)
        course.match_score = _match_score([p.place_id for p in course.places], scores)
        courses.append(course)

    # 모델 출력 순서 대신, 최종적으로 남은 장소들의 같은 평가 기준으로 코스를 정렬한다.
    courses.sort(
        key=lambda c: (c.match_score is not None, c.match_score if c.match_score is not None else 0),
        reverse=True,
    )
    return RecommendCoursesData(courses=courses[: cb.MAX_COURSES])


async def recommend_courses(req: RecommendCoursesRequest, report: Report = _no_report) -> RecommendCoursesData:
    try:
        return await asyncio.wait_for(_recommend(req, report), timeout=settings.TIMEOUT_RECOMMEND_COURSES)
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


# ── 스트리밍 ───────────────────────────────────────────────────────────

StreamItem = tuple[Literal["status"], Stage] | tuple[Literal["result"], RecommendCoursesData | None]


async def recommend_courses_stream(request_id: str | None, req: RecommendCoursesRequest) -> AsyncIterator[StreamItem]:
    """단계를 시작할 때마다 ("status", 단계), 끝나면 ("result", 결과)를 내준다.

    결과가 None 이면 중단 API 로 중단된 것이다. 실패하면 recommend_courses 와 같은
    우리 예외(AIServerError)를 올린다. 시간 제한·중단은 /v1 과 같은 코드를 그대로 쓴다 —
    작업을 따로 돌리고, 작업이 단계를 알릴 때마다 큐에서 꺼내 내보낸다.
    """
    stages: asyncio.Queue[Stage] = asyncio.Queue()
    work = asyncio.create_task(run_cancellable(request_id, recommend_courses(req, stages.put_nowait)))
    next_stage: asyncio.Task | None = None
    try:
        while not work.done():
            next_stage = asyncio.create_task(stages.get())
            await asyncio.wait({next_stage, work}, return_when=asyncio.FIRST_COMPLETED)
            if next_stage.done():
                yield "status", next_stage.result()
            else:
                next_stage.cancel()
        # 작업이 끝나기 직전에 들어온 단계가 남아 있으면 순서대로 먼저 보낸다.
        while not stages.empty():
            yield "status", stages.get_nowait()
        yield "result", work.result()  # 실패면 여기서 예외가 올라간다
    finally:
        # 연결이 끊겨 이 생성기가 닫히면 따로 돌던 작업도 멈춘다. 안 하면 아무도 받지 않는
        # LLM·지도 호출이 끝까지 돈다. 정상으로 끝났을 땐 이미 끝난 작업이라 아무 일도 없다.
        work.cancel()
        if next_stage is not None:
            next_stage.cancel()
