from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Install the bundled appointment blocker playbook atomically.")
    parser.add_argument("--target", default="config/precision_qa_playbook.json")
    args = parser.parse_args()

    source = Path(__file__).parents[1] / "app" / "policies" / "precision_qa_playbook.json"
    target = Path(args.target)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if payload.get("version") != 4 or len(payload.get("items") or []) != 104:
        raise RuntimeError("bundled appointment blocker playbook contract is invalid")

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_bytes() != source.read_bytes():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = target.with_name(f"{target.name}.bak.{stamp}")
        shutil.copy2(target, backup)
        print(f"backed up existing config to {backup}")

    temporary = target.with_suffix(f"{target.suffix}.tmp")
    shutil.copyfile(source, temporary)
    temporary.replace(target)
    print(f"installed {len(payload['items'])} appointment blocker items to {target}")


if __name__ == "__main__":
    main()
