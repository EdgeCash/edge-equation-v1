from .registry import BaseModelRunner

class DemoModel(BaseModelRunner):
    name = "demo-model"

    def run(self, data) -> dict:
        return {"model": self.name, "ok": True}
