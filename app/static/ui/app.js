"use strict";

const STORAGE_KEY = "biorender-guided-workflow-v1";
const FINAL_JOB_STATES = new Set(["completed", "failed", "blocked", "stopped"]);
const stateLabels = {
  login_required: "需要登录",
  login_checking: "等待登录确认",
  canvas_required: "需要指定画布",
  canvas_validating: "正在检查画布",
  prompt_required: "需要绘图需求",
  prompt_parsed: "需求已解析",
  dry_run_confirmation_required: "预演待确认",
  dry_run_failed: "预演失败",
  dry_run_stale: "预演已失效",
  ready_to_execute: "可以开始执行",
  executing: "正在执行",
  stop_requested: "正在安全停止",
  paused: "已安全停止，可继续",
  verifying: "正在验证结果",
  completed: "已完成并验证",
  completed_with_unknown: "已完成但需要人工检查",
  failed: "执行失败",
  blocked_by_policy: "被安全策略阻止"
};
const progressLabels = { waiting: "等待", running: "进行中", completed: "已完成", needs_review: "需检查", blocked: "已阻止", failed: "失败" };
const actionLabels = {
  open_biorender_editor: "准备浏览器",
  search_asset: "搜索素材",
  select_asset_candidate: "选择素材",
  drag_selected_asset: "插入素材",
  add_text: "添加标签",
  edit_text: "添加标签",
  connect_elements: "添加连接",
  move_element: "调整布局",
  resize_element: "调整布局",
  rotate_element: "调整布局",
  group_elements: "调整布局",
  align_elements: "调整布局",
  distribute_elements: "调整布局",
  save_project: "等待保存",
  capture_canvas: "验证结果"
};

const state = {
  step: 1,
  taskMode: "preset",
  prompt: "",
  canvasUrl: "",
  blankCanvasConfirmed: false,
  canvasVerified: false,
  planId: null,
  taskFingerprint: null,
  planFingerprint: null,
  planCanvasUrl: null,
  dryRunId: null,
  dryRunFingerprint: null,
  dryRunSummary: null,
  dryRunStaleReason: null,
  runId: null,
  currentJobId: null,
  currentJobStatus: null,
  currentJobKind: null,
  busy: false,
  polling: false,
  pollTimer: null,
  workflow: null,
  summary: null,
  evidenceRunId: null,
  evidenceSignature: null,
  planSummary: null,
  environment: null,
  hasSavedState: false
};

const byId = (id) => document.getElementById(id);
const queryAll = (selector) => Array.from(document.querySelectorAll(selector));

function setText(id, value) {
  const node = byId(id);
  if (node) node.textContent = value === null || value === undefined || value === "" ? "-" : String(value);
}

function showToast(message, isError = false) {
  const toast = byId("toast");
  toast.textContent = message;
  toast.className = isError ? "toast error" : "toast";
  toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => { toast.hidden = true; }, 4200);
}

function diagnosticMessage(payload, fallback = "请求失败，请稍后重试。") {
  const message = payload?.message || fallback;
  return payload?.diagnostic_hint ? `${message} ${payload.diagnostic_hint}` : message;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) }
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error(diagnosticMessage(payload));
    error.code = payload?.error_code || "REQUEST_FAILED";
    error.diagnosticHint = payload?.diagnostic_hint || null;
    error.details = payload?.details || null;
    throw error;
  }
  return payload;
}

function saveState() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({
    step: state.step,
    taskMode: state.taskMode,
    prompt: state.prompt,
    canvasUrl: state.canvasUrl,
    blankCanvasConfirmed: state.blankCanvasConfirmed,
    canvasVerified: state.canvasVerified,
    planId: state.planId,
    taskFingerprint: state.taskFingerprint,
    planFingerprint: state.planFingerprint,
    planCanvasUrl: state.planCanvasUrl,
    dryRunId: state.dryRunId,
    dryRunFingerprint: state.dryRunFingerprint,
    dryRunStaleReason: state.dryRunStaleReason,
    runId: state.runId,
    currentJobId: state.currentJobId
  }));
}

function loadState() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    if (!saved) return;
    Object.assign(state, saved);
    state.hasSavedState = true;
  } catch (_error) {
    localStorage.removeItem(STORAGE_KEY);
  }
}

function setBusy(value) {
  state.busy = Boolean(value);
  updateControls();
}

function setStep(step, { persist = true } = {}) {
  state.step = Math.max(1, Math.min(5, step));
  queryAll("[data-step-panel]").forEach((panel) => {
    panel.hidden = Number(panel.dataset.stepPanel) !== state.step;
  });
  queryAll("[data-step-indicator]").forEach((indicator) => {
    const number = Number(indicator.dataset.stepIndicator);
    indicator.classList.toggle("current", number === state.step);
    indicator.classList.toggle("completed", number < state.step);
  });
  const titles = ["登录 BioRender", "指定目标画布", "指定绘图需求", "执行任务", "查看完成结果"];
  setText("step-kicker", `当前步骤 ${state.step} / 5`);
  setText("step-title", titles[state.step - 1]);
  if (persist) saveState();
  updateControls();
}

