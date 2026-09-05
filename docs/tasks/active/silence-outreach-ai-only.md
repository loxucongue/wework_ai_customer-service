# silence-outreach-ai-only

- status: active
- owner: Codex
- base_branch: main
- base_sha: `f57a19fef41a6744e884459728e96ad022a35d40`
- production_verified_at: 2026-09-05 17:23 Asia/Shanghai
- production_releases: symlink=`f57a19fe`; running control/reply/worker=`5b90e79e`

## 目标

- 将已开口沉默唤醒开放到全部企微账号。
- 沉默阈值调整为销售/AI 最后回复后 1 分钟。
- 不再限制加微时间，但只处理本次启用水位之后形成的待回复状态，防止历史积压瞬间触达。
- 生成计划前和实际发送前都必须读取平台会话状态；只有明确 AI 模式才能继续，人工或未知状态一律不处理。

## 非目标

- 不改变 V3 Reply、跟进知识召回、逼单 Shadow 和支付权限。
- 不把普通跟进序列自动转换为新的发送链路。
- 不回放上线前形成的历史沉默客户。

## Change contract

- type: 主动触达行为变更与生产发布
- scope: 沉默候选、AI 模式门禁、配置管理、worker 执行与测试
- risk: 全账号和 1 分钟阈值会放大发送量；状态接口异常可能降低触达量；遗漏门禁会误触达人工会话
- validation: 确定性单测、只读线上状态接口抽样、零发送预发布验证、部署后配置/worker/首批任务审计
- rollback: 立即设置 `OUTREACH_FIRST_DAY_SILENCE_ENABLED=false` 并重启 worker；必要时回滚到上线前 release

## 涉及模块与文件所有权

- `ai_paths/app/config.py`
- `ai_paths/app/routers/outreach_admin.py`
- `ai_paths/app/services/outreach/**`
- outreach 相关测试
- 相关架构/运行合同与任务历史

## 不可破坏合同

- 客户状态未知时失败关闭，不能猜测为 AI。
- 发送前必须重新拉取客户会话、AI/人工状态和订单状态。
- 客户新回复、明确退订、删除好友、人工接管、健康风险和交易终态必须阻断。
- 客户边界保持 `corp_id + wechat + external_userid/customer_id`。

## 已确认事实与证据

- 生产当前开关已启用，阈值 3 分钟，仅允许 `DY258,SL0069,dy8832,SL2491,WW0743`。
- worker 会自动激活并执行首日沉默计划；逼单延时仍为 Shadow。
- 当前候选流程只检查好友关系，尚未在计划生成前和发送前显式要求 `ai_auto_reply=true`。
- 生产 symlink 已指向 `f57a19fe`，但三个运行进程仍报告 `5b90e79e`，发布时必须统一重启到同一 SHA。

## 已完成

- 读取项目宪法、架构、运行边界、任务治理和当前生产状态。
- 现场核验生产开关、账号白名单、服务状态和运行 SHA。
- 实现空白名单覆盖全部企微、1 分钟阈值和不限加微时间。
- 增加部署启用时间水位，只接纳水位后的销售/AI 出站消息。
- 在计划生成前和每次发送前增加平台 AI 模式双重门禁；人工明确阻断，未知状态失败关闭。
- 清除只有终态任务的历史空壳计划对新周期的错误阻塞，并为平台预检增加批量预算保护。

## 待办

- 合入 main、统一部署并执行上线后审计。

## 测试结果

- `python -m pytest -q`：197 passed。
- `python -m compileall -q ai_paths/app`：通过。
- 专项覆盖：空白名单全账号、1 分钟默认值、老客户可进入、启用水位、人工门禁、状态未知失败关闭、发送前复检、空壳计划释放。

## 发布与回滚

- 上线前保存当前运行 release `ai-paths-unified-20260905-160551-5b90e79e`。
- 首批任务重点检查 AI/人工门禁、历史积压阻断、1 分钟阈值和实际发送结果。

## 待沉淀的长期结论

- “加微时间不限”与“历史积压不回放”必须通过启用水位同时表达。
- 主动触达必须以平台明确 AI 模式为正向授权，人工或未知状态失败关闭。
