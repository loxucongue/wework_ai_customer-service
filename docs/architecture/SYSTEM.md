# 系统结构

- status: current-code
- owner: project
- last_verified: 2026-09-01 Asia/Shanghai
- source_of_truth: `main@499af018` 及本次策略迁移候选

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

- 客户回复产品接口只保留 V3；旧 V1/V2 回复入口固定返回 410。
- control、reply、workers 可以是独立进程，但必须来自同一个 `main` SHA。
- 第三方协议路径中出现 `v1` 不代表产品 V1，不能按名称删除。
- 仍被 V3 使用的历史内部模块名不代表存在另一套产品运行时；重命名属于后续无行为整理，不应阻塞业务开发。

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

## 发布要求

- `main` 是唯一长期开发和发布分支。
- 生产 release 必须映射到已验证的 `main` commit。
- 策略目录、延时逼单和多步骤跟进必须通过独立开关启用；代码合并不得自动改变发送行为。
- 生产拓扑和数据库状态是动态事实，发布前必须重新读取服务器，不得以本页代替现场核验。
