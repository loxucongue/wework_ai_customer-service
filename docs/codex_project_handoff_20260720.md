# AI Paths 项目 Codex 交接文档

生成时间：2026-07-20
当前分支：`codex/prompt-hierarchy-transaction-20260716`
本文创建时基线提交：`bd877d412 fix: 切换语音转写到豆包ASR`
当前分支和线上 release 会随部署变化，接手时必须分别执行 `git log -1 --oneline` 和 `readlink -f /opt/ai-paths/current` 获取真实状态，不要把本文创建时快照当成当前值。

## 0. 安全声明

这份文档用于把项目上下文交给其他 Codex 窗口。不要把真实密钥、完整手机号、支付截图、客户外部联系 ID、带 token 的媒体签名 URL 写进提交或报告。

本文只记录密钥所在位置和环境变量名，不记录任何密钥值。其他窗口如果需要调试线上接口，应在本机或服务器 `.env` 内读取，不要在终端、日志和最终回复中打印。

## 1. 新窗口快速接手步骤

1. 先读仓库根目录的 `AGENTS.md`，再读本文档。
2. 执行 `git status --short`、`git branch --show-current`、`git log -1 --oneline`，确认当前基线。
3. 修改前先读相关代码、配置和测试，不要凭记忆改。
4. 搜索文件优先用 `rg`；并行读取用 `multi_tool_use.parallel`。
5. 手工编辑文件用 `apply_patch`。不要用容易破坏中文编码的 shell 拼接写文件。
6. 不提交 `.env`、`.tmp_runtime/`、临时日志、客户原始隐私数据。
7. 不使用 `git reset --hard`、`git checkout --` 等会覆盖别人改动的命令，除非用户明确要求。
8. 回复效果相关改动必须先做单节点模型测试，再部署后做全链路线上 smoke。

## 2. 项目宪法

本项目客服回复链路只有一个核心原则：

- 大模型负责业务语义、客户心理、销售节奏和自然表达。
- 代码负责事实输入、数据清洗、工具调用、结构归一、幂等、安全边界和非业务兜底。

禁止新增 Python 关键词分支来决定普通销售意图、客户顾虑、成交阶段或话术节奏。业务回复错了，优先检查：

1. Planner/Reply prompt。
2. 模型输入上下文。
3. 工具事实是否正确。
4. 模型选择和超时重试。
5. SOP/精准回复配置是否把业务规则表达清楚。

代码可以保留硬保护，但硬保护必须是事实、安全、结构或协议边界，不是普通业务判断。

允许代码硬保护的典型范围：

- 消息 JSON/schema 合法。
- 不允许普通客户消息空回复。
- 预约金金额和人数一致：1/2/3/4 人分别 10/20/30/40。
- 已付后不再发 `payment_collection`。
- 当前健康高风险、投诉退款、付款异常、强拒绝、人数超过 4 人时禁止自动发卡。
- `store_address` 必须有真实 `store_id`。
- 图片 URL 必须来自真实案例/素材事实。
- 档期、预约、排客不能编造。
- 距离不能对客户输出公里、分钟、车程。
- 禁止绝对承诺：保证、100%、绝对不会反黑/留疤/反弹。
- 禁止泄露内部 `human_handoff_notice`。

## 3. 协作和代码规范

默认用中文沟通，直接、专业、少废话。用户提出方案时要判断是否符合架构和项目宪法；有冲突要指出，不要盲目迎合。

编码要求：

- 优先遵循已有文件结构和代码风格。
- 不做无关大重构。
- 对复杂改动先给简短计划；小修直接完成。
- 用 `apply_patch` 改文件。
- PowerShell 下特别注意中文编码。出现连续问号、Unicode 替换字符、常见 mojibake 片段等疑似乱码要立即检查。
- 修改中文 prompt、SOP、精准回复后，至少运行一次乱码扫描。
- 不要在报告里打印完整 token、签名 URL、手机号、支付凭证、外部联系 ID。

