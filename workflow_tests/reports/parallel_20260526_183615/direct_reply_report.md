# direct_reply 测试结果分析

## 汇总

| 指标 | 数量 |
|---|---:|
| 总用例 | 24 |
| 自动通过 | 10 |
| 自动通过率 | 41.67% |
| API错误 | 0 |
| 空回复 | 1 |
| 兜底失败话术 | 0 |
| 疑似转人工 | 1 |
| 疑似主动预约 | 10 |
| 疑似门店匹配 | 6 |
| 疑似价格/活动 | 0 |
| 疑似过早推荐项目 | 0 |
| 问题超过1个 | 0 |
| 单条过长 | 0 |

## 明细

| case_id | 当前消息 | scene | intent | 通过 | 问题 | 回复摘要 |
|---|---|---|---|---|---|---|
| DR_001 | 好的 | S4_appointment_negotiating | appointment_confirm | False | likely_appointment_push | 我看到您有一条待安排到店时间的记录，项目是关于【丝雾眉(高级)】的。 目前还没有明确的预约时间，您是想继续确认具体的到店时间吗？ |
| DR_002 | 嗯嗯 | S1_icebreaking | emotion_chat | True |  | 好哒，慢慢想也没关系~ |
| DR_003 | 谢谢 | S1_icebreaking | emotion_chat | True |  | 不客气呀~ 要是之后有想了解的，随时和我说哦 |
| DR_004 | 不用了 | S4_appointment_negotiating | appointment_cancel | False | likely_appointment_push | 好的，看到您当前有一条待安排到店时间的记录，但还没有明确预约时间。 那这次就先不帮您确认时间了。 您之后想约时间，随时联系我就好。 |
| DR_005 | 先这样吧 | S3_deep_consult | emotion_chat | True |  | 好的，没关系~你可以慢慢考虑，有任何想了解的随时和我说哈~ |
| DR_006 | 晚点再说 | S4_appointment_negotiating | appointment_cancel | False | likely_appointment_push, likely_store_match | 好的，不着急。 我看到你这边有一条【养生-丝雾眉(高级)】的待安排记录。 等你方便的时候，我们再确认具体的到店门店和时间。 |
| DR_007 | 明天去要带什么 | S5_appointed_waiting | pre_visit_question | False | likely_handoff, likely_appointment_push, likely_store_match | 您好，关于明天面诊的准备事项，这里有一些通用建议： 1. 请携带好您的手机和身份证件。 2. 建议尽量素颜或少化妆，方便医生面诊时观察皮肤状况。 3. 通常不需要特意空腹，具体安排... |
| DR_008 | 到店要做多久 | S4_appointment_negotiating | pre_visit_question | False | likely_appointment_push | 到店时间具体看项目和现场评估，初次面诊建议预留充足一点时间。 |
| DR_009 | 可以化妆过去吗 | S5_appointed_waiting | pre_visit_question | False | likely_appointment_push | 看到您有一条待安排到店时间的记录，但还没有明确预约时间。 关于皮肤检测前是否可以化妆，建议您尽量素颜或少化妆，这样检测结果会更准确。 您是想继续确认明天到店的具体时间吗？ |
| DR_010 | 需要空腹吗 | S4_appointment_negotiating | pre_visit_question | False | likely_store_match | 一般皮肤类面诊不需要特意空腹，如果后续涉及具体项目，以现场评估和门店安排为准。 |
| DR_011 | 我到了 | S6_visited_not_deal | emotion_chat | False | likely_appointment_push | 到店啦~前台应该已经在等你了，稍等下就会有人接待你的，别着急哈~ |
| DR_012 | 我在路上 | S5_appointed_waiting | emotion_chat | False | likely_appointment_push | 路上注意安全呀~到店后记得联系前台，我们已经在等你啦~ |
| DR_013 | 停车方便吗 | S5_appointed_waiting | store_inquiry | False | likely_store_match | 好的，关于思明店停车是否方便，我先发你门店信息，里面有详细地址可以参考。 |
| DR_014 | 地址再发我一下 | S5_appointed_waiting | store_inquiry | False | likely_store_match | 我先帮你匹配近一点的门店，你方便说下所在城市或常去区域吗？ |
| DR_015 | 不用回复了 | S8_silent_return | emotion_chat | True |  | 好的，那你先考虑，不着急。 |
| DR_016 | 我不考虑了 | S8_silent_return | silence_return | True |  | 好的，那你先考虑，不着急，后面想了解再发我。 |
| DR_017 | 再看看 | S1_icebreaking | emotion_chat | True |  | 没关系，先了解也可以，不用一上来就决定项目。 |
| DR_018 | 收到 | S3_deep_consult | price_inquiry | False | empty_output |  |
| DR_019 | 没事了 | S8_silent_return | emotion_chat | True |  | 好的，没关系~如果之后有任何想聊的或者需要帮助的，随时找我哦~ |
| DR_020 | 我先问问朋友 | S1_icebreaking | emotion_chat | True |  | 好的，问问朋友也是个稳妥的办法呢~等你和朋友聊完，如果有想法了随时和我说呀~ |
| DR_021 | 我周六改周日 | S4_appointment_negotiating | appointment_change | False | likely_appointment_push, likely_store_match | 好的，看到您想把时间从周六改到周日。 我这边看到您当前有一条待安排到店时间的记录，项目是丝雾眉(高级)，门店是上海徐汇店。 为了帮您确认周日的具体时间，请问您是想约周日的上午还是下... |
| DR_022 | 取消吧 | S5_appointed_waiting | appointment_cancel | False | likely_appointment_push | 好的，我看到您有一条待安排到店时间的记录。 您是想取消这条待安排记录吗？我可以先帮您核对一下。 |
| DR_023 | 先别联系我了 | S8_silent_return | silence_return | True |  | 好的，我先不打扰你，后面想了解再发我。 |
| DR_024 | 可以 | S1_icebreaking | human_request | True |  | 好的，有需要你随时发我。 |

## 说明

- 这是自动初筛报告，最终结论需要结合人工查看回复语气和业务上下文。
- 对 SF1 来说，疑似主动预约、门店匹配、价格活动、过早推荐项目都默认视为风险。
- 如果工作流没有返回 subflow 字段，本报告主要根据 scene、intent 和输出话术进行判断。
