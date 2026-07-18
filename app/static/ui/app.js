"use strict";

const state = {
  taskMode: "preset",
  runMode: "dry",
  assets: [
    { id: "asset_1", display_name: "素材 A", search_term: "protein", fallback_terms: "", label_text: "Protein A" },
    { id: "asset_2", display_name: "素材 B", search_term: "cell", fallback_terms: "", label_text: "Cell B" }
  ],
  relations: [{ source_id: "asset_1", target_id: "asset_2", type: "arrow" }],
  currentRunId: null,
  currentJobId: null,
  canResume: false,
  canStop: false,
  busy: false,
  recentRun: null
};

const byId = (id) => document.getElementById(id);
const query = (selector) => document.querySelector(selector);
const queryAll = (selector) => Array.from(document.querySelectorAll(selector));

function make(tag, options = {}) {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined) node.textContent = String(options.text);
  if (options.type) node.type = options.type;
  if (options.value !== undefined) node.value = String(options.value);
  if (options.placeholder) node.placeholder = options.placeholder;
  if (options.title) node.title = options.title;
  return node;
}

function showToast(message, isError = false) {
  const toast = byId("toast");
  toast.textContent = message;
  toast.className = isError ? "toast error" : "toast";
  toast.hidden = false;
  window.setTimeout(() => { toast.hidden = true; }, 4200);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) }
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error(payload?.message || "请求失败，请稍后重试。");
    error.code = payload?.error_code || "REQUEST_FAILED";
    error.details = payload?.details || null;
    throw error;
  }
  return payload;
}

function field(labelText, input) {
  const label = make("label", { text: labelText });
  label.append(input);
  return label;
}

function syncAsset(index, key, value) {
  state.assets[index][key] = value;
  if (key === "id") refreshRelationOptions();
}

function assetInput(index, key, labelText, placeholder = "") {
  const input = make("input", { type: "text", value: state.assets[index][key] || "", placeholder });
  input.maxLength = key === "id" ? 32 : 80;
  input.addEventListener("input", () => syncAsset(index, key, input.value.trim()));
  return field(labelText, input);
}

function renderAssets() {
  const container = byId("asset-list");
  const fragment = document.createDocumentFragment();
  state.assets.forEach((asset, index) => {
    const row = make("div", { className: "repeat-row asset-row" });
    row.append(
      assetInput(index, "display_name", "显示名称"),
      assetInput(index, "search_term", "BioRender 搜索词"),
      assetInput(index, "fallback_terms", "备用搜索词", "逗号分隔，可选"),
      assetInput(index, "label_text", "标签文字", "留空则使用显示名称")
    );
    const actions = make("div", { className: "row-actions" });
    const up = make("button", { className: "icon-button", type: "button", text: "↑", title: "上移" });
    const down = make("button", { className: "icon-button", type: "button", text: "↓", title: "下移" });
    const remove = make("button", { className: "icon-button", type: "button", text: "删除", title: "删除素材" });
    up.disabled = index === 0;
    down.disabled = index === state.assets.length - 1;
    remove.disabled = state.assets.length <= 2;
    up.addEventListener("click", () => moveAsset(index, -1));
    down.addEventListener("click", () => moveAsset(index, 1));
    remove.addEventListener("click", () => removeAsset(index));
    actions.append(up, down, remove);
    row.append(actions);
    fragment.append(row);
  });
  container.replaceChildren(fragment);
}

function moveAsset(index, offset) {
  const target = index + offset;
  if (target < 0 || target >= state.assets.length) return;
  [state.assets[index], state.assets[target]] = [state.assets[target], state.assets[index]];
  renderAssets();
  renderRelations();
}

function removeAsset(index) {
  const removedId = state.assets[index].id;
  state.assets.splice(index, 1);
  state.relations = state.relations.filter((item) => item.source_id !== removedId && item.target_id !== removedId);
  renderAssets();
  renderRelations();
}