常用检查：

```powershell
git diff --check
python -m compileall -q ai_paths/app
PYTHONPATH=ai_paths python -m pytest workflow_tests -q
# 建议用脚本或 rg 扫描连续问号、Unicode replacement char 和常见 mojibake 片段；
# 本 handoff 文档不要写入原始乱码样例，避免扫描误报。
```

## 4. 代码库和服务器位置

本地工作区：

- 当前仓库：`C:\Users\24159\.codex\worktrees\5a63\coze_cli_project`
- 另一个可见 workspace root：`E:\ai_code\pmy_rpa`

服务器：

- 主机：`47.252.81.104`
- SSH key：`C:\Users\24159\.ssh\ai-paths-aliyun.pem`
- 线上当前目录：`/opt/ai-paths/current`
- release 目录：`/opt/ai-paths/releases/`
- 共享运行数据：`/opt/ai-paths/data/`
- 共享门店快照：`/opt/ai-paths/data/store_snapshot.json`
- 线上环境变量：`/opt/ai-paths/.env`
- systemd 服务：`ai-paths.service`
- 健康检查：`http://127.0.0.1:8000/health`

门店快照必须使用共享绝对路径，不要依赖 release 内的 `data/store_snapshot.json`。干净 release 不应覆盖每日门店快照。

## 5. 密钥和配置位置

不要在任何文档或回复里写密钥值。只允许写环境变量名和配置路径。

本地配置：

- 仓库根目录 `.env`
- 可能还有 `ai_paths/.env`

线上配置：

- `/opt/ai-paths/.env`

重要环境变量类别：

- API 鉴权：`AI_PATHS_API_KEY`、`AI_EXTERNAL_API_KEY`、`ALLOW_MISSING_EXTERNAL_API_KEY`
- 模型：`MODEL_PROVIDER`、`MODEL_FAST`、`MODEL_PLANNER`、`MODEL_REPLY`、`MODEL_STRONG`、`MODEL_PLANNER_FALLBACKS`、`MODEL_REPLY_FALLBACKS`、`MODEL_RELAY_API_KEY`、`CLAUDE_RELAY_API_KEY`、`VOLCENGINE_ARK_API_KEY`
- 平台接口：`PLATFORM_AGENT_BASE_URL`、`PLATFORM_AGENT_TOKEN`
- 主动发送：`OUTREACH_SEND_BASE_URL`、`OUTREACH_SEND_AGENT_TOKEN`
- 系统主动触发：`OUTREACH_SYSTEM_BASE_URL`、`OUTREACH_SYSTEM_TOKEN`
- 运行数据：`AI_PATHS_DB_PATH`、`STORE_SNAPSHOT_PATH`
- SOP 包：`SOP_REPLY_PACKS_PATH`
- 精准回复：`PRECISION_QA_PLAYBOOK_PATH`
- Coze 工具：`COZE_API_BASE`、`COZE_OAUTH_CLIENT_ID`、`COZE_OAUTH_PUBLIC_KEY_ID`、`COZE_OAUTH_PRIVATE_KEY_FILE`、`KB_WORKFLOW_ID`、`GEOCODE_WORKFLOW_ID`、`DISTANCE_WORKFLOW_ID`
- 旧语音方案：`AUDIO_TO_TEXT_WORKFLOW_ID` 已被豆包 ASR 替换，保留仅用于历史审计。

豆包语音转写：

- `DOUBAO_ASR_APP_KEY`
- `DOUBAO_ASR_ACCESS_KEY`
- `DOUBAO_ASR_SECRET_KEY`
- `DOUBAO_ASR_API_KEY`，新控制台模式可用，当前线上可为空。
- `DOUBAO_ASR_RESOURCE_ID`，默认 `volc.seedasr.auc`
- `DOUBAO_ASR_SUBMIT_URL`
- `DOUBAO_ASR_QUERY_URL`
- `DOUBAO_ASR_TIMEOUT_SECONDS`
- `DOUBAO_ASR_POLL_INTERVAL_SECONDS`
- `DOUBAO_ASR_POLL_ATTEMPTS`

