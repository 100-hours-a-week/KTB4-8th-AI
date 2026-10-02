#슬롯 병합 로직
from app.schemas.extract import Slots


# category 도 덮어쓰기다. 추가·철회는 프롬프트가 이전 목록을 보고 최종 배열을
# 적어 오므로(extract.yaml 규칙 2-1) 여기서 합치면 "카페 말고"가 반영되지 않는다.
# [] 는 None 이 아니라 "상관없음"으로 덮어쓴다.
def merge_slots(prev: Slots, new: Slots) -> Slots:
    merged = prev.model_dump()
    for field, value in new.model_dump().items():
        if value is not None:
            merged[field] = value
    return Slots(**merged)
