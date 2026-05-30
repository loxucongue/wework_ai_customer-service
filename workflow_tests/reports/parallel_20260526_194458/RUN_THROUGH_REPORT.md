# 全量跑通测试报告

- 测试时间：2026-05-26 19:44 左右
- 执行方式：14 个子流程用例文件并发发起，每个流程内部顺序执行
- 总请求数：336
- 结果目录：`workflow_tests/results/parallel_20260526_194458`
- 报告目录：`workflow_tests/reports/parallel_20260526_194458`

## 跑通结论

- HTTP/脚本错误：0
- 工作流非 0 返回：0
- 有回复输出：310 / 336
- 空回复：26
- 输出覆盖率：92.26%

接口和工作流执行层面已跑通，没有请求失败。

## 分流程统计

| 流程 | 用例数 | 有回复 | 空回复 | API错误 | 非0返回 | 覆盖率 |
|---|---:|---:|---:|---:|---:|---:|
| direct_reply | 24 | 24 | 0 | 0 | 0 | 100% |
| human_handoff | 24 | 22 | 2 | 0 | 0 | 91.67% |
| sf1_new_customer_reply | 24 | 24 | 0 | 0 | 0 | 100% |
| sf10_trust_build | 24 | 23 | 1 | 0 | 0 | 95.83% |
| sf11_emotion_companion | 24 | 21 | 3 | 0 | 0 | 87.5% |
| sf12_after_sales | 24 | 24 | 0 | 0 | 0 | 100% |
| sf2_profile_collect | 24 | 24 | 0 | 0 | 0 | 100% |
| sf3_project_consult | 24 | 24 | 0 | 0 | 0 | 100% |
| sf4_face_consult | 24 | 24 | 0 | 0 | 0 | 100% |
| sf5_competitor_response | 24 | 24 | 0 | 0 | 0 | 100% |
| sf6_store_match | 24 | 24 | 0 | 0 | 0 | 100% |
| sf7_price_consult | 24 | 8 | 16 | 0 | 0 | 33.33% |
| sf8_campaign_push | 24 | 21 | 3 | 0 | 0 | 87.5% |
| sf9_appointment | 24 | 23 | 1 | 0 | 0 | 95.83% |

## 空回复明细

| 流程 | case_id | scene | intent | 当前消息 |
|---|---|---|---|---|
| human_handoff | HH_015 | S3_deep_consult | bargain | 最低价多少，直接给底价 |
| human_handoff | HH_017 | S3_deep_consult | bargain | 你别废话，给最低价 |
| sf10_trust_build | SF10_002 | S1_icebreaking | trust_issue | 你们靠谱吗，不会是骗人的吧 |
| sf11_emotion_companion | SF11_013 | S3_deep_consult | bargain | 太贵了，我感觉自己负担不起 |
| sf11_emotion_companion | SF11_019 | S3_deep_consult | price_inquiry | 我已经问了很多次了，还是不确定 |
| sf11_emotion_companion | SF11_020 | S3_deep_consult | after_sales | 我怕恢复期影响上班，烦 |
| sf7_price_consult | SF7_001 | S3_deep_consult | price_inquiry | 水光多少钱 |
| sf7_price_consult | SF7_002 | S3_deep_consult | price_inquiry | 水光靓颜冻干粉套组是什么价格呢 |
| sf7_price_consult | SF7_003 | S3_deep_consult | price_inquiry | 祛斑大概什么价格 |
| sf7_price_consult | SF7_004 | S3_deep_consult | price_inquiry | 光子嫩肤单次多少钱 |
| sf7_price_consult | SF7_005 | S3_deep_consult | price_inquiry | 皮秒价格多少 |
| sf7_price_consult | SF7_006 | S3_deep_consult | price_inquiry | 热玛吉多少钱一次 |
| sf7_price_consult | SF7_007 | S3_deep_consult | price_inquiry | 超声炮贵不贵 |
| sf7_price_consult | SF7_009 | S3_deep_consult | price_inquiry | 我之前做过水光，还能用新客价吗 |
| sf7_price_consult | SF7_011 | S3_deep_consult | price_inquiry | 老客复购水光多少钱 |
| sf7_price_consult | SF7_015 | S3_deep_consult | bargain | 最低多少钱，直接给我底价 |
| sf7_price_consult | SF7_016 | S3_deep_consult | bargain | 能不能再便宜点 |
| sf7_price_consult | SF7_018 | S3_deep_consult | price_inquiry | 这个价格包含皮肤检测吗 |
| sf7_price_consult | SF7_019 | S3_deep_consult | price_inquiry | 只做半脸是不是便宜点 |
| sf7_price_consult | SF7_022 | S3_deep_consult | price_inquiry | 这个多少钱 |
| sf7_price_consult | SF7_023 | S3_deep_consult | price_inquiry | 刚刚那个PDRN三文鱼冻干修护套组价格呢 |
| sf7_price_consult | SF7_024 | S2_profile_collecting | price_inquiry | 先告诉我大概预算范围就行 |
| sf8_campaign_push | SF8_013 | S3_deep_consult | price_inquiry | 套餐活动多少钱 |
| sf8_campaign_push | SF8_019 | S3_deep_consult | price_inquiry | 淡斑新客体验价多少 |
| sf8_campaign_push | SF8_020 | S3_deep_consult | bargain | 水光活动最低价是多少 |
| sf9_appointment | SF9_019 | S4_appointment_negotiating | price_inquiry | 约之前能不能先告诉我价格 |