当前实现兼容豆包旧控制台 `X-Api-App-Key + X-Api-Access-Key` 和新控制台 `X-Api-Key`。`Secret Key` 当前只存储，不在 HTTP 调用里直接发送。

## 6. 应用入口和主链路

FastAPI 主入口：`ai_paths/app/main.py`

主要接口：

- `POST /chat`
- `POST /reply`
- `POST /chat/workflow-compatible`
- `POST /reply/workflow-compatible`
- `POST /sop/events`
- `GET/PUT /admin/sop-reply-packs`
- `GET/PUT /admin/precision-qa-playbook`
- `GET /admin/sop-events`
- 其他画像、门店、日志、运行记录管理接口。

workflow-compatible 归一层：

- 文件：`ai_paths/app/services/workflow_compat.py`
- 负责把平台 `workflow_id + parameters.content` 这类请求转换成内部 `ChatRequest`。
- 定位卡片应拼成 `定位卡片：标题；地址；经纬度`，不要再写成 `门店位置：`，避免模型误以为客户说的是门店名。
- 语音消息 `msgtype=voice` 进入语音转写。

聊天运行时：

- 文件：`ai_paths/app/chat_runtime.py`
- 负责 SOP Gate、Planner、工具、Reply、发送控制、trace 和最终响应。

LangGraph 节点：

1. `layer_1_input_normalization`：输入归一、图片/语音/定位事实，不做销售判断。
2. `layer_2_background_context`：历史、画像、订单、门店 scope、发送记录。
3. `planner_brain`：唯一业务语义和销售决策中心。
4. `execute_actions`：执行工具，只产出事实。
5. `synthesize_reply`：最终真人微信回复。
6. 后台 `profile_event_extractor`：长期画像更新，不覆盖当前轮事实。

图构建文件：

- `ai_paths/app/graph/builder.py`
- `full_graph`
- `planner_graph`
- `finalize_graph`

## 7. 模型节点职责

### SOP Gate

SOP Gate 是实时客户消息进入普通聊天链路时的路由判断。它看当前客户消息、近聊、未完成 SOP/精准回复候选，决定：

- 直接发 SOP 包。
- 精准回答后顺带 SOP。
- 交普通 AI 工具/Planner/Reply。
- 忽略平台自动开场。

SOP Gate 不应该做 `/sop/events` 的主动触达 no_send 逻辑。客户补充城市、区、地标、定位、付款、语音、实时问题时，优先交普通 AI 或精准回答，不要机械发下一个包。

### Planner

Planner 是业务语义中心。它决定：

- 当前客户真正问什么。
- 是否属于精准回复问题。
- 是否需要工具。
- 是否发案例图、门店卡、预约金卡。
- 当前应该回到哪条销售主线。
- 支付动作：`none/explain/send_now/resend/manual_transfer/after_paid_next_step/ask_party_size`
- 是否已有新的进展：门店、时间、付款、登记等。

Planner 不应该输出大段硬模板。它应输出结构化决策、要点和结构消息要求。

### Reply

Reply 负责把 Planner 决策、工具事实、业务规则和历史承接生成真实微信话术。

要求：

- 像真人销售，不要“尊敬的客户”“温馨提醒”“继续处理”“安排下一步”。
- 先精准回答当前问题，再自然回到未完成主线。
- 不机械复述上一轮规则。
- 需要发卡时本轮直接带 `payment_collection`，不要让客户翻旧卡。
- 不需要发卡时可以做预约金价值解释、登记、门店或活动推进。

### Profile

画像只保存长期事实、偏好、顾虑、置信度和时间。画像不能覆盖当前消息、近 20 条聊天、工具事实或订单事实。

### Vision