function reconcileWorkflowStep(workflow) {
  const backendStep = Number(workflow.step || state.step);
  const explicitRollbackStates = new Set([
    "login_required", "login_checking", "canvas_required", "canvas_validating",
    "prompt_required"
  ]);
  if (explicitRollbackStates.has(workflow.state)) return backendStep;
  // A parsed plan remains a valid prerequisite after the user enters step 4.
  // Backend polling reports prompt_parsed as step 3 until a dry run is
  // confirmed; that is not a rollback signal and must not undo navigation.
  if (state.step === 4 && workflow.state === "prompt_parsed" && state.planId) {
    return 4;
  }
  return backendStep < state.step || backendStep >= 4 ? backendStep : state.step;
}

function renderWorkflow(workflow) {
  if (!workflow) return;
  if (
    state.workflow?.dry_run_completed === true
    && state.dryRunId
    && workflow.state === "prompt_parsed"
    && workflow.dry_run_completed !== true
  ) {
    return;
  }
  if (workflow.dry_run_id) {
    state.dryRunId = workflow.dry_run_id;
    state.dryRunFingerprint = workflow.plan_fingerprint || state.dryRunFingerprint;
    state.taskFingerprint = state.taskFingerprint || workflow.task_fingerprint || null;
    state.planFingerprint = state.planFingerprint || workflow.plan_fingerprint || null;
    if (state.runId === workflow.dry_run_id) state.runId = null;
  }
  if (workflow.dry_run_summary) renderDryRunSummary(workflow.dry_run_summary);
  state.workflow = workflow;
  const reconciledStep = reconcileWorkflowStep(workflow);
  if (reconciledStep !== state.step) {
    setStep(reconciledStep, { persist: false });
  }
  if (workflow.plan_summary && !state.planSummary) {
    state.planSummary = workflow.plan_summary;
    byId("plan-summary").hidden = false;
    setText("summary-assets", workflow.plan_summary.asset_count || 0);
    setText("summary-labels", workflow.plan_summary.label_count || 0);
    setText("summary-relations", workflow.plan_summary.relation_count || 0);
    setText("summary-layout", workflow.plan_summary.layout_description || "-");
    setText("summary-risks", workflow.plan_summary.risks?.length ? workflow.plan_summary.risks.join("；") : "无");
    setText("plan-support-status", workflow.plan_summary.supported === false ? "需要返回修改" : "需求解析通过");
    byId("plan-support-status").className = workflow.plan_summary.supported === false ? "status-badge danger" : "status-badge success";
  }
  setText("state-label", stateLabels[workflow.state] || workflow.state || "状态未知");
  setText("state-reason", workflow.reason || "");
  setText("next-action", workflow.next_action ? `下一步：${workflow.next_action}` : "");
  renderPromptGuidance(workflow);
  renderPromptPhases(workflow);
  setStatusBadge("login-state", state.environment?.browser_login === "verified" ? "已确认" : state.environment?.browser_login === "waiting_user" ? "等待用户登录" : "未检查");
  setStatusBadge("canvas-state", state.canvasVerified ? "已确认" : "未检查");
  setStatusBadge("prompt-state", state.planId ? "已解析" : "未解析");
  setStatusBadge("execution-state", stateLabels[workflow.state] || "未开始");
  if (workflow.state === "completed" || workflow.state === "completed_with_unknown") setStatusBadge("result-state", stateLabels[workflow.state]);
  updateControls();
  saveState();
}

function renderPromptGuidance(workflow) {
  const guidance = {
    prompt_required: "请先解析绘图需求。",
    prompt_parsed: "需求已解析。查看任务摘要后即可进入执行步骤。",
    ready_to_execute: "需求已解析，已进入执行步骤。"
  }[workflow.state];
  if (guidance) setText("prompt-guidance", guidance);
  setText(
    "next-step-hint",
    state.step === 3 && workflow.next_block_reason ? workflow.next_block_reason : ""
  );
}

function renderPromptPhases(workflow) {
  const currentIndex = {
    prompt_required: 0,
    prompt_parsed: 2,
    dry_run_confirmation_required: 3,
    dry_run_stale: 3,
    dry_run_failed: 3,
    ready_to_execute: 5
  }[workflow.state];
  if (currentIndex === undefined) return;
  queryAll("[data-prompt-phase]").forEach((phase, index) => {
    phase.dataset.status = index < currentIndex ? "completed" : index === currentIndex ? "current" : "pending";
    const label = phase.querySelector("small");
    if (label) label.textContent = index < currentIndex ? "已完成" : index === currentIndex ? "当前" : "等待";
  });
}

function setStatusBadge(id, text) {
  const node = byId(id);
  if (!node) return;
  node.textContent = text;
  node.className = "status-badge";
  if (text.includes("已确认") || text.includes("已完成") || text.includes("可以")) node.classList.add("success");
  if (text.includes("等待") || text.includes("需要")) node.classList.add("warning");
  if (text.includes("失败") || text.includes("阻止")) node.classList.add("danger");
}

function buildTask() {
  if (state.taskMode === "prompt") {
    return { mode: "prompt", preset_id: null, prompt: state.prompt.trim(), custom: null };
  }
  return { mode: "preset", preset_id: "pd1", prompt: null, custom: null };
}

function invalidatePlan(reason = null) {
  if (reason && (state.planId || state.dryRunId)) state.dryRunStaleReason = reason;
  state.planId = null;
  state.taskFingerprint = null;
  state.planFingerprint = null;
  state.planCanvasUrl = null;
  state.planSummary = null;
  // The plan changed, so any previously confirmed dry_run no longer applies
  // to what will run next. Drop it to prevent DRY_RUN_TASK_MISMATCH after
  // the user tweaks the prompt.
  state.dryRunId = null;
  state.dryRunFingerprint = null;
  state.dryRunSummary = null;
  byId("dry-run-review").hidden = true;
  byId("plan-summary").hidden = true;
  setText("prompt-guidance", "请先解析绘图需求。");
  renderPromptPhases({ state: "prompt_required" });
  if (state.step >= 3) setStep(3, { persist: false });
  saveState();
  updateControls();
}

