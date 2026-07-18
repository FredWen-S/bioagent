# PD-1 Figure Element Capability Matrix

本文档是当前 PD-1/PD-L1 Figure 的元素级执行合同。它把 FigureSpec、Layout、GUI Action、
Observer、Verification 与 Recovery 对齐到同一个 `logical_element_id`，用于开发、测试、
故障定位和真实 BioRender 验收。

状态口径：下表中的“本地已验证”表示 2026-07-17 在本地兼容编辑器上使用真实 Chromium、
真实鼠标和键盘事件通过；**不表示真实 BioRender 网站已经验收**。真实 BioRender 状态均为
“待 L0–L8 人工登录验收”。

## 1. 素材元素（9）

所有素材共用以下闭环：`Search → Candidate Filter → Select → Insert → Observe → Move →
Resize → Verify → Persist → Resume`。PD-1 Fixture 没有旋转需求；Rotate 能力另有 Chromium
回归，真实 UI 不暴露普通旋转手柄时必须安全停止。

| Element ID | Scientific Role | Element Type | Search Term / Fallbacks | Required Actions | Observation Method | Verification Rule | Recovery Rule | Current Status |
|---|---|---|---|---|---|---|---|---|
| `t_cell_before` | 未治疗组 T cell | Asset / cell | `T cell`; `T lymphocyte`; `immune cell`; `CD8 T cell` | Search, Filter, Select, Insert, Move, Resize | DOM 数量增量、候选弱指纹、局部截图差异、实际 bbox | 普通可拖拽素材；数量 +1；中心约 `(0.15, 0.35)`；尺寸约 `0.14 × 0.14` | 用 `figure_element_id`、DOM/缩略图指纹、bbox、邻近 Label 协调；歧义则 `unknown` | 本地已验证；真实站点待验收 |
| `tumor_cell_before` | 未治疗组 Tumor cell | Asset / cell | `Tumor cell`; `cancer cell`; `malignant cell`; `cell` | 同上 | 同上 | 中心约 `(0.37, 0.35)`；尺寸约 `0.14 × 0.14` | 同上 | 本地已验证；真实站点待验收 |
| `pd1_before` | 未治疗组 PD-1 receptor | Asset / protein | `PD-1 receptor`; `PD-1`; `cell surface receptor`; `receptor` | 同上 | 同上 | 中心约 `(0.15, 0.69)`；尺寸约 `0.14 × 0.14` | 同上 | 本地已验证；真实站点待验收 |
| `pdl1_before` | 未治疗组 PD-L1 ligand | Asset / protein | `PD-L1 ligand`; `PD-L1`; `membrane ligand`; `protein` | 同上 | 同上 | 中心约 `(0.37, 0.69)`；尺寸约 `0.14 × 0.14` | 同上 | 本地已验证；真实站点待验收 |
| `t_cell_after` | 治疗组恢复功能的 T cell | Asset / cell | `T cell`; `T lymphocyte`; `immune cell`; `CD8 T cell` | 同上 | 同上 | 中心约 `(0.63, 0.2933)`；尺寸约 `0.14 × 0.14` | 同上；不得因与左栏同名而匹配到错误实例 | 本地已验证；真实站点待验收 |
| `tumor_cell_after` | 治疗组 Tumor cell | Asset / cell | `Tumor cell`; `cancer cell`; `malignant cell`; `cell` | 同上 | 同上 | 中心约 `(0.85, 0.2933)`；尺寸约 `0.14 × 0.14` | 同上；需结合右栏位置与 Label | 本地已验证；真实站点待验收 |
| `pd1_after` | 治疗组 PD-1 receptor | Asset / protein | `PD-1 receptor`; `PD-1`; `cell surface receptor`; `receptor` | 同上 | 同上 | 中心约 `(0.63, 0.52)`；尺寸约 `0.14 × 0.14` | 同上；不得与左栏 PD-1 混淆 | 本地已验证；真实站点待验收 |
| `pdl1_after` | 治疗组 PD-L1 ligand | Asset / protein | `PD-L1 ligand`; `PD-L1`; `membrane ligand`; `protein` | 同上 | 同上 | 中心约 `(0.85, 0.52)`；尺寸约 `0.14 × 0.14` | 同上；不得与左栏 PD-L1 混淆 | 本地已验证；真实站点待验收 |
| `antibody_after` | 阻断 PD-1 的 anti-PD-1 antibody | Asset / antibody | `Anti-PD-1 antibody`; `anti-PD-1`; `monoclonal antibody`; `antibody`; `Y antibody` | 同上 | 同上 | 中心约 `(0.63, 0.7467)`；尺寸约 `0.14 × 0.14` | 同上 | 本地已验证；真实站点待验收 |

