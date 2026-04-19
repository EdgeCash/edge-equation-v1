import argparse
from edge_equation.engine.pipeline import EnginePipeline
from edge_equation.engine.modes import PipelineMode
from edge_equation.utils.logging import get_logger

logger = get_logger("edge-equation")

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", type=str, default="daily")
    args = parser.parse_args()

    mode = PipelineMode(args.mode)

    logger.info(f"Running Edge Equation engine in mode: {mode.value}")

    pipeline = EnginePipeline()
    result = pipeline.run()

    logger.info(f"Engine result: {result}")

if __name__ == "__main__":
    main()
