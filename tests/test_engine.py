from edge_equation.engine.pipeline import EnginePipeline


def test_pipeline_runs():
    pipeline = EnginePipeline()
    result = pipeline.run()
    assert result["status"] == "ok"