图片理解只提取事实。客户发脸部图片时，不做线上诊断；回复上仍应说多数斑点可以做，发案例/引导到店检测。

## 8. SOP Event 和 SOP 话术包

`/sop/events` 是主动触达接口。平台调用它表示“现在可能需要触达客户”，不是要求机械按 `delay_minutes` 发送固定时间点话术。

当前业务理解：

- 如果最近正在聊天，不主动打断。
- 如果客户沉默且上次任务未解决，先轻触一次，让客户开口。
- 如果之前已经轻触过仍未回，应往下一步主线包推进。
- SOP 包可以由大模型结合上下文润色，让它更像自然连续聊天。
- 润色不能改变硬事实、图片、卡片、金额、门店 ID、支付结构。
- 夜间主动触发应过滤，普通客户主动消息不受夜间限制，必须回复。

Plan A 当前实现：

- 平台仍负责触发时间；SOP Event 模型负责 `send/merge/skip/defer`、阶段选择和文本融合。
- 北京时间 `00:00-08:00` 的主动事件先压入 backlog；最近 30 分钟正在聊天时不插入主动 SOP。
- `SOP_EVENT_DAILY_TOUCH_SOFT_LIMIT` 默认 `2`，只是提供给模型的当日软上限事实。代码同时提供当天/历史次数、最近发送时间、连续沉默、客户新进展和 backlog。
- 夜间积压最多恢复两个相邻候选；候选必须从最早未完成包开始。模型越级、频率理由没有计数证据、冲突理由没有近期客户或结构事实来源时，结构层要求同模型 repair，不替模型生成客户话术。
- 当前 `/sop/events` 执行链没有普通 Reply 接管执行器，`handoff_to_ai_reply` 仅保留协议能力但当前资格恒为 false，防止“交给 AI”后实际无人回复。
- `sop_platform_task` 不受 `ai_auto_reply` 影响，直接使用平台传入 actions 并经过结构清洗、安全校验和幂等发送；不得进入 SOP Event 模型或普通 Reply。
- 详细设计和最终测试结果见 `docs/sop_proactive_wakeup_ab_design_20260720.md`。

SOP 包配置：

- 文件：`config/sop_reply_packs.json`
- 字段包括：`id`、`enabled`、`scope`、`scopes`、`sop_category`、`name`、`purpose`、`mainline_stage`、`direct_answer_capabilities`、`order`、`send_once`、`event_type`、`delay_minutes`、`day_stage`、`customer_state`、`stage_tag`、`triggers`、`reply_messages`

当前包数量：13。

当前包列表：

| id | 名称 | scope/用途 | 启用 |
| --- | --- | --- | --- |
| `s10_new_customer_opening` | 新客破冰 | 新客介绍、问城市/区域 | 是 |
| `s10_need_and_case` | 需求与效果承接 | 需求、案例、效果铺垫 | 是 |
| `s10_activity_intro` | 活动介绍 | 活动价格和价值铺垫 | 是 |
| `s10_objection_resolution` | 收费与预约金顾虑处理 | 收费、预约金顾虑 | 是 |
| `s10_deposit_close` | 预约金推进 | 平台任务收款 | 否 |
| `event_s10_intro_1min` | 事件-1分钟介绍补发 | 定时补发介绍 | 否 |
| `event_s10_store_prompt_5min` | 事件-5分钟问地址 | 沉默问地址 | 是 |
| `event_s10_effect_warmup_30min` | 事件-30分钟效果铺垫 | 效果案例铺垫 | 是 |
| `event_s10_price_quote_60min` | 事件-60分钟报价 | 报价和活动 | 是 |
| `event_s10_deposit_push_70min` | 事件-70分钟通单收款 | 预约金推进 | 是 |
| `event_s10_unpaid_effect_1h` | 事件-未付款1小时效果跟进 | 未付效果跟进 | 是 |
| `event_s10_unpaid_video_2h` | 事件-未付款2小时操作视频 | 操作视频 | 是 |
| `event_s10_day1_final_close` | 事件-当天18点最后收单 | 当日最后收单 | 是 |

