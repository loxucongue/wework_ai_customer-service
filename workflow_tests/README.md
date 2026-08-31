# 测试目录说明

本目录中的确定性回归测试用于防止旧规则回流，不能因名称含 `v1`/`v2` 或为了“清理”而批量删除。产品版本、第三方协议版本、fixture schema 和内部模块名必须分别判断。

目标结构：

- `tests/unit/`：纯函数和局部行为。
- `tests/contracts/`：路由、schema、SOP 状态和不可破坏边界。
- `tests/integration/`：数据库、队列和外部 client 的受控替身。
- `tests/e2e/`：完整但默认离线的业务链。
- `tests/fixtures/`：脱敏、确定性输入。

运行报告、CSV、截图、日志和模型输出写入 ignored 的 `artifacts/`。默认测试不得调用真实模型、真实客户发送或生产写接口。
