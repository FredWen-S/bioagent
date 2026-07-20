# BioRender GUI Agent — 端到端全流程审查报告

> 审查日期：2026-07-18
> 审查对象：`C:\bioagent` @ `feature/graphical-ui`
> 基线：`main @ a971ccd` （已推送至 `origin/main`）
> 功能分支：`feature/graphical-ui @ 9df61c5` （已推送至 `origin/feature/graphical-ui`，三次提交）
> 本报告以真实代码、真实 Git 状态、真实 SQLite Schema、真实测试文件为依据；未运行真实 BioRender。

---

## 一、执行摘要

当前分支已经把 “图形化控制台” 用 FastAPI + 原生 HTML/CSS/JS 落地，前端 `/ui`、`/ui-assets/*` 与后端 `/api/ui/*` 通过共享的 `FigureExecutionService` 复用现有 `WorkflowEngine`、`FigureDatabase`、Planner、Operator、Observer、Policy Guard；未通过 Shell 反向调用 CLI，未引入新的 BioRender 自动化实现，未新增 Node 构建链路。三次提交合计 20 个文件、+3002 行，全部落在 UI、Application Service、Schema、文档、测试范围内，未触及无关模块。

工程本地质量层面高：分层清晰、错误码统一、URL/输入 pydantic 校验严格、AI/付费三层禁止（Frontend 隐藏 + Pydantic 词表 + `ActionSafetyPolicy` + `BioRenderPolicyGuard`）、Observer 严格区分 `expected_bbox` 与 `observed_bbox`、Recovery 走 `checkpoint + reconcile`、Save 状态只接受完成文案。

但审计发现若干 P0/P1 级隐性缺陷、若干 P2 用户体验缺陷、和大量 “仅在本地 Fixture 通过” 的验证契约。这些问题在本地兼容编辑器上被 Fixture DOM 天然满足，一旦进入真实 BioRender 会先在 L1–L3 显现。**因此当前具备进入真实 BioRender L0（仅校准）的条件，但不建议直接推进到 L2 以上，未修复主要 P0/P1 前不建议 merge 到 `main`。**

### 状态口径

```
代码实现状态：          通过
本地静态检查状态：      未验证（沙箱无法安装依赖）
本地单元/契约测试状态： 未验证（沙箱无法安装依赖；已静态审阅 56 个 test_ 函数 + parametrize 展开）
本地 Chromium 状态：    未验证（沙箱无 Playwright；已静态审阅 8 个真实 Chromium 用例的执行方式）
真实 BioRender 状态：   未验证
AI Generate 安全状态：  通过（三层拦截；见 §17）
Recovery 可信度：       有条件通过（见 §14 与 P1-3、P1-8）
GUI 可用性：            有条件通过（见 §4、P0-1、P1-2）
进入 L0 的准备度：      通过
是否建议 Push：         通过（已 Push；无需重新 Push）
是否建议创建 PR：       有条件通过（先修复 P0-1 与 P0-2）
是否建议合并 main：     不通过
```

**总体结论：** 当前项目已达到本地工程验收标准，具备进入真实 BioRender L0 校准测试的条件；但在完成真实 Locator、Canvas、Label、Connector 语义识别、Save 状态识别、Resume 去重的真实验证前，不建议合并到 `main`；GUI 侧至少需要修复 “EXECUTING 状态在服务重启后无法从 GUI Resume” 这一 P0 缺陷，才能作为对非命令行用户可靠交付的界面使用。

---

## 二、当前真实完成度

| 层级 | 结论 | 证据 |
|---|---|---|
| 代码实现 | 通过 | 20 个文件、+3002 行、无 subprocess/shell/eval/exec/CLI 反向调用（`tests/test_ui_page.py::test_ui_does_not_copy_operator_or_shell_out_to_cli` 用文本断言，也在实际源码中确认）。 |
| 契约 (Schema) | 通过 | `app/schemas/ui.py` 使用 pydantic v2 `extra="forbid"`；`SafeEditorUrlMixin` 拒绝 HTTP、非 biorender.com、带凭据的 URL；`FORBIDDEN_INPUT` 词表拦截 AI / export / share / upgrade / template。 |
| 后台任务 | 有条件通过 | `FigureExecutionService` 用 threading 后台跑 live / calibration / manual login；job dict 仅内存；无 daemon 之外的清理；服务重启即丢。 |
| Observer / Verification | 通过（本地） | `PixelDiffInsertionObserver` 用真实 baseline/current 截图；`GeometryObserver` 用真实 observed bbox；`LabelAssociationObserver` 用最近邻 + 精确文字 + 截断检测；`ConnectorGeometryObserver` 检查类型/端点/方向/无关素材碰撞/Label 穿越；`LayoutQualityObserver` 计算 overlap/out-of-bounds/alignment/spacing/z-order。 |
| Recovery | 有条件通过 | `_reconcile_persisted_state` 优先读元素记录（DOM 事实），退化到 pixel diff；`replayed=False` 明确写入 metadata。 |
| Policy Guard | 通过（本地） | `assert_page_safe`、`assert_target_allowed`、`assert_query_allowed`、`scan_ai_controls`；覆盖 AI、AI Credits、Subscription、Template、Export/Download/Share/Purchase/Upgrade。 |
| 真实 BioRender | 未验证 | 无任何真实站点截图；`docs/Real_BioRender_Acceptance.md` 明确 L0–L8 “尚未执行”。 |

---

## 三、Git 审计

### 3.1 分支与基线

```
git branch --show-current             → feature/graphical-ui
git branch -vv
  * feature/graphical-ui 9df61c5 [origin/feature/graphical-ui] docs(ui): add graphical interface user guide
    main                 a971ccd [origin/main]                 add prompt docs
git status                            → nothing to commit, working tree clean
```

- 当前工作区干净。
- 分支基线 `a971ccd` 与实施报告一致。
- **实施报告称 “未 Push”，实际 `origin/feature/graphical-ui` 存在且与 HEAD 一致：分支已 Push。** 这与报告不一致；如果是安全期望（内部未 Push），请核对，否则报告应更新。
- 未创建 PR、未合并到 `main`（这与报告一致）。

### 3.2 敏感文件

`.gitignore` 覆盖：`runtime/*.db`、`runtime/logs/*`、`runtime/screenshots/*`、`runtime/sessions/*`、`runtime/pytest-temp/`、`runtime/browser-*`、`runtime/nonbrowser-*`、`runtime/ui-*`、`.playwright-cli/`、`.venv/`、`output/playwright/*`；仅追踪 `.gitkeep`。执行 `git check-ignore -v runtime/agent.db runtime/migration-check.db` 确认两个数据库文件被忽略。仓库内 `git ls-files runtime output` 只返回 4 个 `.gitkeep`。**未提交 SQLite、浏览器 Profile、Cookie、Token、截图或临时数据。**

### 3.3 3 次提交的职责

```
fa272b5  feat(ui): add graphical control panel and shared execution service
527c736  test(ui): add API page and security coverage
9df61c5  docs(ui): add graphical interface user guide
```

- 提交划分合理：功能 → 测试 → 文档；`9df61c5` 仅 `docs/Graphical_UI_Guide.md`。
- 20 个文件全部与本轮 GUI 需求相关；未见无关重构。
- `app/operator/action_planner.py` 仅 3 行变化、`app/storage/database.py` +49 行、`app/workflow/engine.py` +36 行 — 是复用现有引擎所必须的增量，属于 “Figure 级唯一 Action ID + Element 观察持久化” 支持，符合报告描述。

### 3.4 合并风险

- 20 个文件相对 `main` 有 3002 行新增、33 行删除。绝大多数是新文件（`app/api/ui_routes.py`、`app/services/figure_execution_service.py`、`app/schemas/ui.py`、`app/static/ui/*`、`docs/Graphical_UI_Guide.md`、`tests/test_ui_api.py`、`tests/test_ui_page.py`），冲突风险低。
- 无历史重命名，无从其他分支误带入。

---

## 四、架构与依赖审计

### 4.1 分层

```
app/main.py              → 只导出 app
app/api/main.py          → FastAPI app、CSP 头、异常处理、legacy /v1/* 路由
app/api/ui_routes.py     → 新 /api/ui/* 路由（本轮新增）
app/schemas/ui.py        → GUI 输入/输出 pydantic 模型（本轮新增）
app/services/…           → FigureExecutionService（应用层，本轮新增）
app/workflow/engine.py   → WorkflowEngine（现有）
app/planner/…            → Requirement/Figure/Layout/AssetSearch Planner
app/operator/…           → DryRunOperator + BioRender live operator + Playwright bindings
app/storage/database.py  → 全部 SQLite 访问
app/static/ui/…          → 前端资源（本轮新增）
```

依赖方向：`api → services → workflow → planner + operator + storage`。前端 fetch → `/api/ui/*` → service → engine → operator。**没有循环依赖；前端不硬编码后端内部实现。**

### 4.2 `FigureExecutionService` 承担的职责

- 输入校验（部分，其余由 pydantic 完成）
- 计划编译（`plan_task` / `plan_prompt` → engine）
- 后台 Job 生命周期（内存字典 + threading）
- 状态映射（`friendly_status`、`friendly_kind`、`_progress_steps`）
- 数据库读写委派
- 只读文件白名单 (`_safe_evidence_path`)
- Live operator 工厂
- Safe stop / login / calibration / resume 编排

**这里已经开始向 “大对象” 靠拢（818 行、31 个方法）**，可维护性属可控但不轻量：`ManagedJob` 生命周期、URL 校验（部分与 pydantic 重叠、部分依赖 `redact_url`）、状态映射与 UI 步骤映射均可拆分。属于 P3 维护性问题。

### 4.3 API Schema 与 Domain

`app/schemas/ui.py` 是 API-only 模型；`FigureSpec`、`Requirement`、`Entity`、`Relation` 仍在 `app/schemas/figure_spec.py`。**API 层输入没有混入 domain 层类型，除了 `_custom_spec` 内部把 `CustomFigureInput` 转成 `FigureSpec` — 这是必要的边界代码，非重复模型。**

