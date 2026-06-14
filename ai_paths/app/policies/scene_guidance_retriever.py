from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


ACTIVE_INJECTION_THRESHOLD = 0.75
SHORT_KEYWORD_MAX_LEN = 2


@dataclass(frozen=True)
class SceneGuidance:
    scene_id: str
    family: str
    status: str
    stage_scope: tuple[str, ...]
    examples: tuple[str, ...]
    keywords: tuple[str, ...]
    reply_goal: str
    hard_constraints: tuple[str, ...]
    soft_guidance: tuple[str, ...]
    business_logic: dict[str, Any]
    style_reference: dict[str, Any]
    canonical_sales_reply: str
    source_sales_reply: str
    copy_strength: str
    risk_rewrite: dict[str, Any]
    source: dict[str, Any]

    def to_prompt_context(self) -> dict[str, Any]:
        context: dict[str, Any] = {
            "scene_id": self.scene_id,
            "reply_goal": self.reply_goal,
            "hard_constraints": list(self.hard_constraints),
            "soft_guidance": list(self.soft_guidance),
        }
        if self.stage_scope:
            context["stage_scope"] = list(self.stage_scope)
        if self.business_logic:
            context["business_logic"] = self.business_logic
        if self.style_reference:
            context["style_reference"] = self.style_reference
        if self.canonical_sales_reply:
            context["canonical_sales_reply"] = self.canonical_sales_reply
        if self.source_sales_reply:
            context["source_sales_reply"] = self.source_sales_reply
        if self.copy_strength:
            context["copy_strength"] = self.copy_strength
        if self.risk_rewrite:
            context["risk_rewrite"] = self.risk_rewrite
        if self.source:
            context["source"] = self.source
        return context


def retrieve_scene_guidance(
    *,
    family: str,
    user_message: str,
    preferred_scene_id: str = "",
    top_k: int = 3,
) -> list[dict[str, Any]]:
    family = str(family or "").strip()
    text = _normalize(user_message)
    if not family or not text:
        return []

    preferred = str(preferred_scene_id or "").strip()
    preferred_scene: SceneGuidance | None = None
    if preferred:
        for scene in load_scene_guidance():
            if scene.scene_id == preferred and scene.family == family and scene.status in {"shadow", "active"}:
                preferred_scene = scene
                break

    scored: list[dict[str, Any]] = []
    if preferred_scene:
        scored.append(
            {
                "scene_id": preferred_scene.scene_id,
                "family": preferred_scene.family,
                "score": 1.0,
                "status": preferred_scene.status,
                "match_level": "exact",
                "reply_goal": preferred_scene.reply_goal,
            }
        )

    for scene in load_scene_guidance():
        if scene.family != family or scene.status not in {"shadow", "active"}:
            continue
        if preferred_scene and scene.scene_id == preferred_scene.scene_id:
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
    base = Path(__file__).parent
    paths = [
        base / "scene_guidance.jsonl",
        base / "scene_guidance_business.jsonl",
    ]
    scenes: list[SceneGuidance] = []
    seen: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            text = line.strip()
            if not text:
                continue
            raw = json.loads(text)
            scene_id = str(raw.get("scene_id") or "").strip()
            if not scene_id:
                raise ValueError(f"{path.name}:{line_number} missing scene_id")
            if scene_id in seen:
                raise ValueError(f"{path.name}:{line_number} duplicate scene_id {scene_id}")
            seen.add(scene_id)
            scenes.append(
                SceneGuidance(
                    scene_id=scene_id,
                    family=str(raw.get("family") or "").strip(),
                    status=str(raw.get("status") or "shadow").strip(),
                    stage_scope=tuple(_clean_list(raw.get("stage_scope"))),
                    examples=tuple(_clean_list(raw.get("examples"))),
                    keywords=tuple(_clean_list(raw.get("keywords"))),
                    reply_goal=str(raw.get("reply_goal") or "").strip(),
                    hard_constraints=tuple(_clean_list(raw.get("hard_constraints"))),
                    soft_guidance=tuple(_clean_list(raw.get("soft_guidance"))),
                    business_logic=_clean_dict(raw.get("business_logic")),
                    style_reference=_clean_dict(raw.get("style_reference")),
                    canonical_sales_reply=str(raw.get("canonical_sales_reply") or "").strip(),
                    source_sales_reply=str(raw.get("source_sales_reply") or "").strip(),
                    copy_strength=str(raw.get("copy_strength") or "").strip(),
                    risk_rewrite=_clean_dict(raw.get("risk_rewrite")),
                    source=_clean_dict(raw.get("source")),
                )
            )
    return tuple(scenes)


def _keyword_score(text: str, keywords: tuple[str, ...]) -> float:
    if not keywords:
        return 0.0
    hits = 0
    weighted = 0.0
    longest_hit = 0
    for keyword in keywords:
        key = _normalize(keyword)
        if key and key in text:
            hits += 1
            longest_hit = max(longest_hit, len(key))
            weighted += min(0.4, 0.18 + len(key) / 30)
    if hits <= 0:
        return 0.0
    if hits == 1 and longest_hit <= SHORT_KEYWORD_MAX_LEN:
        return 0.0
    return min(0.98, 0.45 + weighted)


def _example_score(text: str, examples: tuple[str, ...]) -> float:
    best = 0.0
    for example in examples:
        candidate = _normalize(example)
        if not candidate:
            continue
        if candidate in text or text in candidate:
            best = max(best, 0.99)
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


def _clean_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _normalize(value: str) -> str:
    return "".join(str(value or "").strip().lower().split())