function renderPlan(plan) {
  state.planId = plan.run_id;
  state.taskFingerprint = plan.task_fingerprint || null;
  state.planFingerprint = plan.plan_fingerprint || plan.task_fingerprint || null;
  state.planCanvasUrl = state.canvasUrl;
  state.dryRunStaleReason = null;
  state.planSummary = plan.task_summary || null;
  const summary = plan.task_summary || {};
  byId("plan-summary").hidden = false;
  setText("summary-assets", summary.asset_count || 0);
  setText("summary-labels", summary.label_count || 0);
  setText("summary-relations", summary.relation_count || 0);
  setText("summary-layout", summary.layout_description || "-");
  setText("summary-risks", summary.risks?.length ? summary.risks.join("；") : "无");
  setText("plan-support-status", summary.supported === false ? "需要返回修改" : "需求解析通过");
  byId("plan-support-status").className = summary.supported === false ? "status-badge danger" : "status-badge success";
  setText("prompt-guidance", summary.supported === false
    ? "当前需求需要返回修改。"
    : "需求已解析。查看任务摘要后即可进入执行步骤。");
  saveState();
  updateControls();
}

function renderList(id, items, emptyText = "无") {
  const list = byId(id);
  const values = Array.isArray(items) ? items : [];
  const nodes = values.map((value) => Object.assign(document.createElement("li"), { textContent: String(value) }));
  list.replaceChildren(...(nodes.length ? nodes : [Object.assign(document.createElement("li"), { textContent: emptyText })]));
}

function renderDryRunSummary(summary) {
  if (!summary) return;
  state.dryRunSummary = summary;
  state.dryRunStaleReason = null;
  byId("dry-run-review").hidden = false;
  const canvas = summary.target_canvas || {};
  const task = summary.task || {};
  const planned = summary.planned_actions || {};
  const result = summary.result || {};
  const evidence = summary.evidence || {};
  setText("dry-canvas-url", canvas.redacted_url || canvas.figure_identifier || "未提供脱敏 URL");
  setText("dry-canvas-confirmed", canvas.confirmed_test_canvas ? "是，已确认测试画布" : "否，需重新检查画布");
  setText("dry-title", task.figure_title || "-");
  setText("dry-assets", task.asset_count || 0);
  setText("dry-labels", task.label_count || 0);
  setText("dry-connections", task.connection_count || 0);
  setText("dry-total-actions", task.total_action_count || 0);
  renderList("dry-searches", planned.search_assets, "不搜索素材");
  renderList("dry-inserts", planned.insert_assets, "不插入素材");
  renderList("dry-label-actions", planned.add_labels, "不添加标签");
  renderList("dry-connection-actions", planned.add_connections, "不添加连接");
  setText("dry-layout", planned.adjust_layout ? "会按计划调整布局" : "不调整布局");
  setText("dry-policy", result.policy_check_passed ? "通过" : "未通过");
  setText("dry-blocked", result.blocked_action_count || 0);
  setText("dry-warnings", result.warning_count || 0);
  renderList("dry-review-items", result.manual_review_items, "无额外人工复核项");
  setText("dry-live-ready", result.can_enter_live_run ? "可以进入 Live Run" : "不能进入 Live Run");
  setText("dry-screenshot-note", evidence.screenshot_note || "安全预演不产生真实画布截图。");
  const audit = evidence.audit_event;
  setText("dry-audit", audit ? `${audit.event_type} · ${new Date(audit.created_at).toLocaleString()}` : "未找到 dry-run 审计事件");
  const rows = (summary.dry_run_actions || []).map((item) => {
    const row = document.createElement("tr");
    const dryStatus = item.status === "simulated" ? "模拟通过" : item.status;
    const policyStatus = item.policy_status === "policy_allowed" ? "策略允许" : item.policy_status;
    const liveStatus = item.live_execution_status === "planned" ? "计划执行" : item.live_execution_status;
    row.append(
      Object.assign(document.createElement("td"), { textContent: String(item.sequence) }),
      Object.assign(document.createElement("td"), { textContent: actionLabels[item.action_type] || item.action_type }),
      Object.assign(document.createElement("td"), { textContent: `${dryStatus} / ${policyStatus}` }),
      Object.assign(document.createElement("td"), { textContent: item.blocked ? "已阻止" : `${liveStatus} · ${item.risk_level}` })
    );
    return row;
  });
  byId("dry-action-body").replaceChildren(...rows);
  saveState();
}

function renderElements(items) {
  const body = byId("elements-body");
  if (!items?.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.className = "empty-cell";
    cell.textContent = "暂无元素状态。";
    row.append(cell);
    body.replaceChildren(row);
    return;
  }
  const fragment = document.createDocumentFragment();
  items.forEach((item) => {
    const row = document.createElement("tr");
    const status = document.createElement("span");
    status.className = statusClass(item.status);
    status.textContent = item.friendly_status || item.status || "未知";
    const stateCell = document.createElement("td");
    stateCell.append(status);
    row.append(
      Object.assign(document.createElement("td"), { textContent: item.name || "-" }),
      Object.assign(document.createElement("td"), { textContent: item.type || "-" }),
      stateCell,
      Object.assign(document.createElement("td"), { textContent: item.message || "-" })
    );
    fragment.append(row);
  });
  body.replaceChildren(fragment);
}