### 4.4 无重复实现

`app/static/ui/app.js` 只走 fetch API，不复制任何 Operator/Workflow；`test_ui_page.py::test_ui_does_not_copy_operator_or_shell_out_to_cli` 已经在 CI 层用文本断言保护该边界（禁止在 `ui_routes` 中出现 `subprocess`、`shell=True`、`LivePlaywrightOperator`；禁止在 `app.js` 中出现 `innerHTML`）。这是一个合理的护栏测试。

---

## 五、启动流程

### 5.1 CLI 启动

```
python -m app.cli web-ui
  → cmd_web_ui: uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
```

- **`host="127.0.0.1"` 硬编码为回环地址**；无法通过 CLI 参数覆盖为 `0.0.0.0`。见 `app/cli.py:459`。
- `--port` 使用 `argparse.choices=range(1024, 65536)`，拒绝特权端口和越界端口。
- `reload=False`，不会二次装载模块。

### 5.2 PS1 脚本

`scripts/start_web_ui.ps1` 在 `.venv` 存在时使用 `.venv\Scripts\python.exe`，否则回落到系统 `python`；调用 `python -m app.cli web-ui`。两条路径最终一致。

### 5.3 全局资源初始化

- `app/api/main.py` 模块加载时创建：`database = FigureDatabase()`、`engine`、`ui_service`。数据库不存在时 `FigureDatabase.__init__` 会 `mkdir(parents=True)` 并 `initialize()`（`executescript(SCHEMA)` + `_migrate_v2`）。**Schema 与迁移都 IF NOT EXISTS / 幂等。**
- 无端口占用检测；uvicorn 会自行报错并退出。属于可接受。
- 挂载 `/ui-assets` 使用 `check_dir=False`，静态目录缺失时不会因 mount 失败。属于合理。
- CSP 头：`default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'`。搭配 `X-Content-Type-Options: nosniff` 与 `Referrer-Policy: no-referrer`；`/ui`、`/ui-assets/*` 均加。属于良好实践。
- `RequestValidationError` 与 `Exception` 只对 `/api/ui/` 前缀做统一 JSON 结构化；`/v1/*` 走 FastAPI 默认，避免破坏兼容性。

### 5.4 潜在启动缺陷

- 用户在无 `.venv` 时直接 `python -m app.cli web-ui` 会走系统 Python：若 fastapi 未安装，仅 `ImportError` 抛出，缺乏对普通用户友好的提示。属于 P3。
- 无健康启动自检；如 Playwright 未安装、Chromium 未安装，只在真正调用 Live 时才会抛出 `RuntimeError` — 已有明确文案（`app/operator/playwright_live.py:132-135`）。可以接受。

---

## 六、GUI 用户流程审查

按食品科学/生物学非技术用户视角逐步走查（依据 `app/static/ui/index.html`、`app/static/ui/app.js`、`docs/Graphical_UI_Guide.md`）。

### 6.1 页面能否指明下一步

- 页面明确按 “步骤 1 / 2 / 3” 编号，每步骤都有中文说明，`badge-safe`/`badge-neutral` 明确显示 AI 状态、AI Credits 状态、真实验收状态。**普通用户能理解 “AI 禁用” 与 “真实 BioRender 待验收” 的语义。**
- 状态卡片有 `真实 BioRender：待人工验收` 使用 `status-card-caution` 明显区分。
- `unknown` 与 `awaiting_confirmation` 分别映射为 `需要人工检查` 与 `等待人工确认`（`friendly_status`），并有 `test_ui_page.py::test_unknown_state_is_not_mapped_to_success` 断言防退化。

### 6.2 按钮启用条件

前端 `updateControls()` 规则：

| 按钮 | 启用条件 |
|---|---|
| `start-dry` | 非 busy && 未选择 Live |
| `start-live` | 非 busy && Live && URL 非空 && 已勾选 “可丢弃” |
| `calibrate` | 非 busy && Live && URL 非空 && 已勾选 “可丢弃” |
| `inspect-plan` | 非 busy |
| `safe-stop` | 有 currentRunId && can_stop |
| `resume-run` | 非 busy && can_resume && Live && 已勾选 |
| `verify-run` | 有 currentRunId && 非 busy |

规则合理，但存在以下用户体验/状态问题：

- **P0-1（严重）：`can_resume` 只包含 `PAUSED_AUTHENTICATION / PAUSED_APPROVAL / PAUSED_RECONCILIATION / FAILED`（`app/services/figure_execution_service.py:305-311`）。** 若 Web 服务在 Figure 处于 `EXECUTING` 时崩溃或用户关掉终端后重启服务，Figure 状态永久保留为 `EXECUTING`，GUI 会禁用 “继续上次任务”。用户在 GUI 无法恢复该 Run；只能改用 CLI `resume-live-figure`。这与文档 “刷新页面后仍可继续” 承诺相悖。**建议将 `EXECUTING` 加入 `can_resume`，并且在 Startup 阶段把 “没有活跃 Job 且状态 == EXECUTING” 视为 stale，标为 `PAUSED_RECONCILIATION`。**

- **P1-2：环境状态刷新时会自动 `state.currentRunId = state.recentRun.id` 并 `loadRun(...)`（`app/static/ui/app.js:237-240`）。** 多个 Run 存在时，页面初次打开会 “挑最近那个”，用户可能误以为是自己刚才提交的任务。风险：用户按 “继续上次任务” 或 “安全停止” 命中的是别人（或旧）的 Run。**建议：只有页面首次交互（用户主动 Inspect / Dry / Live）后才自动映射；或者在切换 Run 时高亮 “当前查看的是最近的 Run”。**

- **P2：`start-live` 前无 “最后一次审核 URL” 提示。** 前端只做 `hasUrl`（长度 > 0）判断；`/api/ui/check-url` 只是 Pydantic 校验协议+域名，非真正可达性；用户可能把非空白 Figure 误提交。**建议：在启动 Live 前弹窗二次确认 “这将开始修改此 URL 指向的 Figure：<redacted_url>”。**

- **P2：自定义模式最多 15 assets、30 relations、`removeAsset` 至少保留 2 个（`app.js:92`）** — 已由 `CustomFigureInput` 与前端共同约束。但当前 `layout_planner` 是否稳定支持 15 素材，未在 UI 里暴露 “太多素材可能导致自动布局失败或元素越界”。**建议：当 assets 超过 8 时前端提示 “自动布局在大量素材上未做真实验收”。**

- **P2：`checkUrl()` 的成功文案 “URL 格式有效；是否可编辑将在校准时确认” 使用 `field-message` 但不区分成功/警告样式**，且始终追加 `redacted_url` 例如 `https://app.biorender.com/.../<redacted>`；用户可能误以为系统已经 “接受” URL。属于文案 UX。

- **P2：`Delete` 按钮无二次确认**：`removeAsset` 直接删素材并清理相关 Connector。对非技术用户无二次询问。可接受，但建议弹 `confirm()`。

### 6.3 步骤映射准确性

`_progress_steps` 把 15 类 `ActionType` 汇总到 10 个用户步骤（plan/calibrate/policy/search/place/labels/connect/layout/verify/confirm），`_step_status` 优先级 `blocked_by_policy > unknown > failed > running > completed > waiting`。**符合 “不夸大成功” 的目标。**

- `plan` 总是 `completed`（合理，plan 是提交时就完成的）。
- `policy` 只在 `blocked_by_policy` 存在时置 `blocked`，否则 `completed if started else waiting`。**警告：真实运行中如果没有任何 action 命中 policy，UI 会把 policy 步骤显示为 `completed`，但那不代表 “页面上没有 AI 控件被扫描到”，只是 “没有 Action 被拦截”。文案上是清楚的 “检查安全策略”，可接受。**

### 6.4 “Only latest recent Run” 竞态

`refreshEnvironment` 每 10 秒自动运行。若用户在提交 A 之后立刻用另一个标签打开 UI，第二次刷新会 `state.currentRunId = A`，可能撞上正在跑 Live 的 A。属于 P2 可用性问题（见 P1-2）。

### 6.5 GUI 是否可能顺序错误

- Preset 模式最短流程：URL 无需 → Dry Run 即可。
- 自定义模式：可先 Inspect Plan → Dry Run，界面允许直接 Live（有校验）。
- 无强制 “必须先 Dry 才能 Live” 的规则。这在 CLI 也是一致的。可接受。

---

## 七、API 对抗性审查

### 7.1 路由总览（`/api/ui/*`）

| Method | Path | Body Model | Guard |
|---|---|---|---|
| GET  | `/api/ui/status` | – | – |
| GET  | `/api/ui/presets` | – | – |
| POST | `/api/ui/check-url` | `UiEditorUrlRequest` | pydantic URL 白名单 |
| POST | `/api/ui/plans` | `UiPlanRequest` | 词表 + 关系图校验 |
| POST | `/api/ui/dry-run` | `UiDryRunRequest` | 同上 |
| POST | `/api/ui/calibrate` | `UiCalibrationRequest` | URL + `_require_live_confirmation` |
| POST | `/api/ui/login/open` | `UiLoginRequest` | `confirm_manual_login` |
| POST | `/api/ui/login/complete` | – | 状态检查 |
| POST | `/api/ui/live-runs` | `UiLiveRunRequest` | URL + 双重确认 |
| GET  | `/api/ui/jobs/{job_id}` | – | – |
| GET  | `/api/ui/runs/{run_id}` | – | – |
| GET  | `/api/ui/runs/{run_id}/elements` | – | – |
| GET  | `/api/ui/runs/{run_id}/evidence` | – | – |
| POST | `/api/ui/runs/{run_id}/resume` | `UiResumeRequest` | 双重确认 |
| POST | `/api/ui/runs/{run_id}/verify` | – | – |
| POST | `/api/ui/runs/{run_id}/stop` | – | – |
| GET  | `/api/ui/evidence/{evidence_id}` | – | `_safe_evidence_path` |

