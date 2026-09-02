# 生产状态

- status: requires-live-verification
- owner: operations
- source_of_truth: 服务器现场核验

仓库不保存会过期的 release、IP、磁盘、数据库或服务状态快照。每次发布任务都必须现场记录：`main` SHA、三个角色的 release 与健康状态、数据库后端、worker/outbox 消费者、Nginx 路由和回滚点。未现场核验前，任何文档都不能当作生产事实。
