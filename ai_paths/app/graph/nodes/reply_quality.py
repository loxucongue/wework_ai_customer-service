from __future__ import annotations

from difflib import SequenceMatcher
import re

from app.graph import reply_filters
from app.graph.nodes.reply_validation import message_content_text
from app.graph.state import AgentState
from app.policies.reply_quality_policy import (
    REPLY_HARD_FORBIDDEN_TERMS,
    REPLY_LONG_FORM_TASK_TYPES,
    REPLY_MAX_TEXT_MESSAGE_CHARS,
    REPLY_MAX_TEXT_MESSAGE_CHARS_LONG_FORM,
    REPLY_MAX_TOTAL_TEXT_CHARS,
    REPLY_MAX_TOTAL_TEXT_CHARS_LONG_FORM,
    REPLY_PRICE_RULE_TERMS,
    REPLY_THIRD_PERSON_CUSTOMER_TERMS,
)

_PRICE_CLAIM_PATTERN = re.compile(r"(?<!\d)\d{2,5}\s*元|[一二三四五六七八九十百千万]+元")
_BARE_PRICE_NUMBER_PATTERN = re.compile(r"(?<![\d年月日:：/.-])([1-9]\d{1,4})(?![\d年月日:：/.-])")
_S10_ALLOWED_PRICE_NUMBERS = {"10", "258", "268", "280", "520", "680", "1000", "1980"}
_S10_KNOWN_OLD_PRICE_NUMBERS = {"58", "179", "189", "199", "238", "299", "308", "380", "420", "458", "788", "980", "1280", "1580"}
_S10_FORBIDDEN_ACTIVITY_NAMES = (
)
_S10_CUSTOMER_VISIBLE_TERMS = ("S10", "S10淡斑套餐", "S10 淡斑套餐")
_STORE_NEGATIVE_PATTERN = re.compile(
    r"(暂时没有|暂无|没有|没查到|未匹配|未设|未开|没开).{0,12}(门店|店)"
    r"|"
    r"(门店|店).{0,12}(暂时没有|暂无|没有|没查到|未匹配|未设|未开|没开)"
)
_STORE_CONCRETE_PATTERN = re.compile(
    r"最近的是|离.{0,20}最近.{0,8}(是|的)|推荐.{0,12}(门店|店)"
    r"|地址[：:是]|门店.{0,8}(在|位于)|营业时间|停车场|导航"
    r"|步行.{0,8}(米|分钟)|\d+\.?\d*\s*(公里|千米|米)|走.{0,8}(米|分钟)"
)
_STORE_EXISTENCE_PATTERN = re.compile(r"(有的|有店|有门店|有.{0,4}(门店|店))")
_LOCATION_QUESTION_PATTERN = re.compile(r"(哪个城市|在哪个城市|哪个区|在哪个区|您在哪|你在哪|所在城市|城市位置)")
_DISTANCE_CLAIM_PATTERN = re.compile(
    r"\d+\.?\d*\s*(公里|千米|米)|[一二两三四五六七八九十百]+来?米|[一二两三四五六七八九十]+分钟|走.{0,8}(米|分钟)|步行.{0,8}(米|分钟)"
)
_APPOINTMENT_TIME_INVITE_PATTERN = re.compile(
    r"(今天|明天|后天|周[一二三四五六日天]|星期[一二三四五六日天])?.{0,6}(上午|下午|中午|晚上)?\s*[0-9一二三四五六七八九十]{1,2}\s*[点:：]"
    r"|"
    r"(今天|明天|后天|周[一二三四五六日天]|星期[一二三四五六日天]).{0,12}(过来|到店|来店|预约|安排|检测)"
)
_TRANSPORT_QUESTION_TERMS = ("车费报销", "报销车费", "包接送", "接送", "交通补贴", "打车费", "打车报销")
_TRANSPORT_REQUIRED_ANSWER_TERMS = ("没有接送", "不提供接送", "交通费用需自理", "交通费需自理", "没有车费报销", "不报销车费", "暂时没有接送")
_FEE_TRANSPARENCY_QUESTION_TERMS = ("乱收费", "隐形消费", "加价", "加钱", "推销", "强制消费", "额外收费", "另外收费", "其他收费")
_FEE_TRANSPARENCY_REPLY_TERMS = ("隐形消费", "不推销", "不加价", "乱收费", "强制消费", "额外加收", "额外收费")
_ORDER_LOOKUP_CLAIM_PATTERN = re.compile(r"(帮您|给您|我这边).{0,6}(查|核对).{0,8}(订单|历史订单|上一次订单|上次订单)")
_OLD_CUSTOMER_INTERNAL_PRICE_RULE_PATTERN = re.compile(
    r"(超过|超出|大于|高于).{0,6}1000.{0,12}680|"
    r"(不超过|没超过|低于|小于|不到).{0,6}1000.{0,12}520|"
    r"680.{0,12}(超过|超出|大于|高于).{0,6}1000|"
    r"520.{0,12}(不超过|没超过|低于|小于|不到).{0,6}1000"
)


