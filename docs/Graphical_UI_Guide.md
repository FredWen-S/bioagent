# BioRender GUI Agent 图形化界面指南

## 1. 界面用途

图形化界面是现有 BioRender GUI Agent Workflow 的可视化入口，适合不熟悉 Python、
PowerShell 或 JSON 的食品科学、生物学与其他科研用户。用户在浏览器中填写任务，后端仍然
使用同一套 Planner、Layout、Operator、Observer、Verification、Recovery 和 SQLite。

它不是新的绘图画布，也没有复制一套 BioRender 自动化逻辑。CLI 与图形化界面共享
`FigureExecutionService`，最终都进入现有 `WorkflowEngine`。

> 本地兼容编辑器已验证；真实 BioRender 线上验收尚未完成。界面可用于分级验收，但不能
> 将本地结果描述为真实 BioRender 完整绘图成功。

## 2. 适合哪些用户

- 希望通过表单运行 PD-1 / PD-L1 预设的用户；
- 需要配置少量素材和连接关系的科研用户；
- 希望直观看到进度、元素状态和截图证据的用户；
- 不希望直接操作 CLI，但可以完成人工 BioRender 登录的用户。

第一版自定义模式不是任意自然语言科研绘图器。它要求用户明确填写素材、普通素材搜索词、
标签和关系，并使用现有的自动线性布局。

## 3. 启动

先按照 [Environment_Setup.md](Environment_Setup.md) 安装 Python、项目依赖、Playwright 和
Chromium。然后在 PowerShell 中运行：

```powershell
cd C:\bioagent
.\.venv\Scripts\Activate.ps1
python -m app.cli web-ui
```

打开：

```text
http://127.0.0.1:8000/ui
```

也可以使用 Windows 启动脚本：

```powershell
cd C:\bioagent
.\scripts\start_web_ui.ps1
```

服务只监听本机回环地址 `127.0.0.1`，不会自动公开到局域网或互联网。按 `Ctrl+C` 停止。
启动脚本不会安装依赖，也不会修改系统环境。

## 4. 页面结构

页面包含以下区域：

- 环境状态：后端、数据库、登录、当前任务与验收范围；
- 新建绘图任务：PD-1 预设或有限自定义图形；
- BioRender Figure 设置：URL 检查、人工登录和校准；
- 运行模式：安全预演或经确认的真实执行；
- 执行进度：将内部动作汇总成十个用户步骤；
- 元素状态：显示等待、操作中、已确认、未知、失败或安全阻止；
- 截图与证据：只显示项目真实生成且位于允许目录的图片；
- 最终结果：Run ID、元素统计、保存观察和 Workflow 状态。

页面不会把 `unknown` 显示成成功，也不会把 `awaiting_confirmation` 翻译成“完全完成”。

## 5. 第一次安全预演

1. 保持“使用预设图形”和“PD-1 / PD-L1 机制图”。
2. 保持“安全预演”。
3. 可先单击“检查绘图方案”。
4. 单击“开始安全预演”。
5. 查看进度、元素表和最终结果。

安全预演会创建计划、执行 Dry Run 并写入 SQLite，但不会打开或修改 BioRender 页面。
正常自动终点是 `awaiting_confirmation`：计划动作已走完，仍等待用户理解和确认结果。

## 6. 人工登录 BioRender

单击“打开登录窗口”，然后只在新打开的 BioRender 官方浏览器窗口中输入账号、密码和
MFA。界面没有账号密码输入框，后端也不会接收、记录或显示密码、Cookie、Token。

登录完成后返回控制台，单击“我已完成登录”。持久浏览器 Profile 保存在本机；不要将
Profile 目录提交到公共仓库。如果登录失效，重新完成人工登录。

## 7. 使用 PD-1 预设

PD-1 / PD-L1 预设复用项目已有完整 Fixture，覆盖普通素材、Label、Connector、Group、
Align、Distribute、Z-order 和保存状态观察。它适合验证系统能力，不代表真实 BioRender
线上已经通过验收。

安全预演无需 Figure URL。真实执行前应先从 L0/L1 小范围验收开始，参见
[Real_BioRender_Acceptance.md](Real_BioRender_Acceptance.md)，不要第一次就直接对完整预设
执行 L7。

## 8. 创建自定义简单图形

选择“自定义简单图形”，填写：

- 图形名称与研究主题；
- 至少两个素材；
- 每个素材的显示名称、普通 BioRender 搜索词、可选备用搜索词和标签；
- 至少一条关系，类型为普通连线、箭头或抑制线；
- 自动布局。

标签留空时，当前 FigureSpec 约束会使用素材显示名称。第一版最多支持 15 个素材和
30 条关系；每个素材必须参与至少一条关系。备用搜索词用英文逗号分隔。

当前自定义模式只使用现有线性自动布局。左右布局和上下布局没有在界面中提供，因为现有
通用 Planner 尚未对这两项形成稳定的自定义执行合同。

## 9. 校准与真实执行

在 BioRender 中人工准备一个可丢弃的空白 Figure，复制完整的 HTTPS 编辑器 URL。控制台
只接受 BioRender 官方域名，不接受 HTTP、其他站点或包含账号密码的 URL。

1. 将 URL 粘贴到“空白 Figure URL”。
2. 单击“检查 URL”。此步骤只验证格式，不证明页面可编辑。
3. 选择“真实执行”。
4. 勾选“我已确认使用可丢弃的空白 Figure”。
5. 先单击“仅校准界面”。校准会打开页面并检查普通搜索区、结果区和画布。
6. 校准证据正常后，才单击“开始绘制”。

