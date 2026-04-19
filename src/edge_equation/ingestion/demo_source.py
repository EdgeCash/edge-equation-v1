from .sources import IngestionSource, IngestionResult


class DemoSource(IngestionSource):
    name = "demo"

    async def fetch(self) -> IngestionResult:
        # Placeholder payload
        return IngestionResult(source=self.name, payload={"demo": True})
