from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def load_business_rules() -> dict[str, Any]:
    path = Path(__file__).with_name("business_rules.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def business_rules_prompt_section() -> str:
    rules = load_business_rules()
    return json.dumps(rules, ensure_ascii=False, separators=(",", ":"))
