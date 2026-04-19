from typing import Dict, Type

class BaseModelRunner:
    name: str = "base"

    def run(self, data) -> dict:
        raise NotImplementedError

class ModelRegistry:
    def __init__(self) -> None:
        self._models: Dict[str, Type[BaseModelRunner]] = {}

    def register(self, model: Type[BaseModelRunner]) -> None:
        self._models[model.name] = model

    def get(self, name: str) -> Type[BaseModelRunner]:
        return self._models[name]
