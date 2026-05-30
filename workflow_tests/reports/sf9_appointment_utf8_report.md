# SF9_appointment 测试结果分析

## 汇总

| 指标 | 数量 |
|---|---:|
| 总用例 | 24 |
| 自动通过 | 6 |
| 自动通过率 | 25% |
| API错误 | 0 |
| 空回复 | 0 |
| 兜底失败话术 | 0 |
| 疑似转人工 | 0 |
| 疑似主动预约 | 13 |
| 疑似门店匹配 | 12 |
| 疑似价格/活动 | 2 |
| 疑似过早推荐项目 | 0 |
| 问题超过1个 | 2 |
| 单条过长 | 2 |

## 明细

| case_id | 当前消息 | scene | intent | 通过 | 问题 | 回复摘要 |
|---|---|---|---|---|---|---|
| SF9_001 | 我周六到店可以吗 | S4_appointment_negotiating | appointment_intent | False | likely_appointment_push, likely_store_match | 周六我可以先帮你看一下，但要先确认你方便到哪个城市或哪个区到店，这样才能查最近门店和可约时间。 |
| SF9_002 | 我在厦门，周六下午能过去吗 | S4_appointment_negotiating | appointment_intent | False | likely_appointment_push, likely_store_match | 可以，我先帮你看厦门这边周六下午的时间。你方便在思明区、湖里区，还是其他区域到店？这样我才能帮你查最近门店的具体时间。 |
| SF9_003 | 明天下午有时间吗 | S3_deep_consult | appointment_intent | False | likely_appointment_push, likely_store_match | 明天下午我先帮你看一下。不过要先确认你方便在哪个城市或哪个区到店，我才能帮你查最近门店和可约时间哦。 |
| SF9_004 | 今天能去吗 | S4_appointment_negotiating | appointment_intent | False | likely_appointment_push, likely_store_match | 今天我可以先帮你看一下，不过要先确认你方便在哪个城市或哪个区到店，我才能帮你查最近门店和今天的时间。 |
| SF9_005 | 帮我约周六下午2点 | S4_appointment_negotiating | appointment_intent | False | likely_store_match | 好的，我先按周六下午2点帮你确认一下厦门思明区附近门店的时间，确认好再跟你说。 |
| SF9_006 | 那就明天上午10点吧 | S4_appointment_negotiating | appointment_confirm | False | likely_store_match | 好的，我先按明天上午10点，帮你确认一下广州天河附近门店的时间。确认好我再跟你说。 |
| SF9_007 | 我想预约皮肤检测 | S4_appointment_negotiating | appointment_intent | False | likely_appointment_push, likely_store_match | 好的，我先帮你看一下皮肤检测的可约时间。要先确认你方便在哪个城市或哪个区到店，我才能帮你查最近门店和具体时间。 |
| SF9_008 | 我可以先到店看看不做吗 | S4_appointment_negotiating | appointment_intent | False | likely_appointment_push, likely_store_match | 当然可以先到店看看，了解清楚再决定。不过要确认一下，你方便到哪个城市或哪个区到店呢？这样我才能帮你查最近的门店和可约时间。 |
| SF9_009 | 预约需要交定金吗 | S4_appointment_negotiating | appointment_intent | False | likely_appointment_push, likely_store_match | 您好，预约到店面诊是不需要交定金的。我先帮您确认一下周末的时间，需要先了解一下您方便在哪个城市或者哪个区域到店呢？这样我才能为您查询最近的门店和可约时间。 |
| SF9_010 | 我已经约了明天上午，帮我确认一下 | S4_appointment_negotiating | appointment_confirm | False | likely_appointment_push | 好的，我先帮你确认一下明天上午的预约信息，确认好马上告诉你。 |
| SF9_011 | 我明天可能会迟到半小时，可以吗 | S5_appointed_waiting | appointment_change | True |  | 好的，我理解您明天可能会晚到一些。为了确保不影响您的面诊安排，我先帮您确认一下调整到10:30是否可以，或者您看是否需要帮您重新协调其他时间？ |
| SF9_012 | 我想改到周日 | S5_appointed_waiting | appointment_change | True |  | 好的，想从周六下午改到周日的具体时间是吗？我先帮你确认一下周日的可约时间，确认好再跟你说。 |
| SF9_013 | 明天不去了，帮我取消 | S4_appointment_negotiating | appointment_cancel | False | likely_appointment_push | 好的，我先帮你取消明天的预约。 |
| SF9_014 | 算了，不想去了 | S4_appointment_negotiating | appointment_cancel | False | too_many_questions | 好的，这边先帮你记录一下。方便问一下是什么原因让你不想来了吗？比如时间不合适，还是其他安排？ |
| SF9_015 | 你们老催我到店，我不想聊了 | S4_appointment_negotiating | appointment_cancel | True |  | 好的，我这边先不打扰您了。如果您之后有需要或者想了解项目，随时和我说就好。 |
| SF9_016 | 明天去要带什么 | S5_appointed_waiting | pre_visit_question | True |  | 您好，我是您的专属客服。很高兴为您服务！请问有什么可以帮您的吗？ |
| SF9_017 | 到店要做多久 | S4_appointment_negotiating | pre_visit_question | True |  | 您好，我是您的专属客服。有什么可以帮您的吗？ |
| SF9_018 | 地址发我，我直接过去 | S4_appointment_negotiating | store_inquiry | False | likely_store_match | 好的，为您查询厦门门店信息。 我们厦门门店在思明区湖滨南路123号银泰中心3楼。 营业时间：周一至周日 10:00-21:00。 您可以直接导航搜索“XX医美(厦门银泰中心店)”。... |
| SF9_019 | 约之前能不能先告诉我价格 | S4_appointment_negotiating | price_inquiry | False | likely_price_or_campaign | 价格需要结合具体项目来看呢，你可以先告诉我想了解哪个项目的价格，或你的预算偏好、想改善的问题，这样我能更准确地给你参考。 |
| SF9_020 | 我怕去了被推销，可以只面诊吗 | S4_appointment_negotiating | trust_issue | False | likely_appointment_push, likely_price_or_campaign, too_many_questions, too_long_single_message | 你担心到店被推销很正常，我们的流程会先沟通需求，确认方案和费用。 我先发你一张价格透明说明图，上面有几个常见项目的明码标价，无隐形消费。 https://lf3-appstore-... |
| SF9_021 | 如果我约了不去会怎么样 | S4_appointment_negotiating | appointment_intent | False | likely_appointment_push, too_long_single_message | 没关系的，预约后如果临时有变化来不了，提前跟我们说一声就好，我们可以帮你调整时间。  你这边是已经确定了要预约，只是时间上还不确定吗？如果是的话，我可以先帮你看看可约的时间段，你选... |
| SF9_022 | 我从外地过去，能不能安排久一点 | S4_appointment_negotiating | appointment_intent | False | likely_appointment_push, likely_store_match | 好的，理解您从外地过来想多安排一些时间。不过，我需要先确认您方便到哪个城市或哪个区到店，这样才能帮您查询最近的门店和可约时间。 |
| SF9_023 | 我怀孕了，可以先约面诊吗 | S1_icebreaking | appointment_intent | False | likely_appointment_push, likely_store_match | 恭喜您！可以先约面诊，医生会先帮您做专业的评估。为了帮您确认最近门店和方便的时间，需要先了解一下，您方便到哪个城市或哪个区到店呢？ |
| SF9_024 | 我是未成年，可以约吗 | S0_wait_icebreak | appointment_intent | True |  | 您好，我是您的专属客服，很高兴为您服务！请问有什么可以帮您的呢？ |

## 说明

- 这是自动初筛报告，最终结论需要结合人工查看回复语气和业务上下文。
- 对 SF1 来说，疑似主动预约、门店匹配、价格活动、过早推荐项目都默认视为风险。
- 如果工作流没有返回 subflow 字段，本报告主要根据 scene、intent 和输出话术进行判断。
