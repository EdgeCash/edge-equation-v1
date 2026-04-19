from .sources import IngestionSource, IngestionResult

class DemoSource(IngestionSource):
    name = "demo"

    async def fetch(self) -> IngestionResult:
        return IngestionResult(source=self.name, payload={"demo": True})
