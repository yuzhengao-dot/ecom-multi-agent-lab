# 架构设计说明

## 1. 为什么是「Supervisor + 阶段化并行」

电商营销决策不是一条纯流水线：画像必须先算，召回／库存／定价彼此独立可以并行，文案必须等前面的结论，合规必须最后统一过审。把这种依赖硬编码成串行调用会浪费时间，把全部节点都并行又会产生数据竞争。

本项目把「谁依赖谁」交给 Supervisor 在运行时决定，把「并行怎么写回状态」交给 LangGraph 的 reducer：

- Supervisor 输出 `stages: list[list[str]]`，外层有序、内层可并行；
- `dispatch` 用 `Send(f"agent_{name}", state)` 把一个阶段的 Agent 动态分发到同一超步；
- 所有 Agent 节点只往带 `operator.add` reducer 的 `agent_results` / `events` 写，因此并行分支不会互相覆盖；
- `evaluate` 是单节点，负责检查本阶段产出是否齐全、按 `MAX_AGENT_RETRIES` 补跑、推进 `stage_index`。

## 2. 状态契约

`GraphState` 是一个 `TypedDict`，分三类字段：

| 类别 | 字段 | 写入者 |
| --- | --- | --- |
| 输入 | `request`, `trace_id`, `variant`, `variant_config` | runner 的 initial state |
| 编排 | `stages`, `stage_index`, `pending_agents`, `retry_counts`, `revisions`, `next_action` | 单节点（`plan` / `evaluate` / `compliance`） |
| 汇聚 | `agent_results`, `events`, `errors` | 任意并行分支，reducer 追加 |
| 输出 | `plan`, `compliance` | `synthesize` / `compliance` |

`build_context(state)` 把状态投影成 Agent 可见的只读上下文：`request`、`variant`、`agent_data`（已完成 Agent 的 data）、`agent_results`、`plan`。Agent 不直接读图状态，也不直接改图状态。

## 3. 六个专家 Agent

| Agent | 阶段 | 主要工具 | 输出要点 |
| --- | --- | --- | --- |
| profile | 1 | `user_snapshot` | 分层、偏好类目、价格敏感度、客单价 |
| recommendation | 2 | `candidate_products` | Top-N SKU、逐条理由、剔除的缺货 SKU |
| inventory | 2 | `candidate_products` + `inventory_batch` | 可售 / 风险 SKU、补货建议 |
| pricing | 2 | `candidate_products` + `price_book` + `coupons` | 折扣（受变体 cap 限制）、券、预期毛利 |
| marketing | 3 | `user_snapshot` | 标题、正文、CTA，按渠道与文案风格适配 |
| compliance | 汇总后 | `policy_lexicon` | `approved` / `revise` / `rejected` + 修改建议 |

所有 Agent 继承 `BaseAgent`：统一负责工具调用记录、LLM 调用、结构化校验、计时、异常处理与规则降级。新增 Agent 只需实现 `build_payload()`、`required_fields()`、`summarize()`，并在 `specialists.AGENT_CLASSES` 与 `supervisor.ROUTABLE_AGENTS` 注册。

## 4. 真实 LLM 与离线规则引擎

`LLMClient.complete_json(intent, payload)` 是唯一抽象。`OpenAICompatibleLLM` 把 `system_prompt(intent)` 与 `user_prompt(intent, payload)` 发给兼容端点；`MockLLM` 直接调用 `llm/mock.py` 的规则引擎消费**完全相同**的 payload。

这带来两个性质：

1. 离线模式跑出来的不是「假数据占位」，而是与在线模式同构的业务结论，CI 与本地演示可信；
2. 真实 LLM 失败时，Agent 的 `fallback` 复用同一个规则引擎，行为可预期。

规则引擎里的业务判断（例如 20 件库存安全阈值、保留 18 个点毛利安全垫、180 天未下单判定沉睡）都集中在 `llm/mock.py`，是刻意保留的可解释基线，方便与 LLM 结果做对照实验。

## 5. 防幻觉对账

`synthesize` 节点允许 LLM 生成文案，但 `reconcile_plan()` 会用专家结果覆盖：

- `recommended_skus` ← recommendation agent 的 `items`
- `discount_rate` / `coupon_code` ← pricing agent
- `inventory_notes` ← inventory agent
- `segment` ← profile agent
- 缺失的 `headline` / `body` / `cta` ← marketing agent

测试 `test_reconciliation_blocks_hallucinated_numbers` 用故意编造 SKU 与 99% 折扣的假 LLM 验证这条防线。

## 6. 合规重写循环

```
synthesize -> compliance --approved--> END
                        \--revise--> synthesize (最多一次) -> compliance -> END
```

`revisions` 计数保证循环有界。第二次仍不通过时方案被标为 `revised` 或 `rejected` 并附上问题清单，而不是无限重试。生产环境可把 `REQUIRE_APPROVAL=true` 与 LangGraph `interrupt_before=["compliance"]` 结合，接入人工审批后通过 `graph.invoke(None, config)` 继续。

## 7. A/B 实验

`sha256(experiment_key:user_id)` 归一化到 `[0,1]`，小于 `AB_SPLIT` 落 A 组。变体配置不散落在业务代码里，而是集中在 `variant_config()`，影响三处：

- Supervisor 是否编排 pricing agent；
- 推荐条数加成 `top_n_bonus`；
- 文案风格 `copy_style` 与折扣上限 `discount_cap`。

因此一次请求的分桶差异可以完整反映在 `trace.stages`、`agent_results` 和最终 plan 上，便于离线复盘。

## 8. 可观测性

- 结构化 JSON 日志（`observability.JsonFormatter`），字段含 trace_id、variant、mode；
- `RunTrace` 收集 `supervisor.plan`、每个 agent、synthesize、compliance 的 span，记录耗时、token、降级 Agent 与事件流；
- 内存 `TRACE_STORE` 支持 `GET /v1/traces/{trace_id}`，`TRACE_STORE_SIZE` 控制上限；
- `Metrics` 进程内计数器，`GET /metrics` 输出 Prometheus 文本。

生产化替换点：把 `RunTrace` 换成 OpenTelemetry span、把 `TRACE_STORE` 换成 Redis/ES、把 `Metrics` 换成 `prometheus_client`，业务代码无需改动。

## 9. 运行模式与边界

- 每次请求重新编译图（LangGraph 编译开销很小），换来 run-scoped 的 trace 隔离；高吞吐场景可缓存 compiled graph 并把 trace 放进 context。
- SQLite 仅用于演示，首次使用自动建表并导入 `data/seed.json`；生产应替换为真实商品／订单服务。
- 本项目不发送营销消息、不占用库存、不核销优惠券，`dry_run` 字段预留给未来的副作用控制。
