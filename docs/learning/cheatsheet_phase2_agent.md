# Sun Stage 1 Agent 内核速查（D6 草稿）

> 最终目标原则：最精简代码 · 最核心功能 · AI 自动化长任务编程。  
> 遗忘时先翻这页；D7 毕业前补全「自检」勾选。

---

## 端到端数据流

```
CLI (__main__) 装配 loop
  → AgentLoop.run(task)
       Context ← system + user
       tools = registry.openai_schemas()
       for turn ≤ max_turns:
         msg = LLMClient.chat(messages, tools)
         Context ← assistant
         无 tool_calls? → 当文本结束
         有 tool_calls?
           → ToolExecutor（解析→查表→安全门→调用→截断）
           → Context ← tool 结果   ← 观察，供下一轮决策
           → result.finished? → finish 退出
       max_turns 用尽 → stop
```

事件 `on_event` 只展示，不掺业务。

---

## 模块 → 一句话职责

| 模块 | 职责 |
|------|------|
| `__main__.py` | CLI；`_build_loop` 装配；`on_event` 打印进度 |
| `loop.py` | 心脏：reason ↔ tool；三种退出；SYSTEM_PROMPT |
| `llm.py` | OpenAI-compatible 一次 chat；瞬时错误重试；terra 自动 `reasoning_effort=none` |
| `context.py` | 堆 system/user/assistant/tool；token 粗估 |
| `tools/registry.py` | `@tool`：签名+docstring → OpenAI schema |
| `tools/builtins.py` | 四个内置工具挂到共享 registry |
| `tools/executor.py` | 解析 JSON → 查表 → 安全门 → 调用 → 截断 |
| `safety.py` | `assess_command` 危险模式；假阳性可接受 |
| `config.py` | Settings / 环境 / 全局 toml 优先级 |

---

## 四个内置工具

| 工具 | 入参 | 要点 |
|------|------|------|
| `run_shell` | command | 真 shell；`dangerous`（不进 schema） |
| `read_file` | path | 带行号 content |
| `write_file` | path, content | **整文件覆盖** |
| `finish` | summary | `finished: True` → loop 停 |

---

## Loop 三种退出

1. 无 `tool_calls` → 文本当结束  
2. `finish` 且 `finished` → 可验证停止（优先）  
3. `max_turns` 用尽 → `stop`

`max_retries`（API 瞬时失败）≠ `max_turns`（agent 轮次）。

---

## Safety 两道门

1. **模式门**：`assess_command`（rm -rf / sudo / 写系统路径…）  
2. **确认门**：危险 + `require_confirmation` → `confirm_fn`；无通道或拒绝 → 不执行  

故意不拦：`2>/dev/null`、`> /dev/null`。  
非 TTY：不能弹确认 → **自动拒绝**（防卡死）。

截断：大 stdout/content 进 Context 前裁掉，防窗口爆。

---

## 测试测了什么（`test_wiring.py`，无需 API key）

| 测试 | 契约 |
|------|------|
| `test_all_builtin_tools_registered` | 四工具在册 |
| `test_tool_schema_shape` | schema 形状 |
| `test_safety_*` | 拦/不拦边界（含 `/dev/null`） |
| `test_executor_runs_shell` / write_read | 执行真副作用 |
| `test_executor_blocks_*` / confirm_declined | 危险确认门 |
| `test_finish_tool_signals_completion` | finish 信号 |
| `test_llm_retries_*` | 瞬时重试 / 用尽放弃 |
| `test_*reasoning_effort*` | terra + tools → none |

**必须真模型才验**：roadmap Case 1–4（全链路 + 模型决策）。wiring 不替代验收案。

---

## 风险快问

| 如果… | 会怎样 |
|--------|--------|
| 删掉 safety | 危险命令可静默执行 |
| 删掉截断 | 大输出撑爆 Context / 烧 token / 后续轮失真 |
| 删掉 on_event | agent 仍能跑，只是 CLI 无进度 |
| 只用散文结束、不用 finish | 难区分「聊完」与「任务真做完」 |

---

## 自检（D7 前勾）

- [x] 能默画上面架构图  
- [x] 逐文件一句话（不看表）  
- [x] 三种停止 + finish 为何存在  
- [x] safety 拦/不拦（含 `/dev/null`）  
- [x] mini harness 跑通一条验收句（`miniharness`：写 hi.py → python 验证 → finish）  
- [x] 本速查表定稿  

### Mini vs Sun（D7）

| | Mini (`WWW/miniharness`) | Sun |
|--|------|-----|
| 结构 | 单文件闭环 | 多模块 |
| schema | 手写 JSON | `@registry.tool` 推断 |
| 安全/截断 | 几乎无 / 简单切片 | assess+confirm / `_truncate` |
| 停止 | 同：finish / 无 tool_calls / max_turns | 同 |