### 7.2 URL 校验（`SafeEditorUrlMixin`）

- `scheme != "https"` → 拒绝（覆盖 `http`, `javascript`, `file`, `ftp`）。
- hostname 必须 `== "biorender.com"` 或 `endswith(".biorender.com")`；`casefold` 处理大小写。
- `parsed.username` 或 `parsed.password` 存在则拒绝。
- 长度 `min_length=12, max_length=2048`。
- `test_live_run_rejects_unsafe_urls` parametrize 覆盖 `http://`、`https://example.com/`、`https://user:password@...` 三种。

**未覆盖但影响较小的边界：**
- IDN 同形（如 `bioгender.com`）— pydantic 不做同形检查，但 `endswith(".biorender.com")` 会拒绝，安全。
- URL 尾部 `#fragment` / `?query=token` 未被剥离即入库；当前不写数据库，仅传给 Playwright `goto`，会随导航。属于可接受。
- `redact_url()` 只输出 `scheme://netloc/.../<redacted>`；`test_status_and_presets` 未直接断言此。用户仍能透过控制台或日志看到部分 URL。属于 P3。

### 7.3 输入词表

`FORBIDDEN_INPUT` 覆盖 `biorender ai / ai generate / ai edit / ai credits / create with ai / generate figure / upgrade / subscribe / purchase / export / download / share / publish`。分别用于 `title`、`research_topic`、`notes`、`display_name`、`search_term`、`fallback_terms`、`label_text`。

**盲点：**
- 未拒绝 `bio ai`、`generative`、`upgrade version`、`purchase now` 等边缘表达 — 但正则 `\bupgrade\b`, `\bpurchase\b` 已捕获后两者。
- 中文 `订阅 / 购买 / 升级 / 生成图 / AI 生成` 未列入词表。用户提交中文 `AI 生成` 可以通过词表（`ai\s+(?:generate|edit|credits?)` 不匹配 `ai 生成`）。属于 P2。
- HTML/JS 注入：所有字段最后仅进入 `WorkflowEngine.plan_prompt` 或 `_custom_spec` → `FigureSpec`，最终存 JSON；前端 `renderElements` 使用 `textContent`（`app.js:275-282`），未使用 `innerHTML`（`test_ui_page.py::test_ui_does_not_copy_operator_or_shell_out_to_cli` 断言）。DOM 中不会被解析为脚本。**通过。**

### 7.4 Run/Job 行为

- **不存在 Run ID**：`RUN_NOT_FOUND` → 404（`test_unknown_run_has_stable_error`）。
- **已完成 Run 再 Resume**：`start_resume` 直接进入 `WorkflowEngine.execute()`，engine 内部 `if status == COMPLETED: return status`，会立即完成，Job 变 `completed`。**不会重复动作，但会产生一个空 Job**。属于 P3。
- **同一 Run 再启动 Live**：`start_live` 首先 `_ensure_browser_available` → 若已有 live/calibration/login Job 则抛 `BROWSER_BUSY 409`；然后 `plan_task` 创建一个新的 Figure（`_custom_spec` 会生成新 uuid → 新 figure_id），最后 `start_resume(new_figure_id)`。**Preset 模式下每次 Live 都会产生一个新 Figure（`plan_prompt` → `plan_spec` → `save_bundle` upsert 会 `INSERT ... ON CONFLICT DO UPDATE`；但 preset 复用 `pd1_request.txt`，其 FigureSpec.id 是固定的 `figure_pd1_v1`）。** 相同 preset 反复启动会 **覆盖同一 figure_id** 的 spec / actions / element_requirements，同时删掉旧 `figure_entities`/`figure_relations`/`gui_actions`/`element_requirements` 再重建。**但历史 `editor_elements`、`screenshots`、`verification_results`、`audit_events` 不受 `save_bundle` 影响，会滞留残留证据。** 而 `test_multiple_runs_use_distinct_persisted_action_ids` 期望 `first.run_id != second.run_id` — 因为使用 `_custom_task()` 生成 uuid Figure，preset 情况没有类似测试。

  → 这是 **P1-4**：preset 模式下反复启动 Live 会共用一个 `figure_pd1_v1`，历史 screenshots/verification 会与新 Run 混在一起，用户 UI 上 `verified_actions`、`element_summary`、`evidence_summary` 会显示 “上一次 Run 的证据 + 这次 Run 的动作”。**建议：preset 模式每次也用 uuid 生成 `figure_id`，或在 `save_bundle` 时清理 `editor_elements`/`screenshots`/`verification_results`。**

- **Live 缺少 URL 或缺少人工确认**：Pydantic 422（`test_live_run_requires_both_confirmations` 与 `SafeEditorUrlMixin`）。
- **Safe Stop 与 Resume 竞态**：`stop_event.set()` 只在 `_run_live_job` 的 `stop_requested` 回调被 engine 检查（`app/workflow/engine.py:85-98`）。engine 在动作边界检查，若正在执行 GUI 动作，需要等待当前动作完成才生效。**符合 “安全停止在动作边界” 承诺；但如果单个动作耗时 >30 s，用户可能觉得没反应，UI 上 job.status 变为 `stop_requested` 已经提示。**
- **多次 Safe Stop**：无幂等保护 — 每次都 `set()` 与更新消息；`stop_event.set()` 天然幂等；OK。
- **Safe Stop 后 Resume**：`start_resume` 检查 `_active_job_for_figure`；已停止 Job 状态是 `stopped`（属于 `FINAL_JOB_STATES`），因此新的 Job 可创建。**OK。**
- **Resume 重复点击**：`_ensure_browser_available` 拦截；`test_resume_does_not_start_duplicate_job` 已覆盖 (`error_code in {"BROWSER_BUSY", "RUN_ALREADY_ACTIVE"}`)。

### 7.5 证据文件路径

- `evidence_id` 只接受 `"calibration-latest"` 或十进制字符串。非法值 → `EVIDENCE_NOT_FOUND 404`。
- `_safe_evidence_path` 用 `Path.resolve` 后要求：后缀 ∈ {`.png`,`.jpg`,`.jpeg`,`.webp`}；且 `relative_to` 命中 `screenshot_dir | calibration_dir | probe_dir | live_figure_dir`。
- `test_evidence_preview_rejects_paths_outside_allowlist` 通过写入白名单外的 `outside.png` 并断言 `403 EVIDENCE_ACCESS_DENIED`。**这是可靠的路径穿越防线。**
- 符号链接：`Path.resolve` 会跟随；如果 attacker 把白名单目录内的一个 png 建立成指向 `/etc/passwd` 的软链接，`suffix.casefold()` 检查会拦截（非 png/jpg/jpeg/webp）。但若软链接指向另一个 png，`resolve` 后会解析到真实路径 → 若真实路径不在白名单目录，`relative_to` 全部失败 → 返回 `None`。**通过。**

### 7.6 subprocess / shell / eval / exec

`ui_routes.py`、`figure_execution_service.py`、`cli.py`、`app.js` 均未使用 `subprocess`、`shell=True`、`eval`、`exec`、`os.system`（已由 `test_ui_page.py::test_ui_does_not_copy_operator_or_shell_out_to_cli` 保护；再次 grep 核实）。

### 7.7 中间件

CSP 只对 `/ui` 与 `/ui-assets/*` 生效。API 响应 (`/api/ui/*`) 无 CSP，但也非 HTML 页面，可以接受。

---

## 八、CLI 与 GUI 一致性矩阵

| 能力 | CLI 命令 | GUI 入口 | 底层实现 | 行为一致？ | 差异/风险 |
|---|---|---|---|---|---|
| Plan | `plan <request>` | POST `/api/ui/plans` | `FigureExecutionService.plan_task` 或 `plan_prompt` → `WorkflowEngine.plan` | 一致 | GUI 强制 UiTaskInput（preset 或有限自定义）；CLI 接受任意文本或路径。 |
| Dry Run | `demo` | POST `/api/ui/dry-run` | `execute_dry_run(figure_id)` → `WorkflowEngine.execute(DryRunOperator)` | 一致 | `demo` 只支持 PD-1；GUI Dry 支持 preset + 自定义。 |
| Manual Login | `browser-login` | POST `/api/ui/login/open` + `/login/complete` | `LivePlaywrightOperator.manual_login` vs `_run_login_job` | 部分一致 | CLI 是同步阻塞（`input(...)`）；GUI 是后台线程 + `login_complete` event。行为等价但不共享同一函数。 |
| Calibrate | `calibrate-ui --confirm-live` | POST `/api/ui/calibrate` | `BioRenderUiCalibrator` | 一致 | 底层一致，配额检查一致。 |
| Live search only | `live-search-asset --confirm-live` | 无 GUI 入口 | LivePlaywrightOperator + SafeAssetSearch | GUI 缺失 | 属于开发者调试命令，不是 P0；文档已说明。 |
| Live single asset probe | `phase0-search-drag --confirm-live` | 无 GUI 入口 | `BioRenderSingleAssetProbe` | GUI 缺失 | 同上。 |
| Live full figure | `live-figure --confirm-live` | POST `/api/ui/live-runs` | `FigureExecutionService.start_live` → `start_resume` → `_run_live_job` → `engine.execute` | 一致 | 均要求 `--confirm-live` / `confirmed_disposable + confirm_live`。 |
| Resume | `resume-live-figure --run-id --confirm-live` | POST `/api/ui/runs/{id}/resume` | 相同 engine 路径 | 一致（但见 P0-1） | GUI 无法在 `EXECUTING` 状态启用按钮。 |
| Verify | `verify-live-figure --run-id` | POST `/api/ui/runs/{id}/verify` | `verify_run(...)` 读 SQLite | 一致 | 均为只读。 |
| Confirm 最终 | `confirm <figure_id>` | 无 GUI 入口 | `WorkflowEngine.confirm` | GUI 缺失 | GUI 目前无 “最终确认” 按钮，只显示 `awaiting_confirmation` 状态；用户需自己去 CLI 才能把 Figure 标记为 `completed`。属于 **P2-5**（用户体验缺陷）。 |
| Inspect elements | `inspect-elements --run-id` | GET `/api/ui/runs/{id}/elements` | `element_summary` | 一致 | – |
| Safe Stop | 无 | POST `/api/ui/runs/{id}/stop` | `request_safe_stop` | CLI 缺失 | CLI 目前只能 Ctrl+C；这本身是安全的（engine 处理线程停止不同）。可接受。 |