function addAsset() {
  if (state.assets.length >= 15) return showToast("当前自定义模式最多支持 15 个素材。", true);
  let number = state.assets.length + 1;
  let id = `asset_${number}`;
  while (state.assets.some((item) => item.id === id)) { number += 1; id = `asset_${number}`; }
  state.assets.push({ id, display_name: `素材 ${number}`, search_term: "", fallback_terms: "", label_text: "" });
  renderAssets();
  renderRelations();
}

function option(value, label, selected) {
  const item = make("option", { value, text: label });
  item.selected = selected;
  return item;
}

function assetSelect(index, key, labelText) {
  const select = make("select");
  state.assets.forEach((asset) => select.append(option(asset.id, asset.display_name || asset.id, state.relations[index][key] === asset.id)));
  select.addEventListener("change", () => { state.relations[index][key] = select.value; });
  return field(labelText, select);
}

function renderRelations() {
  const container = byId("relation-list");
  const fragment = document.createDocumentFragment();
  state.relations.forEach((relation, index) => {
    if (!state.assets.some((asset) => asset.id === relation.source_id)) relation.source_id = state.assets[0]?.id || "";
    if (!state.assets.some((asset) => asset.id === relation.target_id)) relation.target_id = state.assets[1]?.id || state.assets[0]?.id || "";
    const row = make("div", { className: "repeat-row relation-row" });
    const typeSelect = make("select");
    [["line", "普通连线"], ["arrow", "箭头"], ["inhibition", "抑制线"]].forEach(([value, label]) => {
      typeSelect.append(option(value, label, relation.type === value));
    });
    typeSelect.addEventListener("change", () => { relation.type = typeSelect.value; });
    const remove = make("button", { className: "icon-button", type: "button", text: "删除", title: "删除关系" });
    remove.disabled = state.relations.length <= 1;
    remove.addEventListener("click", () => { state.relations.splice(index, 1); renderRelations(); });
    row.append(assetSelect(index, "source_id", "起点素材"), assetSelect(index, "target_id", "终点素材"), field("类型", typeSelect), remove);
    fragment.append(row);
  });
  container.replaceChildren(fragment);
}

function refreshRelationOptions() { renderRelations(); }

function addRelation() {
  if (state.relations.length >= 30) return showToast("当前自定义模式最多支持 30 条关系。", true);
  state.relations.push({ source_id: state.assets[0]?.id || "", target_id: state.assets[1]?.id || "", type: "arrow" });
  renderRelations();
}

function buildTask() {
  if (state.taskMode === "preset") return { mode: "preset", preset_id: "pd1", custom: null };
  return {
    mode: "custom",
    preset_id: null,
    custom: {
      title: byId("custom-title").value.trim(),
      research_topic: byId("research-topic").value.trim(),
      notes: byId("custom-notes").value.trim() || null,
      assets: state.assets.map((asset) => ({
        id: asset.id.trim(),
        display_name: asset.display_name.trim(),
        search_term: asset.search_term.trim(),
        fallback_terms: asset.fallback_terms.split(",").map((item) => item.trim()).filter(Boolean),
        label_text: asset.label_text.trim() || null
      })),
      relations: state.relations.map((relation) => ({ ...relation })),
      layout: "auto"
    }
  };
}

function livePayload() {
  return {
    editor_url: byId("editor-url").value.trim(),
    task: buildTask(),
    confirmed_disposable: byId("confirm-disposable").checked,
    confirm_live: true,
    enable_biorender_ai: false
  };
}

function confirmationPayload() {
  return {
    confirmed_disposable: byId("confirm-disposable").checked,
    confirm_live: true,
    enable_biorender_ai: false
  };
}

function setBusy(value) {
  state.busy = value;
  updateControls();
}

function updateControls() {
  const hasUrl = byId("editor-url").value.trim().length > 0;
  const confirmed = byId("confirm-disposable").checked;
  const liveSelected = state.runMode === "live";
  byId("start-dry").disabled = state.busy || liveSelected;
  byId("start-live").disabled = state.busy || !liveSelected || !hasUrl || !confirmed;
  byId("calibrate").disabled = state.busy || !liveSelected || !hasUrl || !confirmed;
  byId("inspect-plan").disabled = state.busy;
  byId("safe-stop").disabled = !state.currentRunId || !state.canStop;
  byId("resume-run").disabled = state.busy || !state.canResume || !liveSelected || !confirmed;
  byId("verify-run").disabled = !state.currentRunId || state.busy;
}