每个素材运行记录必须包含：`logical_element_id`、非序号型 `figure_element_id`、
`search_query`、候选 `dom_fingerprint`、可选 `thumbnail_fingerprint`、`expected_bbox`、
`observed_bbox`、`observation_source`、`observation_confidence`、`verification` 和
`evidence_refs`。`candidate_1`、`first_result` 或 `result_index_0` 只可作为瞬时排序信息，
不得作为恢复身份。

## 2. Label 元素（9）

所有 Label 均执行 `Create → Input → Confirm → Move → Resize → Observe → Associate →
Verify → Persist → Resume`。验证通道是 DOM/Accessibility 的精确文字、实际 bbox、目标素材
邻近关系与文字内容盒截断检查；当前未声称使用 OCR。将来 OCR 只能作为补充通道。

| Element ID | Scientific Role | Element Type | Expected Text | Target Element | Required Actions | Observation / Verification Rule | Recovery Rule | Current Status |
|---|---|---|---|---|---|---|---|---|
| `label_t_cell_before` | 左栏 T cell 标注 | Label | `T cell` | `t_cell_before` | Create, Move, Resize | 文字精确；最近素材为目标；不截断、不越界 | 文字 + 目标邻近 + bbox 协调；不因右栏同名而去重 | 本地已验证；真实站点待验收 |
| `label_tumor_cell_before` | 左栏 Tumor cell 标注 | Label | `Tumor cell` | `tumor_cell_before` | 同上 | 同上 | 同上 | 本地已验证；真实站点待验收 |
| `label_pd1_before` | 左栏 PD-1 标注 | Label | `PD-1` | `pd1_before` | 同上 | 同上 | 同上 | 本地已验证；真实站点待验收 |
| `label_pdl1_before` | 左栏 PD-L1 标注 | Label | `PD-L1` | `pdl1_before` | 同上 | 同上 | 同上 | 本地已验证；真实站点待验收 |
| `label_t_cell_after` | 右栏 T cell 标注 | Label | `T cell` | `t_cell_after` | 同上 | 文字相同但必须最近于右栏目标 | 不能只按文字命中左栏 Label | 本地已验证；真实站点待验收 |
| `label_tumor_cell_after` | 右栏 Tumor cell 标注 | Label | `Tumor cell` | `tumor_cell_after` | 同上 | 同上 | 同上 | 本地已验证；真实站点待验收 |
| `label_pd1_after` | 右栏 PD-1 标注 | Label | `PD-1` | `pd1_after` | 同上 | 同上 | 同上 | 本地已验证；真实站点待验收 |
| `label_pdl1_after` | 右栏 PD-L1 标注 | Label | `PD-L1` | `pdl1_after` | 同上 | 同上 | 同上 | 本地已验证；真实站点待验收 |
| `label_antibody_after` | anti-PD-1 标注 | Label | `Anti-PD-1` | `antibody_after` | 同上 | 文字精确；最近素材为 antibody；不截断、不越界 | 文字 + 目标邻近 + bbox 协调 | 本地已验证；真实站点待验收 |