function statusClass(status) {
  if (status === "verified") return "status-pill status-verified";
  if (status === "unknown") return "status-pill status-review";
  if (status === "failed") return "status-pill status-failed";
  if (status === "blocked_by_policy") return "status-pill status-blocked";
  return "status-pill status-default";
}

function renderEvidence(items, runId) {
  const grid = byId("evidence-grid");
  const images = (items || []).filter((item) => item.is_image && item.preview_url);
  const signature = `${runId || "none"}|${images.map((item) => `${item.id}:${item.preview_url}`).join("|")}`;
  setText("evidence-source", runId ? `Live Run ${runId}` : "尚无 Live Run");
  if (state.evidenceSignature === signature && state.evidenceRunId === runId) return;
  state.evidenceSignature = signature;
  state.evidenceRunId = runId;
  if (!images.length) {
    grid.replaceChildren(Object.assign(document.createElement("p"), { className: "empty-card", textContent: "当前 Live Run 暂无截图" }));
    byId("execution-evidence").replaceChildren(Object.assign(document.createElement("span"), { textContent: "当前 Live Run 暂无截图" }));
    return;
  }
  const fragment = document.createDocumentFragment();
  images.forEach((item) => {
    const card = document.createElement("article");
    card.className = "evidence-card";
    const image = document.createElement("img");
    image.src = item.preview_url;
    image.alt = `${item.kind || "运行"} 截图证据`;
    image.loading = "lazy";
    const copy = document.createElement("div");
    copy.append(
      Object.assign(document.createElement("strong"), { textContent: item.kind || "截图" }),
      Object.assign(document.createElement("small"), { textContent: item.name || "" })
    );
    card.append(image, copy);
    fragment.append(card);
  });
  grid.replaceChildren(fragment);
  const latest = images[images.length - 1];
  const executionEvidence = byId("execution-evidence");
  if (latest) {
    const image = document.createElement("img");
    image.src = latest.preview_url;
    image.alt = "最近一次运行截图";
    executionEvidence.replaceChildren(image);
  } else {
    executionEvidence.replaceChildren(Object.assign(document.createElement("span"), { textContent: "暂无截图" }));
  }
}

function finalStatus(summary) {
  if (summary.status === "blocked") return "被安全策略阻止";
  if (summary.status === "failed") return "执行失败";
  if (["paused_approval", "paused_authentication", "paused_reconciliation"].includes(summary.status)) return "已安全停止";
  if (summary.needs_review_elements > 0 || summary.status === "unknown") return "已完成但需要人工检查";
  if (["completed", "awaiting_confirmation"].includes(summary.status)) return "已完成并验证";
  return summary.friendly_status || summary.status || "状态未知";
}

function renderSummary(summary) {
  state.summary = summary;
  state.runId = summary.run_id;
  const displayStatus = finalStatus(summary);
  setText("evidence-source", `Live Run ${summary.run_id}`);
  setText("failure-subcode", summary.failure_subcode || "-");
  setText("result-status", displayStatus);
  setText("result-run-id", summary.run_id);
  setText("result-verified", summary.verified_elements || 0);
  setText("result-review", summary.needs_review_elements || 0);
  setText("result-failed", summary.failed_elements || 0);
  setText("result-blocked", summary.policy_blocked_elements || 0);
  setText("result-save", summary.save_status || "-");
  setText("result-completed-at", summary.completed_at ? new Date(summary.completed_at).toLocaleString() : "-");
  setText("progress-percent", `${summary.progress_percent || 0}%`);
  byId("progress-bar").style.width = `${summary.progress_percent || 0}%`;
  setText("completed-actions", `${summary.completed_actions || 0} / ${summary.total_actions || 0}`);
  setText("current-action", summary.current_action ? (actionLabels[summary.current_action.action_type] || summary.current_action.action_type) : "-");
  setText("current-element", summary.current_action?.element || "-");
  const logs = byId("recent-logs");
  const logItems = (summary.recent_logs || []).map((entry) => {
    const item = document.createElement("li");
    item.textContent = `${actionLabels[entry.action_type] || entry.action_type}：${entry.message || entry.status}`;
    return item;
  });
  logs.replaceChildren(...(logItems.length ? logItems : [Object.assign(document.createElement("li"), { textContent: "暂无运行日志。" })]));
  (summary.steps || []).forEach((item) => {
    const row = document.querySelector(`[data-step="${item.key}"]`);
    if (!row) return;
    row.dataset.status = item.status;
    const small = row.querySelector("small");
    if (small) small.textContent = progressLabels[item.status] || item.status;
  });
  setText("result-message", displayStatus === "已完成但需要人工检查" ? "任务已结束，但 unknown 元素不能视为成功，请人工检查截图。" : `最终状态：${displayStatus}。`);
  if (summary.status === "failed") {
    setText(
      "job-message",
      `Live Run 失败（${summary.failure_subcode || "unknown"}）。${summary.can_resume ? "可继续未完成任务。" : summary.resume_blocked_reason || "请修复后重新开始。"}`
    );
  }
  byId("advanced-details").textContent = JSON.stringify(summary, null, 2);
  byId("result-json").textContent = JSON.stringify(summary, null, 2);
  byId("verify-run").disabled = !state.runId || state.busy;
  byId("continue-run").disabled = !summary.can_resume || state.busy;
  setStatusBadge("result-state", displayStatus);
  saveState();
  updateControls();
}

