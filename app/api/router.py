from fastapi import APIRouter

# 엔드포인트 5개가 모두 /v1 아래에 있다(docs/1 1절).
router = APIRouter(prefix="/v1")
