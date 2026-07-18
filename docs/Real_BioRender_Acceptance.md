# Real BioRender Acceptance Plan

本计划用于人工登录后的分级真实验收。每一级都必须在可丢弃空白 Figure 中执行，保存真实
页面截图和命令输出；上一级通过后再进入下一级。当前仓库只完成了本地兼容编辑器的真实
Chromium 回归，**尚未执行下列真实 BioRender 验收**。

## 通用前置条件

```powershell
cd C:\bioagent
.\.venv\Scripts\Activate.ps1
python -m app.cli browser-login
$BlankFigureUrl = Read-Host "请输入可丢弃空白 Figure 的完整编辑器 URL"
```

- 登录、密码与 MFA 必须由用户输入。
- 使用可丢弃空白 Figure，不要在重要科研图上首测。
- 页面出现 AI Credits、购买/订阅确认、未知阻断弹窗或 Locator 歧义时立即停止。
- 截图位于 `C:\bioagent\output\playwright\`；本文不编造线上截图。

```text
TODO Screenshot
```

## L0：登录后只校准，不修改

前置条件：已人工登录并打开空白 Figure。

```powershell
python -m app.cli calibrate-ui `
  --editor-url $BlankFigureUrl `
  --confirm-live
```

预期结果：识别普通搜索区、结果区、画布和被围栏的 AI 控件；保存 Calibration Profile 与
截图；不创建画布元素。安全停止条件：登录页、未知 Modal、缺失画布、候选区重叠 AI 区。
失败恢复：人工修复页面状态后重新校准；不要复用 invalid profile。

截图证据：Calibration 全页截图、profile JSON。

**不会调用 BioRender AI，不应消耗 AI Credits。**

## L1：只搜索一个普通素材

前置条件：L0 通过。

```powershell
python -m app.cli live-search-asset `
  --editor-url $BlankFigureUrl `
  --query "T cell" `
  --confirm-live
```

预期结果：搜索普通素材，输出非序号型 candidate ID、DOM/缩略图指纹、候选数和证据；
`canvas_modified` 为 `false`。安全停止条件：结果只有 AI、Template、Upgrade、Subscribe、
Purchase，或候选普通素材身份不清。失败恢复：关闭阻断 UI，重新 L0，再换普通 fallback query。

截图证据：搜索输入与结果区截图。

**不会调用 BioRender AI，不应消耗 AI Credits。**

## L2：拖入一个素材

前置条件：L1 通过，画布为空。

```powershell
python -m app.cli phase0-search-drag `
  --editor-url $BlankFigureUrl `
  --query "T cell" `
  --confirm-live
```

预期结果：普通 T cell 候选拖入画布；观察到画布数量合理增加、目标区域变化和实际 bbox；
终点为 `awaiting_confirmation` 或 `completed_probe`。安全停止条件：AI/付费/模板候选、画布
数量歧义、expected bbox 被当成 observed bbox。失败恢复：使用输出 `run_id` 与
`--resume-run`，不得新开任务盲目重拖。

截图证据：drag 前后画布、候选结果、最终观察。

**不会调用 BioRender AI，不应消耗 AI Credits。**

## L3：Move + Resize

前置条件：L2 通过；准备新的空白 Figure，避免与前级混用。

```powershell
python -m app.cli live-figure `
  --request "T cell → Tumor cell" `
  --editor-url $BlankFigureUrl `
  --confirm-live
```

预期结果：工作流中的两个素材均经过 Insert、Move、Resize，动作证据中的 observed bbox 来自
DOM/截图观察。该命令还会继续验证 Label、Connector、Group 与保存，用户应重点检查最初的
transform 证据。安全停止条件：变换手柄不可观察、位置/尺寸超过容差、出现 unknown。
失败恢复：记录 Figure ID，运行 `resume-live-figure`；恢复先协调当前 bbox。

截图证据：每个素材 move/resize 前后截图。

**不会调用 BioRender AI，不应消耗 AI Credits。**

## L4：添加一个 Label

前置条件：L3 的 transform 已通过。

使用 L3 同一命令和 run，检查 `label_step_1`：

```powershell
python -m app.cli inspect-elements `
  --run-id "<figure_id>"