async function loadRun(runId) {
  const [summary, elements, evidence] = await Promise.all([
    api(`/api/ui/runs/${encodeURIComponent(runId)}`),
    api(`/api/ui/runs/${encodeURIComponent(runId)}/elements`),
    api(`/api/ui/runs/${encodeURIComponent(runId)}/evidence`)
  ]);
  if (state.runId && state.runId !== runId) return;
  renderSummary(summary);
  renderElements(elements.items);
  renderEvidence(evidence.items, runId);
}

function setJob(job) {
  if (!job) return;
  state.currentJobId = job.id;
  state.currentJobStatus = job.status;
  state.currentJobKind = job.kind;
  if (job.figure_id) state.runId = job.figure_id;
  const message = diagnosticMessage(job, "后台任务正在运行。");
  byId("job-message").textContent = message;
  if (job.kind === "manual_login") {
    byId("login-message").textContent = message;
  }
  setText("elapsed-time", `${job.elapsed_seconds || 0} 秒`);
  saveState();
  updateControls();
}

function scheduleJobPoll(delay = 0) {
  if (!state.currentJobId || state.polling || state.pollTimer) return;
  state.pollTimer = window.setTimeout(() => {
    state.pollTimer = null;
    pollJob();
  }, delay);
}

async function pollJob() {
  if (!state.currentJobId || state.polling) return;
  state.polling = true;
  let continuePolling = false;
  try {
    const job = await api(`/api/ui/jobs/${encodeURIComponent(state.currentJobId)}`);
    setJob(job);
    if (job.figure_id) await loadRun(job.figure_id);
    if (FINAL_JOB_STATES.has(job.status)) {
      if (job.kind === "canvas_check" && job.result?.canvas_verified) {
        state.canvasVerified = true;
        byId("canvas-result").textContent = "画布检查通过。";
        byId("canvas-details").hidden = false;
        setText("canvas-title", job.result.title || "BioRender Figure");
        setText("canvas-figure-id", job.result.figure_identifier || job.result.redacted_url || state.canvasUrl);
        saveState();
      }
      state.currentJobId = null;
      state.currentJobStatus = job.status;
      state.busy = false;
      if (job.kind === "live_figure" && job.status !== "stopped") {
        setStep(5, { persist: false });
      } else if (job.kind === "live_figure") {
        setStep(4, { persist: false });
      }
      await refreshEnvironment();
      showToast(
        diagnosticMessage(job, "后台任务已结束"),
        job.status === "failed" || job.status === "blocked"
      );
    } else {
      state.busy = true;
      continuePolling = true;
      await refreshWorkflow();
    }
  } catch (error) {
    if (error.code === "JOB_NOT_FOUND") {
      state.currentJobId = null;
      state.busy = false;
      await refreshEnvironment();
    } else {
      continuePolling = true;
    }
    showToast(error.message, true);
  } finally {
    state.polling = false;
    if (continuePolling) scheduleJobPoll(1000);
    updateControls();
  }
}

async function refreshWorkflow() {
  const query = new URLSearchParams();
  if (state.planId) query.set("plan_id", state.planId);
  if (state.dryRunId) query.set("dry_run_id", state.dryRunId);
  if (state.runId && state.runId !== state.dryRunId) query.set("run_id", state.runId);
  try {
    const workflow = await api(`/api/ui/workflow-state?${query.toString()}`);
    renderWorkflow(workflow);
  } catch (error) {
    if (error.code === "RUN_NOT_FOUND") {
      state.runId = null;
      saveState();
    }
  }
}

async function refreshEnvironment() {
  try {
    const [data, version] = await Promise.all([
      api("/api/ui/status"),
      api("/api/version")
    ]);
    state.environment = data;
    state.canvasVerified = Boolean(data.verified_canvas);
    if (data.verified_canvas) {
      byId("canvas-details").hidden = false;
      setText("canvas-figure-id", data.verified_canvas.figure_identifier || data.verified_canvas.redacted_url || state.canvasUrl);
      setText("canvas-title", data.verified_canvas.title || "BioRender Figure");
    } else {
      byId("canvas-details").hidden = true;
    }
    setText("backend-status", data.backend === "normal" ? "正常" : "不可用");
    setText("browser-status", data.active_jobs?.length ? "任务运行中" : data.browser_login === "verified" ? "已连接" : "空闲");
    setText("database-status", data.database === "normal" ? "正常" : "不可用");
    const shortCommit = String(version.git_commit || "unknown").slice(0, 8);
    const dirtySuffix = version.git_dirty ? " +dirty" : "";
    setText(
      "version-status",
      `${version.git_branch || "unknown"}@${shortCommit}${dirtySuffix}`
    );
    byId("version-status").title = (
      `Build: ${version.build_time || "unknown"}\n${version.static_root || ""}`
    );
    setText("login-browser-state", data.active_jobs?.length ? "任务运行中" : "空闲");
    setText("login-detail-state", data.browser_login === "verified" ? "已确认" : data.browser_login === "waiting_user" ? "等待用户登录" : "未检查");
    if (data.browser_login === "verified") byId("login-message").textContent = "登录已由后端确认，可以进入画布步骤。";
    const active = (data.active_jobs || [])[0];
    if (active) {
      setJob(active);
      state.busy = true;
      scheduleJobPoll();
    } else if (state.currentJobId) {
      state.currentJobId = null;
      state.busy = false;
    }
    if (state.runId && !state.currentJobId) {
      await loadRun(state.runId).catch(() => {});
    }
    updateControls();
    await refreshWorkflow();
  } catch (error) {
    setText("backend-status", "连接失败");
    showToast(error.message, true);
  }
}

