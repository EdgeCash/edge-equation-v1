from dataclasses import dataclass
import asyncio
from typing import Any, Dict, List

from edge_equation.ingestion.sources import IngestionSource, IngestionResult
from edge_equation.ingestion.normalizer import NormalizedFrame
from edge_equation.models.registry import ModelRegistry, BaseModelRunner
from edge_equation.posting.formatter import PostPayload
from edge_equation.posting.publisher import Publisher

from edge_equation.ingestion.demo_source import DemoSource
from edge_equation.models.demo_model import DemoModel
from edge_equation.posting.demo_post import build_demo_post

@dataclass
class EnginePipeline:
    """Deterministic multi-stage pipeline."""

    ingestion_sources: List[IngestionSource] = None
    models: List[BaseModelRunner] = None

    def __post_init__(self):
        if self.ingestion_sources is None:
            self.ingestion_sources = [DemoSource()]

        if self.models is None:
            self.models = [DemoModel()]

        self.registry = ModelRegistry()
        for model in self.models:
            self.registry.register(model.__class__)

    async def _run_ingestion(self) -> Dict[str, IngestionResult]:
        results = {}
        for source in self.ingestion_sources:
            results[source.name] = await source.fetch()
        return results

    def _normalize(self, ingestion_results: Dict[str, IngestionResult]) -> NormalizedFrame:
        return NormalizedFrame(data=ingestion_results)

    def _run_models(self, normalized: NormalizedFrame) -> Dict[str, Any]:
        outputs = {}
        for model in self.models:
            outputs[model.name] = model.run(normalized.data)
        return outputs

    def _post(self, model_outputs: Dict[str, Any]) -> None:
        payload: PostPayload = build_demo_post(model_outputs)
        Publisher().publish(payload)

    async def _run_async(self) -> Dict[str, Any]:
        ingestion_results = await self._run_ingestion()
        normalized = self._normalize(ingestion_results)
        model_outputs = self._run_models(normalized)
        self._post(model_outputs)

        return {
            "status": "ok",
            "ingestion": list(ingestion_results.keys()),
            "models": list(model_outputs.keys()),
        }

    def run(self) -> Dict[str, Any]:
        return asyncio.run(self._run_async())
