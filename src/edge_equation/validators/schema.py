from dataclasses import dataclass
from typing import List


@dataclass
class ValidationResult:
    ok: bool
    errors: List[str]
