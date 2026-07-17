# BioRender GUI Agent MVP

这是一个“先规划、后执行、全程可审计”的 BioRender 网页自动化 MVP。它把科研内容理解、
结构化 Figure Specification、素材搜索、布局、有限 GUI 动作、SQLite 状态和人工确认分成独立层。

当前版本默认只做 `dry-run`，不会登录账户、不会点击 BioRender AI、不会导出、发布、共享、删除或
购买任何内容。真实浏览器模块支持 UI 校准，以及一个固定的单素材闭环：搜索普通 `T cell` 素材、
保存候选证据、拖入画布、像素观察、持久化状态并停在人工确认。文字和连接器仍未启用。

## 已实现

- Pydantic v2 严格模型：未知字段、悬空关系、重复 ID、坐标越界会直接拒绝。
- PD-1/PD-L1 双栏示例，以及用户显式给出的 `A → B → C` 流程。
- 科学一致性基础检查：必需实体、孤立实体、激活/抑制冲突、明确要求但缺失的阻断或抑制关系。
- 每个实体最多 5 个 BioRender 搜索词，按标准术语、同义词、上位概念降级。
- 线性、双栏和中心辐射布局的标准化坐标模型。
- 只有 9 类允许动作的 GUI Action allow-list；凭证、导出、发布、分享、订阅和 BioRender AI 参数被拒绝。
- SQLite 表：`figures`、`figure_entities`、`figure_relations`、`gui_actions`、`screenshots`、
  `verification_results`。
- 每个动作有超时、重试次数、状态、错误类型和证据路径；中断后从第一个未成功动作恢复。
- FastAPI、命令行、PD-1 示例与自动化测试。
- 版本化 BioRender UI Calibration Profile：viewport、搜索框、结果区、画布、弹窗、AI 控件与截图。
- BioRender AI/AI credits/订阅/购买/模板上下文 denylist，以及每次交互前的页面和目标检查。
- `expected_bbox` 与 `observed_bbox` 分离；实际位置只允许来自 DOM、Accessibility、截图或其他 Observer。
- live 动作状态：`planned → executing → executed_unverified → verified/unknown`。
- 单素材 probe checkpoint 与 reconcile-based recovery，防止 GUI 已生效但数据库未写时重复拖拽。

## 快速开始

现有环境已经装有 FastAPI 和 Pydantic 时，可直接运行：

```powershell
python -m app.cli demo
```

输出会包含 Figure ID、9 个实体、5 条关系、动作数和数据库路径。dry-run 的逐动作证据写入
`runtime/screenshots/<figure_id>/`，SQLite 默认位于 `runtime/agent.db`。

完整开发安装：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
```

规划任意显式流程：

```powershell
python -m app.cli plan "Sample → Centrifugation → Supernatant" --output output.json
```

规划附带的 PD-1 示例：

```powershell
python -m app.cli plan examples\pd1_request.txt --output pd1-plan.json
```

## API

```powershell
uvicorn app.main:app --reload
```

主要端点：

- `POST /v1/figures/plan`
- `POST /v1/figures/{figure_id}/execute-dry-run`
- `GET /v1/figures/{figure_id}`
- `POST /v1/figures/{figure_id}/confirm`

示例请求：

```json
{
  "request": "制作双栏对比：PD-1/PD-L1 抑制 T 细胞，anti-PD-1 恢复肿瘤杀伤。",
  "editor_url": "https://app.biorender.com/"
}
```

## 真实浏览器 Phase 0

安装可选依赖与 Chromium：

```powershell
pip install -e ".[browser]"
playwright install chromium
```

登录必须由用户手动完成，并保存在项目内的持久化 Profile：

```powershell
python -m app.cli browser-login
```

Agent 不读取、记录或输入密码和 MFA。登录完成后，把一个可丢弃的空白 Figure 完整编辑器 URL
用于校准。真实执行没有挂到 HTTP API，防止误把未校准的 UI 自动化当作稳定服务。

先校准当前 UI：

```powershell
python -m app.cli calibrate-ui `
  --editor-url "https://app.biorender.com/<your-blank-figure>" `
  --confirm-live