async function openLogin() {
  if (state.busy) return;
  setBusy(true);
  try {
    const job = await api("/api/ui/login/open", { method: "POST", body: JSON.stringify({ confirm_manual_login: true }) });
    setJob(job);
    state.busy = true;
    byId("login-message").textContent = "请在新浏览器窗口中手动登录 BioRender，完成后回来检查状态。";
    scheduleJobPoll();
    await refreshWorkflow();
  } catch (error) {
    setBusy(false);
    byId("login-message").textContent = error.message;
    showToast(error.message, true);
  }
}

async function completeLogin() {
  try {
    const job = await api("/api/ui/login/complete", { method: "POST", body: "{}" });
    setJob(job);
    state.busy = true;
    scheduleJobPoll();
  } catch (error) {
    byId("login-message").textContent = error.message;
    showToast(error.message, true);
  }
}

async function checkCanvas() {
  state.canvasUrl = byId("editor-url").value.trim();
  state.blankCanvasConfirmed = byId("confirm-blank").checked;
  saveState();
  if (!state.canvasUrl || !state.blankCanvasConfirmed) {
    byId("canvas-result").textContent = "请填写 Figure URL，并确认这是可测试的空白画布。";
    return;
  }
  setBusy(true);
  try {
    const job = await api("/api/ui/canvas/check", { method: "POST", body: JSON.stringify({ editor_url: state.canvasUrl, confirmed_blank: true }) });
    setJob(job);
    byId("canvas-result").textContent = job.message || "正在检查画布。";
    scheduleJobPoll();
  } catch (error) {
    setBusy(false);
    byId("canvas-result").textContent = error.message;
    showToast(error.message, true);
  }
}

async function parsePrompt() {
  state.prompt = byId("prompt-input").value.trim();
  const task = buildTask();
  if (state.taskMode === "prompt" && state.prompt.length < 3) {
    showToast("请先输入至少 3 个字符的绘图需求。", true);
    return;
  }
  setBusy(true);
  try {
    const plan = await api("/api/ui/plans", { method: "POST", body: JSON.stringify({ task }) });
    renderPlan(plan);
    setText("job-message", "需求已解析。请检查任务摘要后进入执行步骤。");
    showToast("需求解析完成。");
    await refreshWorkflow();
  } catch (error) {
    showToast(error.message, true);
  } finally { setBusy(false); }
}

function livePayload() {
  return {
    editor_url: state.canvasUrl.trim(),
    task: buildTask(),
    plan_id: state.planId,
    dry_run_id: state.dryRunId,
    confirmed_disposable: true,
    confirm_live: true,
    enable_biorender_ai: false
  };
}

async function runDryRun() {
  if (state.busy || !state.planId) return;
  setBusy(true);
  try {
    const summary = await api("/api/ui/dry-run", {
      method: "POST",
      body: JSON.stringify({ plan_id: state.planId, task: buildTask() })
    });
    if (!summary.dry_run_id) throw new Error("安全预演响应缺少 dry_run_id，不能确认。");
    state.dryRunId = summary.dry_run_id;
    state.dryRunFingerprint = summary.plan_fingerprint || state.planFingerprint;
    state.taskFingerprint = summary.task_fingerprint || state.taskFingerprint;
    state.planFingerprint = summary.plan_fingerprint || state.planFingerprint;
    state.dryRunStaleReason = null;
    if (summary.summary) renderDryRunSummary(summary.summary);
    saveState();
    await refreshWorkflow();
    if (summary.dry_run_failed || summary.status === "failed") {
      showToast("安全预演失败，请查看结果后修正并重新预演。", true);
    } else if (summary.dry_run_completed && summary.can_confirm_dry_run) {
      showToast("安全预演完成，请查看内容并确认后再开始真实执行。");
    } else {
      showToast("安全预演未进入可确认状态，请查看阻塞原因。", true);
    }
  } catch (error) {
    showToast(error.message, true);
  } finally { setBusy(false); }
}

async function confirmDryRun() {
  if (state.busy || !state.dryRunId) return;
  setBusy(true);
  try {
    const confirmed = await api(`/api/ui/runs/${encodeURIComponent(state.dryRunId)}/confirm-dry-run`, {
      method: "POST",
      body: JSON.stringify({
        task_fingerprint: state.taskFingerprint,
        plan_fingerprint: state.dryRunFingerprint,
        source_plan_id: state.planId,
        editor_url: state.canvasUrl
      })
    });
    state.dryRunFingerprint = confirmed.plan_fingerprint || state.dryRunFingerprint;
    if (confirmed.summary) renderDryRunSummary(confirmed.summary);
    await refreshWorkflow();
    showToast("安全预演已确认，可以开始真实执行。");
  } catch (error) {
    showToast(error.message, true);
  } finally { setBusy(false); }
}

