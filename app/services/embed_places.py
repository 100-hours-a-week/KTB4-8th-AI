"""장소 요약 임베딩 저장 서비스."""

import asyncio
import time

from app.core.config import settings
from app.core.exceptions import InternalServerError
from app.core.logging import log_llm
from app.models.factory import to_domain_error
from app.retrieval.chroma_store import upsert_places
from app.retrieval.embedder import get_embedder
from app.schemas.embed_places import EmbedPlacesData, EmbedPlacesRequest, PlaceToEmbed

_ENDPOINT = "embed-places"


def _prepare(places: list[PlaceToEmbed]) -> list[PlaceToEmbed]:
    """임베딩할 수 없는 항목을 거르고, 같은 place_id 는 마지막 것만 남긴다.

    빈 요약은 벡터를 만들 수 없어 제외한다. 한 요청 안에 같은 id 가 두 번
    오면 Chroma 가 upsert 를 거부하므로, 나중 값이 최신이라 보고 그것만 쓴다.
    제외된 만큼 embedded_count 가 줄어든다 — 백엔드는 요청 수와 비교해
    누락을 알 수 있다.
    """
    latest: dict[str, PlaceToEmbed] = {}
    for place in places:
        if place.summary.strip():
            latest[place.place_id] = place
    return list(latest.values())


async def embed_places(req: EmbedPlacesRequest) -> EmbedPlacesData:
    places = _prepare(req.places)
    if not places:
        return EmbedPlacesData(embedded_count=0)

    started = time.perf_counter()
    try:
        # embed_query 가 아니라 embed_documents — 저장용 벡터다(embedder.py 참고).
        vectors = await asyncio.wait_for(
            get_embedder().aembed_documents([p.summary for p in places]),
            timeout=settings.TIMEOUT_EMBED_PLACES,
        )
    except Exception as exc:
        log_llm(
            _ENDPOINT,
            settings.EMBEDDING_MODEL,
            time.perf_counter() - started,
            success=False,
            detail=str(exc),
        )
        raise to_domain_error(exc) from exc

    latency = time.perf_counter() - started

    try:
        # Chroma 클라이언트는 동기라 이벤트 루프를 막지 않도록 스레드로 뺀다.
        await asyncio.to_thread(
            upsert_places,
            ids=[p.place_id for p in places],
            embeddings=vectors,
            summaries=[p.summary for p in places],
            place_names=[p.place_name for p in places],
        )
    except Exception as exc:
        raise InternalServerError(f"임베딩 저장 실패: {exc}") from exc

    log_llm(_ENDPOINT, settings.EMBEDDING_MODEL, latency, success=True)

    # 저장이 upsert 라 같은 요청을 다시 보내도 결과가 같다. 실패 시 백엔드가
    # 그대로 재호출하면 된다.
    return EmbedPlacesData(embedded_count=len(places))