## 说明

- 本报告只看整体跑通：请求是否成功、工作流是否返回、是否有最终回复。
- 暂不把回复过长、多问题、疑似推进预约等作为失败项。
- SF8 活动查询依赖后续接口，当前若出现空回复或保守回复，可等接口接入后再复测。

## 回复内容合规初筛

本节是基于规则的内容合规初筛，不替代人工验收。判断重点是：是否空回复、是否有明显越界承诺、是否误入其他业务、是否暴露内部检索词、是否满足该子流程最低承接要求。

| 流程 | 用例数 | 内容初筛通过 | 需人工复核 | 内容失败 | 初筛通过率 |
|---|---:|---:|---:|---:|---:|
| direct_reply | 24 | 24 | 0 | 0 | 100% |
| human_handoff | 24 | 19 | 3 | 2 | 79.17% |
| sf1_new_customer_reply | 24 | 24 | 0 | 0 | 100% |
| sf10_trust_build | 24 | 23 | 0 | 1 | 95.83% |
| sf11_emotion_companion | 24 | 21 | 0 | 3 | 87.5% |
| sf12_after_sales | 24 | 24 | 0 | 0 | 100% |
| sf2_profile_collect | 24 | 24 | 0 | 0 | 100% |
| sf3_project_consult | 24 | 24 | 0 | 0 | 100% |
| sf4_face_consult | 24 | 24 | 0 | 0 | 100% |
| sf5_competitor_response | 24 | 24 | 0 | 0 | 100% |
| sf6_store_match | 24 | 24 | 0 | 0 | 100% |
| sf7_price_consult | 24 | 8 | 0 | 16 | 33.33% |
| sf8_campaign_push | 24 | 21 | 0 | 3 | 87.5% |
| sf9_appointment | 24 | 23 | 0 | 1 | 95.83% |

### 内容失败/复核明细

