from dataclasses import dataclass
import asyncio

from edge_equation.ingestion.demo_source import DemoSource
from edge_equation.models.demo_model import DemoModel
from edge_equation.posting.demo_post import build_demo_post
from edge_equation.posting.publisher import Publisher


@dataclass
class EnginePipeline:
    """Top-level orchestration placeholder."""

    async def _run_async(self) -> dict:
        # 1. Ingestion
        source = DemoSource()
        ingestion_result = await source.fetch()

        # 2. Model
        model = DemoModel()
        model_output = model.run(ingestion_result.payload)

        # 3. Posting
        payload = build_demo_post(model_output)
        publisher = Publisher()
        publisher.publish(payload)

        return {"status": "ok", "model_output": model_output}

    def run(self) -> dict:
        return asyncio.run(self._run_async())
