#프롬프트 YAML 로더
from pathlib import Path

import yaml
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

_PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str, **kwargs) -> list[BaseMessage]:
    # system 은 지시, user 는 외부에서 온 데이터(사용자 발화·영상·검색 결과)다.
    # 둘을 메시지로 나눠야 데이터 속 문구를 모델이 지시로 받지 않는다(프롬프트 인젝션).
    prompt = yaml.safe_load(_PROMPTS_DIR.joinpath(f"{name}.yaml").read_text())
    return [
        SystemMessage(content=prompt["system"].format(**kwargs)),
        HumanMessage(content=prompt["user"].format(**kwargs)),
    ]