**主要一致性问题：**
1. GUI 没有 “最终确认（confirm）” 按钮（P2-5）。
2. GUI Resume 可用状态窄于 CLI（P0-1）。
3. GUI 每次 preset Live 复用 `figure_pd1_v1`（P1-4）。

---

## 九、Workflow 状态机

### 9.1 合法状态

`FigureStatus` = { `created, planned, validated, executing, verifying, repairing, awaiting_confirmation, completed, paused_authentication, paused_approval, paused_reconciliation, blocked, failed` }。

`ActionStatus` = { `planned, executing, executed_unverified, verified, pending, running, succeeded, failed, unknown, blocked_by_policy, skipped, paused` }。

### 9.2 合法跳转（依据 `WorkflowEngine.execute` / `confirm`）

```mermaid
stateDiagram-v2
    [*] --> validated: plan()
    validated --> executing: execute() entry
    validated --> blocked: safety.check() rejects
    executing --> paused_approval: stop_requested at boundary
    executing --> paused_authentication: AuthenticationRequired
    executing --> paused_reconciliation: EXECUTED_UNVERIFIED / UNKNOWN / reconcile-non-verified
    executing --> blocked: PolicyBlocked / UnsafeActionError
    executing --> failed: all retries exhausted
    executing --> verifying: all actions consumed
    verifying --> awaiting_confirmation: after final verification write
    awaiting_confirmation --> completed: confirm()
    paused_reconciliation --> executing: resume via execute()
    paused_approval --> executing: resume via execute()
    paused_authentication --> executing: resume via execute()
    failed --> executing: resume via execute()
    blocked --> [*]
    completed --> [*]
```

### 9.3 潜在非法跳转

- **`failed → verified` 直接跳**：不发生。`execute()` 中只会把 result 写为 `FAILED` 后 `return FigureStatus.FAILED`。resume 会把 Figure 重新设为 `EXECUTING`，然后走 reconcile 路径；reconcile 可能把 Action 记为 `VERIFIED`，但 Figure 状态经过 `EXECUTING → VERIFYING → AWAITING_CONFIRMATION`，不跨过。
- **`blocked → executing`**：`execute()` 首先 `if status == BLOCKED: return status` — 不会重新执行。**属于终态处理。**
- **`awaiting_confirmation → completed`**：仅通过 `confirm()`。`confirm()` 检查 `if FigureStatus(record.status) != AWAITING_CONFIRMATION: raise ValueError`。**属于强约束。**
- **Action `verified` 但 Figure `failed`**：`execute()` 中最后一个 action 抛出 error → 记 `FAILED` result → `return FAILED`。此时之前的 action 是 `verified`，Figure 是 `FAILED`。**这是合法且期望的状态：动作级验证成功，但总体 Run 失败。UI 用元素级状态显示，`verified_actions` 可能不为 0；`friendly_status = 操作失败`。逻辑正确。**
- **Figure `completed` 但存在 `unknown` element requirement**：`confirm()` 只检查 Figure 状态，不检查元素级状态。若 GUI 在 `awaiting_confirmation` 时有 element_requirements.status = 'unknown'（例如某个可选元素未观察到），用户仍可 CLI `confirm`。**属于 P2-6：GUI 没有 “最终确认” 但 CLI 允许在有 unknown 时确认。** 建议 `confirm()` 拒绝有 `unknown` requirement 的 Figure。
- **服务重启后 `EXECUTING` 卡住**：见 P0-1。engine 不会自动检测 stale executing。

### 9.4 数据库 vs 内存 Job 冲突

`ManagedJob` 只在内存字典。Figure 状态在 SQLite。二者可能不一致：

- 服务重启：Job 丢失，Figure 仍为 `EXECUTING`。GUI 上 `active_jobs=[]`，但 `recent_runs[0].status='executing'` → `friendly_status='正在操作'`。用户看到 “正在操作” 但没有活跃 Job，Safe Stop 不可用（`can_stop = _active_job_for_figure != None`），Resume 也不可用（`can_resume` 不含 executing）。→ **死锁 UI 状态**。

---

## 十、FigureExecutionService 深入

### 10.1 线程与竞态

`_lock: threading.RLock` 保护 `_jobs` 和 `_threads`。**关键路径：** `_ensure_browser_available`、`_active_job_for_figure`、`start_resume`、`start_calibration`、`start_manual_login`、`get_job`、`system_status`、`request_safe_stop`、`complete_manual_login` 都加锁。

**未加锁的潜在竞态：**
- `_run_live_job / _run_calibration_job / _run_login_job` 内部对 `job.status`、`job.message` 的更新经由 `_update_job(...)` 加锁；OK。
- `stop_event` 是 `threading.Event`；跨线程可安全设置。
- `_login_complete` 是全局 event；如果两个连续 login job（应被 `_ensure_browser_available` 拦截）同时存在，`_login_complete.clear()` 与 `.set()` 可能相互影响。已由 `_ensure_browser_available` 抑制。
- 数据库连接：`FigureDatabase.connect()` 每次新建 sqlite3 连接（`sqlite3.connect(self.path, timeout=30)`），跨线程安全（Python sqlite3 默认 `check_same_thread=True`；每个 with 块自己 new 一次 → OK）。

### 10.2 Job 生命周期

- `queued → running → (waiting_user | stop_requested) → completed | failed | blocked | stopped`。
- `FINAL_JOB_STATES = {"completed","failed","blocked","stopped"}`。
- **Job 不会从字典中被删除**；随服务运行时间增长，`_jobs` 会累积（每个 Live/Calibration/Login 一个）。属于内存泄漏（P3）。

### 10.3 异常处理

- `_run_live_job` 对未捕获异常写 `failed`、`error_code = type(error).__name__`。**用户拿不到 traceback、错误原因描述**。engine 内部对 `OperatorError` 有 screenshot_path，会写入 `gui_actions.result_json`，GUI 通过 `runs/{id}` 能看到；但 Job 本身仅得到 `type_name`。P2。
- Playwright browser context 泄漏：`_run_login_job` 和 `_run_calibration_job` 用 try/finally 保证 `operator.close()`。`_run_live_job` 依赖 `engine.execute()` 内部的 `finally: operator.close()`（`app/workflow/engine.py:340-341`）。**如果 `engine.execute()` 在进入 `finally` 之前发生 `SystemExit`（例如 uvicorn 强制退出），browser 可能不 close，Chromium 会残留。** 属于 P3。

### 10.4 God-object 迹象

818 行、31 个方法、6 个内部状态字段（`_jobs`、`_threads`、`_lock`、`_login_complete`、`_live_operator_factory`、外加 database/engine），承担校验/编排/映射/文件白名单/静态映射。相比 `WorkflowEngine`（~440 行）已经更胖。**未突破可维护边界，但未来任何 UI 迭代都会继续加码。** 建议逐步拆分：
- `JobRegistry`（管理 ManagedJob 生命周期）
- `UiPresentation`（friendly_status/friendly_kind/_progress_steps）
- `EvidenceService`（`_safe_evidence_path`、`evidence_path`、`evidence_summary`）

### 10.5 “相同 Figure 两个请求 A/B/C/D 同时到达” 场景

- 请求 A `start_live(preset)` → `_ensure_browser_available()` 通过 → `plan_task` 创建 preset Figure（figure_pd1_v1）→ `start_resume(figure_pd1_v1)` → 拿 lock → 新 Job A 添加。
- 请求 B `start_live(preset)` → `_ensure_browser_available()` 拒绝（`BROWSER_BUSY`）。
- 请求 C `stop_run(figure_pd1_v1)` → `_active_job_for_figure(figure_pd1_v1)` → 返回 Job A → `stop_event.set()`。
- 请求 D `start_resume(figure_pd1_v1)` → `_ensure_browser_available()` 拒绝（Job A 仍在跑）→ 返回 `BROWSER_BUSY`。

**终态确定：** 最终 Job A 会走到 `paused_approval` 或 `completed / failed / blocked`；然后 D 可以重试。**属于可确定行为。**

**BUT：** 请求 A + D 若 preset 相同，都会 `plan_task` 覆盖同一 spec — A 已开始执行前提下 D 又覆盖 gui_actions，会导致 A 的 engine 循环 `list_actions()` 拿到新 list，可能读到已删除的 action。**属于 P1-4 的另一面。**

---

## 十一、Planner 与元素需求

### 11.1 计数复核

对 PD-1 preset 计数与文档声明比对（来源：`docs/Element_Capability_Matrix.md`、`app/operator/action_planner.py`、`app/storage/database.py::_element_requirement_rows`）：

- **Assets：9**（表 1，`t_cell_before, tumor_cell_before, pd1_before, pdl1_before, t_cell_after, tumor_cell_after, pd1_after, pdl1_after, antibody_after`）✓。
- **Labels：9**（表 2，为每个 asset 一个 label）✓。
- **Connectors：5**（表 3）✓。
- **Groups：9**（`group_<entity_id>`）✓，由 `action_planner.compile` 尾部生成。
- **Alignments：4**（`align_without_treatment_0073/0074`, `align_anti_pd1_treatment_0075/0076`）✓ — 依赖布局产生 `region_rows`，如果两 asset 同一 row 则触发。
- **Distributions：1** ✓（`distribute_anti_pd1_treatment_0077`），三个素材同一 column。
- **Regions：2** ✓（`without_treatment`、`anti_pd1_treatment`）。
- **Z-order：1** ✓（`layout_z_order`）。
- **Save：1** ✓（`document_save`）。

**合计 41 条 element_requirements** — 与文档承诺一致。**注意 `bundle.actions` 总数是 94，与 README 一致。**

