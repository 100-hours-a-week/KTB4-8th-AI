#프롬프트 YAML 로더
from pathlib import Path

import yaml

_PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str, **kwargs) -> str:
    template = yaml.safe_load(_PROMPTS_DIR.joinpath(f"{name}.yaml").read_text())["template"]
    return template.format(**kwargs)
