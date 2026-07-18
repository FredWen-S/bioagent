# BioRender GUI Agent 简单原理手册

## 1. 项目做什么

BioRender GUI Agent 接收一个科研绘图 Prompt，然后模拟真人使用 BioRender 普通编辑器：

```text
理解绘图需求
→ 规划素材与布局
→ 搜索普通 Icons / Assets
→ 拖入画布
→ 移动和缩放
→ 创建文本框
→ 添加连接线
→ 检查画布结果
→ 等待自动保存
```

项目不会调用 BioRender 自带的 AI 生成功能，也不会主动使用 AI Credits。

## 2. Prompt 如何变成图

Agent 首先把 Prompt 转换成 FigureSpec，确定：

- 图中需要哪些科研元素；
- 每个元素显示什么文字；
- 元素之间有什么关系；
- 元素放在哪个区域；
- 应该使用 Arrow、Line 还是 Inhibition Connector。

目前主要支持内置 PD-1/PD-L1 机制图，以及结构明确的流程，例如：

```text
T cell → Tumor cell → Antibody
```

它还不是能够理解任意复杂科研描述的通用绘图系统。

## 3. 如何搜索普通素材

Agent 在 BioRender 侧边栏的普通素材搜索框中输入关键词，例如：

```text
T cell
Tumor cell
PD-1 receptor
Anti-PD-1 antibody
```

如果第一个关键词没有合适结果，会尝试普通同义词。候选必须具有普通素材和可拖拽证据。
AI、模板、Upgrade、Subscribe、Purchase 等入口不会被选中。

候选身份使用文字、可访问名称、DOM 特征和缩略图特征组成的弱指纹。搜索结果中的
“第一个”“candidate_1”不会被用作长期恢复身份。

## 4. 如何操作画布

选中普通素材后，Agent 使用 Playwright 发送真实的鼠标和键盘事件：

- Drag：把素材拖入画布；
- Move：移动到计划位置；
- Resize：调整大小；
- Rotate：只有界面提供可观察旋转手柄时才执行；
- Add Text：创建文本框并输入 Label；
- Connect：创建 Arrow、Line 或 Inhibition；
- Group、Align、Distribute：组织多个画布元素。

这些动作模拟普通编辑操作，不使用 Generate Figure 或 Create with AI。

## 5. 为什么不能只相信“点击成功”

Playwright 没有报错，只能说明浏览器收到了操作，不能证明图真的画好了。因此每个关键动作
都必须继续观察：

- 画布元素数量是否合理变化；
- 元素实际位置和尺寸是否正确；
- Label 文字是否完整，并靠近正确素材；
- Connector 类型、方向和端点是否正确；
- 是否存在重叠、越界或严重碰撞；
- 是否出现 `Saved` 或 `All changes saved`。

只有观察证据满足规则，状态才是 `verified`。无法确定时进入 `unknown` 并暂停。

## 6. 如何恢复中断任务

每个动作执行前会保存 Checkpoint，元素状态和证据保存在 SQLite 中。中断恢复时不会直接
重放鼠标动作，而是：

```text
读取 Checkpoint
→ 重新观察当前画布
→ 已完成：补记 verified，不重放
→ 部分完成：执行最小修复
→ 确认不存在：重新创建
→ 无法判断：进入 unknown
```

这样可以避免重复插入素材、Label、Connector 或 Group。

## 7. AI 与付费安全边界

以下功能永久禁止：

- BioRender AI Generate；
- Create with AI、AI Edit、AI Assistant；
- AI Credits 确认；
- Upgrade、Subscribe、Purchase；
- 自动导出、下载、分享或发布；
- 自动输入账号、密码或 MFA。

AI 控件可以存在于页面中，但会被记录和围栏。若动作目标或弹窗命中禁止策略，Agent 会
停止、截图、写入审计记录，并返回 `blocked_by_policy`。

## 8. 当前验证状态

- 本地兼容编辑器：已通过真实 Chromium、鼠标和键盘回归；
- 完整 PD-1 本地流程：94 个动作，覆盖 9 个素材、9 个 Label 和 5 个 Connector；
- 真实 BioRender：尚需用户人工登录后按照 L0–L8 分级验收。

真实网站验收步骤见 [Real_BioRender_Acceptance.md](Real_BioRender_Acceptance.md)。