### 11.2 Action ID 唯一性

`GuiActionPlanner.compile` 生成 `id=f"action_{figure_key}_{sequence:04d}_{action_type.value}"`，其中 `figure_key = spec.id.removeprefix("figure_")`。对 preset `figure_pd1_v1` 而言，key 是 `pd1_v1`。若两次 preset 复用同一 `figure_pd1_v1`：save_bundle 里 `DELETE FROM gui_actions WHERE figure_id = ?` 后重新插入 → **不冲突**。但对不同 figure_id 的两次 Run（e.g. 两个自定义 Figure），action ID 会不同，符合报告 “Figure 级唯一 Action ID” 说法。

**遗留问题**：`action_planner.py` 里对 align/distribute 的 `logical_layout_id = f"align_{region_id}_{len(actions):04d}"` 依赖 `len(actions)` 生成，随插入顺序变化；同一 Figure 重跑（save_bundle 覆盖）不冲突。跨 Figure 不同 → 也 OK。

### 11.3 弱指纹

`_weak_figure_element_id(figure_id, element_id, seed) = "weak_" + sha256(...)[:20]`。是稳定 hash，跨 Run 一致，可用于识别 “已存在的元素”。Resume 时通过 `element_id` (logical) 查询 `editor_elements`，不依赖 hash 匹配，但 hash 存于 payload 供审计。**合理。**

### 11.4 “老 Run 与新 Run 是否兼容”

`save_bundle` 会覆盖 spec/actions/element_requirements 但不清理 editor_elements/verification_results/screenshots/audit_events → **老残留会与新 Run 混淆**（P1-4）。

### 11.5 自定义模式限制

- Assets 2–15、Relations 1–30、`layout=Literal["auto"]`。
- `CustomFigureInput.validate_graph` 拒绝：孤立素材、无向重复 relation、self-loop。
- **`_custom_spec` 强制 `LayoutType.LINEAR`。** LayoutPlanner 未审阅是否稳定支持 15 素材的 linear 布局；GUI 亦未提示。属于 P2。

---

## 十二、Playwright Live Operator

### 12.1 定位器优先级

`resolve_first_visible` / `resolve_largest_visible` 使用 `role → label → css` 顺序，见 `locators.py`：`role="searchbox"`、`role="button"`、label-based regex（中英文）、`data-testid` 命中。**属于合理的多策略回退。**

**已知失败模式：**
- 真实 BioRender 无 `[data-testid*='canvas-container']`、`[data-agent-id]`、`[data-agent-kind]`、`[data-entity-id]`、`[data-label-id]`、`[data-relation-id]`。**这些是本地 Fixture 特有属性。** `CANVAS_LOCATORS` 有 `.konvajs-content` 与 `main canvas`、`canvas` 兜底，可以覆盖真实 BioRender。**但 `_direct_element_locator` 和 `_observable_canvas_object_count` 完全依赖 fixture 属性**（`app/operator/playwright_live.py:2294-2308, 2179-2201`）。真实 BioRender 命中概率极低，元素级定位会退化到 `runtime_locators` 缓存 + `_selected_or_last_object`（`SELECTED_OBJECT_LOCATORS`）。
- `SELECTED_OBJECT_LOCATORS` 首选 `[data-testid*='canvas-element'][data-selected='true']` — 也是 fixture 属性。**回退**到 `[data-testid*='selection-box']`、`[class*='selection-box']`、`[class*='transformer']`（如 Konva.js `Transformer`）。真实 BioRender 若用 Konva 则可能命中 `[class*='transformer']`，但对 non-Konva 则失败。

**结论：** 真实 BioRender 一旦 Locator 不匹配，Move/Resize/Add Text 等 Action 会退化到 `page.mouse.click(bbox center)` — 但 bbox 来自 `_element_bbox`，若 `_direct_element_locator` 找不到元素，`_element_bbox` 会走 `_element_record` → 读上一次 observed bbox（如 drag 时的记录）。若浏览器窗口滚动 / 缩放 / DPI 变化，历史 bbox 与真实位置不再对应。**属于 P1-7：真实 UI 元素定位强依赖 fixture 属性。**

### 12.2 固定坐标 / 固定 sleep

**固定 sleep 汇总（`wait_for_timeout` 单位 ms）：**
- `_open_editor`：1500
- `_search_asset`：（在 `SafeAssetSearch` 内部；未在此文件出现）
- `_select_element`：250
- `_move_element`：700 after mouse.up + `_clear_selection` 120
- `_resize_element`：250 (re-click) + 700 + 120
- `_rotate_element`：700 + 120
- `_add_text`：250 + 500 + 120
- `_edit_text`：500 + 120
- `_connect`：700 + 120
- `_group_elements` / `_align_elements` / `_distribute_elements`：500 + 120
- `_observe_autosave` 最长 40×250 = 10 s 轮询

**没有硬编码画布/toolbar 像素坐标。** 所有 Move/Resize/Rotate/Connect 的目标坐标都基于 `_expected_viewport_bbox(action, canvas_bbox)`（`app/operator/playwright_live.py:2225-2253`），从 canvas_bbox 与 action.expected_bbox 转换而来。属于合理。

### 12.3 Viewport 与 DPI

`launch_persistent_context(viewport={"width": 1440, "height": 1000})` 固定。**若用户 Windows 有 125% / 150% 缩放**，Playwright viewport 仍是 CSS 像素，坐标应与 canvas bbox 匹配；Chromium headed 会按 CSS 像素运行。DPI 影响主要在系统鼠标 vs Chromium 内部像素，Playwright 内部 API 使用 CSS 像素，因此不受 Windows 缩放影响。**通过。**

### 12.4 浏览器窗口关闭 / Context 泄漏

`close()` 关闭 context + playwright；`engine.execute()` 的 `finally` 保证调用。**唯一漏点**是 `SystemExit` 中断 finally（uvicorn `--reload=False` 下不太常见）。属于 P3。

### 12.5 页面加载与错误传播

- `page.goto(url, wait_until="domcontentloaded", timeout=60_000)`。
- Auth 检查：URL 含 `login|log-in|sign-in|signin` 或有 `input[type='password']` → 抛 `AuthenticationRequired`。
- Canvas 未检测到 → `UiLayoutChanged`。
- Selection 无 handle → `_closest_canvas_element` 兜底 → 仍无则 `UiLayoutChanged`。

**错误经 `engine.execute` 中的 `OperatorError` handler 保留 `screenshot_path`。** 传播链完整。

---

## 十三、Observer 与 Verification

### 13.1 `expected_bbox` vs `observed_bbox`

严格分离：`GuiActionResult` 有独立字段；`observed_bbox` 需要 `observation_confidence` 与 `observation_source`（模型级 validator）。`_persist_element` 使用 evidence.observed_bbox 写入 `bbox_json`，`expected_bbox` 单独写入。

`test_biorender_phase0.py::test_expected_bbox_is_never_automatically_observed` 用行为断言防退化。

**Grep `observed_bbox = expected_bbox` 或类似赋值** — 未在生产代码出现。**通过。**

### 13.2 Save 状态识别

`_observe_autosave` 匹配 `re.search(r"\ball changes saved\b|^saved$|^已保存$|所有更改已保存", normalized)`。**只接受完成态**；`Saving...` 不匹配；固定等待也不匹配。40 次 × 250 ms = 10 秒轮询窗口。

**风险：** 真实 BioRender 若使用 `Saving...` → 短暂 flash → `Saved` → 立即消失至无文案，10 秒窗口可能错过。**建议：** 增加 mutation observer / event-based 观察，或延长窗口。属于 P2。

### 13.3 Label

- 精确文字（`exact_text = observed_text == expected_text`）
- 最近素材匹配 target（`nearest_id == target_element_id`）
- proximity ≤ `max(90.0, target_bbox.height * 1.7)`
- 内 canvas
- 未截断（DOM measure）

**重复标签（如两次 `T cell`）**：`t_cell_before` 与 `t_cell_after` 都有 label `T cell`，最近邻区分。测试 `test_duplicate_label_text_is_associated_by_target_proximity` 覆盖。

**风险：** 真实 BioRender 若不暴露 DOM 文本（Canvas 渲染），`observable_label_text` 返回 None → `text_exact=False` → 无法通过。属于 P1-9（真实站点 Canvas 渲染时观察失败）。

### 13.4 Connector

- 类型别名映射（`inhibition/blocking_line/t-bar → t_bar`）
- 端点距离 ≤ `max(24.0, max(w, h) * 0.7)`
- 方向：`source_dist + target_dist < reverse_source + reverse_target`
- 无关素材碰撞与 label 碰撞检测
- **只有 route_verified + type_verified + start + end + direction + 无碰撞 → `passed=True`**；否则整体走 UNKNOWN。

**已在文档强调**：真实 BioRender 若不暴露 `data-connector-type` 与 route 几何，会返回 UNKNOWN，不冒充语义验证。**属于合理保守。**

### 13.5 Layout / Region / Z-order

- overlap：交集面积 / min_area > 5%
- out-of-bounds：±2 px 容差
- region 中心必须在 region_box 内
- alignment_deviation ≤ 8 / spacing_deviation ≤ 8（放宽自 5/6 到 8 的最终容差）
- **z-order 用 `getComputedStyle().zIndex`**：真实 BioRender 使用 Konva/Canvas 时 `getComputedStyle` 对 canvas 内子对象没有意义 → 所有 connector `z_order_unknown` → `layout.passed = False` → `_capture_canvas` 返回 UNKNOWN → 整体 Run 会卡在 `paused_reconciliation`。**属于 P1-10：z-order 观察依赖 DOM 元素与 CSS z-index，真实 BioRender 会永久失败。**

### 13.6 pixel diff 阈值

`minimum_target_change = max(80, int(target_area * 0.012))`；`total_changed <= max(20, area * 0.00005)` 判 ABSENT。**在 1440×1000 画布上，1.2% 目标 crop 区域变化。真实 BioRender 素材如果比较小（0.14×0.14 canvas = 约 140×140 px），target_area ≈ 19600，阈值 ≈ 235 px。合理。**

