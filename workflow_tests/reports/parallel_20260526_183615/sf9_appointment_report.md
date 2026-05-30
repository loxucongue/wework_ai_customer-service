# sf9_appointment 测试结果分析

## 汇总

| 指标 | 数量 |
|---|---:|
| 总用例 | 24 |
| 自动通过 | 3 |
| 自动通过率 | 12.5% |
| API错误 | 0 |
| 空回复 | 1 |
| 兜底失败话术 | 0 |
| 疑似转人工 | 1 |
| 疑似主动预约 | 12 |
| 疑似门店匹配 | 11 |
| 疑似价格/活动 | 0 |
| 疑似过早推荐项目 | 0 |
| 问题超过1个 | 2 |
| 单条过长 | 0 |

## 明细

| case_id | 当前消息 | scene | intent | 通过 | 问题 | 回复摘要 |
|---|---|---|---|---|---|---|
| SF9_001 | 我周六到店可以吗 | S4_appointment_negotiating | appointment_intent | False | likely_store_match | 我先帮你匹配近一点的门店，你方便说下所在城市或常去区域吗？具体时段还要再确认。 |
| SF9_002 | 我在厦门，周六下午能过去吗 | S4_appointment_negotiating | appointment_intent | False | likely_store_match | 有的，厦门这边有几家门店，我先发你门店信息，你看哪家更方便。具体周六的时段，我这边还需要再帮你确认一下。 |
| SF9_003 | 明天下午有时间吗 | S4_appointment_negotiating | appointment_intent | False | likely_store_match | 我先帮你匹配近一点的门店，你方便说下所在城市或常去区域吗？ |
| SF9_004 | 今天能去吗 | S4_appointment_negotiating | appointment_intent | False | likely_store_match | 我先帮你匹配近一点的门店，你方便说下所在城市或常去区域吗？ |
| SF9_005 | 帮我约周六下午2点 | S4_appointment_negotiating | appointment_intent | False | likely_appointment_push, likely_store_match | 好的，收到您周六下午2点的预约意向。 我看到您有一条待安排到店时间的记录，但还没有明确预约时间。 为了帮您确认周六下午2点的时间，请问您想预约哪家门店呢？ |
| SF9_006 | 那就明天上午10点吧 | S5_appointed_waiting | appointment_confirm | False | likely_appointment_push | 好的，我记录下您想约明天上午10点。 我看到您有一条待安排到店时间的记录，是上海徐汇店的丝雾眉(高级)。 我这就帮您核对一下明天上午10点这个时段是否可行，稍后回复您。 |
| SF9_007 | 我想预约皮肤检测 | S4_appointment_negotiating | appointment_intent | False | likely_store_match | 我先帮你匹配近一点的门店，你方便说下所在城市或常去区域吗？ |
| SF9_008 | 我可以先到店看看不做吗 | S4_appointment_negotiating | appointment_intent | False | likely_store_match | 我先帮你匹配近一点的门店，你方便说下所在城市或常去区域吗？ |
| SF9_009 | 预约需要交定金吗 | S4_appointment_negotiating | appointment_intent | False | likely_store_match | 我先帮你匹配近一点的门店，你方便说下所在城市或常去区域吗？ |
| SF9_010 | 我已经约了明天上午，帮我确认一下 | S5_appointed_waiting | appointment_confirm | False | likely_appointment_push | 好的，我来帮您核对一下。 看到您当前有一条待安排到店时间的记录，项目是丝雾眉(高级)，在上海徐汇店。 但记录里还没有明确的预约时间，您是想确认明天上午的具体到店时段吗？ |
| SF9_011 | 我明天可能会迟到半小时，可以吗 | S4_appointment_negotiating | appointment_change | False | likely_appointment_push, likely_store_match | 我看到您有一条待安排到店时间的记录，但还没有明确的预约时间。 关于您提到的明天上午10点，我先帮您查询一下门店的预约情况，确认一下时间。 |
| SF9_012 | 我想改到周日 | S4_appointment_negotiating | appointment_change | False | likely_appointment_push | 好的，想从周六下午改到周日。 我看到当前有一条待安排到店时间的记录，但还没有明确预约时间。 您是想改到周日的具体哪个时间段呢？ |
| SF9_013 | 明天不去了，帮我取消 | S4_appointment_negotiating | appointment_cancel | False | likely_appointment_push, likely_store_match, too_many_questions | 看到您有一条待安排到店时间的记录，项目是丝雾眉（高级），门店是上海徐汇店。 是要取消这条待安排记录吗？可以告诉我具体原因吗？ |
| SF9_014 | 算了，不想去了 | S8_silent_return | appointment_cancel | False | likely_appointment_push | 好的，看到您有一条待安排到店时间的记录。 是暂时不想安排，还是想先了解一下其他时间呢？ |
| SF9_015 | 你们老催我到店，我不想聊了 | S8_silent_return | emotion_chat | True |  | 懂你，被一直催促确实有点不舒服。我们后续沟通会更注意节奏，不会再给你这种感觉的。 |
| SF9_016 | 明天去要带什么 | S5_appointed_waiting | pre_visit_question | False | likely_appointment_push | 明天到店的话，建议您带上手机和身份证。 尽量素颜或少化妆，方便面诊评估。 另外，我看到您有一条到店记录，但还没有明确预约时间，需要我帮您确认一下具体到店的安排吗？ |
| SF9_017 | 到店要做多久 | S4_appointment_negotiating | pre_visit_question | False | likely_appointment_push | 到店时长具体看项目和现场评估，初次面诊建议预留充足一点时间哦。 |
| SF9_018 | 地址发我，我直接过去 | S4_appointment_negotiating | store_inquiry | False | likely_store_match | 好的，厦门这边有几家门店，我先发你门店信息，你看哪一家更方便过去。 |
| SF9_019 | 约之前能不能先告诉我价格 | S4_appointment_negotiating | price_inquiry | False | empty_output |  |
| SF9_020 | 我怕去了被推销，可以只面诊吗 | S4_appointment_negotiating | project_inquiry | True |  | 可以的，我们支持只面诊服务。面诊时会先和您沟通具体需求，不会强制推荐项目，您可以放心先过来做皮肤状态评估。 |
| SF9_021 | 如果我约了不去会怎么样 | S4_appointment_negotiating | appointment_confirm | False | likely_handoff, likely_appointment_push | 我理解您的顾虑。关于预约后到店，我们非常欢迎您提前安排时间，这样到店后顾问能更好地为您服务。 我看到您当前有一条待安排的到店记录，但还没有明确预约时间。如果您确定好时间，我可以先帮... |
| SF9_022 | 我从外地过去，能不能安排久一点 | S4_appointment_negotiating | appointment_change | False | likely_appointment_push, too_many_questions | 我看到了，你有一条在上海徐汇店的丝雾眉订单，目前还在待安排时间的状态。 你从外地过来，希望安排得久一点，是想预留更多时间在店里，对吗？ 为了帮你协调，我们需要先确定一个具体的到店日... |
| SF9_023 | 我怀孕了，可以先约面诊吗 | S4_appointment_negotiating | appointment_intent | False | likely_appointment_push | 关于您怀孕后预约面诊的安排，需要进一步和相关同事确认，请您稍后联系我补充信息或咨询。 |
| SF9_024 | 我是未成年，可以约吗 | S0_wait_icebreak | appointment_intent | True |  | 需要进一步确认，建议联系人工同事处理。 |

## 说明

- 这是自动初筛报告，最终结论需要结合人工查看回复语气和业务上下文。
- 对 SF1 来说，疑似主动预约、门店匹配、价格活动、过早推荐项目都默认视为风险。
- 如果工作流没有返回 subflow 字段，本报告主要根据 scene、intent 和输出话术进行判断。