运行记录包含 `logical_label_id`、`figure_element_id`、`target_element_id`、
`expected_text`、`observed_text`、expected/observed bbox、`association_confidence`、
`verification` 与证据路径。若页面只出现相同文字但目标关联错误，结果必须是 `unknown`。

## 3. Connector 元素（5）

连接器验证不以“鼠标已松开”为成功。必须观察到连接器对象，提取实际类型和 DOM 路径，
检查起点/终点、方向、无关素材碰撞及 Label 穿越。细线的截图像素差可能很弱；此时只有
DOM 类型、路径、端点和数量增量共同通过才可验证，前后截图仍必须保存。

| Element ID | Scientific Role | Type | Source → Target | Direction / Anchors / Route | Observation Method | Verification Rule | Recovery Rule | Current Status |
|---|---|---|---|---|---|---|---|---|
| `pd1_pdl1_binding_before` | 未治疗时 PD-1/PD-L1 结合 | `line` | `pd1_before` → `pdl1_before` | source-to-target / edge anchors / straight | DOM type + route + bbox + count + screenshots | 两端靠近正确元素；普通 Line；无严重 Label/无关素材碰撞 | relation ID + source/target/type/route 去重 | 本地已验证；真实站点待验收 |
| `t_cell_inhibition_before` | 肿瘤对 T cell 的抑制 | `t_bar` | `tumor_cell_before` → `t_cell_before` | source-to-target / edge anchors / straight | 同上 | 终点必须为 T-bar，不能画成 Arrow | 同上 | 本地已验证；真实站点待验收 |
| `antibody_blocks_pd1_after` | antibody 阻断 PD-1 | `t_bar` | `antibody_after` → `pd1_after` | source-to-target / right-side anchors / straight | 同上 | 阻断关系必须为 T-bar | 同上 | 本地已验证；真实站点待验收 |
| `pd1_pdl1_blocked_after` | 治疗后相互作用被阻断 | `t_bar` | `pd1_after` → `pdl1_after` | source-to-target / edge anchors / straight | 同上 | 阻断关系必须为 T-bar，目标不得颠倒 | 同上 | 本地已验证；真实站点待验收 |
| `t_cell_killing_after` | T cell 杀伤 Tumor cell | `arrow` | `t_cell_after` → `tumor_cell_after` | source-to-target / edge anchors / straight | 同上 | 箭头方向从 T cell 指向 Tumor cell | 同上 | 本地已验证；真实站点待验收 |

当前语义验证只在页面提供可观察连接器类型和几何路径时成立；若真实 BioRender 只暴露像素、
无法可靠区分 Arrow 与 T-bar，系统必须返回 `unknown`，不得把几何直线冒充语义验证。

## 4. Group、Alignment、Distribution、Region 与 Z-order