**未使用 OCR。** 文档已声明。

---

## 十四、Recovery / Crash Injection

### 14.1 Reconciliation 层级

`_reconcile_persisted_state`（DOM 事实优先） → `PixelDiffInsertionObserver`（截图观察） → UNKNOWN。

**为每个 mutating action 类别提供针对性事实：**
- DRAG：`insertion.passed==True` 已持久化 → VERIFIED, replayed=False
- MOVE/RESIZE：`observed_bbox` 与 `expected_viewport_bbox` 通过 `GeometryObserver` 校验
- ROTATE：`observed_rotation ≈ expected_rotation ± 2°`
- ADD/EDIT_TEXT：`observed_text == expected_text` && `association.passed`
- CONNECT：`connector.passed==True`
- GROUP：所有成员 payload.group_id 一致
- ALIGN / DISTRIBUTE：`_aligned` / `_distributed` 校验后 bbox
- CAPTURE_CANVAS：`layout.passed==True`
- SAVE：`save.passed==True`

**这是本项目最强的 Recovery 契约。**

### 14.2 `replayed: False` 语义

- 只有 `_reconcile_persisted_state` 或 `PixelDiffInsertionObserver.observe → PRESENT ≥ 0.75` 时才写 `replayed=False`。
- ABSENT ≥ 0.9 → `safe_to_retry=True`（engine 会重试一次）
- 其余 → UNKNOWN + `safe_to_retry=False` → 引擎进入 `PAUSED_RECONCILIATION`。

**行为符合 “可信才跳过、可信才重试” 承诺。**

### 14.3 Fixture 过拟合风险

`_reconcile_persisted_state` 判断 DRAG 已生效需要 `verification.insertion.passed==True`；这是上一次 `_drag_asset` 成功时写入 `editor_elements` 的字段。**如果 GUI 变化后上次 drag 未持久化（因为 evidence.status != VERIFIED），reconciliation 只能靠 pixel diff。** 对本地 fixture，pixel diff 灵敏度足够；真实 BioRender 若素材颜色浅、透明，pixel diff 可能不足，退化为 UNKNOWN → 用户见 `paused_reconciliation`。属于 P1-8。

### 14.4 “Resume 按钮连续两次点击”

- 第一次点击 → `start_resume` → 建 Job A → 后台线程启动。
- 第二次点击 → `_ensure_browser_available` → 因 Job A 未 final → 抛 `BROWSER_BUSY 409`。前端 `showToast(error.message, true)` 弹出错误。
- **不会创建重复 Job 或重复 Action。** 通过。

### 14.5 “Crash 后重复 Save”

Save 是最后一步。若 crash 在 `_observe_autosave` 已完成但 `_write_checkpoint / _persist_element` 未完成 → `document_save` 记录不存在 → resume 会重新 observe。**只是重新观察，无副作用。**

---

## 十五、SQLite 审查

### 15.1 Schema 版本

`schema_migrations` 表。目前迁移到 v4：`_migrate_v2` 依次插入 v2、v3、v4 marker。**幂等（`INSERT OR IGNORE`）。** 现有代码不追踪进一步版本；下次结构变更需要新增 `_migrate_v5`。

### 15.2 关键表与约束

- `figures` PK `id`。
- `figure_entities` / `figure_relations` 复合 PK，`ON DELETE CASCADE`。
- `gui_actions` PK `id` + UNIQUE `(figure_id, sequence)`。**Figure 内 sequence 唯一。**
- `screenshots` autoincrement id + FK。
- `verification_results` autoincrement id + FK。
- `editor_elements` 复合 PK `(figure_id, element_id)`；`ON CONFLICT DO UPDATE`（`upsert_editor_element`）。
- `element_requirements` 复合 PK；类似 upsert。
- `probe_runs` / `probe_actions` 齐备。
- `audit_events` autoincrement id。
- `PRAGMA foreign_keys = ON` 在每次 `connect()` 后重新执行（Python 默认关闭）。

### 15.3 事务与锁

`connect()` 是 `contextmanager`，`try: yield ... connection.commit(); except: rollback(); finally: close()`。**每 with 块一个事务；异常自动回滚。**

`sqlite3.connect(..., timeout=30)`：写锁最多等 30 秒。多线程写入并发时 SQLite 会先阻塞后失败。

**风险：** 
- 每个 `list_*` / `get_*` 都 open+close 一个连接，对小规模 GUI 没问题；大量并发下会积累。P3。
- Live 后台线程与前端轮询同时写不同表（gui_actions vs editor_elements vs audit_events），未使用显式事务边界；每个 execute 独立 commit。**有可能出现 “看到 Action verified 但 element 未 upsert”** 的中间态。但由于 UI 每 1.5 秒轮询，属于短暂视觉现象。P3。

### 15.4 索引

- `idx_gui_actions_figure_status(figure_id, status, sequence)`
- `idx_editor_elements_figure_kind(figure_id, kind)`
- `idx_element_requirements_figure_kind(figure_id, kind)`

**未加**：`figures(updated_at DESC)`（`list_figures ORDER BY updated_at DESC`）、`screenshots(figure_id, id DESC)`（`list_screenshots ORDER BY id DESC`）、`audit_events(figure_id / run_id)`。当前数据量不大，可接受。P3。

### 15.5 敏感数据入库

- `figures.spec_json / requirement_json` 包含用户输入的科研主题、备注、素材名字。**不含账号密码 URL** — `figure_execution_service.start_live` 把 `editor_url` 传给 planner 里的 action_planner，`GuiActionPlanner.compile` 用 `action_type = OPEN_EDITOR, arguments = {"url": editor_url, ...}` 存入 `gui_actions.payload_json`。**编辑器 URL 会入库**（包含 Figure ID）。属于设计上的必要行为（Resume 需要 URL）；`redact_url` 只用于 API 响应。属于 P3 提示：若数据库泄露则 Figure URL 也泄露。

### 15.6 多 Run 隔离

`figure_id` 是所有表的分区键。对**不同** `figure_id` 之间，数据隔离良好。**preset 相同 figure_id 时**（P1-4），数据混淆。

### 15.7 崩溃半写

sqlite3 自身 WAL/journal 支持崩溃安全；本代码未显式 `PRAGMA journal_mode=WAL`，默认 `delete` 模式；崩溃后 Journal 回滚。不损坏数据。可接受。

---

## 十六、AI Generate / 付费安全

### 16.1 三层拦截

1. **前端 Frontend**：无 AI 开关；`index.html` badges 明确 `已禁用`；`enable_biorender_ai: Literal[False] = False` 硬约束 (`schemas/ui.py`)。前端提交 `true` → `422`（`test_frontend_cannot_enable_biorender_ai`）。
2. **Pydantic 输入**：`FORBIDDEN_INPUT` 词表拦截 title/topic/notes/asset name/search term/label 中的 AI/Export/Share/Upgrade/…
3. **Runtime Policy**：`ActionSafetyPolicy.check(action)` 检查 forbidden argument keys 与 forbidden value patterns；`BioRenderPolicyGuard.assert_page_safe(page)` 在每个 action 执行前后扫描页面 AI/Credits 弹窗；`assert_target_allowed(locator)` 检查 locator 文本；`assert_query_allowed(query)` 检查搜索词。

### 16.2 扫描时机

`playwright_live.execute()`：
1. `policy.check(action)` — 参数扫描
2. `_ensure_editor_open` → `_open_editor` 内 `BioRenderUiCalibrator` + `biorender_policy.assert_page_safe(page)`
3. `biorender_policy.assert_page_safe(page)` — action 前
4. handler 内部各种 `assert_target_allowed(...)`
5. handler 完成后再次 `biorender_policy.assert_page_safe(page)`

**每个 Action 前后各一次页面扫描。**

### 16.3 语言覆盖

`AI_CONTROL_PATTERNS` / `AI_CREDIT_PATTERNS` / `SUBSCRIPTION_PATTERNS` / `TEMPLATE_PATTERNS` 均为英文（`模板` 中文覆盖 template）。

**盲点：** 中文界面 `使用 AI 生成`、`AI 生成`、`AI 编辑`、`AI 积分`、`订阅`、`购买`、`升级` 均未覆盖。**若 BioRender 提供中文界面，Policy Guard 大多数模式会漏检。** 属于 P1-11。**建议：** 补齐中文。

### 16.4 Resume 是否重扫

Resume 走 `engine.execute()` → 每 action 都会经过 `policy.check` + `assert_page_safe`。**Resume 也重扫。通过。**

### 16.5 前端是否可绕过后端

- `enable_biorender_ai` 是 `Literal[False]`。前端设 `true` 或去掉字段都会被 422 拒。
- 未确认 (`confirm_manual_login`, `confirmed_disposable`, `confirm_live`) 会被 409 拒。
- 无 “skip policy” 参数。

**通过。**

### 16.6 Policy 命中后行为

- `_run_live_job` 中，engine 将 Figure 设为 `BLOCKED`，Job 变 `blocked`。
- action_result 有 `screenshot_path`, `metadata.evidence_kind='policy_block'`。
- `audit_events` 表插入 `blocked_by_policy`。
- 用户 UI 上 element_summary 显示 `blocked_by_policy`；证据可查。

### 16.7 AI Credits 未消耗 —— 是否可验证

**代码禁止 AI**：通过（源码禁止调用、拒绝 target、拒绝 dialog）。

**本地 Fixture 验证**：`test_biorender_phase0.py::test_ai_control_is_rejected_but_ordinary_generate_text_is_not` + `test_ai_credit_or_subscription_modal_stops_page` 覆盖。

**真实 BioRender 未验证** — 用户必须在 L0 记录 AI Credits 消耗量对照。

---

## 十七、后台任务与并发

（部分已在 §10、§14 覆盖，本节汇总）

