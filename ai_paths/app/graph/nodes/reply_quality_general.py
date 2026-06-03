from __future__ import annotations

import re
from typing import Any

from app.graph import planner_helpers, reply_filters, task_state
from app.graph.nodes.reply_quality_types import ReplyQualityCallbacks
from app.graph.state import AgentState
from app.policies.constants import CITY_NAMES


def check_general_trust_image(state: AgentState, text: str, intents: set[str], content: str, project: str, image_info: dict[str, Any], known_visible: list[Any], callbacks: ReplyQualityCallbacks) -> bool | None:
        if reply_filters.has_internal_reply_leak(text):
            return True
        if callbacks.rejects_more_questions(content) and callbacks.asks_followup_question(text):
            return True
        if "trust_issue" in intents and not intents & {"appointment_intent", "appointment_confirm", "appointment_change", "appointment_cancel"}:
            trust_appointment_push_terms = [
                "确认预约",
                "继续帮你确认预约",
                "继续帮你预约",
                "帮你预约",
                "告诉我你所在的城市",
                "所在城市",
                "城市",
                "哪个城市",
                "去哪个城市",
                "城市的门店",
                "具体门店",
                "哪个门店",
                "哪天方便",
                "预约时间",
                "可约时间",
                "安排到店",
                "附近有哪些门店",
                "帮你看看附近",
                "门店老师",
            ]
            if any(term in text for term in trust_appointment_push_terms):
                return True
            if planner_helpers._is_soft_fee_concern(content) and any(
                term in text
                for term in [
                    "不会乱收费",
                    "不会到店乱收费",
                    "绝对没有其他收费",
                    "肯定没有其他收费",
                    "绝不会额外加收",
                    "没有隐形消费",
                    "无隐形消费",
                    "全程没有隐形消费",
                    "不会临时加项",
                    "明码标价",
                    "最该放心",
                    "正规备案",
                    "正规渠道",
                    "备案产品",
                    "备案设备",
                    "持证老师",
                    "具体资质",
                    "资质认证",
                    "统一培训",
                    "服务可追溯",
                    "收费可复盘",
                    "所有项目",
                    "所有门店",
                    "所有淡斑类项目",
                ]
            ):
                return True
            if planner_helpers._is_soft_fee_concern(content) and any(
                term in text
                for term in [
                    "附近有哪些门店",
                    "帮你看看附近",
                    "门店老师",
                    "体验档位",
                    "活动档位",
                    "近期可安排",
                    "锁定这个改善方向",
                    "更关注斑点",
                    "整体肤色",
                    "匹配合适的方向",
                    "适合的改善方向",
                    "更适合哪类",
                ]
            ):
                return True
        if callbacks.has_known_image_context(state):
            repeat_image_terms = ["发照片", "发张照片", "发一张", "照片发", "发我照片", "正脸自然光", "自然光下", "正脸照片", "拍张照片", "再发一张", "再发张", "看看皮肤状态", "帮你看看皮肤"]
            if any(term in text for term in repeat_image_terms):
                return True
            if intents & {"project_inquiry", "image_inquiry"}:
                anchor_terms = set(known_visible) | {"斑", "色沉", "肤色不均", "泛红", "痘印", "毛孔", "图片", "照片", "描述"}
                if not any(term and term in text for term in anchor_terms):
                    return True
                if any(term in text for term in ["聊聊你主要想改善", "方便描述一下", "可以先和我聊聊", "可以具体说说", "这样我可以给你一些参考建议"]):
                    return True
            if known_visible and any(term in content for term in ["能不能", "能弄淡", "能淡", "到底能不能"]):
                if any(term in content for term in ["斑", "色沉", "淡"]):
                    answer_terms = ["能淡", "可以淡", "有机会淡", "能改善", "可以改善", "能做淡化", "淡化方向"]
                    if not any(term in text for term in answer_terms):
                        return True
                    if any(term in text for term in ["可以具体说说", "想改善哪方面", "方便描述一下", "再补充一下", "补充一下斑点", "更精准地推荐", "更精准地帮你"]):
                        return True
                    return False
            need_text = " ".join([content, *map(str, known_visible), str(image_info.get("image_desc") or "")])
            if (
                intents & {"project_inquiry", "image_inquiry"}
                and any(term in need_text for term in ["斑", "点状", "色沉", "肤色不均", "暗沉"])
                and not any(term in need_text for term in ["敏感", "泛红", "屏障", "刺痛", "干痒", "红血丝"])
                and any(term in text for term in ["舒缓修护", "屏障重建", "敏感修护", "修护方向", "修护", "泛红"])
            ):
                return True
        return None

