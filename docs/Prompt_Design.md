# BioRender GUI Agent 当前 Prompt 设计手册

## 1. Prompt 的作用

当前项目中的画图 Prompt 只负责描述科研内容，不负责描述 GUI 操作。它告诉 Planner：

- 需要画哪些元素；
- 元素之间的先后关系；
- 是否使用内置 PD-1/PD-L1 双栏机制图。

Prompt 中不应该出现：

- 点击、鼠标、拖拽或坐标；
- BioRender 搜索框操作；
- 素材候选序号；
- Playwright Locator；
- AI Generate、AI Credits 或购买操作。

搜索关键词、画布位置和 GUI 动作由项目内部 Planner 生成。

## 2. 当前实际支持范围

当前 Planner 是确定性 MVP，不是任意自然语言科研绘图模型。它只支持两类输入。

### 2.1 显式线性流程

格式：

```text
元素 A → 元素 B → 元素 C
```

也可以使用 ASCII 箭头：

```text
元素 A -> 元素 B -> 元素 C
```

要求：

- 必须包含 2–15 个节点；
- 每个箭头只表示从左到右的 Flow；
- 每个节点会创建一个素材和一个同名 Label；
- 相邻节点之间会创建普通 Arrow；
- 当前不支持在线性 Prompt 中声明 Line、Inhibition 或双向关系；
- 当前不支持分支、汇合、循环或嵌套分组。

示例：

```text
Sample → Centrifugation → Supernatant
```

```text
T cell → Tumor cell → Antibody
```

### 2.2 内置 PD-1/PD-L1 双栏机制图

当 Prompt 同时包含 `PD-1` 和 `PD-L1` 时，当前 Planner 会使用固定的 PD-1/PD-L1
FigureSpec，而不是从自由文本中重新推理机制。因此只有用户确实需要该固定机制时才应使用。

推荐使用标准 Prompt：

```text
制作一张双栏机制图。左侧表示未经治疗时，肿瘤细胞上的 PD-L1 与 T 细胞上的 PD-1
结合，从而抑制 T 细胞。右侧表示加入抗 PD-1 抗体后，PD-1/PD-L1 相互作用被阻断，
T 细胞恢复对肿瘤细胞的杀伤。
```

该 Prompt 会生成固定内容：

- 9 个素材；
- 9 个 Label；
- 5 个 Connector；
- 未治疗与 anti-PD-1 treatment 两个 Panel；
- Line、Arrow 和 T-bar/Inhibition 关系；
- 共 94 个 GUI 动作。

仅仅在其他机制中偶然提到 PD-1 和 PD-L1，也会触发该固定 Fixture。因此 GPT 转换器必须
确认用户要求的确是“PD-1/PD-L1 抑制及 anti-PD-1 阻断”机制，不能只做关键词匹配。

## 3. 自然语言转换流程

目前自然语言到受支持 Prompt 的转换不在 CLI 内自动调用 GPT。推荐流程是：

```text
用户自然语言
→ GPT Prompt Normalizer
→ 受支持的线性 Prompt 或标准 PD-1 Prompt
→ BioRender GUI Agent CLI
→ FigureSpec、Layout 和 GUI Actions
```

如果自然语言不能无损转换成这两种格式，GPT 应明确返回不支持，而不是发明关系。

## 4. 可直接交给 GPT 的转换指令

复制下面整段指令，将最后的 `{{用户自然语言需求}}` 替换为实际需求：