def model_reply_unsafe(
    state: AgentState,
    messages: list[dict[str, object]],
) -> bool:
    text = "\n".join(
        message_content_text(message.get("content"))
        for message in messages
        if isinstance(message, dict) and message.get("type") != "human_handoff"
    ).strip()
    if not text:
        return True
    if reply_filters.has_internal_reply_leak(text):
        return True
    if any(term in text for term in REPLY_HARD_FORBIDDEN_TERMS):
        return True
    if _has_s10_offer_violation(state, text):
        return True
    if any(term in text for term in REPLY_THIRD_PERSON_CUSTOMER_TERMS):
        return True
    if _has_unbacked_price_claim(state, text):
        return True
    if _has_unbacked_store_claim(state, text):
        return True
    if _misses_required_store_fact_use(state, text):
        return True
    if _asks_for_location_already_provided(state, text):
        return True
    if _has_unbacked_distance_claim(state, text):
        return True
    if _has_unbacked_appointment_time_claim(state, text):
        return True
    if _transport_question_not_answered(state, text):
        return True
    if _proactively_mentions_hidden_fee_terms(state, text):
        return True
    if _leaks_old_customer_internal_price_rule(text):
        return True
    if _mentions_old_customer_price_without_old_profile(state, text):
        return True
    if _has_unbacked_order_lookup_claim(state, text):
        return True
    if _has_poor_visible_format(state, messages):
        return True
    if _violates_sales_script_contract(state, text):
        return True
    return False


def _has_unbacked_price_claim(state: AgentState, text: str) -> bool:
    if _has_price_facts(state):
        return False
    price_claims = [match.group(0).strip() for match in _PRICE_CLAIM_PATTERN.finditer(text)]
    if _is_price_task(state):
        price_claims.extend(match.group(1).strip() for match in _BARE_PRICE_NUMBER_PATTERN.finditer(text))
    if price_claims:
        return not _price_claims_are_user_echo(state, price_claims)
    return any(term in text for term in REPLY_PRICE_RULE_TERMS)


def _has_s10_offer_violation(state: AgentState, text: str) -> bool:
    if not _has_active_s10_offer(state):
        return False
    if any(term in text for term in _S10_CUSTOMER_VISIBLE_TERMS):
        return True
    if _S10_FORBIDDEN_ACTIVITY_NAMES and any(term in text for term in _S10_FORBIDDEN_ACTIVITY_NAMES):
        return True
    price_numbers = _price_numbers_from_text(text)
    if not price_numbers:
        return False
    return any(number not in _S10_ALLOWED_PRICE_NUMBERS for number in price_numbers)


def _has_active_s10_offer(state: AgentState) -> bool:
    structured = _structured_facts(state)
    offer = structured.get("active_offer_context")
    if isinstance(offer, dict) and str(offer.get("project_code") or "") == "S10":
        return True
    price_facts = structured.get("price_facts")
    if isinstance(price_facts, list):
        return any(isinstance(item, dict) and str(item.get("project_code") or "") == "S10" for item in price_facts)
    return False


def _price_numbers_from_text(text: str) -> set[str]:
    numbers: set[str] = set()
    for match in _PRICE_CLAIM_PATTERN.finditer(text):
        numbers.update(re.findall(r"\d+", match.group(0)))
    if _looks_like_price_context(text):
        numbers.update(
            number
            for number in (match.group(1) for match in _BARE_PRICE_NUMBER_PATTERN.finditer(text))
            if number in _S10_KNOWN_OLD_PRICE_NUMBERS
        )
    return {item.lstrip("0") or "0" for item in numbers}


