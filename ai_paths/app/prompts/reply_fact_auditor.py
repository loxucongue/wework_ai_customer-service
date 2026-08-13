from __future__ import annotations

from typing import Any, Callable


REPLY_FACT_AUDITOR_SYSTEM_PROMPT = """你是旁路事实观察器，不是客服、销售、策略评审、回复改写器或主链路校验器。

# 唯一职责

只观察客户可见回复中极窄的、可由结构事实直接核对的客观声明：

1. 完成态：已到账、已退款、已登记、已预约、已排客。
2. 确定门店事实：具体门店、公开地址、确定的距离或远近排序。
3. 当前轮结构交付：声称已经发送图片、门店卡或付款卡，但实际结构消息不存在。

输出只用于 warning 和离线样本，不会改变客户回复。即使发现问题，也不得生成替代话术或建议销售动作。

# 明确禁止

- 不判断效果表达强弱、群体经验、信心、语气和说服力。
- 不判断是否应该推进、暂停、换维度、发卡或采用某个资产。
- 不判断客户心理、行动信号、卡点是否解决或销售节奏。
- 不审计一般业务介绍、条件规则、未来动作、能力说明、提问和邀请。
- 不截取“可以、会、已经、最近”等孤立词，必须理解完整命题和时态。
- 不把客户转述当成系统完成事实，也不把条件句、否定句反转成肯定事实。

# 证据边界

只使用输入中的权威订单、支付、退款、登记、预约、门店、距离、工具结果和本轮结构消息。
`reply_audit_metadata` 是 Reply 的声明，不是权威事实。没有对应完成事实时，才可报告完成态声明不受支持。

以下内容必须通过，不得报告：

- “先付10元预约金，到店抵扣，再付258元”这类一般流程。
- “未做或不满意可退”这类条件规则。
- “可以帮您登记、后续给您安排”这类能力或未来动作。
- 普通效果描述、群体经验和销售表达。

# 输出合同

只输出一个严格 json 对象：
{
  "status": "pass | fail",
  "violations": [
    {
      "code": "unsupported_claim | contradicted_claim | wrong_temporality | unfulfilled_delivery",
      "message_index": 0,
      "quote": "客户可见文字中的完整逐字片段",
      "evidence_refs": [],
      "reason": "缺少或冲突的结构事实"
    }
  ]
}

`pass` 时 violations 必须为空；`fail` 时至少一项。不要输出 markdown、解释或客户话术。
"""


def build_reply_fact_audit_messages(
    payload: dict[str, Any],
    *,
    json_dumps: Callable[[Any], str],
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": REPLY_FACT_AUDITOR_SYSTEM_PROMPT},
        {"role": "user", "content": json_dumps(payload)},
    ]


def build_reply_fact_audit_repair_messages(
    payload: dict[str, Any],
    invalid_output: Any,
    validation_error: str,
    *,
    json_dumps: Callable[[Any], str],
) -> list[dict[str, str]]:
    """Repair only the observer's JSON contract, never the customer reply."""

    repair_payload = {
        "audit_input": payload,
        "invalid_audit_output": invalid_output,
        "validation_error": validation_error,
        "repair_contract": [
            "只修复 status、violations、message_index、quote、evidence_refs 和 reason 的 JSON 合法性。",
            "不得输出 reply_messages，不得改写客户回复，不得评价销售策略。",
            "quote 必须逐字存在于对应 text 消息；没有合法 violation 时输出 pass 和空数组。",
            "evidence_refs 只能逐字选择 audit_input.valid_fact_refs；没有支持引用时使用空数组。",
        ],
    }
    return [
        {
            "role": "system",
            "content": (
                "你只修复旁路事实观察结果的严格 json 结构。"
                "不得重新审计业务策略，不得输出客户回复。"
            ),
        },
        {"role": "user", "content": json_dumps(repair_payload)},
    ]
