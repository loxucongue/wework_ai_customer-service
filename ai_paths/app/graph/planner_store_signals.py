from __future__ import annotations

from app.graph.planner_dispute_signals import has_fee_or_refund_dispute
from app.graph.planner_project_signals import has_advantage_question, has_case_request


def has_appointment_record_query(content: str) -> bool:
    if not content:
        return False
    terms = ["我有没有预约", "我约的是", "约的是几点", "预约成功", "查一下预约", "查下预约", "是不是约了", "有没有约", "之前是不是约"]
    return any(term in content for term in terms)


def has_store_inquiry(content: str) -> bool:
    if has_advantage_question(content) or has_case_request(content) or has_fee_or_refund_dispute(content):
        return False
    trust_terms = ["正规", "靠谱", "骗人", "被骗", "资质", "营业执照", "证照", "许可证", "真假", "隐形消费", "被坑", "安全", "售后"]
    if any(term in content for term in trust_terms):
        return False
    hard_store_terms = [
        "地址",
        "哪里",
        "附近",
        "停车",
        "导航",
        "怎么过去",
        "地铁",
        "营业",
        "哪家近",
        "离我近",
        "近吗",
        "近不近",
        "位置",
        "路线",
        "搬走",
        "搬了吗",
        "搬走了吗",
        "还在",
        "还开",
        "还营业",
        "开门",
        "关门",
        "闭店",
        "停业",
        "几点开",
        "几点关",
        "营业时间",
        "换地址",
        "换地方",
        "店还在",
        "门店还在",
    ]
    if any(term in content for term in hard_store_terms):
        return True
    if not any(term in content for term in ["门店", "店"]):
        return False
    appointment_terms = ["预约", "能约", "能去", "周六", "周日", "明天", "后天", "下午", "上午", "到店"]
    if any(term in content for term in trust_terms + appointment_terms):
        return False
    return True
