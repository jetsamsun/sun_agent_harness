# 学习计划二阶段：吃透 Stage 1 Agent 内核

> **目标**：以 Sun 源码为活教材，吃透「最小智能体」如何工作。学完能用自己的话
> 画出完整数据流，并能从零手敲一个能跑通「问句 → 调工具 → 观察 → finish」的 mini harness。
>
> **时间盒**：7 个工作日（约每天 60–90 分钟），**周末跳过**。源码内核 ≈ 500 行，够吃透、不够拖。
> **日历**：2026-07-22（三）起 → 2026-07-30（四）结；中间 7/25–7/26 周末休息。
>
> **前置**：一阶段 CLI 已毕业（`PLAN.md`）。config / typer / 入口可当黑盒，本阶段只盯 Agent 内核。
>
> **节奏规则**（沿用一阶段）：
> - 一小节一小节；讲原理 → 读真实代码 → 动手 → **验证卡点**。
> - **验证不通过不得进入下一节。**
> - 每节完成后更新本文件「状态 / 验证 / 学习日志」。

---

## 一周总览（仅工作日）

| 日期 | 星期 | 天 | 小节 | 核心文件 | 状态 | 验证 |
|------|------|----|------|----------|------|------|
| 07-22 | 三 | D1 | 鸟瞰：数据流与接线 | `__main__.py`（`_build_loop` / `_on_event`） | ✅ | ✅ |
| 07-23 | 四 | D2 | 工具注册与 schema | `tools/registry.py` · `builtins.py` | ⬜ | ⬜ |
| 07-24 | 五 | D3 | 执行器与安全门 | `tools/executor.py` · `safety.py` | ⬜ | ⬜ |
| — | 六日 | — | **周末跳过** | — | — | — |
| 07-27 | 一 | D4 | 上下文与 LLM 调用 | `context.py` · `llm.py` | ⬜ | ⬜ |
| 07-28 | 二 | D5 | AgentLoop 心脏 | `loop.py` | ⬜ | ⬜ |
| 07-29 | 三 | D6 | 端到端复盘 + 测试即说明书 | `tests/test_wiring.py` · 真跑一次 | ⬜ | ⬜ |
| 07-30 | 四 | D7 | 毕业：手敲 mini harness | 自建小项目（不抄贴） | ⬜ | ⬜ |

状态图例：⬜ 未开始 · 🔵 进行中 · ✅ 已掌握 · ⏸ 暂停

### 心智模型（先背这句，后面每天往里填）

```
用户任务
  → Context(messages)
  → LLM(chat + tools schema)
  → tool_calls?
       是 → Executor(校验→安全门→跑→截断) → 结果写回 Context → 再 LLM
       否 / finish → 结束
```

`harness` = 把上面每一步做成**可靠副作用**的薄壳；模型只负责决策，不动手。

---

## 各天详细内容与验证标准

### D1（07-22 三）— 鸟瞰：数据流与接线
- **学什么**：CLI 如何把「任务」交给内核；`AgentLoop` / `LLMClient` / `ToolExecutor` /
  `registry` 谁依赖谁；`Event` 回调只负责展示、不掺业务。
- **读代码**：
  - `__main__.py`：`_build_loop()`、`_on_event()`、`run()`
  - `loop.py` 只扫类签名 + `SYSTEM_PROMPT`（细读留 D5）
- **动手**：在纸上（或 mermaid）画出从 `sun "统计 .py"` 到第一次 `run_shell` 的调用链；
  对照源码标出 5 个类各自职责（一句话）。
- **✅ 验证卡点**：不看代码，能口述「谁创建 loop、谁注册工具、事件往哪冒」；
  能指出 `on_event` 删掉后 agent **仍然能跑**（只是 CLI 没进度显示）。

### D2（07-23 四）— 工具注册与 schema
- **学什么**：`@registry.tool` 如何从函数签名生成 OpenAI function schema；
  `dangerous` 标记含义；四个内置工具各自契约（入参 / 返回 dict 形状）。
- **读代码**：`tools/registry.py`（全文）、`tools/builtins.py`（全文）、`tools/__init__.py`
- **动手**：
  1. `uv run python -c "from harness.tools import registry; import json; print(json.dumps(registry.openai_schemas(), indent=2, ensure_ascii=False))"`
  2. 自己加一个玩具工具 `ping`（返回 `{"pong": True}`），跑 schema 确认出现；学完可删。
- **✅ 验证卡点**：能说清「模型看到的 tools 参数从哪来」；能解释
  docstring / type hint / `dangerous=` 各自进了 schema 的哪一层（或没进）。

### D3（07-24 五）— 执行器与安全门
- **学什么**：`ToolExecutor.execute` 流水线：解析 JSON → 查 registry → 危险确认 →
  调用 → 截断大输出；`assess_command` 拦什么、故意不拦什么（`2>/dev/null` 坑）。
- **读代码**：`tools/executor.py`、`safety.py`（全文）
- **动手**：
  1. `uv run pytest tests/test_wiring.py -q`（应全绿，无需 API key）
  2. 对照 `test_safety_*` 自己再写 2 条命令预测：拦 / 不拦，再跑 `assess_command` 验证
  3. 用 `confirm_fn=lambda ...: False` 手动调一次危险 `run_shell`，看返回 error 长什么样
- **✅ 验证卡点**：能画出 executor 四步流水线；能解释「非 TTY + 危险命令 = 自动拒绝」
  为什么必须这样（roadmap 踩坑记录）；能复述输出截断解决什么问题。

### D4（07-27 一）— 上下文与 LLM 调用
- **学什么**：`Context` 如何堆 system / user / assistant / tool 消息；
  `LLMClient.chat` 如何把 messages + tools 交给 OpenAI-compatible API；
  retry / backoff 何时触发。
