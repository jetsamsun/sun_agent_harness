# 学习计划：把 Python 项目做成可安装的 CLI 工具

> **目标**：以 Sun Agent Harness 为活教材，吃透"如何编写一个可通过 CLI
> 安装 / 运行 / 移除的 Python 工具"。学完能自己从零做一个类似的工具。
>
> **节奏规则**（Davis 约定）：
> - 一小节一小节来，随时开始、随时结束。
> - 每节含：讲原理 → 读真实代码 → 动手实验 → **验证卡点**。
> - **验证不通过不得进入下一节。**
> - 每节完成后更新本文件的「状态」和「验证结果」。

---

## 进度总览

| # | 小节 | 状态 | 验证 |
|---|------|------|------|
| 1 | pyproject.toml 与项目骨架 | ✅ 已掌握 | ✅ |
| 2 | 入口函数：`sun` 命令如何跑起来 | ✅ 已掌握 | ✅ |
| 3 | typer 子命令与参数路由 | ✅ 已掌握 | ✅ |
| 4 | 配置管理：key 存哪、优先级 | ✅ 已掌握 | ✅ |
| 5 | 打包与安装机制（uv tool install/uninstall） | ✅ 已掌握 | ✅ |
| 6 | 分发：install.sh 一行安装 | ⬜ 未开始 | ⬜ |
| 7 | 毕业项目：从零做一个 mini CLI 工具 | ⬜ 未开始 | ⬜ |

状态图例：⬜ 未开始 · 🔵 进行中 · ✅ 已掌握 · ⏸ 暂停

---

## 各小节详细内容与验证标准

### 小节 1 — pyproject.toml 与项目骨架
- **学什么**：包名/命令名/导入名的区别；`[project]`/`[project.scripts]`/
  `[build-system]`/`[tool.*]` 各段作用；`src/` 布局为什么这么摆。
- **读代码**：`pyproject.toml`、项目目录树。
- **动手**：加一个命令别名（如 `sunny`）指向同一入口，重装后验证它能用。
- **✅ 验证卡点**：能用自己的话说清「敲 `sun` 为什么能跑代码」，并能独立
  在 `[project.scripts]` 里加一个新命令别名并让它生效。

### 小节 2 — 入口函数：`sun` 命令如何跑起来
- **学什么**：console_scripts 生成的可执行文件干了啥；`main()` 入口；
  `sys.argv` 预处理（Sun 的自由文本路由）。
- **读代码**：`src/harness/__main__.py` 的 `main()` 和 `if __name__`。
- **动手**：在 `main()` 里加一句打印 argv，观察 `sun "hi"` 被改写成 `sun run "hi"`。
- **✅ 验证卡点**：能画出「敲 sun → sun.exe → main() → typer」的调用链，并解释
  argv 预处理解决了什么问题。

### 小节 3 — typer 子命令与参数路由
- **学什么**：`typer.Typer()`、`@app.command()`、`@app.callback()`；子命令
  vs 自由文本如何分流；`typer.Option`/`Argument`。
- **读代码**：`__main__.py` 里的 model/config/version/update/remove 命令。
- **动手**：加一个新子命令 `sun ping`（打印 "pong"），重装后验证。
- **✅ 验证卡点**：能独立新增一个子命令并跑通；能说清 `sun model`（子命令）
  和 `sun "任务"`（自由文本）为什么走不同路径。

### 小节 4 — 配置管理：key 存哪、优先级
- **学什么**：环境变量 / 项目 .env / 全局 config.toml 三层来源；pydantic-settings
  的 `settings_customise_sources` 优先级；`sun model` 如何写配置。
- **读代码**：`config.py`、`config_writer.py`。
- **动手**：用三种方式各设一次 model 名，验证「env > .env > 全局」的覆盖顺序。
- **✅ 验证卡点**：能预测「同时存在 env 和全局配置时，最终用哪个」并实测验证正确。

### 小节 5 — 打包与安装机制
- **学什么**：wheel 是什么；`uv tool install` 把命令装到哪（~/.local/bin）；
  `--force` 重装；`uv tool uninstall <包名>`；开发期 `uv run` vs 全局安装的区别。
- **读代码**：无（命令行为主），观察安装产物路径。
- **动手**：完整走一遍 卸载 → 从本地装 → 验证 → 卸载 → 从 GitHub 装。
- **✅ 验证卡点**：能说清 `uv run sun` 和全局 `sun` 用的是不是同一份代码；
  能独立完成一次干净的卸载+重装并验证版本。

### 小节 6 — 分发：install.sh 一行安装
- **学什么**：`curl | bash` 原理；install.sh 的检测/安装/配 PATH 逻辑；
  `set -euo pipefail`；GitHub raw 托管。
- **读代码**：`install/install.sh`。
- **动手**：逐行读脚本，`bash -n` 语法检查，讲清每一步在干嘛。
- **✅ 验证卡点**：能逐段解释 install.sh，并说清它和「从 GitHub 装 sun」是两件事。

### 小节 7 — 毕业项目（综合验证）
- **做什么**：从零建一个 mini CLI 工具（如 `note`：`note add/list/remove`，
  配置存全局 toml，可 `uv tool install`），不抄 Sun 代码、凭理解自己写。
- **✅ 验证卡点**：`note` 能安装、加子命令、跑、卸载全通 —— 即毕业。

---

## 学习日志（每节完成后追加）

<!-- 格式：日期 · 小节 · 结论 · 验证是否通过 -->
- 2026-07-07 · 小节 1 · 掌握 pyproject.toml 骨架 + [project.scripts] 机制 · ✅ 验证通过（链路讲全、别名写法正确）
- 2026-07-08 · 小节 2 · 掌握 main() 入口 + argv 预处理机制 · ✅ 验证通过（调用链四步正确、理解预处理解决"省掉 run"问题）
- 2026-07-08 · 小节 3 · 掌握 typer 子命令注册/路由 + Argument vs Option + callback + 自由文本分流 · ✅ 验证通过（两路径区别正确、新增子命令两步骤正确）
- 2026-07-09 · 小节 4 · 掌握配置四层优先级（env > .env > 全局 toml > 默认）+ settings_customise_sources 元组顺序定优先级 + env_prefix 前缀 + 自定义 _GlobalTomlSource + 读写分离（tomllib 只读不写） · ✅ 验证通过（实测逐层覆盖，同时存在 env/.env 时预测 env 赢且实测正确）
- 2026-07-12 · 小节 5 · 掌握打包安装机制三大认知：① 打包=把 src/+entry_points+元数据凝固成快照；② 命令名在 entry_points.txt（`sun = harness.__main__:main`），不在代码里，uv 据此生成 launcher；③ uv tool install 装到隔离 venv（Windows 在 %APPDATA%\uv\tools\<包>\），不污染系统 Python，卸载连根端掉不留残。三种运行方式实测对比：`uv run sun` 读 src/ 实时源码、本地装/GitHub 装读隔离 venv 快照——改源码版本号后 uv run 反映、全局 sun 不变，亲手证明"装的是快照非链接"。GitHub 装带 @<commit> 冻结语义 · ✅ 验证通过（答对"两入口读不同代码"、卸载命令正确、三实验全通）