| Logical ID | Scope | Required Action | Verification Rule | Recovery Rule | Current Status |
|---|---|---|---|---|---|
| `group_t_cell_before` | asset + `label_t_cell_before` | Group | 两成员共享逻辑组；组移动时两者 delta 一致 | 两成员已带相同逻辑组 ID 则不重放 | 本地已验证；真实站点待验收 |
| `group_tumor_cell_before` | asset + Label | Group | 同上 | 同上 | 本地已验证；真实站点待验收 |
| `group_pd1_before` | asset + Label | Group | 同上 | 同上 | 本地已验证；真实站点待验收 |
| `group_pdl1_before` | asset + Label | Group | 同上 | 同上 | 本地已验证；真实站点待验收 |
| `group_t_cell_after` | asset + Label | Group | 同上 | 同上 | 本地已验证；真实站点待验收 |
| `group_tumor_cell_after` | asset + Label | Group | 同上 | 同上 | 本地已验证；真实站点待验收 |
| `group_pd1_after` | asset + Label | Group | 同上 | 同上 | 本地已验证；真实站点待验收 |
| `group_pdl1_after` | asset + Label | Group | 同上 | 同上 | 本地已验证；真实站点待验收 |
| `group_antibody_after` | asset + Label | Group | 同上 | 同上 | 本地已验证；真实站点待验收 |
| `align_without_treatment_0073` | 左栏第一行 2 assets | Align middle | 中心线偏差 ≤ 5 px（终局 ≤ 8 px） | 当前几何已满足则不重放 | 本地已验证；真实站点待验收 |
| `align_without_treatment_0074` | 左栏第二行 2 assets | Align middle | 同上 | 同上 | 本地已验证；真实站点待验收 |
| `align_anti_pd1_treatment_0075` | 右栏第一行 2 assets | Align middle | 同上 | 同上 | 本地已验证；真实站点待验收 |
| `align_anti_pd1_treatment_0076` | 右栏第二行 2 assets | Align middle | 同上 | 同上 | 本地已验证；真实站点待验收 |
| `distribute_anti_pd1_treatment_0077` | `t_cell_after`, `pd1_after`, `antibody_after` | Distribute vertically | 相邻间距偏差 ≤ 6 px（终局 ≤ 8 px） | 当前间距已满足则不重放 | 本地已验证；真实站点待验收 |
| `region_without_treatment` | 左 Panel | Section boundary | 素材中心均在左区 | 终局重新观察 | 本地已验证；真实站点待验收 |
| `region_anti_pd1_treatment` | 右 Panel | Section boundary | 素材中心均在右区 | 终局重新观察 | 本地已验证；真实站点待验收 |
| `layout_z_order` | 全画布 | Observe Z-order | Connector 不高于 asset/Label；不可观察则 unknown | 重新观察，不猜测 | 本地已验证；真实站点待验收 |

当前 Fixture 需要 Group、Align、Distribute 和组整体移动；不要求 Ungroup、SetZOrder 或 Group
Resize 动作。系统会观察 Z-order，但不会在真实 UI 未证实时盲目执行 SetZOrder。明显布局错误指标：

- `overlap_count`
- `out_of_bounds_count`
- `alignment_deviation`
- `spacing_deviation`
- `label_collision_count`
- `connector_collision_count`
- `label_association_failure_count`
- `region_violations`
- `z_order_issues` / `z_order_unknown`

## 5. 保存状态

| Logical ID | Allowed Operation | Observation | Verification | Recovery | Current Status |
|---|---|---|---|---|---|
| `document_save` | 仅观察编辑器 autosave | 可见 `Saved` / `All changes saved`，时间、URL、UI profile fingerprint、截图 | 只接受完成状态；`Saving...` 或固定等待不算成功 | Resume 后重新观察；不触发 Export/Download/Share | 本地已验证；真实站点待验收 |

普通画布动作完成、编辑器自动保存、显式 Save、导出、下载、分享是不同状态。项目只允许观察
普通 Figure 的 autosave；自动导出、下载、分享、发布、购买和升级永久禁止。

## 6. 状态与恢复判定

元素需求使用现有 action state 与 element state 组合表达：`planned`、`searching`、
`candidate_selected`、`executing`、`executed_unverified`、`verified`、`failed`、`unknown`、
`blocked_by_policy`。不为状态数量而扩展数据库，但必须能回答元素是否真实存在、属性是否满足、
能否安全重试及是否需要人工接管。

统一恢复顺序：读取元素状态 → 重新校准 → 获取当前 Observation → 已满足则补记 verified →
部分满足则最小修复 → 可信不存在才创建 → 无法判断则 unknown。真实 Chromium Crash Injection
已经覆盖 asset、Label、Connector 和 Group 在“GUI 已完成、动作结果未写”时不重复执行。

## 7. 验收边界

- 代码能力：完成上述元素合同、状态持久化、策略阻断和调试命令。
- 本地 Chromium：完整 94 动作 PD-1 回归通过，包含 9 assets、9 Labels、5 Connectors。
- 真实 BioRender：尚未执行本轮 L0–L8 线上验收；不得据此声称 BioRender 已支持或给出成功率。

真实验收步骤见 [Real_BioRender_Acceptance.md](Real_BioRender_Acceptance.md)。
