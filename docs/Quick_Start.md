# BioRender GUI Agent Quick Start

目标：使用 BioRender 普通编辑器完成科研绘图，**不调用 BioRender AI Generate、
Create with AI、AI Edit，也不消耗 AI credits**。

> 已通过本地兼容编辑器上的真实 Chromium 回归；真实 BioRender 完整 Figure 仍需用户在
> 可丢弃空白 Figure 上人工验收。

## 1. 安装

Windows 10/11 最简单的方式是在项目根目录双击：

```text
Install-BioAgent.cmd
```

它会检查 Python 3.11/3.12、创建 `.venv`、安装 `.[browser]`、只安装 Playwright Chromium、
初始化目录与 SQLite，并执行本地快速自检。它不会访问 BioRender。完整说明见
[Windows_Installation_Guide.md](Windows_Installation_Guide.md)。开发者手动安装方式如下：

```powershell
cd C:\bioagent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,browser]"
playwright install chromium
```

## 2. 启动图形化界面

Windows 用户可以直接双击 `Start-BioAgent.cmd`。也可以运行：

```powershell
.\.venv\Scripts\python.exe -m app.cli web-ui
```

浏览器打开：

```text
http://127.0.0.1:8000/ui
```

图形化界面是现有 Workflow 的可视化入口，不会绕过现有安全策略。选择 PD-1 预设并单击
“开始安全预演”，即可在不操作 BioRender 的情况下完成第一次使用。完整界面说明见
[Graphical_UI_Guide.md](Graphical_UI_Guide.md)。

也可以使用 Windows 启动脚本：

```powershell
.\scripts\start_web_ui.ps1
```

## 3. CLI Dry Run

```powershell
python -m app.cli demo
```

此命令只在本地生成计划、SQLite 状态和证据，不打开 BioRender。

## 4. 人工登录

```powershell
python -m app.cli browser-login
```

账号、密码和 MFA 必须由用户亲自输入。准备一个可丢弃空白 Figure 并复制完整 URL。

图形化界面用户可以单击“打开登录窗口”，登录完成后返回控制台单击“我已完成登录”。

## 5. 校准

```powershell
$BlankFigureUrl = Read-Host "请输入空白 Figure 完整 URL"
python -m app.cli calibrate-ui `
  --editor-url $BlankFigureUrl `
  --confirm-live
```

## 6. 先验证单素材

只搜索、不修改画布：

```powershell
python -m app.cli live-search-asset `
  --editor-url $BlankFigureUrl `
  --query "T cell" `
  --confirm-live
```

搜索并拖入一个普通素材：

```powershell
python -m app.cli phase0-search-drag `
  --editor-url $BlankFigureUrl `
  --query "T cell" `
  --confirm-live
```

## 7. 执行完整 Figure

内置 PD-1/PD-L1 Figure：

```powershell
python -m app.cli live-figure `
  --editor-url $BlankFigureUrl `
  --confirm-live
```

明确流程：

```powershell
python -m app.cli live-figure `
  --request "T cell → Tumor cell → Antibody" `
  --editor-url $BlankFigureUrl `
  --confirm-live
```

支持普通素材搜索/拖拽、Move、Resize、可用时 Rotate、Label、Connector、Group、Align、
Distribute、截图验证和 BioRender 自动保存状态观察。

## 8. 中断恢复

```powershell
python -m app.cli resume-live-figure `
  --run-id "<figure_id>" `
  --confirm-live
```

恢复会先观察现场，不会盲目重复插入。无法确认时返回 `paused_reconciliation`。

只读检查元素与最终证据：

```powershell
python -m app.cli inspect-elements --run-id "<figure_id>"
python -m app.cli verify-live-figure --run-id "<figure_id>"
```

## 9. 安全结果

- `awaiting_confirmation`：自动步骤已有证据，等待用户检查真实画布；
- `unknown`：证据不足，停止；
- `blocked_by_policy`：检测到 AI、credits、购买或其他禁止上下文，已停止并保存证据。

完整 Live 证据：

```text
C:\bioagent\output\playwright\figures\<figure_id>\
```

完整 PD-1 计划包含 94 个动作、9 个素材、9 个 Label、5 个 Connector 和 9 个 Group。
元素合同见 [Element_Capability_Matrix.md](Element_Capability_Matrix.md)，真实网站必须按
[Real_BioRender_Acceptance.md](Real_BioRender_Acceptance.md) 从 L0 逐级验收。
