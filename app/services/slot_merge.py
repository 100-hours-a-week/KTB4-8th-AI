#슬롯 병합 로직
from app.schemas.extract import Slots


def merge_slots(prev: Slots, new: Slots) -> Slots:
    merged = prev.model_dump()
    for field, value in new.model_dump().items():
        if value is not None:
            merged[field] = value
    return Slots(**merged)