```

Profile JSON 和截图保存在：

```text
output/playwright/calibration/<date>/<profile_id>/
```

搜索、拖拽并验证一个普通素材：

```powershell
python -m app.cli phase0-search-drag `
  --editor-url "https://app.biorender.com/<your-blank-figure>" `
  --query "T cell" `
  --confirm-live
```

证据保存在：

```text
output/playwright/probes/<run_id>/
```

正常结果为 `awaiting_confirmation`，不会自动导出、共享、删除或修改账户。

如果运行在拖拽后、数据库确认前中断，使用输出中的 Run ID 恢复：

```powershell
python -m app.cli phase0-search-drag `
  --resume-run "<probe_run_id>" `
  --confirm-live
```

恢复逻辑会重新校准和截图：

```text
素材已经存在 → 记录 verified，不重放拖拽
素材可信地不存在 → 允许一次安全重试
无法判断或 UI Profile 变化 → unknown，暂停人工检查
```

旧命令仍作为兼容别名，但走同一条可验证链路：

```powershell
python -m app.cli phase0-probe `
  --editor-url "https://app.biorender.com/<your-blank-figure>" `
  --confirm-live
```

只有明确传入 `--confirm-live` 才会连接 live 编辑器。命令不会自动新建 Figure；用户必须提供已经
检查过的空白 Figure URL。

按 Playwright 的验证流程，每次关键 DOM 变化后都会重新定位关键区域。候选必须位于校准后的结果区，
明确可拖拽，具有普通素材卡片或缩略图证据，并通过 AI、模板、订阅和购买策略检查。不会默认信任
第一个结果。

已知 AI 控件会被校准记录并禁止作为交互目标；AI credits、Generate Figure、AI Edit、订阅或购买
确认弹窗会立即停止运行并保存失败截图。规则不会粗暴禁止普通操作中的单独 `generate` 单词。

错误结果包含：错误类型、Workflow State、最后动作、截图路径、人工检查建议和是否可安全恢复。

## 设计边界

确定性规划器不会凭空补全任意复杂机制。除 PD-1 内置验收案例外，用户必须提供明确的箭头流程、
直接提交合法 FigureSpec，或后续接入返回同一严格 schema 的多模态模型。科学校验只是基础防错，
不是文献审查，也不证明图中的科研结论真实。

完整 Figure 的视觉验证仍未实现。单素材 probe 只使用拖拽前后画布像素差异，要求变化发生在目标区域；
无法定位变化时返回 `unknown`，不会把 Playwright 没抛异常当成成功。UI 或素材卡片不满足安全证据时
也会暂停，而不是退回盲目坐标点击。

## 目录

```text
app/
  api/          FastAPI
  planner/      需求、Figure、素材与布局规划
  operator/     动作编译、dry-run、Playwright 与 BioRender 校准/Policy/Observer/Recovery
  verifier/     科学一致性检查
  workflow/     显式可恢复状态机
  storage/      SQLite 审计存储
  schemas/      严格数据契约
examples/       PD-1 验收请求
tests/          规划、校验、恢复与证据测试
runtime/        数据库与持久浏览器会话（运行产物不入库）
output/playwright/  Calibration 与 live probe 截图证据（运行产物不入库）
```

## 测试

普通测试使用 Mock Page 和离线像素图片，不访问真实 BioRender：

```powershell
pytest
```

覆盖 AI Policy、普通候选筛选、校准失败、expected/observed 分离、动作状态、Pixel Observer、恢复去重、
unknown 暂停、SQLite V2 迁移、dry-run 隔离和 `--confirm-live` 安全门。真实 BioRender Probe 只做手工验收。
