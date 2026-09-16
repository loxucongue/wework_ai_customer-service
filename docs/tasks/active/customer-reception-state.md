# 客户接待状态同步接口

- 状态：deployed_pending_identity_configuration
- 发布结果：2026-09-16 已从 clean `main@93a99f5c50608e1513bd09272dcc3ac7600a20d5` 统一发布三个后端角色，release `ai-paths-unified-20260916-reception-93a99f5c`；生产迁移从 `20260910_01` 升至 `20260914_01`，三张接待状态表均为 0 行。Nginx 精确路由已部署并限定现有平台来源 IP；control/reply/worker/nginx active、`NRestarts=0`，V2 退役入口 410、控制面健康及管理页 200。缺少专用凭证、授权企业及权威成员映射，接口当前安全返回 `503 reception_state_not_configured`，尚未接收业务上报，也未接管任何消费者。
- 分支：`codex/customer-reception-state`
- base：`e2faaccdc4565973932884c04a004df0d9ac9173`
- 目标：实现 `POST /api/ai/customer/reception-state`，严格协议、企业授权、权威身份绑定、事务持久化及事件幂等/版本/关系生命周期检查。
- 当前授权：代码、迁移与精确路由已发布；不包含存量回填或状态来源切换。生产尚缺专用凭证、授权企业范围和权威成员映射，不得推测身份配置或宣称接口已经可接收业务上报。
- Change contract：新增控制面路由、接待状态服务/模型、独立存储表及增量迁移、直接回归测试和接口文档。风险集中在并发和身份映射；通过唯一约束、事务锁和失败关闭验证。应用回滚保留已收事件，非空表禁止降级删除。
- ownership：新增 reception_state 模块；`config.py` 的专用凭证配置；`main.py` 控制面路由注册；存储 schema 和新增迁移；直接测试。既有付款/性能和退款的运行链文件保持原状；SOP 优先级已进入 base。
- 非目标：本轮不让状态快照接管 Reply/SOP/主动跟进，不取消远程查询、不调整模型、发送或接待开关。完整执行链切换需单独验收。
- 身份取证：现有 customer_identity_links 的 platform_user_id 是平台人员 ID，不能证明企微成员 UserID；需要独立的权威成员/关系绑定，未知映射拒绝，不能直接把 UserID 当 wechat。
- 验证：严格字段、凭证/企业、重复/乱序/冲突、删除/重加、事务并发、重启、迁移与受保护回滚；全量确定性测试。MySQL 专项须真实隔离实例，缺少时明确未验收。
- 发布两道门：接口可用候选与本地状态接管分开；未完成聚合存量同步和发送端拦截联调前不得宣布远程查询移除。
- 回滚：应用回滚保留加法状态表，禁止重放历史发送；不删除已有事件。

## 2026-09-14 接口候选证据

- 原生产基线：`ff158e477b1b06ac2332770d51c78cf83ca6a68a`；发布回滚点为 `ai-paths-unified-20260914-sop-priority-ff158e47`。本次只新增接口、加法表和 Nginx 路由，不配置或猜测业务身份。
- 实现：独立控制路由、专用Bearer凭证＋企业范围、成员权威映射＋verified客户关系校验；事件哈希唯一键、MySQL行锁/SQLite写事务、完整通知审计、版本获胜、退役关系阻断和持久版本失效标记。HTTP200仅在提交后返回。Nginx候选配置精确路由到控制面。
- 精确文件集：`app/reception_state.py`、`app/routers/reception_state.py`、`app/services/reception_state_service.py`；`config.py`、`main.py`仅配置与注册；`storage/mysql_schema.py`、`schema.sql`、`store_base.py`新增3表；迁移`20260914_01`；`deploy/ai-paths.conf`新增精确路由；两份直接测试、接口与合同文档。没有修改Reply、SOP、唤醒消费者。
- 接口专项：SQLite/HTTP 27项通过；MySQL 8.4.7专项3项通过，涵盖多连接并发、乱序、冲突、重连、HTTP持久化和重放。完整全仓复测1075通过、12跳过；最后补充的MySQL HTTP测试在专项3项中单独通过。初次全仓既有50ms/80ms素材指纹时序测试偶发失败，未改该代码/测试，单项和全仓复测通过。
- 迁移演练：独立loopback实例及虚构数据库中，重复升级、空表降级/再升级、结构漂移拒绝、非空表受保护回滚均通过；实际关闭并重启该MySQL进程后，已提交标记仍存在。生产没有执行DDL。
- 性能：30次隔离MySQL ASGI请求，P50 21.02ms、P95 26.60ms，每次9条SQL；零模型、零客户发送。不能据此承诺公网RDS生产耗时或V3链路提速；消费者接管后仍需同口径比较。
- 静态检查：全部变更Python文件Ruff通过，diff空白检查通过；未改前端。原始演练与性能产物只在ignored artifacts，临时MySQL完成后关闭、保留数据用于复核。
- 交付边界：接口代码可供审核联调；存量同步未完成，本地状态接管未完成，远程查询未移除，聚合最终发送拦截未联调。持久invalidated_through_version是后续接入的版本标记，本阶段不宣称已取消任何实际发送任务。
- 启用待办：提供专用凭证、企业范围、权威成员映射和当前 verified 关系，然后使用虚构身份联调。第二道门须明确消费者 ownership、存量覆盖100%、增量可靠推送和聚合发送拦截证据，另行授权切换。
