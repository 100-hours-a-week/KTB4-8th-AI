from fastapi import APIRouter

# prefix 를 두지 않고 엔드포인트마다 전체 경로를 적는다.
# 버전과 무관한 운영용 /health 가 이 라우터 하나에 같이 붙기 때문.
router = APIRouter()

# 인프라 헬스체크용
@router.get("/health")
def health():
    return {"status": "ok"}