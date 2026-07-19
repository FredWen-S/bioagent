# BioRender GUI Agent 极简用户手册

## 它能做什么

BioRender GUI Agent 使用浏览器模拟真人编辑 BioRender，不调用 BioRender 自带 AI。

支持：

- 打开已有 Figure；
- 搜索并拖入普通 Icons / Assets；
- 移动、缩放，界面支持时旋转；
- 添加、编辑、移动和缩放文字；
- 添加 Arrow、Line、Inhibition Connector；
- Group、Align、Distribute；
- 执行内置 PD-1/PD-L1 Figure；
- 检查每个元素的 observed bbox、置信度、验证和证据；
- 截图、验证、SQLite 状态和中断恢复。

永久禁止：BioRender AI Generate、Create with AI、AI Edit、AI credits、购买、升级、
自动登录、自动导出、分享和删除。发现 AI 或 credits 弹窗时会截图并返回
`blocked_by_policy`。

> 当前完整能力已通过本地兼容编辑器上的真实 Chromium 测试，但尚未形成可公开引用的
> 真实 BioRender 完整 Figure 成功案例。线上运行必须使用人工登录和可丢弃空白 Figure。

## 安装

```powershell
cd C:\bioagent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,browser]"
playwright install chromium
```

## 第一次启动：图形化界面

```powershell
python -m app.cli web-ui
```

访问 `http://127.0.0.1:8000/ui`，保持“PD-1 / PD-L1 机制图”和“安全预演”，然后单击
“开始安全预演”。这不会打开或修改 BioRender。

界面还提供有限自定义图形、人工登录、URL 检查、校准、经确认的真实执行、进度、元素状态、
真实截图证据、安全停止、Resume 和只读 Verify。它只是现有 Workflow 的可视化入口，不会
绕过 AI Generate Policy Guard。详见 [Graphical_UI_Guide.md](Graphical_UI_Guide.md)。

## CLI Dry Run

```powershell
python -m app.cli demo
```

这会本地规划和模拟 PD-1 Figure，写入 `C:\bioagent\runtime\agent.db`，不会打开或
修改 BioRender。

## 第一次 Live 运行

图形化界面用户应选择“真实执行”、输入可丢弃的空白 Figure URL，并主动勾选确认。没有
URL 或确认时“开始绘制”保持禁用。以下命令提供相同核心 Workflow 的 CLI 入口。

1. 手动登录：

```powershell
python -m app.cli browser-login
```

2. 在 BioRender 中准备一个可丢弃空白 Figure，复制完整 URL，然后校准：

```powershell
$BlankFigureUrl = Read-Host "请输入空白 Figure 完整 URL"
python -m app.cli calibrate-ui `
  --editor-url $BlankFigureUrl `
  --confirm-live
```

3. 先做单素材测试：

只搜索普通素材、不修改画布：

```powershell
python -m app.cli live-search-asset `
  --editor-url $BlankFigureUrl `
  --query "T cell" `
  --confirm-live
```

搜索并拖入素材：

```powershell
python -m app.cli phase0-search-drag `
  --editor-url $BlankFigureUrl `
  --query "T cell" `
  --confirm-live
```

4. 执行完整 PD-1 Figure：

```powershell
python -m app.cli live-figure `
  --editor-url $BlankFigureUrl `
  --confirm-live
```

执行其他明确流程：

```powershell
python -m app.cli live-figure `
  --request "T cell → Tumor cell → Antibody" `
  --editor-url $BlankFigureUrl `
  --confirm-live
```

## 中断恢复

```powershell
python -m app.cli resume-live-figure `
  --run-id "<figure_id>" `
  --confirm-live
```

恢复前会先观察画布。元素已经存在时不会重复插入；无法判断时返回
`paused_reconciliation` 并停止。

只读检查：

```powershell
python -m app.cli inspect-elements --run-id "<figure_id>"
python -m app.cli verify-live-figure --run-id "<figure_id>"
```

## 如何判断结果

- `verified`：Observer 有证据确认该步骤；
- `unknown`：不能确认，必须暂停；
- `blocked_by_policy`：检测到 AI、credits 或禁止界面；
- `awaiting_confirmation`：自动步骤结束，等待用户检查真实画布。

Live 证据默认位于：

```text
C:\bioagent\output\playwright\figures\<figure_id>\
```

即使显示 `awaiting_confirmation`，用户仍应检查素材语义、最终布局和 BioRender 保存状态。

## 常见问题

**找不到浏览器：**

```powershell
playwright install chromium
```

**登录失效：** 重新运行 `python -m app.cli browser-login`，由用户手动登录。

**找不到搜索框、画布或工具：** 查看失败截图；这通常表示 BioRender UI 已变化。不要改成
盲目坐标点击。

**出现 AI/credits 弹窗：** 运行应自动停止。关闭弹窗并回到普通素材搜索，不要绕过策略。

**状态为 `unknown`：** 查看证据并使用原 Figure ID 恢复；不要重新启动一个会重复插入的任务。

**测试通过但线上失败：** 本地真实 Chromium 测试不等于真实 BioRender 验收。请保存真实
站点截图和 UI Profile，安全停止并记录差异。

完整 PD-1 计划为 94 个动作，覆盖 9 个素材、9 个 Label、5 个 Connector 和 9 个 Group。
逐元素状态见 [Element_Capability_Matrix.md](Element_Capability_Matrix.md)，真实站点按
[Real_BioRender_Acceptance.md](Real_BioRender_Acceptance.md) 的 L0–L8 执行。