async function refreshEnvironment() {
  try {
    const data = await api("/api/ui/status");
    byId("backend-status").textContent = data.backend === "normal" ? "正常" : "不可用";
    byId("database-status").textContent = data.database === "normal" ? "正常" : "不可用";
    byId("login-status").textContent = data.browser_login === "waiting_user" ? "等待用户操作" : "尚未验证";
    byId("active-status").textContent = data.active_jobs.length ? "正在运行" : "无运行任务";
    state.recentRun = data.recent_runs[0] || null;
    if (!state.currentRunId && state.recentRun) {
      state.currentRunId = state.recentRun.id;
      loadRun(state.currentRunId).catch((error) => showToast(error.message, true));
    }
    if (!state.currentRunId && data.calibration_evidence) renderEvidence([data.calibration_evidence]);
    updateControls();
  } catch (error) {
    byId("backend-status").textContent = "连接失败";
    showToast(error.message, true);
  }
}

function statusClass(status) {
  if (status === "verified") return "status-pill status-verified";
  if (status === "unknown") return "status-pill status-review";
  if (status === "failed") return "status-pill status-failed";
  if (status === "blocked_by_policy") return "status-pill status-blocked";
  return "status-pill status-default";
}

function renderElements(items) {
  const body = byId("elements-body");
  if (!items.length) {
    const row = make("tr");
    const cell = make("td", { className: "empty-cell", text: "尚无元素状态。" });
    cell.colSpan = 5;
    row.append(cell);
    body.replaceChildren(row);
    return;
  }
  const fragment = document.createDocumentFragment();
  items.forEach((item) => {
    const row = make("tr");
    const stateCell = make("td");
    stateCell.append(make("span", { className: statusClass(item.status), text: item.friendly_status }));
    row.append(
      make("td", { text: item.name }),
      make("td", { text: item.type }),
      stateCell,
      make("td", { text: item.verified ? "已确认" : "未确认" }),
      make("td", { text: item.message })
    );
    fragment.append(row);
  });
  body.replaceChildren(fragment);
}

function renderEvidence(items) {
  const grid = byId("evidence-grid");
  const images = items.filter((item) => item.is_image && item.preview_url);
  if (!images.length) {
    grid.replaceChildren(make("p", { className: "empty-card", text: "暂无截图" }));
    return;
  }
  const fragment = document.createDocumentFragment();
  images.forEach((item) => {
    const card = make("article", { className: "evidence-card" });
    const image = make("img");
    image.src = item.preview_url;
    image.alt = `${item.kind} 截图证据`;
    image.loading = "lazy";
    const copy = make("div");
    copy.append(make("strong", { text: item.kind }), make("small", { text: item.name }));
    card.append(image, copy);
    fragment.append(card);
  });
  grid.replaceChildren(fragment);
}

const stepLabels = {
  waiting: "等待",
  running: "进行中",
  completed: "已完成",
  needs_review: "需要检查",
  blocked: "已阻止",
  failed: "失败"
};