def _looks_like_price_context(text: str) -> bool:
    return any(
        term in text
        for term in (
            "价格",
            "报价",
            "费用",
            "活动",
            "优惠",
            "定金",
            "预约金",
            "尾款",
            "原价",
            "做付",
            "到店付",
            "补",
            "元",
        )
    )


def _is_price_task(state: AgentState) -> bool:
    tasks = [state.get("primary_task") or {}, *(state.get("secondary_tasks") or [])]
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_type = str(task.get("type") or "").strip()
        policy_hint = str(task.get("policy_hint") or "").strip()
        if task_type == "price_inquiry" or policy_hint.startswith("SF7_"):
            return True
    return False


def _has_unbacked_distance_claim(state: AgentState, text: str) -> bool:
    if not _DISTANCE_CLAIM_PATTERN.search(text):
        return False
    return not _has_distance_facts(state)


def _has_distance_facts(state: AgentState) -> bool:
    structured = _structured_facts(state)
    for key in ("store_facts", "recommended_store"):
        value = structured.get(key)
        items = value if isinstance(value, list) else [value]
        for item in items:
            if not isinstance(item, dict):
                continue
            haystack = " ".join(str(item.get(field) or "") for field in ("distance", "distance_text", "route_distance", "walk_time"))
            if haystack.strip():
                return True
    return False


def _has_unbacked_appointment_time_claim(state: AgentState, text: str) -> bool:
    if not _APPOINTMENT_TIME_INVITE_PATTERN.search(text):
        return False
    source = str(state.get("normalized_content") or "")
    if _APPOINTMENT_TIME_INVITE_PATTERN.search(source):
        return False
    return not _has_available_time_facts(state)


def _has_available_time_facts(state: AgentState) -> bool:
    structured = _structured_facts(state)
    facts = structured.get("appointment_facts")
    if not isinstance(facts, list):
        return False
    for item in facts:
        if not isinstance(item, dict):
            continue
        slots = item.get("slots")
        if isinstance(slots, dict) and any(slots.values()):
            return True
        if isinstance(slots, list) and slots:
            return True
    return False


def _transport_question_not_answered(state: AgentState, text: str) -> bool:
    query = str(state.get("normalized_content") or "")
    if not any(term in query for term in _TRANSPORT_QUESTION_TERMS):
        return False
    return not any(term in text for term in _TRANSPORT_REQUIRED_ANSWER_TERMS)


def _proactively_mentions_hidden_fee_terms(state: AgentState, text: str) -> bool:
    query = str(state.get("normalized_content") or "")
    if any(term in query for term in _FEE_TRANSPARENCY_QUESTION_TERMS):
        return False
    return any(term in text for term in _FEE_TRANSPARENCY_REPLY_TERMS)


def _has_unbacked_order_lookup_claim(state: AgentState, text: str) -> bool:
    exact_policy_id = str(state.get("exact_policy_id") or "")
    if exact_policy_id != "SF7_OLD_CUSTOMER_PRICE":
        return False
    if not _ORDER_LOOKUP_CLAIM_PATTERN.search(text):
        return False
    return True


def _leaks_old_customer_internal_price_rule(text: str) -> bool:
    return bool(_OLD_CUSTOMER_INTERNAL_PRICE_RULE_PATTERN.search(text))


def _mentions_old_customer_price_without_old_profile(state: AgentState, text: str) -> bool:
    if "老客价" not in text:
        return False
    profile_facts = _structured_facts(state).get("customer_profile_facts")
    if not isinstance(profile_facts, list) or not profile_facts:
        return True
    first = profile_facts[0]
    if not isinstance(first, dict):
        return True
    return str(first.get("kind") or "") != "2"


def _has_unbacked_store_claim(state: AgentState, text: str) -> bool:
    if not _current_turn_asks_store_or_route(state):
        return False
    has_store_facts = _has_positive_store_facts(state)
    no_store_confirmed = _has_confirmed_no_store_match(state)
    if _STORE_NEGATIVE_PATTERN.search(text):
        if has_store_facts:
            return True
        return not no_store_confirmed
    if _STORE_CONCRETE_PATTERN.search(text):
        return not has_store_facts
    if _STORE_EXISTENCE_PATTERN.search(text) and not has_store_facts:
        return True
    return False


