# 系统结构

- status: current-code
- owner: project
- last_verified: 2026-09-01 Asia/Shanghai
- source_of_truth: 当前 `main` 代码树；精确版本以 `git rev-parse HEAD` 为准

## 代码结构

同一个 `main` 提交构建三个运行角色：

```text
外部平台 / 管理端
        |
      Nginx
  +-----+------------------+
  |                        |
V3 reply                 control API
客户回复                 管理、回调、SOP 控制面
  |                        |
  +----------共享存储-------+
                           |
                         workers
                SOP、outreach、恢复与 outbox
```

- 客户回复产品接口只保留 V3；应用内不再注册旧 V1/V2 路由，公网由 Nginx 对退役地址固定返回 410。
- control、reply、worker 是三个独立进程角色，必须来自同一个 `main` SHA。角色分别使用
  `AI_PATHS_SERVICE_ROLE=control|reply|worker`；只有 worker 允许
  `AI_PATHS_BACKGROUND_WORKERS_ENABLED=true`。
- 第三方协议路径中出现 `v1` 不代表产品 V1，不能按名称删除。
- 历史接口/schema 版本号只用于读取旧审计或兼容第三方协议，不构成第二套产品运行时。

## 仓库结构

```text
ai_paths/app/main.py              FastAPI 生命周期、路由、worker 编排
ai_paths/app/runtime_services.py  服务依赖装配，禁止在路由文件重复创建客户端
ai_paths/app/runtime_roles.py     运行角色标准化与旧环境值只读兼容
ai_paths/app/runtime_routes.py    按角色收口实际暴露的 FastAPI 路由
ai_paths/app/graph/               唯一 V3 回复图
ai_paths/app/services/            平台、SOP、存储、发送与策略服务
ai_paths/app/policies/            版本化运行策略
ai_paths/scripts/                 运维、迁移、隔离评测脚本
projects/                         管理前端
workflow_tests/                   当前确定性合同测试与隔离评测夹具
config/                           部署时读取的业务配置
deploy/                           受版本控制的服务和 Nginx 模板
docs/                             当前架构、合同、运行手册和现场状态
```

日志、测试结果、构建包、数据库、门店快照和上传文件都属于运行数据，必须写入 Git 忽略目录，不能作为代码事实来源。

## V3 回复链

```text
shared context
  → semantic router / evidence
  → read-only tools
  → evidence join
  → Reply
  → commit / send audit
```

Reply 是当前唯一销售语义决策节点。模型负责意图、心理、卡点、节奏和表达；代码负责权威事实、工具、schema、幂等、交易边界、安全和发送结果。

## 第三方 SOP

Worker 中只保留第三方 SOP 两段式链路：`pending` 提供触发时间节点，
`store-visit-pending` 提供实际发送内容。两者配对后共同进入判断、发送、消费、
策略数据回传和送达确认。旧 `/sop/events` 接收器、旧事件模型重试、夜间次日融合
及延迟重放已删除；历史 `sop_events` 和 `sop_send_tasks` 只用于审计、客户数据清理
以及已有 `source_kind=sop_event` 派发的终态兼容。

## 发布要求

- `main` 是唯一长期开发和发布分支。
- 生产 release 必须映射到已验证的 `main` commit。
- 策略目录、延时逼单和多步骤跟进必须通过独立开关启用；代码合并不得自动改变发送行为。
- 生产拓扑和数据库状态是动态事实，发布前必须重新读取服务器，不得以本页代替现场核验。
