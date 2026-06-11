from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "ai_paths") not in sys.path:
    sys.path.insert(0, str(ROOT / "ai_paths"))

from app.policies.scene_guidance_retriever import load_scene_guidance  # noqa: E402


REQUIRED_FAMILIES = {
    "SF5_COMPETITOR_COMPARE",
    "SF6_STORE_INQUIRY",
    "SF7_PRICE_ACTIVITY",
    "SF9_APPOINTMENT",
    "SF10_TRUST_BUILD",
    "SF12_AFTER_SALES",
}


def main() -> int:
    scenes = load_scene_guidance()
    errors: list[str] = []
    ids: set[str] = set()
    for scene in scenes:
        if scene.scene_id in ids:
            errors.append(f"duplicate scene_id: {scene.scene_id}")
        ids.add(scene.scene_id)
        if not scene.family:
            errors.append(f"{scene.scene_id}: missing family")
        if scene.status not in {"draft", "shadow", "active"}:
            errors.append(f"{scene.scene_id}: invalid status {scene.status}")
        if not scene.examples:
            errors.append(f"{scene.scene_id}: missing examples")
        if not scene.keywords:
            errors.append(f"{scene.scene_id}: missing keywords")
        if not scene.reply_goal:
            errors.append(f"{scene.scene_id}: missing reply_goal")
        if not scene.hard_constraints:
            errors.append(f"{scene.scene_id}: missing hard_constraints")
        if not scene.soft_guidance:
            errors.append(f"{scene.scene_id}: missing soft_guidance")

    family_counts = Counter(scene.family for scene in scenes)
    missing_families = REQUIRED_FAMILIES - set(family_counts)
    for family in sorted(missing_families):
        errors.append(f"missing required family: {family}")

    print(f"scene_count={len(scenes)}")
    for family, count in sorted(family_counts.items()):
        print(f"{family}={count}")

    if errors:
        print("errors:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("scene_guidance validation ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