async function startLive() {
  if (state.busy || !state.planId || !state.dryRunId) return;
  setBusy(true);
  try {
    const job = await api("/api/ui/live-runs", { method: "POST", body: JSON.stringify(livePayload()) });
    setJob(job);
    setStep(4, { persist: false });
    state.busy = true;
    scheduleJobPoll();
    await refreshWorkflow();
  } catch (error) {
    setBusy(false);
    showToast(error.message, true);
  }
}

async function safeStop() {
  if (!state.currentJobId && !state.runId) return;
  try {
    const path = state.currentJobId ? `/api/ui/jobs/${encodeURIComponent(state.currentJobId)}/stop` : `/api/ui/runs/${encodeURIComponent(state.runId)}/stop`;
    const job = await api(path, { method: "POST", body: "{}" });
    setJob(job);
    state.busy = true;
    scheduleJobPoll();
    await refreshWorkflow();
  } catch (error) { showToast(error.message, true); }
}

async function resumeRun() {
  if (!state.runId || state.busy) return;
  setBusy(true);
  try {
    const job = await api(`/api/ui/runs/${encodeURIComponent(state.runId)}/resume`, { method: "POST", body: JSON.stringify({ confirmed_disposable: true, confirm_live: true, enable_biorender_ai: false }) });
    setJob(job);
    setStep(4, { persist: false });
    scheduleJobPoll();
  } catch (error) { setBusy(false); showToast(error.message, true); }
}

async function verifyRun() {
  if (!state.runId || state.busy) return;
  try {
    const result = await api(`/api/ui/runs/${encodeURIComponent(state.runId)}/verify`, { method: "POST", body: "{}" });
    renderSummary(result);
    await refreshWorkflow();
    showToast(result.verification_passed ? "证据验证通过。" : "验证发现需要人工检查的内容。", !result.verification_passed);
  } catch (error) { showToast(error.message, true); }
}

async function newTask() {
  try { await api("/api/ui/workflow/reset", { method: "POST", body: "{}" }); } catch (_error) { /* local reset still keeps the UI usable */ }
  state.canvasUrl = "";
  state.blankCanvasConfirmed = false;
  state.canvasVerified = false;
  state.planId = null;
  state.taskFingerprint = null;
  state.planFingerprint = null;
  state.planCanvasUrl = null;
  state.dryRunId = null;
  state.dryRunFingerprint = null;
  state.dryRunSummary = null;
  state.dryRunStaleReason = null;
  state.runId = null;
  state.currentJobId = null;
  state.summary = null;
  byId("editor-url").value = "";
  byId("confirm-blank").checked = false;
  byId("prompt-input").value = "";
  byId("prompt-count").textContent = "0 / 3000";
  invalidatePlan();
  setStep(2);
  await refreshEnvironment();
}

function updateControls() {
  const workflowState = state.workflow?.state || "login_required";
  const backendButtons = state.workflow?.buttons || {};
  const hasPrompt = state.taskMode === "preset" || state.prompt.trim().length >= 3;
  const running = state.busy || ["login_checking", "canvas_validating", "executing", "stop_requested", "verifying"].includes(workflowState);
  byId("open-login").disabled = state.busy || workflowState !== "login_required";
  byId("complete-login").disabled = !state.currentJobId || state.currentJobKind !== "manual_login" || state.currentJobStatus !== "waiting_user";
  byId("check-canvas").disabled = state.busy || workflowState !== "canvas_required" || !state.canvasUrl || !state.blankCanvasConfirmed;
  byId("parse-prompt").disabled = state.busy || backendButtons.parse_prompt !== true || !hasPrompt;
  byId("run-dry-run").disabled = state.busy || backendButtons.run_dry_run !== true || !state.planId;
  const workflow = state.workflow || {};
  const promptMatches = Boolean(state.taskFingerprint) && workflow.task_fingerprint === state.taskFingerprint;
  const planMatches = Boolean(state.planFingerprint)
    && workflow.plan_fingerprint === state.planFingerprint
    && state.dryRunFingerprint === state.planFingerprint;
  const canvasMatches = Boolean(state.planCanvasUrl) && state.canvasUrl === state.planCanvasUrl && state.canvasVerified;
  const summaryLoaded = Boolean(state.dryRunSummary);
  const canConfirm = !state.busy
    && Boolean(state.dryRunId)
    && workflow.dry_run_completed === true
    && workflow.dry_run_failed !== true
    && workflow.dry_run_confirmed === false
    && workflow.can_confirm_dry_run === true
    && promptMatches
    && planMatches
    && canvasMatches
    && summaryLoaded
    && !state.dryRunStaleReason;
  byId("confirm-dry-run").disabled = !canConfirm;
  let confirmReason = "预演结果已加载，可以确认。";
  if (state.busy) confirmReason = "预演结果仍在加载。";
  else if (state.dryRunStaleReason) confirmReason = state.dryRunStaleReason;
  else if (workflow.dry_run_failed) confirmReason = "预演失败，不能确认。";
  else if (!state.dryRunId) confirmReason = "找不到 dry_run_id；请重新执行安全预演。";
  else if (!workflow.dry_run_completed) confirmReason = "尚未完成安全预演。";
  else if (workflow.dry_run_confirmed) confirmReason = "预演结果已确认。";
  else if (!summaryLoaded) confirmReason = "预演摘要仍在加载，暂不能确认。";
  else if (!promptMatches) confirmReason = "当前 Prompt 已修改，请重新预演。";
  else if (!canvasMatches) confirmReason = "当前画布已变化，请重新预演。";
  else if (!planMatches) confirmReason = "当前计划与预演指纹不一致，请重新预演。";
  else if (workflow.can_confirm_dry_run !== true) confirmReason = workflow.reason || "后端尚未允许确认。";
  setText("dry-run-confirm-reason", confirmReason);
  byId("start-live").disabled = state.busy || backendButtons.start_live !== true;
  byId("start-live").textContent = state.summary?.status === "failed" ? "修复后重试" : "开始执行";
  byId("resume-run").disabled = state.busy || backendButtons.resume !== true;
  byId("safe-stop").disabled = !state.currentJobId && !state.runId || !["login_checking", "canvas_validating", "executing", "stop_requested", "verifying", "paused"].includes(workflowState);
  byId("previous-step").disabled = state.busy || state.step <= 1;
  const canGoNext = (state.step === 1 && state.environment?.browser_login === "verified")
    || (state.step === 2 && state.canvasVerified)
    || (state.step === 3 && state.workflow?.state === "prompt_parsed" && Boolean(state.planId))
    || (state.step === 4 && Boolean(state.summary));
  byId("next-step").disabled = state.busy || state.step >= 5 || !canGoNext;
  if (state.step === 3 && !state.busy && !canGoNext && state.workflow?.next_block_reason) {
    setText("next-step-hint", state.workflow.next_block_reason);
  } else if (state.step !== 3 || state.busy || canGoNext) {
    setText("next-step-hint", "");
  }
  document.querySelector(".bottom-actions").classList.toggle("running", running);
  byId("run-canvas").textContent = state.canvasUrl || "未指定";
  byId("run-task").textContent = state.planSummary ? `${state.planSummary.asset_count || 0} 个素材，${state.planSummary.relation_count || 0} 条连接` : "未解析";
  byId("editor-url").value = state.canvasUrl;
  byId("confirm-blank").checked = state.blankCanvasConfirmed;
  if (byId("prompt-input").value !== state.prompt) byId("prompt-input").value = state.prompt;
}