function renderSummary(summary) {
  state.currentRunId = summary.run_id;
  state.canResume = Boolean(summary.can_resume);
  state.canStop = Boolean(summary.can_stop);
  byId("result-run-id").textContent = summary.run_id;
  byId("result-total").textContent = summary.total_elements;
  byId("result-verified").textContent = summary.verified_elements;
  byId("result-review").textContent = summary.needs_review_elements;
  byId("result-failed").textContent = summary.failed_elements;
  byId("result-blocked").textContent = summary.policy_blocked_elements;
  byId("result-save").textContent = summary.save_status;
  byId("result-workflow").textContent = `${summary.friendly_status}（${summary.status}）`;
  byId("progress-percent").textContent = `${summary.progress_percent}%`;
  byId("progress-bar").style.width = `${summary.progress_percent}%`;
  summary.steps.forEach((step) => {
    const row = query(`[data-step="${step.key}"]`);
    if (!row) return;
    row.dataset.status = step.status;
    row.querySelector("small").textContent = stepLabels[step.status] || step.status;
  });
  if (summary.status === "awaiting_confirmation") {
    byId("result-message").textContent = "绘图动作已结束，正在等待人工检查和确认；这不代表完全完成。";
  } else if (summary.needs_review_elements > 0 || summary.status === "paused_reconciliation") {
    byId("result-message").textContent = "部分元素无法可靠识别，请检查截图后决定是否继续。";
  } else {
    byId("result-message").textContent = `当前任务状态：${summary.friendly_status}。`;
  }
  byId("advanced-details").textContent = JSON.stringify(summary, null, 2);
  byId("resume-run").disabled = !state.canResume || state.busy || state.runMode !== "live" || !byId("confirm-disposable").checked;
  byId("safe-stop").disabled = !state.canStop;
  byId("verify-run").disabled = false;
}

async function loadRun(runId) {
  const [summary, elements, evidence] = await Promise.all([
    api(`/api/ui/runs/${encodeURIComponent(runId)}`),
    api(`/api/ui/runs/${encodeURIComponent(runId)}/elements`),
    api(`/api/ui/runs/${encodeURIComponent(runId)}/evidence`)
  ]);
  renderSummary(summary);
  renderElements(elements.items);
  renderEvidence(evidence.items);
}

async function inspectPlan() {
  setBusy(true);
  try {
    const summary = await api("/api/ui/plans", { method: "POST", body: JSON.stringify({ task: buildTask() }) });
    renderSummary(summary);
    await loadRun(summary.run_id);
    showToast("绘图方案已检查并保存。")
  } catch (error) {
    showToast(error.message, true);
    byId("advanced-details").textContent = JSON.stringify(error.details, null, 2);
  } finally { setBusy(false); }
}

async function startDryRun() {
  setBusy(true);
  byId("job-message").textContent = "正在执行安全预演。";
  try {
    const summary = await api("/api/ui/dry-run", { method: "POST", body: JSON.stringify({ task: buildTask() }) });
    renderSummary(summary);
    await loadRun(summary.run_id);
    byId("job-message").textContent = "安全预演已完成；未操作真实 BioRender 页面。";
    showToast("安全预演完成。")
  } catch (error) {
    byId("job-message").textContent = error.message;
    showToast(error.message, true);
  } finally { setBusy(false); }
}

async function startLive() {
  setBusy(true);
  try {
    const job = await api("/api/ui/live-runs", { method: "POST", body: JSON.stringify(livePayload()) });
    state.currentJobId = job.id;
    setBusy(true);
    state.currentRunId = job.figure_id;
    byId("job-message").textContent = job.message;
    await pollJob();
  } catch (error) {
    setBusy(false);
    showToast(error.message, true);
  }
}

async function pollJob() {
  if (!state.currentJobId) return;
  try {
    const job = await api(`/api/ui/jobs/${encodeURIComponent(state.currentJobId)}`);
    byId("job-message").textContent = job.message;
    if (job.figure_id) {
      state.currentRunId = job.figure_id;
      await loadRun(job.figure_id);
    }
    if (["completed", "failed", "blocked", "stopped"].includes(job.status)) {
      state.currentJobId = null;
      setBusy(false);
      showToast(job.message, job.status === "failed" || job.status === "blocked");
      return;
    }
    window.setTimeout(pollJob, 1500);
  } catch (error) {
    setBusy(false);
    showToast(error.message, true);
  }
}

async function checkUrl() {
  const output = byId("url-result");
  output.className = "field-message";
  try {
    const result = await api("/api/ui/check-url", { method: "POST", body: JSON.stringify({ editor_url: byId("editor-url").value.trim() }) });
    output.textContent = `${result.message} ${result.redacted_url}`;
  } catch (error) {
    output.className = "field-message error";
    output.textContent = error.message;
  }
}

