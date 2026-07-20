# BioRender GUI Agent MVP

## Windows 最简安装与启动

Windows 10/11 用户无需手动配置虚拟环境：

1. 双击 `Install-BioAgent.cmd`。
2. 安装完成后双击 `Start-BioAgent.cmd`。
3. 打开 `http://127.0.0.1:8000/ui`（启动脚本默认会自动打开）。
4. 需要连接 BioRender 时，在界面中人工启动登录并亲自输入凭据。

脚本支持 Windows PowerShell 5.1 和 PowerShell 7，使用项目内 `.venv`，不会要求管理员权限，
也不会自动登录、打开真实 Figure、执行 Live Run 或调用 BioRender AI Generate。完整安装、
修复和卸载说明见 [Windows 安装指南](docs/Windows_Installation_Guide.md)。

BioRender GUI Agent 通过 Playwright 模拟真人编辑 BioRender：搜索普通素材、拖入画布、
调整元素、添加文字和连接线、排版，并观察 BioRender 的自动保存状态。

项目的硬约束是：**绝不调用 BioRender AI Generate、Create with AI、AI Edit，
也不确认任何 AI credits、购买或升级操作。** AI 控件会被记录并围栏；动作目标或弹窗命中
AI、credits、购买、订阅或升级策略时，运行会立即保存截图并返回 `blocked_by_policy`。

> 当前能力已在本地 BioRender 兼容编辑器上使用真实 Chromium、真实鼠标和键盘事件完成
> 自动化回归；尚无可公开引用的真实 BioRender 完整 Figure 成功案例。真实网站必须使用
> 人工登录和可丢弃的空白 Figure 验收，不能把本地测试结果当作线上成功。

## 功能

- 打开用户提供的 BioRender Figure；
- 多关键词普通素材搜索、候选筛选、等待和失败重试；
- 拖拽、移动、缩放；当前 UI 暴露普通旋转手柄时可旋转，否则安全停止；
- 添加、编辑、移动和缩放 Label；
- 添加 Arrow、Line 和 Inhibition Connector；
- 多元素 Group、Align、Distribute；
- 执行内置 PD-1/PD-L1 Figure 或明确的 `A → B → C` 流程；
- 观察元素数量、位置、尺寸、文字、连接线和自动保存状态；
- 为每个逻辑元素保存弱身份指纹、实际 bbox、置信度、验证结果和证据；
- SQLite Checkpoint、截图证据和 reconcile-based 恢复，不盲目重放已生效动作；
- Dry Run、CLI、FastAPI 和人工最终确认。
- 面向非命令行用户的本地图形化控制台；CLI 与 GUI 共享同一 Workflow/Application Service。

## 命令行安装

项目声明 Python 3.11+；Windows 一键安装器当前只接受已验证的 Python 3.11 或 3.12。
开发者手动安装可使用：

```powershell
cd C:\bioagent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,browser]"
playwright install chromium
python -m app.cli --help
```

## 五分钟 Dry Run

```powershell
cd C:\bioagent
.\.venv\Scripts\Activate.ps1
python -m app.cli demo
```

`demo` 会规划并本地模拟内置 PD-1/PD-L1 Figure，写入 SQLite 和逐动作证据；它不会
打开 BioRender，也不会修改在线 Figure。正常终点是 `awaiting_confirmation`。

不熟悉命令行的用户可以启动图形化控制台：

```powershell
python -m app.cli web-ui
```

然后访问 `http://127.0.0.1:8000/ui`。控制台支持 PD-1 预设、有限自定义关系图、Dry Run、
人工登录、校准、显式确认后的 Live Mode、进度、元素状态、证据、Resume 和只读 Verify。
完整说明见 [docs/Graphical_UI_Guide.md](docs/Graphical_UI_Guide.md)。

规划自定义显式流程：

```powershell
python -m app.cli plan "Sample → Centrifugation → Supernatant" `
  --output C:\bioagent\output.json
```

## Live Mode

### 1. 人工登录

```powershell
python -m app.cli browser-login
```

用户必须亲自输入账号、密码和 MFA。Agent 不读取或填写凭证。登录完成后关闭浏览器，
会话保存在本机持久 Chromium Profile 中。

### 2. 准备空白 Figure 并校准

不要在重要 Figure 上首次测试。先在 BioRender 中人工创建或打开一个可丢弃空白 Figure，
复制完整编辑器 URL：

```powershell
$BlankFigureUrl = Read-Host "请输入可丢弃空白 Figure 的完整编辑器 URL"
python -m app.cli calibrate-ui `
  --editor-url $BlankFigureUrl `
  --confirm-live
```

校准找不到普通搜索区、结果区或画布时会保存证据并停止。

### 3. 执行完整 Figure

默认执行内置 PD-1/PD-L1 请求：

```powershell
python -m app.cli live-figure `
  --editor-url $BlankFigureUrl `
  --confirm-live
```

也可以执行明确流程：

```powershell
python -m app.cli live-figure `
  --request "T cell → Tumor cell → Antibody" `
  --editor-url $BlankFigureUrl `
  --confirm-live
```

