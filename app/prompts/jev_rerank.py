"""Jev 재순위의 지시와 판단 기준. 후보마다 noul 질문을 하나씩 보내 확률을 받는다.

벤치(11/4, ~/keepgo-bench/split_compare/rerank_jev)와 같은 문구다. 문구를 바꾸면
RECOMMEND_JEV_MIN_SCORE 도 다시 재야 한다.
"""

INSTRUCTIONS = "이 장소가 요청에 맞는지 판단한다. 장소 - {place_name}: {summary}"

CRITERIA = {
    "true": "정성 조건에 맞거나, 지정 카테고리이거나, 함께 들르기 자연스럽다",
    "false": "정성 조건에 명백히 반대되거나 요청과 맞지 않는다",
}
