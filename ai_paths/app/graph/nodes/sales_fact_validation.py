from __future__ import annotations

import re
from typing import Any


def validate_sales_price_fact_boundaries(messages: list[dict[str, Any]]) -> None:
    """Reject a small set of customer-visible 268 price fact conflicts.

    These checks do not infer customer intent or choose a sales response.  They
    only protect explicit, versioned offer facts after the model has decided
    what to say.
    """

    text = _combined_text(messages)
    if "268" not in text:
        return
    for sentence in _sentences(text):
        if "268" not in sentence:
            continue
        compact = re.sub(r"\s+", "", sentence)
        if _claims_full_face_268(compact):
            raise ValueError("offer_268_full_face_claim_conflict")
        if _claims_bilateral_cheek_split_price(compact):
            raise ValueError("offer_bilateral_cheek_split_price_conflict")
        if _claims_face_and_hand_total_268(compact):
            raise ValueError("offer_face_hand_total_268_conflict")
        if _claims_repeat_visit_268(compact):
            raise ValueError("offer_repeat_visit_268_unverified")


def _claims_full_face_268(text: str) -> bool:
    if not re.search(r"(?:268(?:元)?[^。！？]{0,12}(?:全脸|整脸)|(?:全脸|整脸)[^。！？]{0,12}268)", text):
        return False
    return not bool(
        re.search(
            r"(?:不是|不代表|不等于|不能说|不能表述为|并非|并不是|不承诺)[^。！？]{0,12}(?:268(?:元)?[^。！？]{0,6}(?:全脸|整脸)|(?:全脸|整脸))",
            text,
        )
        or re.search(r"(?:268(?:元)?)不是(?:全脸|整脸)", text)
    )


def _claims_bilateral_cheek_split_price(text: str) -> bool:
    if re.search(r"(?:左脸|左边|一边)[^。！？]{0,10}268[^。！？]{0,18}(?:右脸|右边|另一边)[^。！？]{0,10}268", text):
        return not _has_negated_split_boundary(text)
    if re.search(r"(?:两边|双侧|左右脸颊)[^。！？]{0,10}(?:536|两个268)", text):
        return not _has_negated_split_boundary(text)
    return False


def _claims_face_and_hand_total_268(text: str) -> bool:
    has_areas = bool(
        re.search(r"(?:脸部?|面部)[^。！？]{0,16}手部?|手部?[^。！？]{0,16}(?:脸部?|面部)", text)
    )
    if not has_areas:
        return False
    total_claim = bool(
        re.search(r"(?:总共|一共|合计|一起做(?:只要|就是|是)?)[^。！？]{0,10}268", text)
        or re.search(r"268(?:元)?[^。！？]{0,10}(?:脸(?:部)?和手(?:部)?都做|脸手一起做)", text)
    )
    if not total_claim:
        return False
    return not bool(
        re.search(r"(?:不是|并非|不能|不共用|不包含)[^。！？]{0,12}(?:总共|一共|一起|268)", text)
        or re.search(r"(?:总共|一共|一起)[^。！？]{0,8}(?:不是|并非|不能)[^。！？]{0,8}268", text)
    )


def _claims_repeat_visit_268(text: str) -> bool:
    if not re.search(r"(?:第二次|下次|复访|复做|后续再做|以后再做|再次做)", text):
        return False
    if not re.search(r"268(?:元)?", text):
        return False
    return not bool(
        re.search(
            r"(?:不确定|不能确定|不能保证|不承诺|不是固定|未固定|没有固定|届时|到时|以[^。！？]{0,12}为准)",
            text,
        )
        or re.search(r"(?:第二次|下次|复访|复做|后续再做|以后再做|再次做)[^。！？]{0,8}(?:不是|并非)268", text)
    )


def _has_negated_split_boundary(text: str) -> bool:
    return bool(
        re.search(r"(?:不会|不按|不是|不需要|并非)[^。！？]{0,14}(?:左右|左脸|右脸|一边|两边)[^。！？]{0,12}(?:拆|分开|分别|各)", text)
        or re.search(r"(?:左右|左脸|右脸|一边|两边)[^。！？]{0,12}(?:不会|不按|不是|不需要)[^。！？]{0,10}(?:拆|分开|分别|各)", text)
    )


def _combined_text(messages: list[dict[str, Any]]) -> str:
    values: list[str] = []
    for message in messages:
        if not isinstance(message, dict) or str(message.get("type") or "text") != "text":
            continue
        content = message.get("content")
        if isinstance(content, dict):
            content = content.get("text") or content.get("content") or ""
        value = str(content or "").strip()
        if value:
            values.append(value)
    return "\n".join(values)


def _sentences(text: str) -> list[str]:
    return [value for value in re.split(r"[。！？!?\n]+", text) if value]
