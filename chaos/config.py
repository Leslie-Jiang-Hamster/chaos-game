from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ModelConfig:
    model: str
    model_name: str
    api_key: str
    base_url: str = "https://ark.cn-beijing.volces.com/api/v3"

    @property
    def display_name(self) -> str:
        return self.model_name.replace("-", " ").strip() or self.model


def load_model_config(path: str | Path) -> ModelConfig:
    values: dict[str, str] = {}
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip("'\"")

    model = values.get("model", "")
    api_key = values.get("apikey", "")
    if not model.startswith("ep-"):
        raise ValueError("key.yaml 中的 model 必须填写火山引擎 endpoint ID（ep- 开头）。")
    if not api_key:
        raise ValueError("key.yaml 中缺少 apikey。")

    return ModelConfig(
        model=model,
        model_name=values.get("model_name", model),
        api_key=api_key,
    )
