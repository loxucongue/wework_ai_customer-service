# 系统结构

- status: current
- owner: project
- last_verified: 2026-08-31 Asia/Shanghai
- source_of_truth: 生产 systemd、Nginx 配置、实际 release 与代码 import

## 当前生产拓扑

```text
外部平台 / 管理浏览器
          |
        Nginx
   +------+------+-------------------+
   |             |                   |
V3 reply      管理/回调/API         页面
:8013          :8000               :5000
                   |
            shared storage/queue
                   |
              workers :8014
         SOP / outreach / recovery
```

当前 V3 是回复 sidecar，不是完整单体。共享主服务仍承载管理 API、平台回调、SOP 控制面等；后台 worker 独立运行。因此“只保留 V3”指客户回复产品接口只留 V3，不等于关闭所有历史命名的进程。

当前还有一个非直观依赖：`service-rule-data` outbox 消费器由 V3 进程启动，即使该进程设置了 `AI_PATHS_BACKGROUND_WORKERS_ENABLED=false`。统一架构时必须把它显式迁移到 workers，并保证单一消费者，不能把它随 sidecar 装配一起删掉。

## 目标拓扑

- 一个 `main` commit 同时构建 API、worker、frontend。
- 客户回复只暴露 V3 接口。
- API 与 worker 可拆进程，但必须同 SHA、同合同、同发布清单。
- V1/V2 回复代码在 V3 合并完成并有合同测试后移除。
- 历史 schema 只读兼容和第三方协议版本可保留，禁止产生新的产品 V1/V2 数据。

## 所有权

- V3 代码线：reply、semantic router、knowledge、V3 prompt/evaluation。
- 当前 main：第三方 SOP、outreach、MySQL、回调恢复、管理日志。
- 必须人工整合：应用入口、配置、storage schema、Nginx/systemd、前端代理。