async function openLogin() {
  try {
    const job = await api("/api/ui/login/open", { method: "POST", body: JSON.stringify({ confirm_manual_login: true }) });
    state.currentJobId = job.id;
    byId("complete-login").disabled = false;
    byId("job-message").textContent = "登录窗口正在打开；请只在 BioRender 官方页面手动输入账号信息。";
    showToast("请在新浏览器窗口中手动登录。")
  } catch (error) { showToast(error.message, true); }
}

async function completeLogin() {
  try {
    const job = await api("/api/ui/login/complete", { method: "POST", body: "{}" });
    byId("job-message").textContent = job.message;
    byId("complete-login").disabled = true;
    state.currentJobId = job.id;
    setBusy(true);
    await pollJob();
  } catch (error) { showToast(error.message, true); }
}

async function calibrate() {
  setBusy(true);
  try {
    const payload = { ...confirmationPayload(), editor_url: byId("editor-url").value.trim() };
    const job = await api("/api/ui/calibrate", { method: "POST", body: JSON.stringify(payload) });
    state.currentJobId = job.id;
    byId("job-message").textContent = job.message;
    await pollJob();
  } catch (error) { setBusy(false); showToast(error.message, true); }
}

async function safeStop() {
  if (!state.currentRunId) return;
  try {
    const job = await api(`/api/ui/runs/${encodeURIComponent(state.currentRunId)}/stop`, { method: "POST", body: "{}" });
    byId("job-message").textContent = job.message;
  } catch (error) { showToast(error.message, true); }
}

async function resumeRun() {
  if (!state.currentRunId) return;
  setBusy(true);
  try {
    const job = await api(`/api/ui/runs/${encodeURIComponent(state.currentRunId)}/resume`, { method: "POST", body: JSON.stringify(confirmationPayload()) });
    state.currentJobId = job.id;
    await pollJob();
  } catch (error) { setBusy(false); showToast(error.message, true); }
}

async function verifyRun() {
  if (!state.currentRunId) return;
  try {
    const result = await api(`/api/ui/runs/${encodeURIComponent(state.currentRunId)}/verify`, { method: "POST", body: "{}" });
    renderSummary(result);
    byId("result-message").textContent = result.message;
    showToast(result.verification_passed ? "现有证据验证通过。" : "验证发现需要检查的内容。", !result.verification_passed);
  } catch (error) { showToast(error.message, true); }
}

queryAll('input[name="task-mode"]').forEach((input) => input.addEventListener("change", () => {
  state.taskMode = input.value;
  byId("preset-panel").hidden = state.taskMode !== "preset";
  byId("custom-panel").hidden = state.taskMode !== "custom";
}));

queryAll('input[name="run-mode"]').forEach((input) => input.addEventListener("change", () => {
  state.runMode = input.value;
  byId("live-confirmation").hidden = state.runMode !== "live";
  updateControls();
}));

byId("editor-url").addEventListener("input", updateControls);
byId("confirm-disposable").addEventListener("change", updateControls);
byId("add-asset").addEventListener("click", addAsset);
byId("add-relation").addEventListener("click", addRelation);
byId("refresh-status").addEventListener("click", refreshEnvironment);
byId("check-url").addEventListener("click", checkUrl);
byId("open-login").addEventListener("click", openLogin);
byId("complete-login").addEventListener("click", completeLogin);
byId("calibrate").addEventListener("click", calibrate);
byId("inspect-plan").addEventListener("click", inspectPlan);
byId("start-dry").addEventListener("click", startDryRun);
byId("start-live").addEventListener("click", startLive);
byId("safe-stop").addEventListener("click", safeStop);
byId("resume-run").addEventListener("click", resumeRun);
byId("verify-run").addEventListener("click", verifyRun);

renderAssets();
renderRelations();
updateControls();
refreshEnvironment();
window.setInterval(refreshEnvironment, 10000);
