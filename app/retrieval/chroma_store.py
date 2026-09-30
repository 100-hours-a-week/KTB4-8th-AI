"""Chroma 임베딩 저장·검색.

임베디드 방식이다 — 별도 서버 없이 이 프로세스가 CHROMA_PATH 에 직접 쓴다.

주의: 한 디렉터리를 여러 프로세스가 동시에 쓰도록 만들어진 게 아니다.
`uvicorn --workers N` 으로 프로세스를 늘리면 같은 경로에 N 개가 붙어 락
충돌이나 인덱스 불일치가 날 수 있다. 워커를 늘려야 하면 Chroma 를 서버
모드로 분리한다(docs/3 "스케일아웃 시 Chroma 분리 구조").
"""

from functools import lru_cache

import chromadb
from chromadb.api.models.Collection import Collection

from app.core.config import settings

_COLLECTION = "places"


@lru_cache(maxsize=1)
def _collection() -> Collection:
    client = chromadb.PersistentClient(path=settings.CHROMA_PATH)
    # 텍스트 임베딩은 방향이 의미를 담으므로 코사인 거리를 쓴다(기본값은 l2).
    # 컬렉션을 처음 만들 때만 적용된다 — 이미 있으면 기존 설정을 따른다.
    return client.get_or_create_collection(
        _COLLECTION, configuration={"hnsw": {"space": "cosine"}}
    )


def upsert_places(
    ids: list[str],
    embeddings: list[list[float]],
    summaries: list[str],
    place_names: list[str],
) -> None:
    """같은 id 가 있으면 덮어쓰고 없으면 넣는다(docs/1 2-6절).

    place_id 가 id 와 metadata 에 둘 다 들어가는 건 의도다 — id 는 upsert
    키, metadata 는 recommend-courses 가 where 필터로 후보를 좁힐 때 쓴다.
    """
    _collection().upsert(
        ids=ids,
        embeddings=embeddings,
        documents=summaries,
        metadatas=[
            {"place_id": pid, "place_name": name} for pid, name in zip(ids, place_names)
        ],
    )


def get_embeddings(place_ids: list[str]) -> list[list[float]]:
    """저장된 장소 벡터를 꺼낸다. 없는 id 는 조용히 빠진다(취향 벡터 계산용)."""
    if not place_ids:
        return []
    result = _collection().get(ids=place_ids, include=["embeddings"])
    embeddings = result.get("embeddings")
    return [list(v) for v in embeddings] if embeddings is not None else []


def search_places(query_vector: list[float], place_ids: list[str], n_results: int) -> list[str]:
    """place_ids 범위 안에서 query_vector 와 가까운 순으로 place_id 를 돌려준다.

    범위를 백엔드가 준 후보로 한정하므로, 삭제된 장소의 벡터가 남아 있어도
    결과에 나오지 않는다(docs/1 2-7). 아직 임베딩되지 않은 후보는 결과에서
    빠지니 호출부가 따로 챙긴다.
    """
    if not place_ids:
        return []
    result = _collection().query(
        query_embeddings=[query_vector],
        where={"place_id": {"$in": place_ids}},
        n_results=n_results,
    )
    return result["ids"][0] if result["ids"] else []

