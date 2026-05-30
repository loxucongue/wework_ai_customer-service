# DIRECT_REPLY 测试结果分析

## 汇总

| 指标 | 数量 |
|---|---:|
| 总用例 | 24 |
| 自动通过 | 16 |
| 自动通过率 | 66.67% |
| API错误 | 0 |
| 空回复 | 0 |
| 兜底失败话术 | 0 |
| 疑似转人工 | 0 |
| 疑似主动预约 | 6 |
| 疑似门店匹配 | 3 |
| 疑似价格/活动 | 1 |
| 疑似过早推荐项目 | 0 |
| 问题超过1个 | 1 |
| 单条过长 | 1 |

## 明细

| case_id | 当前消息 | scene | intent | 通过 | 问题 | 回复摘要 |
|---|---|---|---|---|---|---|
| DR_001 | 好的 | S4_appointment_negotiating | appointment_confirm | False | likely_appointment_push, likely_store_match | 好的，我先帮你确认一下周六下午的时间。不过要先确认你方便在哪个城市或哪个区到店，我才能帮你查最近门店和具体时间。 |
| DR_002 | 嗯嗯 | S1_icebreaking | emotion_chat | True |  | 嗯嗯，不着急的~要是有想了解的项目或者担心的问题，随时和我说就好啦。 |
| DR_003 | 谢谢 | S7_dealed_active | emotion_chat | True |  | 不客气呀~有任何需要随时和我说哦~ |
| DR_004 | 不用了 | S4_appointment_negotiating | appointment_cancel | True |  | 好的，了解。如果之后有需要或者想重新安排时间，随时和我说就好。 |
| DR_005 | 先这样吧 | S3_deep_consult | emotion_chat | True |  | 好哒～如果之后想聊聊或者了解项目，随时找我就好😊 |
| DR_006 | 晚点再说 | S4_appointment_negotiating | appointment_cancel | False | likely_appointment_push | 好的，那等你方便的时候我们再约。想预约的时候随时和我说，我帮你查时间。 |
| DR_007 | 明天去要带什么 | S5_appointed_waiting | pre_visit_question | True |  | 您好，我是您的专属客服，很高兴为您服务。请问有什么可以帮您的吗？ |
| DR_008 | 到店要做多久 | S4_appointment_negotiating | pre_visit_question | True |  | 您好，我是您的专属客服，很高兴为您服务。请问有什么可以帮您的吗？😊 |
| DR_009 | 可以化妆过去吗 | S5_appointed_waiting | pre_visit_question | True |  | 您好，我是您的专属客服。有什么可以帮您的吗？ |
| DR_010 | 需要空腹吗 | S4_appointment_negotiating | pre_visit_question | True |  | 您好，我是您的专属客服。很高兴为您服务！ |
| DR_011 | 我到了 | S6_visited_not_deal | emotion_chat | False | likely_appointment_push | 到店啦~可以先联系前台确认下流程，有任何需要帮忙的随时和我说哦。 |
| DR_012 | 我在路上 | S5_appointed_waiting | emotion_chat | True |  | 好哒，路上注意安全，别着急哦~ |
| DR_013 | 停车方便吗 | S5_appointed_waiting | store_inquiry | False | likely_appointment_push, likely_store_match | 您好，您预约的是我们厦门思明店哦。 关于停车：思明店这边有合作的商场停车场，到店后可以咨询前台领取停车券，具体优惠以门店当日为准。您也可以选择公共交通，地铁1号线到“镇海路站”出来... |
| DR_014 | 地址再发我一下 | S5_appointed_waiting | store_inquiry | False | likely_store_match, too_long_single_message | 好的，马上发您。 【XX医美中心】 📍地址：XX市XX区XX路XX号XX大厦X楼 ⏰营业时间：周一至周日 10:00-20:00 🚇地铁：X号线XX站X号口出，步行约5分钟 �... |
| DR_015 | 不用回复了 | S3_deep_consult | emotion_chat | True |  | 好的，不打扰你啦～如果之后想聊聊变美或者其他事情，随时找我就行～ |
| DR_016 | 我不考虑了 | S4_appointment_negotiating | appointment_cancel | False | likely_appointment_push | 好的，了解。那我先帮你把之前的预约记录取消了。 |
| DR_017 | 再看看 | S1_icebreaking | emotion_chat | True |  | 没关系，先了解也可以的，不用急着决定哦~ |
| DR_018 | 收到 | S3_deep_consult | price_inquiry | False | likely_price_or_campaign | 你想了解哪个具体项目的价格呀？不同项目和活动价格不一样，你可以告诉我项目名称或想改善的问题，我帮你确认下~ |
| DR_019 | 没事了 | S0_wait_icebreak | emotion_chat | True |  | 好的，那你先忙，有需要随时再找我哦~ |
| DR_020 | 我先问问朋友 | S3_deep_consult | emotion_chat | True |  | 好的呀，和朋友商量一下也很正常～有任何想法后续随时和我说就好～ |
| DR_021 | 我周六改周日 | S5_appointed_waiting | appointment_change | True |  | 好的，收到你从周六改到周日的需求。我先帮你确认一下周日的时间是否方便。 |
| DR_022 | 取消吧 | S5_appointed_waiting | appointment_cancel | False | likely_appointment_push, too_many_questions | 好的，我先帮你确认取消。可以简单和我说下是什么原因想取消明天的预约吗？是时间不合适，还是其他安排有冲突？ |
| DR_023 | 先别联系我了 | S8_silent_return | silence_return | True |  | 您好，我是您的专属客服。有什么可以帮您的吗？ |
| DR_024 | 可以 | S0_wait_icebreak | emotion_chat | True |  | 您好！很高兴为您服务。请问有什么可以帮您的吗？ |

## 说明

- 这是自动初筛报告，最终结论需要结合人工查看回复语气和业务上下文。
- 对 SF1 来说，疑似主动预约、门店匹配、价格活动、过早推荐项目都默认视为风险。
- 如果工作流没有返回 subflow 字段，本报告主要根据 scene、intent 和输出话术进行判断。