重要风险：SOP 配置页面保存的是运行时配置。部署时如果 release 内 `config/sop_reply_packs.json` 覆盖线上文件，页面改过的图片和文案会回退。要长期解决，应把 `SOP_REPLY_PACKS_PATH` 指向 release 外共享路径，或把页面改动导出后提交进 Git。

清画像、流程进度、测试计数时，用户通常也期望清除已经成功发送过的 `send_once_key`。

## 9. 精准回复配置

精准回复配置默认文件：

- `ai_paths/app/policies/precision_qa_playbook.json`

管理服务：

- `ai_paths/app/services/precision_qa_playbook_service.py`
- `PRECISION_QA_PLAYBOOK_PATH` 可覆盖默认路径。
- 服务默认路径和可写路径要检查，避免页面保存到一个文件、模型读取另一个文件。

顶层字段：

- `version`：版本。
- `purpose`：精准回复库用途。
- `global_answer_policy`：所有精准回复共用原则。
- `questions`：问题库列表。

`global_answer_policy` 当前含义：

- `first_answer`：先回答客户真正问的那一点。
- `confidence`：先给信心，再说明到店检测边界。
- `mainline_resume`：精准回答后用封闭式问题或下一主线 SOP 回到成交主线。
- `variation`：示例用于校准，不能逐字复读。
- `facts`：价格、退款、门店、图片、支付和预约事实必须来自业务规则或工具事实。

每个精准问题字段：

- `id`：问题 ID，例如 `one_session_effect`。
- `intent_definition`：语义定义，不是关键词。
- `customer_psychology`：客户心理。
- `question_role`：在成交中的阻碍级别，例如 `core_blocker`。
- `must_answer`：必须回答的要点。
- `must_not_substitute`：不能用什么替代回答。
- `first_ask_strategy`：第一次问时怎么答。
- `repeated_ask_strategy`：客户反复问时怎么换角度加深。
- `allowed_confidence`：允许的信心表达。
- `forbidden_claims`：禁止承诺。
- `evidence_requirement`：所需事实来源，例如业务规则、工具、无。
- `resume_mainline_stage`：回答后回到哪个销售主线。
- `reply_examples`：优秀话术示例，只作校准，不可机械照抄。

当前核心精准回复主题包括：

- 一次能不能做好、是不是要反复做。
- 价格透明、隐形消费。
- 反黑、反弹、安全、留疤。
- 效果和案例。
- 门店距离、广告定位质疑。
- 手能不能做、手和脸价格。
- 不属于线上活动的项目范围，例如除皱、祛眼袋、黑眼圈、水光等。痘印、痘坑属于当前线上淡斑活动改善范围。

## 10. 销售主线和“一句话带过”

主线不是硬模板，而是销售目标顺序：

1. 新客破冰和技术/项目认知。
2. 城市、区域、门店落点。
3. 需求和案例效果铺垫。
4. 活动和价格铺垫。
5. 预约金价值和付款决策。
6. 已付后登记姓名、电话、门店、到店日期/时间意向。

客户问精准问题时，不能拿 SOP 大段介绍替代。正确方式：

1. 先精准回答客户真正关心的问题。
2. 如果是轻微偏题，用一句话带过。
3. 立刻自然回到最早未完成主线。
4. 尽量使用封闭式问题推进，例如“您是看到线上活动进来的对吧？”、“您是在 XX 区附近方便些对吗？”

典型例子：

- 客户问“一次能不能好”：先回答多数客户一次能看到改善、具体看斑点类型/深浅/时间，不能只罗列雀斑晒斑都能做；随后回到活动或案例。
- 客户问“手能不能做”：手上斑点也属于活动范围，可以同活动价，但不要主动展开手脸同做细节；随后回到活动和门店。
- 客户问“皱纹/眼袋/黑眼圈/水光”：线上活动不是这些项目，避免到店客诉；随后拉回斑点改善。