```

预期结果：`T cell` 文字精确存在，目标为 `step_1`，有 observed bbox、关联置信度、截断检查
与证据；Resume 不创建第二个同名 Label。安全停止条件：编辑器未提交文字、文字截断、Label
靠近错误素材、相同文字匹配歧义。失败恢复：运行 `resume-live-figure` 进行协调；无法判断则
保持 `unknown`。

截图证据：输入前、提交后、Move/Resize 后。

**不会调用 BioRender AI，不应消耗 AI Credits。**

## L5：添加一个 Connector

前置条件：L4 通过。

```powershell
python -m app.cli verify-live-figure `
  --run-id "<figure_id>"
```

预期结果：`flow_1` 是从 T cell 到 Tumor cell 的 Arrow；记录实际类型、起终点、方向、路径、
碰撞和截图；只读验证按该 run 的元素需求计算库存。安全停止条件：
端点错误、方向颠倒、T-bar/Arrow 类型不符、严重穿过 Label、DOM 无法识别语义。
失败恢复：用原 Figure ID 恢复；已存在且已验证的 relation 不得重建。

截图证据：connector 前后、端点附近与最终画布。

**不会调用 BioRender AI，不应消耗 AI Credits。**

## L6：三个素材的小型 Figure

前置条件：L5 通过；使用新的空白 Figure URL。

```powershell
python -m app.cli live-figure `
  --request "T cell → Tumor cell → Antibody" `
  --editor-url $BlankFigureUrl `
  --confirm-live
```

预期结果：3 assets、3 Labels、2 Arrows，含 Move/Resize、Group、Align/Distribute、终局观察和
autosave；正常终点为 `awaiting_confirmation`。安全停止条件：任何 inventory 缺失、重叠、
越界、Label 错绑、Connector 错连或保存状态不可见。失败恢复：保存 Figure ID 与截图，运行
`resume-live-figure`。

截图证据：逐动作截图、final-canvas、save-status-observed。

**不会调用 BioRender AI，不应消耗 AI Credits。**

## L7：完整 PD-1 Figure

前置条件：L6 通过；使用新的空白 Figure URL。

```powershell
python -m app.cli live-figure `
  --editor-url $BlankFigureUrl `
  --confirm-live
```

预期结果：94 个强类型动作；9 assets、9 Labels、5 Connectors、9 Groups、4 Align、1
Distribution、终局布局质量和 autosave 全部有证据；返回 `awaiting_confirmation`。用户必须
人工检查科学叙事、素材语义和最终视觉。安全停止条件：任一动作 `unknown`、policy block、
元素库存不足、布局指标失败、保存状态未完成。失败恢复：不要确认完成；保存 Figure ID 并按
证据定位单个元素后使用 `resume-live-figure`。

```powershell
python -m app.cli inspect-elements --run-id "<figure_id>"
python -m app.cli verify-live-figure --run-id "<figure_id>"
```

截图证据：94 动作证据、final-canvas、save-status-observed。

**不会调用 BioRender AI，不应消耗 AI Credits。**

## L8：中断后 Resume，验证不重复

前置条件：L7 已至少执行到一个素材或 Label；仅在可丢弃 Figure 中人工关闭测试浏览器或让
进程正常中断，禁止损坏数据库。

```powershell
python -m app.cli resume-live-figure `
  --run-id "<figure_id>" `
  --confirm-live
```

预期结果：重新校准并协调现场；已存在且满足要求的 asset、Label、Connector 或 Group 被
补记 verified，`replayed` 为 false；部分满足时只做最小修复；歧义时进入
`paused_reconciliation`。安全停止条件：UI profile 变化、弱身份冲突、元素数量可能重复、
任何 policy finding。失败恢复：保留现场、数据库、profile 和截图，人工检查；不要删除状态
后重跑。

截图证据：中断前最后 checkpoint、resume-current、协调结果、恢复后库存。

**不会调用 BioRender AI，不应消耗 AI Credits。**

## 验收记录模板

```text
Level:
Date / operator:
Disposable Figure URL (redacted if needed):
Figure ID / probe ID:
Command:
Observed result:
Evidence paths:
Policy findings:
AI Credits before / after (user-observed only):
Pass / Fail / Unknown:
Notes and UI differences:
```

只有用户完成 L0–L8 并保存真实证据后，才可更新“真实 BioRender 已验收”的状态；本地测试
结果不能替代该记录。