def _has_positive_store_facts(state: AgentState) -> bool:
    structured = _structured_facts(state)
    stores = structured.get("store_facts")
    if isinstance(stores, list) and any(isinstance(item, dict) and (item.get("name") or item.get("address")) for item in stores):
        return True
    recommended = structured.get("recommended_store")
    return isinstance(recommended, dict) and bool(recommended.get("name") or recommended.get("address"))


def _misses_required_store_fact_use(state: AgentState, text: str) -> bool:
    if not _has_positive_store_facts(state):
        return False
    query = str(state.get("normalized_content") or "")
    if not any(term in query for term in ("有店吗", "有门店吗", "有没有店", "有没有门店", "在广西", "在桂林", "在厦门", "在上海", "门店在哪里", "门店在哪")):
        return False
    if _reply_mentions_store_fact(state, text):
        return False
    return True


def _reply_mentions_store_fact(state: AgentState, text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    structured = _structured_facts(state)
    candidates: list[dict[str, object]] = []
    stores = structured.get("store_facts")
    if isinstance(stores, list):
        candidates.extend(item for item in stores if isinstance(item, dict))
    recommended = structured.get("recommended_store")
    if isinstance(recommended, dict):
        candidates.append(recommended)
    for item in candidates:
        name = re.sub(r"\s+", "", str(item.get("name") or ""))
        address = re.sub(r"\s+", "", str(item.get("address") or ""))
        if name and name in compact:
            return True
        if address and (address in compact or address[:8] in compact):
            return True
    return False


def _has_confirmed_no_store_match(state: AgentState) -> bool:
    structured = _structured_facts(state)
    status = structured.get("store_lookup_status")
    return isinstance(status, dict) and bool(status.get("no_store_match_confirmed"))


def _structured_facts(state: AgentState) -> dict[str, object]:
    fact_envelope = state.get("fact_envelope") if isinstance(state, dict) else {}
    if not isinstance(fact_envelope, dict):
        return {}
    structured = fact_envelope.get("structured_facts")
    return structured if isinstance(structured, dict) else {}


def _current_turn_asks_store_or_route(state: AgentState) -> bool:
    task_types = {
        str(task.get("type") or "").strip()
        for task in [state.get("primary_task") or {}, *(state.get("secondary_tasks") or [])]
        if isinstance(task, dict)
    }
    if "store_inquiry" in task_types:
        return True
    query = str(state.get("normalized_content") or "")
    store_terms = (
        "门店",
        "店",
        "地址",
        "哪里",
        "在哪",
        "附近",
        "地铁站",
        "机场",
        "停车",
        "营业时间",
        "导航",
        "怎么过去",
        "有店",
        "有门店",
    )
    return any(term in query for term in store_terms)


def _asks_for_location_already_provided(state: AgentState, text: str) -> bool:
    if not _LOCATION_QUESTION_PATTERN.search(text):
        return False
    query = str(state.get("normalized_content") or "")
    if not query:
        return False
    location_markers = (
        "地铁站",
        "机场",
        "火车站",
        "高铁站",
        "车站",
        "蔡塘",
        "高崎",
        "广西",
        "桂林",
        "厦门",
        "上海",
        "重庆",
        "西安",
        "深圳",
        "广州",
        "杭州",
        "成都",
        "南京",
        "武汉",
        "长沙",
        "福州",
        "泉州",
    )
    return any(marker in query for marker in location_markers)


def _has_price_facts(state: AgentState) -> bool:
    fact_envelope = state.get("fact_envelope") if isinstance(state, dict) else {}
    if not isinstance(fact_envelope, dict):
        return False
    structured = fact_envelope.get("structured_facts")
    if isinstance(structured, dict):
        if structured.get("price_facts"):
            return True
        offer = structured.get("active_offer_context")
        if isinstance(offer, dict) and offer.get("new_customer_price"):
            return True
    usable = fact_envelope.get("usable_facts")
    if isinstance(usable, list):
        return any("pricing_rules" in str(item) for item in usable)
    return False


def _price_claims_are_user_echo(state: AgentState, price_claims: list[str]) -> bool:
    source_text = "\n".join(
        [
            str(state.get("normalized_content") or ""),
            *[str(item) for item in (state.get("conversation_history") or [])[-6:]],
        ]
    )
    compact_source = re.sub(r"\s+", "", source_text)
    if not compact_source:
        return False
    for claim in price_claims:
        compact_claim = re.sub(r"\s+", "", claim)
        if compact_claim and compact_claim in compact_source:
            continue
        digits = "".join(re.findall(r"\d+", compact_claim))
        if digits and digits in compact_source:
            continue
        return False
    return True


def _has_poor_visible_format(state: AgentState, messages: list[dict[str, object]]) -> bool:
    text_messages = [
        message_content_text(message.get("content"))
        for message in messages
        if isinstance(message, dict) and message.get("type") == "text"
    ]
    text_messages = [text for text in text_messages if text]
    if len(text_messages) > 2:
        return True

    long_form = _is_long_form_turn(state)
    per_message_limit = REPLY_MAX_TEXT_MESSAGE_CHARS_LONG_FORM if long_form else REPLY_MAX_TEXT_MESSAGE_CHARS
    total_limit = REPLY_MAX_TOTAL_TEXT_CHARS_LONG_FORM if long_form else REPLY_MAX_TOTAL_TEXT_CHARS
    if any(len(text) > per_message_limit for text in text_messages):
        return True
    if sum(len(text) for text in text_messages) > total_limit:
        return True
    if len(text_messages) == 2 and _looks_redundant(text_messages[0], text_messages[1]):
        return True
    return False


def _is_long_form_turn(state: AgentState) -> bool:
    task_types = {
        str(task.get("type") or "").strip()
        for task in [state.get("primary_task") or {}, *(state.get("secondary_tasks") or [])]
        if isinstance(task, dict)
    }
    return bool(task_types & REPLY_LONG_FORM_TASK_TYPES)


def _looks_redundant(first: str, second: str) -> bool:
    first_compact = re.sub(r"\s+", "", first)
    second_compact = re.sub(r"\s+", "", second)
    if not first_compact or not second_compact:
        return False
    if first_compact in second_compact or second_compact in first_compact:
        return True
    first_tokens = _char_ngrams(first_compact)
    second_tokens = _char_ngrams(second_compact)
    if not first_tokens or not second_tokens:
        return False
    overlap = len(first_tokens & second_tokens)
    smaller = min(len(first_tokens), len(second_tokens))
    return smaller > 0 and overlap / smaller >= 0.55


def _char_ngrams(text: str, size: int = 4) -> set[str]:
    if len(text) <= size:
        return {text}
    return {text[index : index + size] for index in range(0, len(text) - size + 1)}


def _violates_sales_script_contract(state: AgentState, text: str) -> bool:
    scene = _active_scene_context(state)
    canonical = str(scene.get("canonical_sales_reply") or "").strip()
    if not canonical:
        return False
    copy_strength = str(scene.get("copy_strength") or "").strip().lower()
    if copy_strength != "high":
        return False

    compact_text = re.sub(r"\s+", "", text)
    compact_canonical = re.sub(r"\s+", "", canonical)
    if not compact_text or not compact_canonical:
        return False
    if compact_canonical in compact_text:
        return False

    scene_family = str(scene.get("family") or "").strip()
    scene_id = str(scene.get("scene_id") or "").strip()
    if scene_family == "SF6_STORE_INQUIRY" or scene_id.startswith("SF6_STORE_"):
        # Store answers must include real facts such as address or hours. Do not
        # reject them merely because the visible text is longer than the sales
        # script skeleton.
        return False

    explanation_terms = (
        "综合评估",
        "需要结合",
        "根据您",
        "个性化",
        "匹配适合",
        "专业皮肤检测分析",
        "斑的类型",
        "层次",
        "肌肤状态",
        "千篇一律",
        "专业人员",
        "推荐合适",
        "改善方案",
    )
    if any(term in text for term in explanation_terms):
        return True

    allowed_length = max(56, len(compact_canonical) * 2 + 8)
    if len(compact_text) > allowed_length:
        return True

    ratio = SequenceMatcher(None, compact_text, compact_canonical).ratio()
    if len(compact_canonical) <= 24:
        return ratio < 0.38
    return ratio < 0.3


def _active_scene_context(state: AgentState) -> dict[str, object]:
    contexts = state.get("scene_guidance_context") if isinstance(state, dict) else []
    if not isinstance(contexts, list):
        return {}
    for item in contexts:
        if isinstance(item, dict) and item.get("canonical_sales_reply"):
            return item
    return {}