## 11. 当前业务规则记忆

客户基础：

- 投流进来的客户默认是对斑点改善有兴趣的人群。
- 不要让客户先发照片做线上诊断。
- 看到客户脸部图片，可以承接“多数斑点可以先到店检测评估”，不要做医疗诊断。

效果：

- 客户问效果、怕没效果、怕反黑、要效果图时，若近期没有真实案例图发送证据，Planner 应调用 `kb_search(case_studies)`，Reply 应发送真实案例图。
- SOP 已完成、文字说过“我给您看案例”不等于真实图片证据。
- 回答顺序：先给信心，再给案例/参考，再引导到店检测。

反黑/留疤/伤肤：

- 允许“一般不会反黑”“绝大多数客户反馈正常/不错”。
- 禁止“保证不会”“100%不会”“绝对不会”。
- 要说到店先检测评估，按皮肤状态操作，适合再安排。

价格和预约金：

- 当前活动价：268。
- 每位预约金：10 元。
- 预约金用于锁活动资格，不是锁定具体时间。
- 到店抵扣；做的话再付 258。
- 未做或不满意可退预约金。
- 没有隐形消费，不强制加项目。
- 旧口径“不做不退10元”已废弃，发现必须修。

发卡：

- 预约金卡必须有同门店、同金额的有效未付订单，或本轮开单/复用成功并取得真实 `order_id`。
- 客户有新的成交进展时可以直接发/重发本轮 `payment_collection`，但仍需满足上述订单关联事实。
- 不要说“刚才那张卡还能点”“翻一下前面的入口”。
- 仍禁止在已付、健康风险、投诉退款、强拒绝、人数超过 4 人等安全禁区发卡。

支付后：

- 支付成功截图、转账成功截图、订单接口 `prepay_paid` 都可以作为已付事实。
- 平台 `prepay_required` 表示需支付预约金，`prepay_paid` 表示已支付预约金。
- `【未知消息类型】` 在当前业务中常代表客户转账，应在输入归一层作为支付证据处理。
- 已付后当前临时流程是登记姓名、电话、门店、到店日期/时间意向。
- 普通已付流程目前禁止 `available_time/create_order_plan`，不承诺正式排期完成。

门店：

- 所有门店事实必须来自工具、平台或共享门店快照。
- 客户给省、市、县、区、镇、村、地标、定位卡都要尽量给出稳定回复。
- 如果客户明确到区且该区有多家门店，直接发该区全部真实门店卡，让客户选。
- 如果一个地级市只有少量门店，也可以一次发该市全部门店卡。
- 县城、乡镇、村镇没有门店时，查父级城市/省内真实门店，推荐相对方便的真实门店，不要只说查不到。
- 广告定位质疑时，解释平台同城投放/定位展示机制，说明同城真实门店服务一致，再发可去门店卡。
- 不输出公里、分钟、车程。
- 地名歧义且无城市、省份、坐标时，简短反问确认城市/区。

项目范围：

- 手上斑点可以按活动价 268 承接。
- 客户没问手脸是否同价时，不主动强调两者价格差异。
- 客户追问手脸是不是一个价格时，按当前规则说明手也是活动价 268，但不能承诺手脸同做。
- 痘印、痘坑属于当前线上淡斑活动改善范围。除皱、祛眼袋、黑眼圈、水光等不是当前线上活动预约项目，要明确线上活动不含这些，避免客诉。

## 12. 语音转写

当前已从 Coze `Audiototxt` 工作流切到豆包 ASR。

代码：

- `ai_paths/app/services/voice_transcription.py`
- `ai_paths/app/services/workflow_compat.py`

线上 smoke 记录：