- **读代码**：`context.py`、`llm.py`（全文）
- **动手**：
  1. 手写 5 条假消息（含一条 `role=tool`），打印 `ctx.messages()` 形状
  2. （可选，需 key）用 `LLMClient` 发一次**不带工具**的 chat，确认链路通
  3. 读清 `max_retries` 与 loop 的 `max_turns` 是两件不同的事
- **✅ 验证卡点**：能画出一轮里 messages 如何增长（user → assistant(tool_calls) →
  tool → assistant…）；能说清「API 报错重试」≠「agent 多轮思考」。

### D5（07-28 二）— AgentLoop 心脏（最重要一天）
- **学什么**：`run()` 的 for 循环；三种退出：`finish` 工具 / 无 tool_calls 当文本结束 /
  `max_turns`；`SYSTEM_PROMPT` 如何约束「先验证再宣称成功」。
- **读代码**：`loop.py`（逐行吃透，约 100 行）
- **动手**：
  1. 在 `run()` 里临时加 turn 日志（或只靠 `_on_event` 观察），跑：
     `uv run sun "创建一个 tmp_hello.py 打印 hi，运行验证后删掉它"`
  2. 对着终端输出，把每一轮标成：`think | tool_call | tool_result | finish`
  3. 故意把 `SUN_MAX_TURNS=2` 跑一个稍复杂任务，亲眼看 stop 文案
- **✅ 验证卡点**：合上电脑能默写 loop 伪代码（≤15 行）；能解释为什么用
  `finish` 工具当停止条件，而不是「模型说完了就算完」。

### D6（07-29 三）— 端到端复盘 + 测试即说明书
- **学什么**：把 D1–D5 串成一张图；`test_wiring.py` 覆盖了内核哪些契约；
  哪些必须真模型才能验（roadmap 四案）。
- **读代码**：`tests/test_wiring.py`（全文）；回头扫一遍 `docs/roadmap.md` Stage 1
- **动手**：
  1. 不看代码，默写「四个内置工具 + loop 三种退出 + safety 两道门」
  2. 真跑 roadmap Case 1 或 Case 3（需 API key），边跑边对照事件流
  3. 开始写速查表草稿 `cheatsheet_phase2_agent.md`（模块 → 一句话职责）
- **✅ 验证卡点**：能对着架构图指出每个文件；被问「删掉 safety 会怎样 /
  删掉 Context 截断会怎样」能答出风险；wiring 测试全绿且能指出测的是哪条契约。

### D7（07-30 四）— 毕业项目：手敲 mini harness
- **做什么**：另建小项目（建议 `D:\phpstudy_pro\WWW\miniharness`），**不复制粘贴 Sun**，
  凭理解实现最小闭环：
  - 1 个 LLM 调用（OpenAI-compatible）
  - ≥2 个工具（建议 `run_shell` + `finish`，可再加 `write_file`）
  - Agent loop + max_turns
  - CLI：`mini "任务"` 能跑通一个验收句
- **验收句**（任选其一跑通即可）：
  - 「当前目录有多少个文件？」
  - 「写 `hi.py` 打印 hello 并运行验证」
- **✅ 验证卡点**：验收句真实工具输出闭环（不是模型瞎编）；你能讲清自己的 loop
  与 Sun 的异同（至少 3 点）；补完 `cheatsheet_phase2_agent.md`。

---

## 日历日程（2026，仅周一至周五）

| 日期 | 星期 | 任务 | 备注 |
|------|------|------|------|
| **07-22** | 三 | D1 鸟瞰 | 只建立地图，别陷进细节 |
| **07-23** | 四 | D2 工具层 | 偏静态，好上手 |
| **07-24** | 五 | D3 执行+安全 | 有 pytest，反馈快 |
| 07-25～26 | 六日 | — | **周末休息，不学** |
| **07-27** | 一 | D4 上下文+LLM | 概念日，可略短 |
| **07-28** | 二 | D5 Loop | **必留足时间**，当天真跑任务 |
| **07-29** | 三 | D6 复盘 | 写 cheatsheet 草稿 |
| **07-30** | 四 | D7 毕业 | 手敲 mini；通不过就顺延工作日，不赶 Stage 2 |

缓冲：某天卡壳 → 当天只做「读 + 验证口述」，动手挪到**下一个工作日**；**不要跳过验证卡点**；周末不补课（除非你主动想加练）。

---

## 刻意不学（本周边界）

- Stage 2：`edit_file` / 代码搜索 / 测试自修 / 上下文压缩 / REPL 会话记忆 / 计划模式 —— 吃透 Stage 1 再说
- Stage 3：多模型路由、多 Agent、持久记忆、MCP、沙箱 —— 见 [roadmap 北极星](../roadmap.md)，本周不碰
- 一阶段已会的打包安装 —— 除非毕业项目需要再装一次

---

## 学习日志（每节完成后追加）

<!-- 格式：日期 · 小节 · 结论 · 验证是否通过 -->

- **2026-07-22 · D1 · 鸟瞰**：`_build_loop` 装配 loop；工具经 `registry` 在 import builtins 时注册，`loop.run` 里 `openai_schemas()` 交给 `llm.chat`；`on_event` 只影响 CLI 展示。验证通过。

---

## 毕业标准（一周结束时自检）

- [ ] 能默画 Stage 1 架构图（与 README Architecture 一致或更细）
- [ ] 能逐文件一句话说明职责
- [ ] 能解释三种停止条件 + finish 为何存在
- [ ] 能解释 safety 拦/不拦的边界（含 `/dev/null`）
- [ ] mini harness 跑通至少一条验收句
- [ ] 写好 `cheatsheet_phase2_agent.md`
