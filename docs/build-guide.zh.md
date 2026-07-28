# Agent DevTools 搭建全流程

本文解释这个工具为什么按当前顺序搭建，以及每一步为下一步提供什么基础。目标不是先做一个托管监控平台，而是先让开发者能在本机可靠地解释一条 Agent 运行。

## 0. 先定义边界

第一步不是写 UI，而是明确产品边界：工具记录用户自己控制的 Agent 执行，不能声称能够抓取 Codex、Claude Code 或其他平台的隐藏推理和内部计费。这个边界决定了数据源必须来自 SDK、显式审计事件或用户导入的 Trace 文件。

## 1. 定义 Trace 合约

先定义 `schemas/trace.schema.json`，核心结构是：

```text
Run -> Step[] -> cost / duration / status / error / events
```

这样做的原因是 SDK、CLI、Web UI、SQLite、PostgreSQL 和 TypeScript SDK 必须交换同一种数据。先有契约，后续组件才能独立演进，不会把某个运行时或供应商 SDK 锁死为系统中心。

## 2. 实现 Python SDK

Python SDK 负责创建 Run、记录 Step、捕获异常和 wall-clock 耗时，再写出 JSON Trace。它提供 `TraceContext`、`traced_agent`、`traced_model`、`traced_tool` 和框架适配器。

先做 SDK 的原因是没有可靠原始数据，CLI 和 UI 只能展示演示数据。模型调用在这里读取响应的 `usage`，把 Token 和成本放入统一的 `Cost` 结构；工具调用在这里保存输入、输出和错误。

## 3. 实现文件写入和隐私保护

每次 Run 结束写入 `traces/<run-id>.trace.json`。文件是第一阶段的存储边界：可携带、可版本控制、可离线分析。写入前可做脱敏，导出和推送前也会做隐私预检。

先选择文件而不是远程数据库，是为了让安装零配置、调试可离线完成。之后的 SQLite 和 PostgreSQL 都可以从同一 JSON 合约导入。

## 4. 实现 CLI 分析层

CLI 读取 Trace，提供 `list`、`show`、`analyze`、`diff`、`replay`、`regression-check` 等命令。分析模块聚合成本、延迟、失败、循环和重试。

CLI 在 Web UI 之前完成，因为它是最容易自动化和测试的观察层，也能成为 CI 门禁。`regression-check` 让候选运行相对 baseline 的失败、Token、成本或耗时恶化时直接返回非零退出码。

## 5. 加入本地 SQLite 索引和 loopback API

JSON 文件适合交换，但不适合大量搜索。因此 `watch`/`serve` 将新 Trace 脱敏后导入 `.agent-devtools/traces.db`。本地 API 只绑定 `127.0.0.1`，给 Web UI 和 MCP 读取同一个索引。

这一步的目的，是让 UI 自动发现本机最新 Trace，而不是每次都让用户手工选文件，同时不引入公网服务或账号系统。

## 6. 构建 Web UI

Web UI 用 React/Vite 呈现同一 Trace 合约：Timeline 用于定位流程，Inspector 用于查看单步数据，Analysis 用于看聚合指标，Diff/Experiment/Replay 用于比较和复现。评测报告视图单独导入 `evaluate --output` 的 JSON，因此静态 GitHub Pages 也能使用。

UI 放在本地 API 之后，是因为先有稳定读取接口，页面才不需要直接访问用户文件系统。静态部署时保留样例和手动导入，而本地运行时才自动读取本机索引。

## 7. 接入 Codex MCP

MCP 使用 stdio 暴露 `list_recent_traces`、`analyze_trace`、`compare_traces` 和 `record_external_audit`。它只查询显式记录到本地索引的 Trace。

这样做的原因是 Codex 可以直接用工具回答“最新一次哪里失败”“两次运行哪里不同”，但不会越过平台边界读取隐藏会话数据。审计工具允许把可见工程动作留档，适用于发布、排障和复盘。

## 8. 增加团队 PostgreSQL 服务

本地模式足够个人调试，团队模式需要共享项目、角色和留存策略。`PostgresTeamRepository` 将项目、Token 哈希和 Trace 存入 PostgreSQL；HTTP API 以 Bearer Token 区分 Reader、Writer 和 Admin，并按项目和过期时间隔离数据。

先有本地 JSON/SQLite，后加 PostgreSQL 的原因是避免把个人用户绑到数据库运维，同时保留同一 Trace 合约。生产环境由 `agent-devtools team-serve` 结合 TLS 反向代理和密钥管理部署。

## 9. 增加评测和 CI

评测模块把数据集、回答、确定性关键点检查、人工标注、难度分层和失败聚类写入报告。CI 生成自包含的 regression fixture，运行 `regression-check` 和 `evaluate`，并上传评测报告 artifact。

自包含 fixture 很重要：CI 不能引用开发机的未提交 Trace，否则克隆仓库后就会失败。评测先用可复现规则和人工评分，避免把未经校准的模型 Judge 当作事实来源。

## 10. 验证、审计和发布

每次发布依次运行：Python 测试、发布守卫、Web UI 数据测试/lint/build、TypeScript SDK 测试/build、依赖审计。发布守卫检查个人路径、未声明的 Web 依赖和环境文件；CI 与 Pages 工作流在 `main` 推送后执行。

这个顺序的理由是先验证数据与后端行为，再验证浏览器构建，最后检查供应链与发布配置。GitHub Pages 仅部署静态 UI；本地 Trace、MCP 和 PostgreSQL 服务仍在用户或团队控制的运行环境中。

## 后续演进原则

新增功能先问三个问题：是否仍遵守 Trace 合约、是否能在本地无云账号运行、是否会误导用户以为能采集宿主平台的隐藏遥测。只有三个答案都清楚，才扩展 SDK、CLI、UI 或团队服务。