- request_id：`ac20e333-5348-40d6-803a-11a44ca98e8e`
- provider：`doubao_asr`
- 转写状态：`ok`
- 测试文本转成：`我在厦门湖里区。`
- ASR 耗时约 5.9 秒。
- 全链路耗时约 54 秒，说明 ASR 已通，但整体回复链路仍偏慢。

注意：

- 企微 signed media URL 需要先解析/下载到豆包可访问的音频。
- 日志里可以记录状态、provider、耗时，不要打印完整签名 URL。
- 语音失败时普通消息仍不能空回复，应给中性兜底或请客户发文字确认。

## 13. 工具和外部系统

Coze 工具：

- 知识库/案例：`kb_search`
- 地理编码：`geocode`
- 距离排序：`distance_calculate`
- 旧语音转写工作流已废弃。

平台 agent：

- 客户上下文。
- 订单查询。
- `prepay_required/prepay_paid`。
- 手机号同步。
- 门店/订单相关接口。

门店：

- 共享快照 `/opt/ai-paths/data/store_snapshot.json`。
- `customer_store_lookup` 和 `store_scope_summary` 都应使用完整共享快照降级。
- 快照修改后应无需发布代码即可刷新读取。

主动发送：

- `OUTREACH_SEND_*` 用于向客户发消息。
- 主动发送真实客户前必须得到用户明确确认。
- 中文编码必须验证，避免发送成连续问号。

## 14. 部署流程

部署前：

1. 工作区确认。
2. 跑必要测试。
3. 提交干净 commit。
4. 不从 dirty 工作区发包。

典型流程：

```powershell
git diff --check
python -m compileall -q ai_paths/app
PYTHONPATH=ai_paths python -m pytest workflow_tests -q
git status --short
git add <files>
git commit -m "<message>"
```

服务器发布：

```powershell
# 本地创建 archive 后 scp 到服务器
# 服务器解压到 /opt/ai-paths/releases/<release-name>
# 然后：
ln -sfn /opt/ai-paths/releases/<release-name> /opt/ai-paths/current
systemctl restart ai-paths.service
curl -fsS http://127.0.0.1:8000/health
```

部署后：

- 用专用测试客户 smoke。
- 记录 request_id、耗时、Planner/Reply 原始 JSON、工具事实、最终消息。
- 不要用真实客户做无审核发送。

## 15. 测试体系

确定性测试：

```powershell
python -m compileall -q ai_paths/app
PYTHONPATH=ai_paths python -m pytest workflow_tests -q
```

最近全量结果：

- `528 passed, 1 warning`

常用专项：

- `workflow_tests/test_voice_transcription.py`
- `workflow_tests/test_platform_reply_runtime.py`
- `workflow_tests/test_sales_mainline_precision_qa.py`
- `workflow_tests/test_sop_event_flow.py`
- `workflow_tests/test_prompt_refactor_contract.py`
- `workflow_tests/test_store_scope_resilience.py`

单节点模型测试：

- 用于调 Planner、Reply、SOP Gate、SOP Event prompt。
- 使用模拟上下文和真实历史片段，不污染线上客户历史。
- 重点看语义、主线、真人感、事实安全。
- 不要用关键词命中率替代语义评估。

全链路线上测试：

- 部署后走真实接口。
- 验证 SOP Gate、Planner、工具、Reply、发送、日志、耗时、持久化。
- 必须使用专用测试客户或用户明确指定客户。

回复质量验收维度：

- 当前问题是否精准回答。
- 历史承接是否正确。
- 是否自然回到销售主线。
- 是否像真人微信聊天。
- 是否事实安全。
- 是否有成交动作。
- 是否无空回复、无 502、无结构错误。

## 16. 日志和排查

查日志时优先要这些字段：

- `request_id/event_id`
- HTTP 状态。
- 最终 `reply_messages`
- `sop_gate` 输出。
- Planner 原始 JSON。
- Reply 原始 JSON。
- 工具输入和工具事实。
- `conversation_fetch.status/used_message_count`
- `voice_transcription.status`
- `reply_control.sync_return/async_final`
- 节点耗时、模型耗时、token。

