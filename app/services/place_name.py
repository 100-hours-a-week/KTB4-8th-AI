"""상호 비교용 이름 정리. 코스 추천 중복 제거와 영상 분석 폐점 확인이 같이 쓴다."""


def brand_name(name: str) -> str:
    """'싱싱샐러드 일산정발산점' → '싱싱샐러드'. 띄어 쓴 마지막 말이 '~점'일 때만 떼고 공백을 없앤다."""
    words = name.split()
    if len(words) > 1 and words[-1].endswith("점"):
        words = words[:-1]
    return "".join(words)
