# SF11_emotion_companion 测试结果分析

## 汇总

| 指标 | 数量 |
|---|---:|
| 总用例 | 24 |
| 自动通过 | 17 |
| 自动通过率 | 70.83% |
| API错误 | 0 |
| 空回复 | 0 |
| 兜底失败话术 | 0 |
| 疑似转人工 | 0 |
| 疑似主动预约 | 2 |
| 疑似门店匹配 | 2 |
| 疑似价格/活动 | 2 |
| 疑似过早推荐项目 | 0 |
| 问题超过1个 | 0 |
| 单条过长 | 2 |

## 明细

| case_id | 当前消息 | scene | intent | 通过 | 问题 | 回复摘要 |
|---|---|---|---|---|---|---|
| SF11_001 | 我有点焦虑，不知道到底要不要做 | S3_deep_consult | emotion_chat | True |  | 我懂这种纠结的感觉，考虑变美项目时确实会有点拿不定主意。你是担心效果还是恢复期呢？ |
| SF11_002 | 我就是随便看看，不一定做 | S1_icebreaking | emotion_chat | True |  | 没关系，先了解也可以，不用一上来就决定项目。 |
| SF11_003 | 我怕疼，想想就紧张 | S3_deep_consult | emotion_chat | True |  | 怕疼确实会让人有点紧张呢，很多人刚开始都会担心这个。其实现在的项目操作都会尽量减轻痛感，你可以先了解下具体的流程，这样心里也能更踏实些~ |
| SF11_004 | 看别人皮肤都好好，我有点自卑 | S1_icebreaking | emotion_chat | True |  | 懂你的感受，看到别人皮肤好自己有点落差很正常～其实每个人肤质和状态都有自己的节奏，你平时最在意皮肤哪里呢？ |
| SF11_005 | 我脸上的斑真的很烦，看着心情不好 | S3_deep_consult | project_inquiry | True |  | 有的，淡斑方向上可以先了解光子嫩肤、皮秒/超皮秒这类项目 你的斑是散在点状还是片状？这有助于更精准推荐哦 |
| SF11_006 | 一直长痘真的烦死了 | S1_icebreaking | emotion_chat | True |  | 懂你，长痘真的太影响心情了😟 最近是反复长还是刚冒出来呀？ |
| SF11_007 | 算了，不想聊了 | S4_appointment_negotiating | appointment_cancel | False | likely_appointment_push | 好的，没关系的。如果你之后有需要了解项目或者想预约到店看看，随时可以再联系我。 |
| SF11_008 | 你们老是让我到店，我有点烦 | S4_appointment_negotiating | complaint_refund | True |  | 您好，我是您的专属客服，很高兴为您服务。请问有什么可以帮您的吗？ |
| SF11_009 | 今天心情不好，先不想决定 | S3_deep_consult | emotion_chat | True |  | 懂你，心情不好的时候确实很难静下心做决定～先不着急，想聊点别的或者之后再考虑都可以，我都在这儿～ |
| SF11_010 | 我是不是太丑了 | S3_deep_consult | emotion_chat | True |  | 别这么说呀，有时候我们会突然对自己没信心，但其实在别人眼里可能和我们想的不一样呢。你是最近觉得哪个地方让你有这种想法吗？ |
| SF11_011 | 朋友说我没必要做，我又有点想试 | S1_icebreaking | emotion_chat | True |  | 懂你，朋友的意见和自己的想法冲突时确实会纠结。你自己最在意的是改善暗沉这件事吗？ |
| SF11_012 | 我纠结水光和光子，不知道选哪个 | S3_deep_consult | project_inquiry | False | likely_appointment_push | 水光和光子各有侧重哦。水光偏补水改善干燥卡粉，光子偏肤色不均和综合肤质调整。 结合你想改善干和暗沉的情况，可先了解这两种方向哪个更贴合你的主要需求，也建议到店做个皮肤检测确认下~ |
| SF11_013 | 太贵了，我感觉自己负担不起 | S3_deep_consult | price_inquiry | False | likely_store_match, likely_price_or_campaign | 我理解你的感受，这个价格是系统统一口径的，具体需要看你想了解的项目细节和当前门店活动哦。如果预算有限，也可以和我说说你更关注的改善方向或预算，我帮你看看有没有更合适的项目推荐~ |
| SF11_014 | 我真的很怕做坏了怎么办 | S3_deep_consult | trust_issue | False | too_long_single_message | 你担心做坏是很正常的，我们会严格遵循安全流程，先为你发送术后护理跟进说明图。 https://lf6-appstore-sign.oceancloudapi.com/ocean-c... |
| SF11_015 | 我做完一直红肿疼，心态崩了 | S7_dealed_active | after_sales | True |  | 先别太着急，红肿疼痛这种情况我这边不直接判断正不正常，需要护理老师进一步确认。 请你发一张现在的照片，再告诉我做的项目、操作时间，以及疼痛持续了多久、有没有越来越明显？ |
| SF11_016 | 你们别骗我，我要投诉 | S3_deep_consult | complaint_refund | True |  | 您好，我是您的专属客服。有什么可以帮您的吗？ |
| SF11_017 | 你能不能陪我聊聊 | S1_icebreaking | emotion_chat | True |  | 当然可以呀，我在这里陪你聊～你最近有没有遇到什么想分享的事情呀？ |
| SF11_018 | 你说话好像机器人哈哈 | S1_icebreaking | emotion_chat | True |  | 哈哈，其实我是智能助手，但会尽量像真人一样陪你聊的～ 你今天是想聊聊皮肤护理或者变美相关的事情吗？ |
| SF11_019 | 我已经问了很多次了，还是不确定 | S3_deep_consult | price_inquiry | False | likely_price_or_campaign | 价格需要按具体项目大类和系统配置确认哦，你是想了解水光还是光子的价格呢？ |
| SF11_020 | 我怕恢复期影响上班，烦 | S3_deep_consult | trust_issue | False | too_long_single_message | 你担心恢复期影响上班很正常，我们会提前帮你规划护理细节。我先发你术后护理跟进说明图，里面有不同阶段的护理建议。 https://lf9-appstore-sign.oceanclo... |
| SF11_021 | 我现在不想花钱，只想先听听 | S1_icebreaking | emotion_chat | True |  | 没关系，先听听也很好，不用急着决定花钱～你现在想先了解哪些方面呢？我们可以先聊聊你的想法～ |
| SF11_022 | 感觉医美好复杂，我看不懂 | S3_deep_consult | emotion_chat | True |  | 确实，医美项目种类多，刚开始了解的时候确实容易觉得复杂呢～ 你现在是担心项目太复杂还是对具体哪些项目不太清楚呀？我们可以慢慢帮你理清楚～ |
| SF11_023 | 我先冷静一下，晚点再说 | S4_appointment_negotiating | appointment_cancel | False | likely_store_match | 好的，没问题，你先冷静考虑一下。 等你考虑好了，或者对时间、门店有什么想法了，随时跟我说，我再帮你安排。 |
| SF11_024 | 别回了 | S0_wait_icebreak | emotion_chat | True |  | 好的，明白啦~您如果后续有需要随时和我说，不打扰您哦😊 |

## 说明

- 这是自动初筛报告，最终结论需要结合人工查看回复语气和业务上下文。
- 对 SF1 来说，疑似主动预约、门店匹配、价格活动、过早推荐项目都默认视为风险。
- 如果工作流没有返回 subflow 字段，本报告主要根据 scene、intent 和输出话术进行判断。