日志面板目标是能看到单个模型调用：

- 模型名。
- 输入 messages/prompt。
- 输出真实纯 JSON。
- fallback/hedge/timeout 情况。

如果线上界面看不到最终消息，要去运行记录和发送结果里查 `sync_return`、`async_final`、平台发送返回。

## 17. 当前已知风险

1. 全链路耗时仍偏长。最新语音 smoke 全链路约 54 秒，ASR 只占约 5.9 秒，主要慢在模型/工具/repair 链路。
2. `brain_v2_normalizer.py`、`reply_validation.py`、`current_turn_context.py`、`action_nodes.py` 仍偏大。后续只能做行为等价拆分，必须 golden test，不要一次大拆。
3. SOP 配置页面改动可能被部署覆盖。需要共享配置路径或把页面改动提交到 Git。
4. 精准回复配置的默认文件和可写路径可能不同。修改前要确认 `PRECISION_QA_PLAYBOOK_PATH`。
5. 旧文档和 `AGENTS.md` 后半段有终端显示乱码风险。不要复制乱码到新文件；如要修文档，单独处理 UTF-8。
6. 平台语音、定位、未知消息类型等输入格式仍可能变化。归一层要保留原始 payload 以便联调。
7. 真实客户主动发送前必须人工确认。以前发生过中文编码发成问号的问题，发送前必须确认 payload 编码。

## 18. 常见任务分工建议

主窗口作为主大脑，负责架构、规则冲突、业务方案和上线节奏。

可以交给其他 Codex 窗口的任务：

1. 单节点模型测试：维护 Planner/Reply/SOP Gate/SOP Event fixture 和报告。
2. SOP 配置页面：继续完善已经上线的 `/sop/precision` 精准回复编辑、预览和审计能力。
3. 门店地理边界：省、市、县、镇、村、地标、定位卡全场景测试。
4. 日志面板：展示模型输入 messages、prompt、纯 JSON 输出、耗时、fallback。
5. 性能分析：整轮耗时预算、模型 timeout、工具并发、repair 次数。
6. 文案知识库：把业务会议和销冠话术提炼进精准回复，不污染价格和退款事实。

其他窗口提交结果时必须汇报：

- 改了哪些文件。
- 有没有改变业务规则。
- 跑了哪些测试。
- 线上是否部署。
- 关键 request_id/event_id。
- 是否存在回滚点。

## 19. 最近关键提交和状态

最近关键提交：

- `bd877d412 fix: 切换语音转写到豆包ASR`
- `efefb442b fix: 增加语音转写重试`
- `11cbe6f1b fix: 支持企微语音消息转写`
- `34fea45f9 fix: 保留小城市门店集合事实`
- `52f9ff34c fix: 小规模城市门店一次发全`

当前线上状态：

- `/opt/ai-paths/current` 指向 `ai-paths-server-20260720160939-doubao-asr-bd877d412f79`
- `ai-paths.service` 已重启并通过 health check。
- 豆包 ASR smoke 已通过。
- 全量确定性测试最近为 `528 passed, 1 warning`。

## 20. 不要忘记的长期经验

- 干净 release 不应携带或覆盖运行门店快照，必须依赖共享 `STORE_SNAPSHOT_PATH`。
- 效果图是否已发，只能看真实图片发送证据，不能看 SOP 阶段完成。
- 订单关联是发卡硬前置；新成交进展可以本轮直接发卡，但必须先有匹配有效订单或本轮开单/复用成功。
- 首次加微固定 SOP 在客户已回复或会话拉取失败时不能空历史降级乱发。
- SOP Event 被触发意味着需要判断是否主动触达，不等于按时间机械发固定包。
- 精准问答必须先回答客户心理核心，再回主线。
- 回复问题不要修成 Python 关键词兜底；优先修模型输入和提示词。
