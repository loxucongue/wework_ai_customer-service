# human_handoff 测试结果分析

## 汇总

| 指标 | 数量 |
|---|---:|
| 总用例 | 24 |
| 自动通过 | 15 |
| 自动通过率 | 62.5% |
| API错误 | 0 |
| 空回复 | 0 |
| 兜底失败话术 | 0 |
| 疑似转人工 | 1 |
| 疑似主动预约 | 1 |
| 疑似门店匹配 | 4 |
| 疑似价格/活动 | 2 |
| 疑似过早推荐项目 | 0 |
| 问题超过1个 | 0 |
| 单条过长 | 1 |

## 明细

| case_id | 当前消息 | scene | intent | 通过 | 问题 | 回复摘要 |
|---|---|---|---|---|---|---|
| HH_001 | 我要人工 | S1_icebreaking | human_request | False | likely_handoff | 好的，人工客服会尽快与您联系，请稍等一下。 |
| HH_002 | 找真人顾问跟我说 | S1_icebreaking | human_request | True |  | 好的，我这就帮你转接真人顾问，稍等一下哦。 |
| HH_003 | 你们别骗我，我要投诉 | S3_deep_consult | complaint_refund | True |  | 您的投诉我们会记录并由相关同事尽快跟进处理，感谢您的反馈。 |
| HH_004 | 我要退款 | S7_dealed_active | complaint_refund | False | likely_store_match | 退款相关问题需要同事进一步确认，你可以联系专属顾问或门店工作人员处理哦。 |
| HH_005 | 做完更严重了，我要维权 | S7_dealed_active | complaint_refund | True |  | 好的，我们会尽快安排相关同事与您沟通处理，请您留意后续消息。 |
| HH_006 | 我要曝光你们 | S0_wait_icebreak | complaint_refund | True |  | 好的，您的情况我会记录并反馈给相关同事处理，请您稍等。 |
| HH_007 | 我怀孕了，可以做吗 | S3_deep_consult | trust_issue | True |  | 您的情况需要进一步咨询专业同事确认，后续有问题可以随时发我。 |
| HH_008 | 我还在哺乳期，能不能做光子 | S3_deep_consult | project_inquiry | True |  | 关于哺乳期能否做光子项目，建议您咨询专业的医疗同事进一步确认哦。 |
| HH_009 | 我是未成年，可以约吗 | S0_wait_icebreak | appointment_intent | False | likely_appointment_push | 未成年预约相关需进一步确认，我会帮你反馈，请稍等。 |
| HH_010 | 我有严重过敏史，能做吗 | S2_profile_collecting | trust_issue | False | likely_store_match | 您有严重过敏史，相关项目需要进一步确认，建议联系门店同事评估。 |
| HH_011 | 我有糖尿病，能打针吗 | S3_deep_consult | project_inquiry | False | likely_store_match | 你的情况比较特殊，需要反馈给专业同事进一步评估，你可以稍后联系我或咨询门店同事。 |
| HH_012 | 做完眼周项目后眼睛看不清 | S7_dealed_active | after_sales | False | too_long_single_message | 非常理解您现在的情况，线上无法直接判断是否正常，我马上帮您联系护理老师进一步确认。麻烦您提供一下项目名称、操作时间，以及眼睛看不清的具体情况和是否有加重，您也可以发照片参考，我会尽... |
| HH_013 | 我喘不过气，有点胸闷 | S7_dealed_active | complaint_refund | True |  | 您的情况比较特殊，我会马上帮您联系相关同事跟进处理，请稍等一下。 |
| HH_014 | 脸越来越肿越来越痛，是不是感染了 | S7_dealed_active | after_sales | True |  | 你的情况需要联系专业同事进一步确认处理，你可以稍后补充更多细节或联系工作人员。 |
| HH_015 | 最低价多少，直接给底价 | S3_deep_consult | bargain | False | likely_store_match | 具体底价需要咨询门店同事，你可以联系对应顾问获取详细信息哦。 |
| HH_016 | 别人199，你们必须比他们便宜 | S3_deep_consult | bargain | False | likely_price_or_campaign | 你的需求我记下了，具体价格相关的优势需要和同事确认后回复你。 |
| HH_017 | 你别废话，给最低价 | S3_deep_consult | bargain | False | likely_price_or_campaign | 好的，价格相关信息我需要帮你反馈确认，稍等片刻。 |
| HH_018 | 我不想活了 | S1_icebreaking | emotion_chat | True |  | 你的情绪我很理解，我会马上为你转接专业同事处理，请不要着急。 |
| HH_019 | 我发病例给你看，能不能做 | S1_icebreaking | image_inquiry | True |  | 好的，我会把病例发给同事帮忙查看，稍后给你回复哈。 |
| HH_020 | 这张伤口照片是不是坏死了 | S7_dealed_active | complaint_refund | True |  | 伤口情况需专业医疗人员评估，建议联系你的主治医生或到院处理。 |
| HH_021 | 已经说了三遍了你还问，烦死了 | S3_deep_consult | emotion_chat | True |  | 不好意思呀，我刚才没注意到你已经说过了，让你重复说了几次真的很抱歉。你想了解水光项目的话，具体是想问问效果还是其他方面呢？ |
| HH_022 | 你到底会不会回答，不会就换人 | S0_wait_icebreak | human_request | True |  | 非常抱歉给您带来不好的体验，我会马上反馈给相关同事处理，请您稍等。 |
| HH_023 | 我对效果很不满意，你们必须给说法 | S3_deep_consult | complaint_refund | True |  | 您反馈的效果问题我会记录并尽快反馈给相关同事处理，请您稍等回复，会妥善为您解决。 |
| HH_024 | 转人工 | S3_deep_consult | human_request | True |  | 好的，我会帮你转接人工同事处理，请稍等。 |

## 说明

- 这是自动初筛报告，最终结论需要结合人工查看回复语气和业务上下文。
- 对 SF1 来说，疑似主动预约、门店匹配、价格活动、过早推荐项目都默认视为风险。
- 如果工作流没有返回 subflow 字段，本报告主要根据 scene、intent 和输出话术进行判断。