确认框意味着允许修改当前空白 Figure，并不关闭后端安全检查。后端仍会执行现有 Policy
Guard；前端不能传入 AI 开关，也不能绕过确认。

## 10. 查看进度

页面把内部动作汇总为检查任务、校准、策略检查、搜索、放置、标签、连接、布局、验证和
人工确认十个阶段。每个阶段只使用以下用户状态：等待、进行中、已完成、需要检查、已阻止
和失败。

“查看运行详情”包含受限的运行摘要，不包含 Traceback、Cookie、Token 或完整敏感 URL。
页面每约 1.5 秒查询任务进度。刷新页面后，持久化的 Figure、动作和元素状态仍可从 SQLite
读取；当前进程内的后台线程不会因为页面刷新而重新启动。

## 11. 处理 `unknown`

`unknown` 表示 Observer 不能可靠判断 GUI 结果。它不是成功，也不应该通过重复点击“开始
绘制”解决。

1. 查看元素说明和对应截图；
2. 检查 BioRender 是否出现遮挡、弹窗或 UI 变化；
3. 保留当前 Figure，不要人工重复插入同一元素；
4. 在确认页面安全后，使用“继续上次任务”；
5. 如果仍然无法判断，停止并人工接管。

Recovery 会先 reconciliation：确认元素已存在则跳过，部分满足则做最小修复，可信地不存在
才创建；仍有歧义时继续暂停。

## 12. 安全停止、继续与重新验证

“暂停或安全停止”设置停止请求，Workflow 在当前 GUI 动作完成后的动作边界保存状态并
暂停，不会粗暴杀死进程或绕过 SQLite Checkpoint。

“继续上次任务”只在存在可恢复 Run、当前没有其他浏览器任务、已选择真实执行并重新确认
空白 Figure 后可用。相同 Run 不能被重复启动。

“重新验证”只读取当前 SQLite 中的元素、布局与保存证据，不打开或修改 BioRender。它不会
将本地兼容编辑器证据转换为真实 BioRender 线上验收结果。

## 13. 截图与证据

页面只预览数据库中登记且位于以下允许目录内的 PNG/JPEG/WebP：

```text
C:\bioagent\runtime\screenshots\
C:\bioagent\output\playwright\calibration\
C:\bioagent\output\playwright\figures\
C:\bioagent\output\playwright\probes\
```

文件不存在时显示“暂无截图”。证据 API 不接受任意路径，不能借此读取其他文件。截图可能
包含 Figure 内容，请按科研数据和账号安全要求保存，不要直接上传到公共仓库。

## 14. 为什么 BioRender AI 始终禁用

项目目标是模拟真人使用普通素材编辑器，而不是调用 BioRender AI Generate。界面没有
BioRender AI、Create with AI、AI Edit、AI Credits 或 Template Generate 入口。后端会在
动作前后检查相关按钮、菜单、弹窗以及 Upgrade、Subscribe、Purchase 等上下文。

命中策略时流程为：停止动作、截图、写入审计事件、设置 `blocked_by_policy`、保存
Checkpoint、等待人工检查。普通素材不足时也不会降级到 BioRender AI。

系统同样不自动执行 Export、Download、Share、Publish、购买或升级。普通 Figure 自动保存
状态可以被观察，但导出和分享不属于本界面能力。

## 15. 当前验收状态和限制

- 本地 UI 和本地兼容编辑器可自动测试；
- 真实 BioRender 完整线上验收仍需用户人工登录后执行；
- Planner 支持 PD-1 预设和界面明确填写的有限关系图，不支持任意自由文本；
- 自定义图形目前只提供自动线性布局；
- BioRender UI 更新可能造成校准或 Locator 失败；
- 旋转只在普通旋转手柄可观察时执行；
- 科研正确性、素材语义和最终视觉表达仍需人工审核；
- 不支持自动登录、导出、分享、购买、升级或公开部署控制台。

## 16. 常见问题

**页面打不开：** 确认终端中的服务仍在运行，并访问
`http://127.0.0.1:8000/ui`，不要使用 `https://`。

**端口 8000 已占用：**

```powershell
python -m app.cli web-ui --port 8001
```

然后访问 `http://127.0.0.1:8001/ui`。

**“开始绘制”不可用：** 需要选择真实执行、填写有效 Figure URL，并主动勾选空白 Figure
确认。任务运行中也会禁用重复启动。

**为什么校准按钮不可用：** 校准会打开真实页面，因此也要求选择真实执行、填写 URL 和
完成确认。

**URL 格式有效但校准失败：** URL 检查只验证协议和域名；登录失效、页面不是编辑器、UI
发生变化或弹窗遮挡仍会导致校准失败。查看校准截图并安全停止。

**出现 `blocked_by_policy`：** 不要绕过策略。关闭 AI、credits、付费或分享弹窗，保存证据，
确认回到普通素材编辑器后再决定是否恢复。

**页面刷新后看不到正在运行的线程：** 状态接口会读取当前服务进程中的任务和 SQLite。
如果 Web 服务本身重启，原后台线程不会重建，但持久化 Run 仍可在重新确认后 Resume。

**能否把服务开放给同事：** 第一版只设计为单机回环控制台。不要通过端口转发或反向代理
公开；账号 Profile、截图和科研 Figure 都应留在受控设备中。
