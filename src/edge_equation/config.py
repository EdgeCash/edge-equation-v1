from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class EngineConfig:
    env: str = "local"
    log_level: str = "INFO"


CONFIG = EngineConfig()