def check_project_store_dispute(state: AgentState, text: str, intents: set[str], content: str, project: str, image_info: dict[str, Any], known_visible: list[Any], callbacks: ReplyQualityCallbacks) -> bool | None:
        if callbacks.is_generic_project_intro(content) and any(term in text for term in ["肉毒", "水光", "热玛吉", "超声炮", "光子", "皮秒"]):
            return True
        if intents & {"project_inquiry", "image_inquiry"} and not intents & {"price_inquiry", "campaign_inquiry", "ad_price_check"}:
            if not any(term in content for term in ["多少钱", "价格", "预算", "费用", "活动", "贵", "便宜"]):
                if any(term in text for term in ["价格没查到", "暂时没有查到该方向可直接引用的明确价格", "暂时没查到该方向", "核价", "报价"]):
                    return True
        if reply_filters.has_sensitive_external_terms(text):
            sanitized_text = reply_filters.sanitize_unasked_project_names(text)
            if reply_filters.has_sensitive_external_terms(sanitized_text):
                return True
        if callbacks.is_unclear_need(content) and any(term in text for term in ["小气泡", "水杨酸", "肉毒", "水光", "热玛吉", "超声炮", "光子", "皮秒"]):
            return True
        if callbacks.is_unclear_need(content) and any(term in text for term in ["PDRN", "三文鱼", "焕肤"]):
            return True
        if "trust_issue" in intents and "store_inquiry" not in intents and any(term in text for term in ["匹配到", "你看哪家更方便", "\n1.", "门店："]):
            return True
        if "store_inquiry" in intents and callbacks.is_single_store_fact_query(state):
            extra_push_terms = [
                "如果需要",
                "如需",
                "要不要",
                "需要我",
                "我可以发",
                "可以发",
                "发导航",
                "发停车",
                "查可约",
                "可约时间",
                "预约",
                "哪天来",
                "哪天方便",
            ]
            if any(term in text for term in extra_push_terms):
                return True
            if "停车" not in content and "停车" in text:
                return True
            route_terms = ["地址", "导航", "哪里", "怎么过去", "位置", "路线"]
            if not any(term in content for term in route_terms) and "导航" in text:
                return True
        if "store_inquiry" in intents and any(term in text for term in ["每家都有", "都有专属停车", "都支持地铁", "地铁直达", "专属停车场"]):
            return True
        if (intents & {"complaint_refund", "human_request"} or planner_helpers._has_fee_or_refund_dispute(content)) and any(
            term in text for term in ["没看到订单", "没有看到订单", "没看到费用记录", "没有看到费用记录", "没有相关记录"]
        ):
            return True
        if planner_helpers._has_fee_or_refund_dispute(content) and any(term in text for term in ["地址：", "营业时间", "匹配到", "你看哪家更方便"]):
            return True
        if "ad_price_check" in intents and any(term in text for term in ["列几个预算参考", "几个预算参考", "换成", "其他配置里"]):
            return True
        if "competitor_compare" in intents and any(term in text for term in ["列几个预算参考", "小贝先给你列几个预算", "胶原类项目", "PDRN", "新客体验价", "日常单次价", "活动价"]):
            return True
        if "competitor_compare" in intents:
            digits = callbacks.extract_price_digits(content)
            if digits and not any(digit in text for digit in digits[:2]):
                return True
            if any(term in content for term in ["一次见效", "一次就能", "一次就"]) and "一次" not in text:
                return True
        if callbacks.is_identity_question(content) and any(term in text for term in ["真人客服", "我是人工", "不是AI", "不是机器人"]):
            return True
        if callbacks.has_effect_guarantee_request(content) and any(
            term in text for term in ["多数人", "明显提亮", "3-5次", "3到5次", "一定", "所有项目", "顾问档期", "先面诊", "不收费"]
        ):
            return True
        return None