function goNext() {
  if (state.busy) return;
  if (state.step === 1 && state.environment?.browser_login === "verified") return setStep(2);
  if (state.step === 2 && state.canvasVerified) return setStep(3);
  if (state.step === 3 && state.planId && state.workflow?.state === "prompt_parsed") return setStep(4);
  if (state.step === 4 && state.summary) return setStep(5);
  showToast(state.workflow?.reason || "请先完成当前步骤。", true);
}

function goPrevious() {
  if (!state.busy && state.step > 1) setStep(state.step - 1);
}

queryAll('input[name="task-mode"]').forEach((input) => input.addEventListener("change", () => {
  state.taskMode = input.value;
  byId("preset-panel").hidden = state.taskMode !== "preset";
  byId("prompt-panel").hidden = state.taskMode !== "prompt";
  invalidatePlan("任务类型已修改，请重新解析并预演。");
  updateControls();
}));
byId("prompt-input").addEventListener("input", () => {
  state.prompt = byId("prompt-input").value;
  byId("prompt-count").textContent = `${state.prompt.length} / 3000`;
  if (state.planId || state.dryRunId) invalidatePlan("当前 Prompt 已修改，请重新预演。");
  updateControls();
});
byId("editor-url").addEventListener("input", () => {
  const changedFromPlan = Boolean(state.planCanvasUrl) && byId("editor-url").value.trim() !== state.planCanvasUrl;
  state.canvasUrl = byId("editor-url").value.trim();
  state.canvasVerified = false;
  if (changedFromPlan || state.dryRunId) invalidatePlan("当前画布已变化，请重新检查画布、解析需求并重新预演。");
  updateControls();
  saveState();
});
byId("confirm-blank").addEventListener("change", () => {
  state.blankCanvasConfirmed = byId("confirm-blank").checked;
  updateControls();
  saveState();
});
byId("open-login").addEventListener("click", openLogin);
byId("complete-login").addEventListener("click", completeLogin);
byId("check-canvas").addEventListener("click", checkCanvas);
byId("parse-prompt").addEventListener("click", parsePrompt);
byId("run-dry-run").addEventListener("click", runDryRun);
byId("confirm-dry-run").addEventListener("click", confirmDryRun);
byId("start-live").addEventListener("click", startLive);
byId("safe-stop").addEventListener("click", safeStop);
byId("resume-run").addEventListener("click", resumeRun);
byId("verify-run").addEventListener("click", verifyRun);
byId("continue-run").addEventListener("click", resumeRun);
byId("view-run").addEventListener("click", () => byId("result-details").open = true);
byId("new-task").addEventListener("click", newTask);
byId("previous-step").addEventListener("click", goPrevious);
byId("next-step").addEventListener("click", goNext);
byId("refresh-status").addEventListener("click", refreshEnvironment);

loadState();
state.hasSavedState = Boolean(localStorage.getItem(STORAGE_KEY));
byId("prompt-input").value = state.prompt;
byId("prompt-count").textContent = `${state.prompt.length} / 3000`;
byId("preset-panel").hidden = state.taskMode !== "preset";
byId("prompt-panel").hidden = state.taskMode !== "prompt";
queryAll('input[name="task-mode"]').forEach((input) => { input.checked = input.value === state.taskMode; });
setStep(state.step, { persist: false });
updateControls();
refreshEnvironment();
window.setInterval(refreshEnvironment, 10000);