- **线程模型**：每 Job 一 `threading.Thread(daemon=True)`。
- **停止能力**：`stop_event: threading.Event`；engine 在动作边界检测。
- **Uvicorn reload**：`reload=False` 硬编码，避免文件变更重启导致 Job 丢失。
- **多 Worker**：CLI 不支持 `--workers`；若用户直接 `uvicorn app.main:app --workers 4`，每 Worker 一个 `ui_service`，Job 分裂。文档未提示。属于 P3。
- **单 Live 并发**：`_ensure_browser_available` 强制串行 live_figure/calibration/manual_login。**明确限制单 Live Run。**
- **不同 Figure 并发**：`start_resume` 的 `_ensure_browser_available` 阻挡任何 live/calibration/manual_login 并发，实际是全局单浏览器。合理。
- **重启后 EXECUTING 卡住**：P0-1。

---

## 十八、测试质量

**沙箱无法运行 pytest（无 fastapi/pydantic 依赖）。** 以下为静态审阅。

### 18.1 文件与函数计数

```
tests/test_api.py                          1 test
tests/test_biorender_browser_integration.py 8 tests (real Chromium)
tests/test_biorender_phase0.py            14 tests (含 2 parametrize)
tests/test_element_capabilities.py         8 tests (含 2 parametrize)
tests/test_planning.py                     6 tests (含 1 parametrize)
tests/test_scientific_guard.py             1 test
tests/test_ui_api.py                      11 tests (含 1 parametrize)
tests/test_ui_page.py                      4 tests
tests/test_workflow.py                     3 tests
                                          --
def-level total                           56
```

parametrize 展开后大约 **70+ 个 test node** — 与实施报告 “70 tests pass” 一致。

### 18.2 类型分布

| 类别 | 数量 | 说明 |
|---|---|---|
| Unit (schemas, safety, planner) | ~15 | test_planning、test_scientific_guard、test_workflow、部分 test_element_capabilities |
| API contract (`TestClient`) | ~15 | test_api、test_ui_api、test_ui_page |
| Local browser fixture (real Chromium) | 8 | test_biorender_browser_integration，全部走本地 `biorender_editor.html` fixture |
| Live semantics (无浏览器) | ~14 | test_biorender_phase0，用 fake page 或 storage 断言 |
| Real BioRender | 0 | 未存在 |

### 18.3 关键弱点

- **Chromium 测试 100% 依赖本地 `tests/fixtures/biorender_editor.html`。** 该 fixture 有 `data-testid` / `data-selected` / `data-connector-type` / `data-group-id` / `data-rotation` / `data-agent-*` 属性，与 `LivePlaywrightOperator` 的 Locators 100% 匹配。**这些属性在真实 BioRender 极大概率不存在。** 因此 “本地 Chromium 通过” **不是** “真实 BioRender 通过”。文档、代码注释已多次声明；测试仍属过拟合。P1-12。
- **无服务重启后 Resume 的 e2e 测试。** P1-1 → P0-1 未被测试覆盖。
- **无多 preset Run 交叉污染测试。** P1-4 未被测试覆盖。
- **无中文 AI 词表测试。** P1-11 未被覆盖。
- `test_multiple_runs_use_distinct_persisted_action_ids` 只覆盖自定义 preset 而非 preset。P1-4 相关。
- `test_resume_does_not_start_duplicate_job` monkey-patches `_run_live_job`，是一个可靠的单元 + 服务级测试。

### 18.4 未有 skip / xfail

grep 中未见 `@pytest.mark.skip` 或 `xfail`。良好。

---

## 十九、文档一致性

- `README.md` 与 `docs/Quick_Start.md`：CLI 命令、`http://127.0.0.1:8000/ui`、`--confirm-live`、94 动作数、9 assets/9 labels/5 connectors/9 groups — 与代码一致。
- `docs/Element_Capability_Matrix.md` 41 条元素合同与 `_element_requirement_rows` 生成量一致。
- `docs/Real_BioRender_Acceptance.md` L0–L8 分级明确 “尚未执行”；未夸大。
- `docs/Graphical_UI_Guide.md` 与前端行为一致：`unknown` 不是成功、`awaiting_confirmation` 不是完成、preset 复用 Fixture、自定义模式限制 15/30、只允许 HTTPS BioRender 域名 URL。
- **文档未提**：GUI 无 “最终确认” 按钮（P2-5）、preset 反复 Live 会覆盖 figure_pd1_v1（P1-4）、服务重启后 EXECUTING 无法 GUI Resume（P0-1）、真实 BioRender 中文界面可能绕过 Policy 词表（P1-11）、z-order 观察依赖 CSS z-index（P1-10）。

---

## 二十、真实 BioRender 分级验收准备度

| 级别 | 目标 | 当前代码支持 | 本地证据 | 真实网站未知点 | 失败后安全状态 | 是否建议执行 |
|---|---|---|---|---|---|---|
| L0 | Calibration | `cmd_calibrate_ui` / `/api/ui/calibrate` 完整 | 有 | 搜索/结果/画布 Locator 若命名变化 | 只保存 calibration profile；无画布修改；`invalid` 状态清晰 | **建议执行** |
| L1 | 只搜索一个素材 | `cmd_live_search_asset` 完整 | 有 (`test_real_chromium_searches_multiple_assets_and_blocks_ai_dialog`) | Search 输入定位；结果面板 DOM | 无画布修改 | 有条件建议：先做 L0 |
| L2 | 拖入一个素材 | `cmd_phase0_search_drag` 完整；有 reconcile 支持 | 有 (`test_real_chromium_reconciliation_suppresses_duplicate_insert`) | canvas 是 DOM 还是 Konva；drag target 是否落在正确 canvas 区域；`_observable_canvas_object_count` 会返回 None | 只影响单个 Figure 的一个素材；可 resume | 有条件建议：需要人工检查 pixel diff 结果 |
| L3 | Move + Resize | 完整；`_move_element` / `_resize_element` | 有 (`test_full_pd1_workflow_uses_real_chromium_fixture`) | `_direct_element_locator` 用的 `data-agent-id` / `data-entity-id` 在真实 UI 不存在；`_selected_or_last_object` 依赖 `data-selected` | Element 状态 UNKNOWN；不会破坏 Figure | **不建议直接执行** — 元素定位极大概率失败 |
| L4 | Label | 完整；label observer 用最近邻 + 精确文字 + 截断 | 有 | 若 BioRender 用 Canvas 渲染文本，`inner_text` 返回空 → 文字不 exact → 全部 UNKNOWN | UNKNOWN；不会重复插入 | 不建议 |
| L5 | Connector | 完整；`connector_observer` 需要 `data-connector-type` | 有 | 真实 BioRender 不太可能暴露 `data-connector-type` | UNKNOWN | 不建议 |
| L6 | Mini Figure | 依赖 L3–L5 | 有本地 | 同上 | UNKNOWN 或 `paused_reconciliation` | 不建议 |
| L7 | Full PD-1 | 依赖所有 | 有本地 94 动作 | 同上 + z-order 通常不可观察 | 大概率 `paused_reconciliation` | 强烈不建议 |
| L8 | Crash + Resume | 完整 | 有本地 | 依赖 checkpoint + 观察，pixel diff 灵敏度 | UNKNOWN；不重放 | 只在 L1–L2 上做小范围演练 |

**结论**：
```
Ready for L0                        → 通过
Conditionally ready for L1          → 通过
Conditionally ready for L2          → 有条件通过（先做 L0 + L1）
Not ready for L3 and above          → 不通过
```

---

## 二十一、问题清单（P0–P3）

### P0：阻止真实验收 / 生产可用性

**P0-1 · 服务重启后 EXECUTING 状态 Figure 在 GUI 无法 Resume**
- 证据：`app/services/figure_execution_service.py:305-311` `can_resume` 集合；`app/workflow/engine.py:82` `set_status(EXECUTING)`。
- 复现：启动 Live → 中途 Ctrl+C 关掉 Web 服务 → 重启 → 打开 UI → 该 Figure `status='executing'` → “继续上次任务” 按钮永久禁用。
- 影响：文档承诺 “持久化 Run 仍可 Resume”，但对最常见的中断场景（服务重启）失败。
- 根因：`can_resume` 未包含 `EXECUTING`；启动阶段无 “stale executing” 侦测。
- 建议修复：`can_resume` 加入 `FigureStatus.EXECUTING.value`；`FigureExecutionService.__init__` 或 `system_status` 首次调用时，把 “无对应 Job 且状态 == EXECUTING” 的 Figure 标记为 `PAUSED_RECONCILIATION` + 写 `audit_events`。
- 是否阻止 L0：否（L0 是 calibration，不产生 EXECUTING），但阻止 L2+ 真实用户流程。

**P0-2 · Preset 模式 Live/Dry 反复运行时 `figure_pd1_v1` 被覆盖 + 老证据滞留**
- 证据：`save_bundle` 只清理 figure_entities/relations/gui_actions/element_requirements，不清 editor_elements/screenshots/verification_results/audit_events；`pd1_request.txt` 固定 → planner 复用 `figure_pd1_v1`。
- 复现：Dry Run PD-1 → 再次 Dry Run PD-1 → `run_summary` 里 `verified_actions/verified_elements` 混合旧数据；甚至可能出现 “Run 中新 Action verified 但 element_requirements 仍显示上一次的 verified/unknown”。
- 影响：用户对 UI 数据产生错误信任；Recovery 逻辑读到旧 `insertion.passed==True` 直接 skip drag — 但当前 Figure 其实是空画布。**属于 “伪造 verified” 风险。**
- 根因：preset 无 uuid；`save_bundle` 清理表不完整。
- 建议修复：
  1. `plan_task(preset)` 内为每次 preset Run 生成 `figure_id = f"figure_pd1_{uuid.uuid4().hex[:12]}"`，然后深拷贝 spec 并覆盖 id；
  2. 或者 `save_bundle` 在检测到 `figure_id` 存在时，同步清 editor_elements / screenshots / verification_results / audit_events；
  3. 或者显式禁止对同一 figure_id 反复 `save_bundle`（当 spec 未变时可返回既存记录）。
