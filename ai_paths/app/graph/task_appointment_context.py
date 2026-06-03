from __future__ import annotations


def has_non_appointment_interrupt(content: str) -> bool:
    if not content:
        return False
    hard_terms = [
        "投诉",
        "退款",
        "退钱",
        "退给我",
        "骗人",
        "骗子",
        "被坑",
        "坑我",
        "太坑",
        "乱收费",
        "加钱",
        "额外收费",
        "收费不一样",
        "效果不好",
        "效果一点也不好",
        "效果一点都不好",
        "一点效果都没有",
        "一点用都没",
        "没效果",
        "没变化",
        "跟没做一样",
        "白做",
        "白花钱",
        "为什么这么慢",
        "怎么这么慢",
        "回复太慢",
        "回消息太慢",
        "没人回",
        "等这么久",
    ]
    return any(term in content for term in hard_terms)


def looks_like_appointment_context(text: str) -> bool:
    if not text:
        return False
    appointment_terms = [
        "预约",
        "到店",
        "来店",
        "接待",
        "过来",
        "过去",
        "可约",
        "空闲",
        "时间",
        "几点",
        "位置",
        "安排位置",
        "五点",
        "5点",
        "下午",
        "上午",
        "现在过来",
        "现在过去",
        "马上过来",
        "直接过去",
    ]
    store_terms = ["门店", "店", "地址", "厦门", "上海", "重庆", "成都", "嘉定", "百星", "思明", "徐汇", "静安", "浦东"]
    strong_schedule_terms = ["安排位置", "位置", "几点", "几点呀", "现在过来", "现在过去", "马上过来", "直接过去", "可约", "空闲"]
    if any(term in text for term in strong_schedule_terms):
        return True
    return any(term in text for term in appointment_terms) and any(term in text for term in store_terms)
