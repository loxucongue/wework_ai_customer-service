from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


ACTIVE_INJECTION_THRESHOLD = 0.75


@dataclass(frozen=True)
class SceneGuidance:
    scene_id: str
    family: str
    status: str
    examples: tuple[str, ...]
    keywords: tuple[str, ...]
    reply_goal: str
    hard_constraints: tuple[str, ...]
    soft_guidance: tuple[str, ...]

    def to_prompt_context(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "reply_goal": self.reply_goal,
            "hard_constraints": list(self.hard_constraints),
            "soft_guidance": list(self.soft_guidance),
        }


def retrieve_scene_guidance(
    *,
    family: str,
    user_message: str,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    family = str(family or "").strip()
    text = _normalize(user_message)
    if not family or not text:
        return []

    scored: list[dict[str, Any]] = []
    for scene in load_scene_guidance():
        if scene.family != family or scene.status not in {"shadow", "active"}:
            continue
        score = max(_keyword_score(text, scene.keywords), _example_score(text, scene.examples))
        if score <= 0:
            continue
        scored.append(
            {
                "scene_id": scene.scene_id,
                "family": scene.family,
                "score": round(score, 4),
                "status": scene.status,
                "match_level": _match_level(score),
                "reply_goal": scene.reply_goal,
            }
        )
    return sorted(scored, key=lambda item: float(item["score"]), reverse=True)[: max(1, min(top_k, 5))]


def active_scene_guidance_context(candidates: list[dict[str, Any]], *, top_k: int = 1) -> list[dict[str, Any]]:
    if not candidates:
        return []
    by_id = {scene.scene_id: scene for scene in load_scene_guidance()}
    output: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.get("status") != "active" or float(candidate.get("score") or 0) < ACTIVE_INJECTION_THRESHOLD:
            continue
        scene = by_id.get(str(candidate.get("scene_id") or ""))
        if not scene:
            continue
        context = scene.to_prompt_context()
        context["score"] = candidate.get("score")
        context["match_level"] = candidate.get("match_level")
        output.append(context)
        if len(output) >= max(1, min(top_k, 2)):
            break
    return output


@lru_cache(maxsize=1)
def load_scene_guidance() -> tuple[SceneGuidance, ...]:
    path = Path(__file__).with_name("scene_guidance.jsonl")
    scenes: list[SceneGuidance] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        text = line.strip()
        if not text:
            continue
        raw = json.loads(text)
        scene_id = str(raw.get("scene_id") or "").strip()
        if not scene_id:
            raise ValueError(f"scene_guidance.jsonl:{line_number} missing scene_id")
        if scene_id in seen:
            raise ValueError(f"scene_guidance.jsonl:{line_number} duplicate scene_id {scene_id}")
        seen.add(scene_id)
        scenes.append(
            SceneGuidance(
                scene_id=scene_id,
                family=str(raw.get("family") or "").strip(),
                status=str(raw.get("status") or "shadow").strip(),
                examples=tuple(_clean_list(raw.get("examples"))),
                keywords=tuple(_clean_list(raw.get("keywords"))),
                reply_goal=str(raw.get("reply_goal") or "").strip(),
                hard_constraints=tuple(_clean_list(raw.get("hard_constraints"))),
                soft_guidance=tuple(_clean_list(raw.get("soft_guidance"))),
            )
        )
    return tuple(scenes)


def _keyword_score(text: str, keywords: tuple[str, ...]) -> float:
    if not keywords:
        return 0.0
    hits = 0
    weighted = 0.0
    for keyword in keywords:
        key = _normalize(keyword)
        if key and key in text:
            hits += 1
            weighted += min(0.4, 0.18 + len(key) / 30)
    if hits <= 0:
        return 0.0
    return min(0.98, 0.45 + weighted)


def _example_score(text: str, examples: tuple[str, ...]) -> float:
    best = 0.0
    for example in examples:
        candidate = _normalize(example)
        if not candidate:
            continue
        if candidate in text or text in candidate:
            best = max(best, 0.92)
            continue
        best = max(best, SequenceMatcher(None, text, candidate).ratio())
    return best if best >= 0.5 else 0.0


def _match_level(score: float) -> str:
    if score >= 0.78:
        return "high"
    if score >= 0.6:
        return "medium"
    return "low"


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def _normalize(value: str) -> str:
    return "".join(str(value or "").strip().lower().split())
