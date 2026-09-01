# 质量门禁与测试治理

- status: current-code
- owner: project
- baseline_commit: `f36add37412310613936be5bbd8f58fb591fead3`
- production_status: 待现场核验；本文不表示已部署

## 统一入口

干净环境先安装 `requirements-dev.txt`，再执行：

```text
python scripts/run_quality_gates.py
```

入口按顺序执行 Python 编译、静态债务审计、离线确定性测试、V3 路由合同、隔离启动、前端锁文件安装、TypeScript、ESLint 和生产构建。任一步失败即返回非零状态。仅运行后端时可使用 `--skip-frontend`；这不等于完整发布验收。

CI 使用 Python 3.11、Node 22、pnpm 9 和冻结的前端锁文件。默认门禁不得配置生产 token，不调用真实模型、不发送真实客户消息、不读取真实客户数据。

## 测试治理基线

`scripts/quality_audit.py` 使用 Python AST 计数，基线位于 `quality/baseline.json`。当前基线：

| 项目 | 数量 | 约束 |
|---|---:|---|
| 测试直接导入私有符号 | 157 | 禁止增长 |
| 涉及测试文件 | 32 | 报告项，不作为独立阈值 |
| `except Exception` | 188 | 禁止增长 |
| 裸 `except` | 0 | 禁止新增 |
| 静默 `pass` / `return None` | 57 | 禁止增长 |
| 其中纯 `pass` | 19 | 禁止增长 |
| 启发式 fail-open 返回 | 20 | 禁止增长，逐项人工复核 |

基线是债务上限，不是认可清单。减少计数后应同步降低基线；不得通过改名、动态导入或放宽扫描范围绕过门禁。fail-open 是保守的语法启发式：异常分支直接返回 `True`、成功字符串或空容器会被标记，不代表每一项都已确认是业务漏洞。

## 已知豁免

隔离启动测试已能在无生产凭据、后台 worker 关闭、临时 SQLite 下启动并访问 `/health`，但关闭阶段触发 `main.py` 中未定义名称。由于质量门禁任务禁止修改该文件，测试暂以 `strict xfail` 记录；发布前必须修复并移除豁免。`strict` 保证缺陷修复后测试意外通过时仍提醒维护者清理豁免。

Windows 本机的 `pnpm validate` 组合脚本依赖 POSIX shell 语法，可能返回路径语法错误；可分别执行 `pnpm ts-check` 与 `pnpm lint:build` 获取等价检查结果。完整 CI 和正式发布门禁仍以 Linux 上统一入口返回 0 为准，不豁免任何前端检查。

## 评测数据边界

`workflow_tests/fixtures/sales_strategy_gold_candidates_20260831.json` 的 400 条数据是自动生成候选，不是人工金标，不得据此宣称模型准确率、业务效果或人工验收完成。真实模型效果验证与部署后全链路验证不属于确定性 CI，结果必须现场产生且不得伪造。