| 流程 | case_id | 级别 | 问题 | 当前消息 | 回复摘要 |
|---|---|---|---|---|---|
| human_handoff | HH_015 | fail | 空回复 | 最低价多少，直接给底价 |  |
| human_handoff | HH_016 | review | 转人工回复缺少承接对象 | 别人199，你们必须比他们便宜 | 多对比一下是对的。医美项目确实不能只看一个报价，更建议先把项目内容对齐，例如产品、剂量、部位、服务细节这些是否和对方一致，这样价格才有可比性哦。 |
| human_handoff | HH_017 | fail | 空回复 | 你别废话，给最低价 |  |
| human_handoff | HH_019 | review | 转人工回复缺少承接对象 | 我发病例给你看，能不能做 | 照片这边只能先看个大概方向，您可以告诉我最想改善的是斑点、毛孔、痘印、暗沉还是其他方面，我再帮您按方向分析。 |
| human_handoff | HH_021 | review | 转人工回复缺少承接对象 | 已经说了三遍了你还问，烦死了 | 抱歉，刚才重复提问了让你不耐烦了，有需要随时联系我。 |
| sf10_trust_build | SF10_002 | fail | 空回复 | 你们靠谱吗，不会是骗人的吧 |  |
| sf11_emotion_companion | SF11_013 | fail | 空回复 | 太贵了，我感觉自己负担不起 |  |
| sf11_emotion_companion | SF11_019 | fail | 空回复 | 我已经问了很多次了，还是不确定 |  |
| sf11_emotion_companion | SF11_020 | fail | 空回复 | 我怕恢复期影响上班，烦 |  |
| sf7_price_consult | SF7_001 | fail | 空回复 | 水光多少钱 |  |
| sf7_price_consult | SF7_002 | fail | 空回复 | 水光靓颜冻干粉套组是什么价格呢 |  |
| sf7_price_consult | SF7_003 | fail | 空回复 | 祛斑大概什么价格 |  |
| sf7_price_consult | SF7_004 | fail | 空回复 | 光子嫩肤单次多少钱 |  |
| sf7_price_consult | SF7_005 | fail | 空回复 | 皮秒价格多少 |  |
| sf7_price_consult | SF7_006 | fail | 空回复 | 热玛吉多少钱一次 |  |
| sf7_price_consult | SF7_007 | fail | 空回复 | 超声炮贵不贵 |  |
| sf7_price_consult | SF7_009 | fail | 空回复 | 我之前做过水光，还能用新客价吗 |  |
| sf7_price_consult | SF7_011 | fail | 空回复 | 老客复购水光多少钱 |  |
| sf7_price_consult | SF7_015 | fail | 空回复 | 最低多少钱，直接给我底价 |  |
| sf7_price_consult | SF7_016 | fail | 空回复 | 能不能再便宜点 |  |
| sf7_price_consult | SF7_018 | fail | 空回复 | 这个价格包含皮肤检测吗 |  |
| sf7_price_consult | SF7_019 | fail | 空回复 | 只做半脸是不是便宜点 |  |
| sf7_price_consult | SF7_022 | fail | 空回复 | 这个多少钱 |  |
| sf7_price_consult | SF7_023 | fail | 空回复 | 刚刚那个PDRN三文鱼冻干修护套组价格呢 |  |
| sf7_price_consult | SF7_024 | fail | 空回复 | 先告诉我大概预算范围就行 |  |
| sf8_campaign_push | SF8_013 | fail | 空回复 | 套餐活动多少钱 |  |
| sf8_campaign_push | SF8_019 | fail | 空回复 | 淡斑新客体验价多少 |  |
| sf8_campaign_push | SF8_020 | fail | 空回复 | 水光活动最低价是多少 |  |
| sf9_appointment | SF9_019 | fail | 空回复 | 约之前能不能先告诉我价格 |  |

### 内容维度结论

- 当前最主要的内容失败仍是空回复，集中在 SF7 价格咨询，其次是 SF8/SF11/HUMAN_HANDOFF/SF10/SF9 的少量空回复。
- 已输出内容中未发现大面积绝对效果承诺、明显医疗诊断、贬低竞品或暴露知识库检索词。
- HUMAN_HANDOFF 的 2 条底价强议价用例为空回复，需要补转人工兜底。
- SF7 需要优先加末端输出兜底，否则普通价格、复购价、部位价、底价等会继续无最终回复。
