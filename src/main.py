from edge_equation.engine.pipeline import EnginePipeline
from edge_equation.utils.logging import get_logger

logger = get_logger("edge-equation")


def main() -> None:
    logger.info("Starting Edge Equation v1 engine...")
    pipeline = EnginePipeline()
    result = pipeline.run()
    logger.info(f"Engine result: {result}")


if __name__ == "__main__":
    main()