```text
你是 BioRender GUI Agent 的 Prompt Normalizer。

你的任务是把用户的自然语言科研绘图需求，转换成当前项目实际支持的画图 Prompt。

当前只支持两种输出：

一、显式线性流程

格式：
元素 A → 元素 B → 元素 C

规则：
- 节点数量必须为 2–15 个。
- 每个箭头只表示从左到右的普通 Flow。
- 只保留用户明确提供的核心实体或步骤。
- 不得添加用户没有说明的中间步骤或科研关系。
- 分支、汇合、循环、双向关系、抑制关系和多 Panel 不能伪装成普通线性流程。

二、固定 PD-1/PD-L1 双栏机制图

只有用户明确要求以下完整机制时才使用：
- 未治疗时 PD-1 与 PD-L1 结合；
- T cell 被抑制；
- Anti-PD-1 antibody 阻断 PD-1/PD-L1；
- T cell 恢复对 Tumor cell 的杀伤。

此时必须输出：
制作一张双栏机制图。左侧表示未经治疗时，肿瘤细胞上的 PD-L1 与 T 细胞上的 PD-1
结合，从而抑制 T 细胞。右侧表示加入抗 PD-1 抗体后，PD-1/PD-L1 相互作用被阻断，
T 细胞恢复对肿瘤细胞的杀伤。

通用规则：
- 只输出转换结果，不解释。
- 不输出 Markdown 代码块。
- 不写点击、拖拽、鼠标、坐标或 BioRender 操作。
- 不写搜索关键词或素材候选序号。
- 不调用或建议 BioRender AI Generate。
- 不提 AI Credits。
- 不确定时不得猜测。
- 如果无法无损转换，输出：
  UNSUPPORTED：当前需求无法转换成 2–15 节点的线性流程或固定 PD-1/PD-L1 双栏图。

用户自然语言需求：
{{用户自然语言需求}}
```

## 5. 转换示例

### 示例一：实验流程

用户输入：

```text
画一个血液样本经过离心后获得血浆，再进行 ELISA 检测的实验流程。
```

转换结果：

```text
Blood sample → Centrifugation → Plasma → ELISA
```

### 示例二：简单作用流程

用户输入：

```text
画出 T 细胞作用于肿瘤细胞，随后加入抗体的流程。
```

转换结果：

```text
T cell → Tumor cell → Antibody
```

这里的箭头只表示普通 Flow。它不会自动表达抑制、结合或阻断语义。

### 示例三：PD-1/PD-L1 机制

用户输入：

```text
对比没有治疗和使用 anti-PD-1 后，PD-1/PD-L1 对 T 细胞杀伤肿瘤的影响。
```

转换结果应为第 2.2 节中的标准 PD-1 Prompt。

### 示例四：不支持的分支图

用户输入：

```text
画一张 MAPK 信号通路，RAS 同时激活 RAF 和 PI3K，并包含反馈抑制环。
```

转换结果：

```text
UNSUPPORTED：当前需求无法转换成 2–15 节点的线性流程或固定 PD-1/PD-L1 双栏图。
```

不能把它错误压缩成 `RAS → RAF → PI3K`，因为这会改变原始科学关系。

## 6. Prompt 质量检查

送入 CLI 前检查：

- [ ] 是 2–15 节点的明确线性流程，或标准 PD-1/PD-L1 Prompt；
- [ ] 没有分支、循环或隐含关系；
- [ ] 没有加入用户未提供的科学内容；
- [ ] 节点名称简短，适合作为素材搜索概念和 Label；
- [ ] 没有 GUI 操作、坐标或候选序号；
- [ ] 没有 BioRender AI Generate 或 AI Credits 指令；
- [ ] PD-1 Prompt 与固定 Fixture 的科学叙事完全一致。

## 7. 先规划再运行

建议先只生成和检查计划，不操作 BioRender：

```powershell
python -m app.cli plan "Sample → Centrifugation → Supernatant" `
  --output C:\bioagent\output.json
```

确认计划后再运行 Live Mode：

```powershell
python -m app.cli live-figure `
  --request "Sample → Centrifugation → Supernatant" `
  --editor-url $BlankFigureUrl `
  --confirm-live
```

如果 GPT 输出 `UNSUPPORTED`，不要把该文本直接传给 CLI。应简化需求、手工提供明确线性流程，
或等待未来 Planner 支持更复杂 FigureSpec。

## 8. 设计原则

当前 Prompt 设计遵守三个原则：

1. **科研内容与 GUI 分离**：Prompt 不描述鼠标操作。
2. **显式关系优先**：Planner 只复制明确箭头，不猜测科学关系。
3. **不支持时安全失败**：复杂需求返回 `UNSUPPORTED`，不为了完成绘图而编造内容。

工作原理见 [How_It_Works.md](How_It_Works.md)，运行环境见
[Environment_Setup.md](Environment_Setup.md)。