- 是否阻止 L0：否；阻止 L2+ 与所有 preset 反复用例。

---

### P1：真实验收高风险

**P1-3 · `_direct_element_locator` / `_observable_canvas_object_count` / `SELECTED_OBJECT_LOCATORS` 强依赖 fixture 属性**
- 证据：`app/operator/playwright_live.py:2294-2308, 2179-2201`；`app/operator/biorender/locators.py::SELECTED_OBJECT_LOCATORS`。
- 影响：真实 BioRender 无 `data-agent-id` / `data-entity-id` / `data-label-id` / `data-relation-id` / `data-agent-kind` / `data-testid*='canvas-element-*'`；元素级重定位与数量核算失败。
- 建议：为真实站点设计 “坐标 + 弱指纹” 的元素身份缓存，允许在 Locator 缺失时用 bbox + tag 组合定位。

**P1-4 · Preset 反复 Live 会覆盖同一 figure_id（另见 P0-2）**

**P1-7 · z-order 观察依赖 CSS z-index**
- 证据：`playwright_live.py:2214-2223`；`observer.py::LayoutQualityObserver`。
- 影响：真实 BioRender 若用 Konva/Canvas，`getComputedStyle().zIndex` 无意义 → 所有 connector 走 `z_order_unknown` → `layout.passed=False` → `_capture_canvas` UNKNOWN。
- 建议：真实站点单独走 “Konva 层次或 SVG DOM order” 观察；无可观察时明确写 `z_order_unknown` 但不阻塞 `layout.passed`。

**P1-8 · Label 观察依赖 DOM 文本**
- 证据：`playwright_live.py::_observable_label_text`；`observer.py::LabelAssociationObserver`。
- 影响：真实 BioRender 若用 Canvas 渲染文本，`inner_text` 与 `data-label-text` 均空 → 每个 Label 都 UNKNOWN。
- 建议：为真实站点接入 OCR 补充通道（可选），或将 Label 观察改为 “Canvas + 局部 pixel signature” 作为 fallback；至少允许 “未观察到但没有多余文字” 时进入 `awaiting_confirmation` 而非 UNKNOWN。

**P1-9 · Connector 类型识别依赖 `data-connector-type`**
- 证据：`playwright_live.py::_observable_connector_route`。
- 影响：真实 BioRender 极可能不暴露；Connector 观察永远 UNKNOWN。文档已声明 “无法可靠区分则 unknown”，属于保守合理，但会阻塞 L5。
- 建议：为真实站点尝试从 `aria-label` 或 tool state 上次点击的按钮推断类型。

**P1-11 · Policy Guard 词表未覆盖中文界面**
- 证据：`app/schemas/ui.py::FORBIDDEN_INPUT`、`app/operator/biorender/policy_guard.py`。
- 影响：BioRender 若切中文，`AI 生成 / 使用积分 / 订阅 / 购买 / 升级 / 模板` 中大部分不匹配。可能误击真实付费入口。
- 建议：补齐中文词表；策略正则加 `re.UNICODE`。

**P1-12 · Chromium 集成测试 100% Fixture 过拟合**
- 证据：`tests/fixtures/biorender_editor.html` 提供 fixture 特有属性；`tests/test_biorender_browser_integration.py` 全部使用 `fixture_url()`。
- 影响：CI 上 “本地 Chromium 通过” 不能代表真实 BioRender 通过。
- 建议：分类命名（`test_local_fixture_*`）；至少加一个 disabled 的 `@pytest.mark.real_biorender` 骨架，以便未来接入。

**P1-13 · Save 状态 10 秒轮询窗口偏短 + 只识别 4 种文案**
- 证据：`playwright_live.py::_observe_autosave` `range(40)` + `wait_for_timeout(250)` = 10 s。
- 影响：真实 BioRender 有 “Saving…” → 短 200 ms 完成后立即消失至无文案；或某语言下文案不同。
- 建议：使用 `page.on('response')` 或 MutationObserver；或延长窗口至 20 秒；或允许识别 Auto-saved timestamp。

---

### P2：产品可用性

**P2-2 · UI 自动映射最近 Run 可能选错**
- 证据：`app.js:237-240`。
- 建议：只在没有 `currentRunId` 且用户主动触发过任何交互后才自动映射；或在 header 明确 “正在查看：最近的 Run <id>”。

**P2-5 · GUI 无 “最终确认” 按钮**
- 证据：`app.js` 无 `/api/ui/runs/{id}/confirm` 调用；`api/ui_routes.py` 无对应 endpoint。
- 影响：`awaiting_confirmation` 状态无法在 GUI 里被终结成 `completed`。用户在 GUI 里做完 Live 后仍需 CLI。
- 建议：新增 POST `/api/ui/runs/{run_id}/confirm` 与前端按钮；仅在 element_requirements 无 `unknown` 时启用。

**P2-6 · `confirm()` 不检查 `unknown` 元素**
- 证据：`app/workflow/engine.py::confirm`。
- 建议：`confirm` 中扫描 `list_element_requirements(figure_id)`，若任意 `status in {"unknown","failed","blocked_by_policy"}` 则拒绝。

**P2-13 · `check-url` 成功文案 UX 弱 + 无成功样式**

**P2-14 · Live 启动前无二次弹窗 + 无 redacted URL 显示**

**P2-15 · 自定义模式 15 素材时无 “可能超出 Planner 稳定验收范围” 提示**

**P2-16 · 保存状态 `Saving...` 期间用户可能重复触发 Live**（`ensure_browser_available` 已保护，但 UI 未即时反馈）

---

### P3：代码质量与维护

- **P3-1** · `FigureExecutionService` 变胖（818 行、31 方法）。
- **P3-2** · `ManagedJob` 无 GC；随时间累积。
- **P3-3** · `main.py` 模块加载即建立数据库连接；单元测试若不覆盖 `app.api.main.app` 可能污染 `runtime/agent.db`（`test_ui_page.py` 会）。
- **P3-4** · 未使用 `PRAGMA journal_mode = WAL`。
- **P3-5** · 缺 `figures(updated_at DESC)` / `screenshots(figure_id, id DESC)` / `audit_events(figure_id, run_id)` 索引。
- **P3-6** · uvicorn 未强制 `workers=1`；CLI 未阻挡多 Worker 场景。
- **P3-7** · `redact_url` 逻辑较弱（只截 path），若日志包含 fragment/query 仍可能泄露 token。
- **P3-8** · Job 失败时 `error_code = type(error).__name__`，无 traceback；UI 无诊断信息。
- **P3-9** · Chromium context 关闭仅依赖 finally；SystemExit 场景可能残留 profile 锁。
- **P3-10** · `Delete` 素材 / `Delete` 关系无 `confirm()`。
- **P3-11** · 中文注释与英文注释混用；一致性可优化。

---

## 二十二、建议修复顺序

1. **P0-2**：为 preset 生成 uuid `figure_id`，或让 `save_bundle` 清空老 `editor_elements/screenshots/verification_results`。
2. **P0-1**：`can_resume` 加入 `EXECUTING`；Startup 检测 stale executing 并置 `PAUSED_RECONCILIATION`。
3. **P1-11**：Policy 词表补齐中文。
4. **P1-13**：Save 观察扩窗与 MutationObserver。
5. **P1-3, P1-7, P1-8, P1-9**：为真实 BioRender 设计 Locator/Observer fallback（可能需要在 L0/L1 结束后按真实截图逐一决定）。
6. **P2-5, P2-6**：GUI Confirm 按钮 + 拒绝含 unknown 的 confirm。
7. **P2-2, P2-14**：UI 状态与二次确认。
8. **P3**：低优先级重构。

---

## 二十三、审计中发现并已尝试的修复

本轮未做任何代码修改；本报告仅审计。

---

## 二十四、下一步真实验收步骤

1. 在完成 P0-2、P0-1、P1-11 修复后，用可丢弃 Figure 执行 **L0（`calibrate-ui`）** 并保存 calibration profile + 截图。
2. 通过后执行 **L1（`live-search-asset`）**，重点核对：搜索输入命中、结果 DOM 属性是否包含 `data-testid`，AI 按钮是否被 policy 记录。若 “搜索结果 DOM 属性缺失” 现象出现，先补 fallback 再上 L2。
3. **L2 前** 需要人工比对 fixture 与真实 BioRender 的 `data-*` 属性差异，估算 P1-3 修复量。
4. **L3+** 在 P1-3、P1-7、P1-8、P1-9 修复完之前，不建议执行；若必须执行，只做单素材 Move/Resize 并接受高比例 UNKNOWN。
5. 每次执行后，把 L0 模板（`docs/Real_BioRender_Acceptance.md::验收记录模板`）中的 `Level / Date / operator / Command / Observed result / Evidence paths / Policy findings / AI Credits before / after / Pass|Fail|Unknown / Notes` 记入项目，作为真实证据。

---

## 二十五、最终结论

```
代码实现状态：          通过
本地测试状态：          未验证（沙箱受限）；静态审阅确认覆盖合理
本地 Chromium 状态：    未验证（同上）；Fixture 存在但真实 BioRender 差异大
真实 BioRender 状态：   未验证
AI Generate 安全状态：  有条件通过（英文覆盖良好，中文 P1-11）
Recovery 可信度：       有条件通过（本地强，真实 P1-3/8）
GUI 可用性：            有条件通过（P0-1、P2-5、P2-6 未修复前不适合非命令行用户全流程）
进入 L0 的准备度：      通过
是否建议 Push：         通过（已 Push，`origin/feature/graphical-ui` 与 HEAD 同步）
是否建议创建 PR：       有条件通过（先修 P0-1、P0-2）
是否建议合并 main：     不通过
```

当前项目已达到本地工程验收标准，并具备进入真实 BioRender L0 校准测试的条件；但在完成 preset 多 Run 隔离、EXECUTING 状态恢复、真实 Locator/Canvas/Label/Connector 语义识别、以及中文 Policy 词表补齐之前，不建议合并到 `main`，也不建议直接推进到真实 BioRender 的 L2+ 操作。