`--confirm-live` 是必需安全门，但不会关闭任何策略检查。正常结果是
`awaiting_confirmation`，表示所有计划动作已有对应 Observer 证据，仍需用户检查真实
画布和科研表达。

若运行中断，使用原输出中的 Figure ID 恢复：

```powershell
python -m app.cli resume-live-figure `
  --run-id "<figure_id>" `
  --confirm-live
```

恢复会先协调当前画布：已存在则记录并跳过；可信地不存在才允许一次重试；无法判断或
UI Profile 变化则进入 `paused_reconciliation`，不会重复插入。

### 单素材安全探针

只搜索普通素材、不修改画布：

```powershell
python -m app.cli live-search-asset `
  --editor-url $BlankFigureUrl `
  --query "T cell" `
  --confirm-live
```

验证搜索和拖拽：

```powershell
python -m app.cli phase0-search-drag `
  --editor-url $BlankFigureUrl `
  --query "T cell" `
  --confirm-live
```

## 状态含义

| 状态 | 含义 |
|---|---|
| `verified` | Observer 已确认该动作要求的像素、DOM、几何或可访问性证据 |
| `executed_unverified` | 指令可能已发送，但不能证明 GUI 结果 |
| `unknown` | 现场有歧义；暂停，不能当作成功 |
| `blocked_by_policy` | 命中 BioRender AI、AI credits、购买或其他禁止上下文 |
| `awaiting_confirmation` | 自动步骤完成，等待用户最终检查 |

Playwright 没有抛异常不等于 GUI 成功。最终 Figure 必须确认所有预期 asset、label 和
connector 的身份、数量与位置，且能观察到 BioRender 保存状态，才能结束自动阶段。

查看单个元素的计划、观察和证据（只读）：

```powershell
python -m app.cli inspect-elements --run-id "<figure_id>"
python -m app.cli verify-live-figure --run-id "<figure_id>"
```

## 安全边界

Agent 不会：

- 调用 BioRender AI Generate、Create with AI、AI Edit 或 AI Assistant；
- 确认 AI credits、购买、订阅或升级；
- 自动输入密码或 MFA；
- 自动导出、分享、发布或删除 Figure；
- 在恢复时盲目重放已生效的拖拽或插入。

发现 AI/credits 对话框时应保留失败截图并停止，不应通过修改代码绕过策略。

## 输出文件

```text
C:\bioagent\runtime\agent.db
    Figure、动作、元素、Checkpoint 和 Verification 状态

C:\bioagent\runtime\screenshots\<figure_id>\
    Dry Run 证据

C:\bioagent\output\playwright\calibration\
    UI Calibration Profile 与截图

C:\bioagent\output\playwright\figures\<figure_id>\
    完整 Live Figure 的逐动作和最终画布证据

C:\bioagent\output\playwright\probes\<run_id>\
    单素材 Probe 证据
```

浏览器 Profile 和截图可能包含会话或 Figure 信息，不应提交到公共仓库。

## FastAPI

```powershell
python -m app.cli web-ui
```

Web UI 位于 `http://127.0.0.1:8000/ui`。图形界面是现有 Workflow 的可视化入口；它不
通过 Shell 调用 CLI，也不复制 Operator。Live API 需要空白 Figure URL 和双重显式确认，
任务提交后返回 Run/Job ID，由页面轮询 SQLite 与后台任务状态。后端 Policy Guard 始终
生效，前端没有 BioRender AI 开关。

## 测试

```powershell
python -m pytest
```

测试包含普通单元测试、策略/恢复测试，以及本地 BioRender 兼容页面上的真实 Chromium
浏览器测试。浏览器测试不是 Mock，但也不是 BioRender 线上验收。

关键回归覆盖：多关键词搜索、拖拽、移动、缩放、旋转、Label、Connector、Group、
Align、Distribute、元素级恢复去重、策略截图和完整 PD-1 Figure。当前 PD-1 计划为 94 个
动作，覆盖 9 个素材、9 个 Label、5 个 Connector 和 9 个 Group。

## 已知限制

- 真实 BioRender UI 会变化，Locator 或工具名称不匹配时会安全停止；
- 旋转只在当前 UI 暴露可观察的普通旋转手柄时执行；
- Planner 目前只支持内置 PD-1/PD-L1 案例和明确的箭头流程；
- 科学一致性检查不是文献审查；最终科研正确性必须人工确认；
- 当前没有已公开验证的真实 BioRender 完整 Figure 成功率或成功截图；
- 不支持自动导出、分享、删除、购买或无人值守登录。

极简使用说明见 [docs/User_Manual.md](docs/User_Manual.md)，工作原理见
[docs/How_It_Works.md](docs/How_It_Works.md)，安装环境见
[docs/Environment_Setup.md](docs/Environment_Setup.md)，当前 Prompt 规范见
[docs/Prompt_Design.md](docs/Prompt_Design.md)，图形化界面见
[docs/Graphical_UI_Guide.md](docs/Graphical_UI_Guide.md)。
逐元素合同见 [docs/Element_Capability_Matrix.md](docs/Element_Capability_Matrix.md)，真实站点
分级验收见 [docs/Real_BioRender_Acceptance.md](docs/Real_BioRender_Acceptance.md)。
