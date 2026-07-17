# BioRender GUI Agent MVP

这是一个“先规划、后执行、全程可审计”的 BioRender 网页自动化 MVP。它把科研内容理解、
结构化 Figure Specification、素材搜索、布局、有限 GUI 动作、SQLite 状态和人工确认分成独立层。

当前版本默认只做 `dry-run`，不会登录账户、不会点击 BioRender AI、不会导出、发布、共享、删除或
购买任何内容。真实浏览器模块是 Phase 0 探针，只启用打开编辑器、搜索素材、选择候选、拖拽和截图；
文字与连接器在拿到当前 BioRender UI 的校准记录前会安全失败，不会假装成功。

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

Agent 不读取、记录或输入密码和 MFA。登录完成后，建议把当前空白 Figure 的完整编辑器 URL 传给
探针。真实执行尚未挂到 HTTP API，防止误把未校准的 UI 自动化当作稳定服务。

在一个可丢弃的空白 Figure 中执行 Phase 0 验收（搜索 `T cell`、拖到画布中心、逐步截图）：

```powershell
python -m app.cli phase0-probe `
  --editor-url "https://app.biorender.com/<your-blank-figure>" `
  --confirm-live
```

只有明确传入 `--confirm-live` 才会修改 Figure；若还没有空白 Figure，可额外传
`--create-new`，但更推荐用户先手工创建可丢弃的空白 Figure，以免 UI 变更导致选错模板。

按 Playwright 的验证流程，每次关键 DOM 变化后都应重新定位元素；每个 live 动作都会保存全页截图。
如果搜索框、候选素材或画布定位失败，会返回 `ui_layout_changed` / `search_no_result`，禁止沿用旧坐标。

## 设计边界

确定性规划器不会凭空补全任意复杂机制。除 PD-1 内置验收案例外，用户必须提供明确的箭头流程、
直接提交合法 FigureSpec，或后续接入返回同一严格 schema 的多模态模型。科学校验只是基础防错，
不是文献审查，也不证明图中的科研结论真实。

视觉验证当前只记录“执行完整性”，不会把 dry-run 冒充成视觉检查。下一阶段需要在已登录会话中完成
BioRender 当前 UI 的 Phase 0 校准，然后再启用文字、连接器、画布元素边界识别和最多三轮局部修复。

## 目录

```text
app/
  api/          FastAPI
  planner/      需求、Figure、素材与布局规划
  operator/     动作编译、安全策略、dry-run 与保守的 Playwright 探针
  verifier/     科学一致性检查
  workflow/     显式可恢复状态机
  storage/      SQLite 审计存储
  schemas/      严格数据契约
examples/       PD-1 验收请求
tests/          规划、校验、恢复与证据测试
runtime/        数据库、会话与截图（运行产物不入库）
```
