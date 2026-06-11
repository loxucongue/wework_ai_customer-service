from __future__ import annotations


def has_pre_visit_question(content: str) -> bool:
    text = str(content or "")
    return any(
        term in text
        for term in [
            "要带什么",
            "带什么",
            "能不能化妆",
            "可以化妆",
            "要不要空腹",
            "需要空腹",
            "到店流程",
            "第一次去注意什么",
        ]
    )


def is_strong_multi_recap_request(content: str) -> bool:
    flags = [
        has_pre_visit_question(content),
        asks_store_or_address_recap(content),
        asks_price_recap(content),
    ]
    return sum(1 for flag in flags if flag) >= 2


def asks_other_store_options(content: str) -> bool:
    text = str(content or "")
    return any(
        term in text
        for term in [
            "其他门店",
            "还有哪家",
            "还有别的",
            "其他店",
            "更多门店",
            "附近门店",
            "哪家更方便",
        ]
    )


def asks_store_or_address_recap(content: str) -> bool:
    text = str(content or "")
    recap_terms = [
        "再说一遍",
        "顺一遍",
        "帮我顺",
        "再发一个",
        "再给我",
        "再讲一遍",
        "重复一下",
    ]
    target_terms = ["地址", "门店", "哪家店", "店名", "位置"]
    return any(term in text for term in target_terms) and any(term in text for term in recap_terms)


def asks_price_recap(content: str) -> bool:
    text = str(content or "")
    recap_terms = [
        "再说一遍",
        "顺一遍",
        "帮我顺",
        "再给我",
        "再讲一遍",
        "重复一下",
        "参考价格",
        "价格帮我",
    ]
    target_terms = ["价格", "价位", "多少钱", "预算", "费用"]
    return any(term in text for term in target_terms) and any(term in text for term in recap_terms)
