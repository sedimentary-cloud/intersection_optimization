import { api } from './api.js';
import { buildDefaultConstraintDraft, buildDefaultPlan } from './defaults.js';
import { renderAuditPanel, renderConstraintEditor, renderConstraintList } from './constraints.js';
import { renderResults } from './results.js';
import { deepClone, state } from './state.js';
import { renderTables } from './tables.js';
import { renderTopology } from './topology.js';

const $ = (id) => document.getElementById(id);
const PLAN_STORAGE_KEY = 'signal-timing-web-plans';

function toast(message, type = 'info', timeout = 3200) {
  const node = $('toast');
  if (!node) return;
  node.textContent = message;
  node.style.background = type === 'error' ? '#991b1b' : (type === 'warn' ? '#92400e' : '#0f172a');
  node.hidden = false;
  clearTimeout(toast.__timer);
  toast.__timer = setTimeout(() => { node.hidden = true; }, timeout);
}

function setText(id, text) {
  const node = $(id);
  if (node) node.textContent = text;
}

function setBadge(id, text, cls) {
  const node = $(id);
  if (!node) return;
  node.textContent = text;
  node.className = `badge ${cls}`;
}

function buildPayload() {
  return {
    intersection: deepClone(state.intersection),
    constraints: deepClone(state.constraints),
    solver: deepClone(state.solver),
  };
}

function phaseLabel(phaseId) {
  const phase = state.intersection?.phases?.find((p) => p.id === phaseId);
  if (!phase) return phaseId;
  const movements = Object.keys(phase.capacity || {});
  return movements.length ? `${phase.id} · ${movements.join(', ')}` : phase.id;
}

// ---------------------------------------------------------------------------
// 渲染：左栏
// ---------------------------------------------------------------------------
function renderPhaseList() {
  const node = $('phase-list');
  if (!node) return;
  const phases = state.intersection?.phases || [];
  node.innerHTML = phases.map((phase) => `
    <div class="list-item ${state.selectedPhaseId === phase.id ? 'active' : ''}" data-phase="${escapeHtml(phase.id)}">
      <div>
        <div>${escapeHtml(phase.id)}</div>
        <small>${Object.keys(phase.capacity || {}).length} 个流向</small>
      </div>
      <span class="delete-link" data-action="delete-phase" data-phase="${escapeHtml(phase.id)}">删除</span>
    </div>`).join('') || '<div class="hint">暂无相位</div>';

  node.querySelectorAll('.list-item').forEach((row) => {
    row.addEventListener('click', (event) => {
      const phaseId = row.dataset.phase;
      if (event.target.dataset.action === 'delete-phase') {
        deletePhase(phaseId);
        return;
      }
      state.selectedPhaseId = phaseId;
      state.selectedMovementId = null;
      renderPhaseList();
      renderProperty();
      renderTopology();
    });
  });
}

function renderProperty() {
  const node = $('property-panel');
  if (!node) return;
  if (state.selectedMovementId) {
    const movement = state.intersection.movements.find((m) => m.id === state.selectedMovementId);
    if (!movement) {
      state.selectedMovementId = null;
      renderProperty();
      return;
    }
    const servers = state.intersection.phases.filter((p) => Number(p.capacity?.[movement.id] || 0) > 0);
    node.innerHTML = `
      <div class="section-head"><span>属性 · 流向</span></div>
      <div class="form-row">
        <label>id<input id="prop-movement-id" value="${escapeHtml(movement.id)}" /></label>
        <label>需求 (veh/h)<input id="prop-movement-demand" type="number" min="0.001" step="1" value="${escapeHtml(movement.demand)}" /></label>
      </div>
      <div class="hint">服务该流向的相位：${servers.map((p) => `<span class="status-pill ok">${escapeHtml(p.id)}</span>`).join(' ') || '<span class="status-pill bad">无</span>'}</div>
      <div class="button-row">
        <button class="btn btn-ghost danger" id="prop-delete-movement">删除流向</button>
      </div>`;
    $('prop-movement-id').addEventListener('change', (event) => renameMovement(movement.id, event.target.value.trim()));
    $('prop-movement-demand').addEventListener('change', (event) => {
      movement.demand = Number(event.target.value);
      renderTopology();
      renderPhaseList();
    });
    $('prop-delete-movement').addEventListener('click', () => deleteMovement(movement.id));
    return;
  }
  if (state.selectedPhaseId) {
    const phase = state.intersection.phases.find((p) => p.id === state.selectedPhaseId);
    if (!phase) {
      state.selectedPhaseId = null;
      renderProperty();
      return;
    }
    const rows = state.intersection.movements.map((movement) => `
      <tr>
        <td>${escapeHtml(movement.id)}</td>
        <td><input type="number" min="0" step="10" data-prop-capacity="${escapeHtml(movement.id)}" value="${Number(phase.capacity?.[movement.id] || 0) || ''}" placeholder="不服务" /></td>
      </tr>`).join('');
    node.innerHTML = `
      <div class="section-head"><span>属性 · 相位</span></div>
      <div class="form-row"><label>id<input id="prop-phase-id" value="${escapeHtml(phase.id)}" /></label></div>
      <div class="hint">在矩阵中输入饱和流率 a_pm (veh/h)；0/空表示不服务。</div>
      <table class="data-table" style="margin-top:7px"><thead><tr><th>流向</th><th>a_pm</th></tr></thead><tbody>${rows || '<tr><td colspan="2">暂无流向</td></tr>'}</tbody></table>
      <div class="button-row"><button class="btn btn-ghost danger" id="prop-delete-phase">删除相位</button></div>`;
    $('prop-phase-id').addEventListener('change', (event) => renamePhase(phase.id, event.target.value.trim()));
    node.querySelectorAll('[data-prop-capacity]').forEach((input) => {
      input.addEventListener('change', () => {
        const movementId = input.dataset.propCapacity;
        const value = Number(input.value);
        if (Number.isFinite(value) && value > 0) phase.capacity[movementId] = value;
        else delete phase.capacity[movementId];
        renderTopology();
        renderPhaseList();
        renderTables();
      });
    });
    $('prop-delete-phase').addEventListener('click', () => deletePhase(phase.id));
    return;
  }
  node.innerHTML = '<div class="section-head"><span>属性</span></div><div class="hint">选择拓扑中的车道或表格中的行以编辑。</div>';
}

function renderTask() {
  const task = state.task;
  const mini = $('task-mini-status');
  const miniBar = $('task-mini-progress');
  const solveStatus = $('solve-status');
  const solveBar = $('solve-progress');
  const cancelBtn = $('btn-cancel');
  if (!task) {
    if (mini) mini.textContent = '暂无求解任务';
    if (miniBar) miniBar.style.width = '0%';
    if (solveStatus) solveStatus.textContent = '尚未提交任务';
    if (solveBar) { solveBar.style.width = '0%'; solveBar.className = 'progress-bar'; }
    if (cancelBtn) cancelBtn.disabled = true;
    $('task-logs').textContent = '—';
    return;
  }
  const statusText = `${task.status || 'queued'} · ${task.stage || ''} · ${task.message || ''}`;
  if (mini) mini.textContent = statusText;
  if (solveStatus) solveStatus.textContent = statusText;
  const widthMap = { queued: 8, preparing: 25, solving: 70, done: 100, error: 100, cancelled: 100, failed: 100, infeasible: 100 };
  const width = widthMap[task.stage] ?? (task.status === 'succeeded' ? 100 : 35);
  for (const bar of [miniBar, solveBar]) {
    if (!bar) continue;
    bar.style.width = `${width}%`;
    bar.className = 'progress-bar';
    if (task.status === 'running' && task.stage === 'solving') bar.className = 'progress-bar indeterminate';
    if (['failed', 'infeasible'].includes(task.status)) bar.style.background = 'linear-gradient(90deg,#f87171,#dc2626)';
    else bar.style.background = '';
  }
  if (cancelBtn) cancelBtn.disabled = ['succeeded', 'failed', 'infeasible', 'cancelled'].includes(task.status);
  const logs = $('task-logs');
  if (logs) logs.textContent = (task.logs || []).join('\n') || '—';
}

// ---------------------------------------------------------------------------
// 渲染：求解控制台
// ---------------------------------------------------------------------------
function referenceLayerGroups(layerMap, phases) {
  const groups = new Map();
  phases.forEach((phase, index) => {
    const raw = Number(layerMap.get(phase.id));
    const layer = Number.isFinite(raw) ? Math.max(1, Math.round(raw)) : index + 1;
    if (!groups.has(layer)) groups.set(layer, []);
    groups.get(layer).push(phase.id);
  });
  return [...groups.entries()].sort((a, b) => a[0] - b[0]);
}

function buildReferenceOrder(layerMap, phases) {
  return referenceLayerGroups(layerMap, phases).map(([, phaseIds]) => (
    phaseIds.length === 1 ? phaseIds[0] : [...phaseIds]
  ));
}

function normalizeReferenceLayerMap(layerMap, phases) {
  const normalized = new Map();
  referenceLayerGroups(layerMap, phases).forEach(([, phaseIds], index) => {
    phaseIds.forEach((phaseId) => normalized.set(phaseId, index + 1));
  });
  return normalized;
}

function buildReferenceLayerMap(referenceOrder, phases) {
  const layerMap = new Map();
  let maxLayer = 0;
  if (Array.isArray(referenceOrder)) {
    referenceOrder.forEach((item, index) => {
      const group = Array.isArray(item) ? item : [item];
      group.forEach((phaseId) => {
        if (phases.some((phase) => phase.id === phaseId)) {
          layerMap.set(phaseId, index + 1);
          maxLayer = Math.max(maxLayer, index + 1);
        }
      });
    });
  }
  let nextLayer = maxLayer + 1;
  phases.forEach((phase) => {
    if (!layerMap.has(phase.id)) {
      layerMap.set(phase.id, nextLayer);
      nextLayer += 1;
    }
  });
  return layerMap;
}

function referencePreviewHtml(layerMap, phases) {
  const groups = referenceLayerGroups(layerMap, phases);
  if (!groups.length) return '<span class="muted">暂无相位</span>';
  return groups.map(([layer, phaseIds]) => (
    `<span class="ref-preview-layer"><b>第${layer}层</b>${escapeHtml(phaseIds.join(' / '))}</span>`
  )).join('<span class="muted"> → </span>');
}

function renderReferenceOrderEditor() {
  const wrap = $('solver-ref-editor');
  if (!wrap) return;
  const phases = state.intersection?.phases || [];
  if (!phases.length) {
    wrap.innerHTML = '<div class="hint">暂无相位，无法编辑参考顺序。</div>';
    return;
  }
  let layerMap = buildReferenceLayerMap(state.solver.reference_order, phases);
  layerMap = normalizeReferenceLayerMap(layerMap, phases);
  const commit = () => {
    state.solver.reference_order = buildReferenceOrder(layerMap, phases);
  };
  const render = () => {
    wrap.innerHTML = `
      <table class="data-table ref-table">
        <thead><tr><th>相位</th><th>层级</th><th>调整</th></tr></thead>
        <tbody>
          ${phases.map((phase) => {
            const currentLayer = layerMap.get(phase.id) || 1;
            return `<tr>
              <td>${escapeHtml(phase.id)}</td>
              <td><input type="number" min="1" step="1" value="${currentLayer}" data-ref-layer="${escapeHtml(phase.id)}" /></td>
              <td>
                <button type="button" class="mini-btn" data-ref-move="up" data-ref-id="${escapeHtml(phase.id)}">↑ 上移</button>
                <button type="button" class="mini-btn" data-ref-move="down" data-ref-id="${escapeHtml(phase.id)}">↓ 下移</button>
              </td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
      <div class="ref-preview">${referencePreviewHtml(layerMap, phases)}</div>
      <div class="hint">同一层内的相位可以互换；层号越小越靠前。修改后自动生成 <code>reference_order</code>，不需要手写 JSON。</div>`;

    wrap.querySelectorAll('[data-ref-layer]').forEach((input) => {
      input.addEventListener('change', () => {
        const phaseId = input.dataset.refLayer;
        const value = Math.max(1, Math.round(Number(input.value) || 1));
        layerMap.set(phaseId, value);
        layerMap = normalizeReferenceLayerMap(layerMap, phases);
        commit();
        render();
      });
    });

    wrap.querySelectorAll('[data-ref-move]').forEach((button) => {
      button.addEventListener('click', () => {
        const phaseId = button.dataset.refId;
        const currentLayer = layerMap.get(phaseId) || 1;
        const nextLayer = button.dataset.refMove === 'up'
          ? Math.max(1, currentLayer - 1)
          : currentLayer + 1;
        layerMap.set(phaseId, nextLayer);
        layerMap = normalizeReferenceLayerMap(layerMap, phases);
        commit();
        render();
      });
    });
  };
  render();
  commit();
}

function renderSolverEditor() {
  const node = $('solver-editor');
  if (!node) return;
  const solver = state.solver || {};
  node.innerHTML = `
    <div class="form-grid">
      <label>fixed_cycle<input id="solver-fixed" type="number" min="0" step="0.1" value="${solver.fixed_cycle ?? ''}" placeholder="留空=自由周期" /></label>
      <label>time_limit (s)<input id="solver-time-limit" type="number" min="0.1" step="1" value="${solver.time_limit}" /></label>
      <label>eps_cycle<input id="solver-eps-cycle" type="number" min="0" step="0.001" value="${solver.eps_cycle}" /></label>
      <label>eps_waste<input id="solver-eps-waste" type="number" min="0" step="0.001" value="${solver.eps_waste}" /></label>
      <label>waste_abs_tol<input id="solver-waste-abs" type="number" min="0" step="0.1" value="${solver.waste_abs_tol ?? ''}" placeholder="空=算法默认" /></label>
      <label>mip_rel_gap<input id="solver-mip-gap" type="number" min="0" step="0.0001" value="${solver.mip_rel_gap}" /></label>
      <label>参考顺序模式<select id="solver-ref-mode">
        <option value="off" ${solver.reference_order_mode === 'off' ? 'selected' : ''}>off</option>
        <option value="hard" ${solver.reference_order_mode === 'hard' ? 'selected' : ''}>hard</option>
        <option value="soft" ${solver.reference_order_mode === 'soft' ? 'selected' : ''}>soft</option>
        <option value="prefer" ${solver.reference_order_mode === 'prefer' ? 'selected' : ''}>prefer</option>
      </select></label>
      <label>max_fallback<input id="solver-max-fallback" type="number" min="0" step="1" value="${solver.max_fallback}" /></label>
    </div>
    <div class="form-row">
      <label>reference_order（相位层级）</label>
      <div id="solver-ref-editor"></div>
    </div>
    <div class="form-row">
      <label>order_rules (JSON 数组)<textarea id="solver-rules" placeholder='[{"type":"precedence","before":"P1","after":"P2"}]'>${escapeHtml(JSON.stringify(solver.order_rules || []))}</textarea></label>
    </div>
    <div class="form-grid">
      <label class="checkline"><input id="solver-run-ordering" type="checkbox" ${solver.run_ordering ? 'checked' : ''} /> 运行排序</label>
      <label class="checkline"><input id="solver-cycle-reduction" type="checkbox" ${solver.allow_cycle_reduction ? 'checked' : ''} /> 允许周期回收</label>
      <label class="checkline"><input id="solver-zero-slack" type="checkbox" ${solver.enforce_zero_slack ? 'checked' : ''} /> 零余量配时</label>
      <label class="checkline"><input id="solver-disp" type="checkbox" ${solver.disp ? 'checked' : ''} /> 输出 HiGHS 日志</label>
    </div>`;

  const read = () => {
    const fixed = $('solver-fixed').value;
    state.solver.fixed_cycle = fixed === '' ? null : Number(fixed);
    state.solver.time_limit = Number($('solver-time-limit').value);
    state.solver.eps_cycle = Number($('solver-eps-cycle').value);
    state.solver.eps_waste = Number($('solver-eps-waste').value);
    const abs = $('solver-waste-abs').value;
    state.solver.waste_abs_tol = abs === '' ? null : Number(abs);
    state.solver.mip_rel_gap = Number($('solver-mip-gap').value);
    state.solver.reference_order_mode = $('solver-ref-mode').value;
    state.solver.max_fallback = Number($('solver-max-fallback').value);
    state.solver.run_ordering = $('solver-run-ordering').checked;
    state.solver.allow_cycle_reduction = $('solver-cycle-reduction').checked;
    state.solver.enforce_zero_slack = $('solver-zero-slack').checked;
    state.solver.disp = $('solver-disp').checked;
    try {
      const rulesText = $('solver-rules').value.trim();
      state.solver.order_rules = rulesText ? JSON.parse(rulesText) : [];
    } catch (err) {
      toast('order_rules JSON 格式错误', 'error');
    }
  };
  node.querySelectorAll('input, select, textarea').forEach((element) => {
    if (element.dataset.refLayer !== undefined) return;
    element.addEventListener('change', read);
    if (element.tagName === 'INPUT' && element.type !== 'checkbox') element.addEventListener('input', read);
  });
  renderReferenceOrderEditor();
}

// ---------------------------------------------------------------------------
// 结构编辑辅助
// ---------------------------------------------------------------------------
function renameMovement(oldId, nextId) {
  if (!nextId || nextId === oldId) return;
  if (state.intersection.movements.some((m) => m.id === nextId)) {
    toast(`流向 id ${nextId} 已存在`, 'warn');
    return;
  }
  const movement = state.intersection.movements.find((m) => m.id === oldId);
  if (!movement) return;
  movement.id = nextId;
  for (const phase of state.intersection.phases) {
    if (Object.prototype.hasOwnProperty.call(phase.capacity, oldId)) {
      phase.capacity[nextId] = phase.capacity[oldId];
      delete phase.capacity[oldId];
    }
  }
  state.intersection.lost_time = state.intersection.lost_time.map(([a, b, t]) => [a === oldId ? nextId : a, b === oldId ? nextId : b, t]);
  state.selectedMovementId = nextId;
  renderAll();
}

function deleteMovement(movementId) {
  state.intersection.movements = state.intersection.movements.filter((m) => m.id !== movementId);
  state.intersection.phases.forEach((phase) => { delete phase.capacity[movementId]; });
  state.intersection.lost_time = state.intersection.lost_time.filter(([a, b]) => a !== movementId && b !== movementId);
  if (state.selectedMovementId === movementId) state.selectedMovementId = null;
  renderAll();
}

function renamePhase(oldId, nextId) {
  if (!nextId || nextId === oldId) return;
  if (state.intersection.phases.some((p) => p.id === nextId)) {
    toast(`相位 id ${nextId} 已存在`, 'warn');
    return;
  }
  const phase = state.intersection.phases.find((p) => p.id === oldId);
  if (!phase) return;
  phase.id = nextId;
  state.intersection.lost_time = state.intersection.lost_time.map(([a, b, t]) => [a === oldId ? nextId : a, b === oldId ? nextId : b, t]);
  state.solver.reference_order = (state.solver.reference_order || []).map((group) => {
    if (Array.isArray(group)) return group.map((phaseId) => (phaseId === oldId ? nextId : phaseId));
    return group === oldId ? nextId : group;
  });
  state.selectedPhaseId = nextId;
  renderAll();
  renderSolverEditor();
}

function deletePhase(phaseId) {
  state.intersection.phases = state.intersection.phases.filter((p) => p.id !== phaseId);
  state.intersection.lost_time = state.intersection.lost_time.filter(([a, b]) => a !== phaseId && b !== phaseId);
  state.solver.reference_order = (state.solver.reference_order || [])
    .map((group) => {
      if (Array.isArray(group)) return group.filter((itemId) => itemId !== phaseId);
      return group === phaseId ? null : group;
    })
    .filter((group) => group !== null && (!Array.isArray(group) || group.length > 0));
  if (!state.solver.reference_order.length) state.solver.reference_order = null;
  if (state.selectedPhaseId === phaseId) state.selectedPhaseId = null;
  renderAll();
  renderSolverEditor();
}

function addPhase() {
  const id = prompt('新相位 id：', `P${state.intersection.phases.length + 1}`);
  if (!id) return;
  if (state.intersection.phases.some((p) => p.id === id)) {
    toast('相位 id 已存在', 'warn');
    return;
  }
  const firstMovement = state.intersection.movements[0]?.id;
  state.intersection.phases.push({ id, capacity: firstMovement ? { [firstMovement]: 1800 } : {} });
  renderAll();
  renderSolverEditor();
}

let movementIdTouched = false;

function movementDirectionOf(mid) {
  const first = String(mid || '').trim().toUpperCase()[0];
  return ['N', 'S', 'E', 'W'].includes(first) ? first : 'N';
}

function nextMovementId(direction, type) {
  const typePart = type === 'left' ? 'left' : 'thr';
  const existing = new Set(state.intersection.movements.map((movement) => movement.id));
  let base = `${direction}_${typePart}`;
  let candidate = base;
  let suffix = 2;
  while (existing.has(candidate)) {
    candidate = `${base}_${suffix}`;
    suffix += 1;
  }
  return candidate;
}

function setSelectValue(select, value) {
  if (!select) return;
  try {
    select.value = value;
  } catch (err) {
    // 部分非浏览器 DOM 实现不暴露 value setter；回退到 selectedIndex。
  }
  const options = Array.from(select.options || []);
  const index = options.findIndex((option) => option.value === String(value));
  if (index >= 0) select.selectedIndex = index;
}

function refreshMovementModal() {
  const direction = $('movement-direction')?.value || 'N';
  const type = $('movement-type')?.value || 'left';
  if (!movementIdTouched) {
    const idInput = $('movement-id');
    if (idInput) idInput.value = nextMovementId(direction, type);
  }
  const phaseSelect = $('movement-phase');
  if (!phaseSelect) return;
  const phases = state.intersection?.phases || [];
  const current = phaseSelect.value;
  phaseSelect.innerHTML = '<option value="">暂不指定（后续在相位矩阵中配置）</option>'
    + phases.map((phase) => `<option value="${escapeHtml(phase.id)}" ${phase.id === current ? 'selected' : ''}>${escapeHtml(phase.id)}</option>`).join('');
}

function openMovementDialog() {
  if (!state.intersection) return;
  movementIdTouched = false;
  const directionSelect = $('movement-direction');
  const typeSelect = $('movement-type');
  setSelectValue(directionSelect, movementDirectionOf(state.selectedMovementId || 'N_left'));
  setSelectValue(typeSelect, String(state.selectedMovementId || '').toLowerCase().includes('left') ? 'left' : 'through');
  const idInput = $('movement-id');
  if (idInput) idInput.value = '';
  const demandInput = $('movement-demand');
  if (demandInput) demandInput.value = '300';
  refreshMovementModal();
  const modal = $('movement-modal');
  if (modal) modal.hidden = false;
  idInput?.focus?.();
  if (typeof idInput?.select === 'function') idInput.select();
}

function closeMovementDialog() {
  const modal = $('movement-modal');
  if (modal) modal.hidden = true;
}

function confirmMovementDialog() {
  const id = $('movement-id')?.value.trim() || '';
  const demand = Number($('movement-demand')?.value || 0);
  const phaseId = $('movement-phase')?.value || '';
  if (!id) {
    toast('请填写流向 id', 'error');
    return;
  }
  if (state.intersection.movements.some((movement) => movement.id === id)) {
    toast(`流向 id ${id} 已存在`, 'warn');
    return;
  }
  if (!(Number.isFinite(demand) && demand > 0)) {
    toast('需求必须大于 0', 'error');
    return;
  }
  state.intersection.movements.push({ id, demand });
  if (phaseId) {
    const phase = state.intersection.phases.find((item) => item.id === phaseId);
    if (phase) phase.capacity[id] = 1800;
  }
  state.selectedMovementId = id;
  state.selectedPhaseId = null;
  closeMovementDialog();
  renderAll();
  toast(`已添加流向 ${id}`, 'info');
}

// ---------------------------------------------------------------------------
// 约束交互
// ---------------------------------------------------------------------------
async function performAudit(constraint) {
  try {
    const response = await api.audit({ intersection: deepClone(state.intersection), constraint: deepClone(constraint) });
    if (!response.ok) {
      state.audit = null;
      renderAuditPanel($('audit-panel'), state);
      toast((response.errors || []).map((e) => e.message).join('; ') || '审计失败', 'error');
      return;
    }
    state.audit = response.report;
    renderAuditPanel($('audit-panel'), state);
    toast(response.report?.trap_subsets?.length ? '审计发现恒违反陷阱' : '审计完成', response.report?.trap_subsets?.length ? 'warn' : 'info');
  } catch (err) {
    toast(`审计失败：${err.message}`, 'error');
  }
}

function renderConstraints() {
  renderConstraintList($('constraint-list'), state, {
    onEditConstraint(index) {
      state.editingConstraintIndex = index;
      state.constraintDraft = deepClone(state.constraints[index]);
      renderConstraints();
      renderAuditPanel($('audit-panel'), state);
    },
    onDeleteConstraint(index) {
      state.constraints.splice(index, 1);
      if (state.editingConstraintIndex === index) {
        state.editingConstraintIndex = -1;
        state.constraintDraft = buildDefaultConstraintDraft();
      } else if (state.editingConstraintIndex > index) {
        state.editingConstraintIndex -= 1;
      }
      renderConstraints();
    },
    onAuditConstraint(index) {
      performAudit(state.constraints[index]);
    },
  });
  renderConstraintEditor($('constraint-editor'), state, {
    onDraftChange() {},
    onDraftStructuralChange() {
      renderConstraintEditor($('constraint-editor'), state, this);
    },
    onValidationError(message) {
      toast(message, 'error');
    },
    onSaveConstraint(draft) {
      if (state.editingConstraintIndex >= 0) state.constraints[state.editingConstraintIndex] = draft;
      else state.constraints.push(draft);
      state.editingConstraintIndex = -1;
      state.constraintDraft = buildDefaultConstraintDraft();
      renderConstraints();
      performAudit(draft);
    },
    onClearConstraint() {
      state.editingConstraintIndex = -1;
      state.constraintDraft = buildDefaultConstraintDraft();
      renderConstraints();
    },
    onAuditDraft(draft) {
      performAudit(draft);
    },
  });
}

// ---------------------------------------------------------------------------
// 服务端交互
// ---------------------------------------------------------------------------
async function checkHealth() {
  try {
    const health = await api.health();
    setBadge('health-badge', `服务正常 · v${health.version}`, 'badge-ok');
  } catch (err) {
    setBadge('health-badge', '服务不可用', 'badge-error');
  }
}

async function validateCurrent() {
  try {
    const response = await api.validate(buildPayload());
    state.validation = response;
    const text = response.ok
      ? `校验通过：${response.summary?.movement_count || 0} 流向 / ${response.summary?.phase_count || 0} 相位 / ${response.summary?.constraint_count || 0} 约束`
      : `校验失败：${(response.errors || []).map((e) => e.message).join('；')}`;
    setText('solve-status', text);
    toast(text, response.ok ? 'info' : 'error');
    return response;
  } catch (err) {
    toast(`校验请求失败：${err.message}`, 'error');
    return null;
  }
}

function stopTaskPolling() {
  if (state.taskTimer) {
    clearInterval(state.taskTimer);
    state.taskTimer = null;
  }
}

async function pollTask(taskId) {
  try {
    const task = await api.task(taskId);
    state.task = task;
    renderTask();
    if (task.status === 'succeeded') {
      stopTaskPolling();
      const response = await api.result(taskId);
      state.result = response.result;
      renderResults($('result-body'), state);
      setBadge('result-status', '求解成功', 'badge-ok');
      toast('求解完成，结果已渲染', 'info');
    } else if (['failed', 'infeasible', 'cancelled'].includes(task.status)) {
      stopTaskPolling();
      const message = task.error?.message || task.message || task.status;
      setText('solve-status', `任务结束：${task.status} · ${message}`);
      setBadge('result-status', task.status, task.status === 'infeasible' ? 'badge-warn' : 'badge-error');
      toast(message, 'error');
    }
  } catch (err) {
    stopTaskPolling();
    toast(`轮询失败：${err.message}`, 'error');
  }
}

async function submitSolve() {
  const validation = await validateCurrent();
  if (!validation?.ok) return;
  try {
    const response = await api.optimize(buildPayload());
    state.result = null;
    state.task = { ...response, logs: [`${new Date().toLocaleTimeString()} 任务已提交`] };
    renderTask();
    renderResults($('result-body'), state);
    setBadge('result-status', '求解中', 'badge-running');
    stopTaskPolling();
    state.taskTimer = setInterval(() => pollTask(response.task_id), 900);
  } catch (err) {
    const detail = err.body?.error?.detail?.errors;
    toast(detail ? detail.map((item) => item.message).join('；') : `提交失败：${err.message}`, 'error');
  }
}

async function cancelCurrent() {
  if (!state.task?.task_id) return;
  try {
    const task = await api.cancel(state.task.task_id);
    state.task = task;
    renderTask();
    toast('取消请求已记录（轮内不可中断）', 'warn');
  } catch (err) {
    toast(`取消失败：${err.message}`, 'error');
  }
}

// ---------------------------------------------------------------------------
// 方案管理
// ---------------------------------------------------------------------------
function loadPlans() {
  try {
    state.plans = JSON.parse(localStorage.getItem(PLAN_STORAGE_KEY) || '{}');
  } catch (err) {
    state.plans = {};
  }
  const select = $('plan-select');
  if (!select) return;
  select.innerHTML = '<option value="">— 选择方案 —</option>' + Object.keys(state.plans).sort().map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join('');
}

function persistPlans() {
  localStorage.setItem(PLAN_STORAGE_KEY, JSON.stringify(state.plans));
  loadPlans();
}

function savePlan() {
  const name = prompt('方案名称：', `方案 ${new Date().toLocaleString()}`);
  if (!name) return;
  state.plans[name] = buildPayload();
  persistPlans();
  toast(`方案「${name}」已保存到 localStorage`, 'info');
}

function deletePlan() {
  const name = $('plan-select')?.value;
  if (!name) return;
  if (!confirm(`删除方案「${name}」？`)) return;
  delete state.plans[name];
  persistPlans();
  toast('方案已删除', 'info');
}

function applyPlan(plan) {
  state.intersection = deepClone(plan.intersection);
  state.constraints = deepClone(plan.constraints || []);
  state.solver = { ...buildDefaultPlan().solver, ...(plan.solver || {}) };
  state.selectedMovementId = null;
  state.selectedPhaseId = null;
  state.editingConstraintIndex = -1;
  state.constraintDraft = buildDefaultConstraintDraft();
  state.validation = null;
  state.audit = null;
  state.task = null;
  state.result = null;
  stopTaskPolling();
  renderAll();
  renderSolverEditor();
}

function exportPlan() {
  const blob = new Blob([JSON.stringify(buildPayload(), null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = 'signal-timing-plan.json';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function importPlanFile(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const plan = JSON.parse(reader.result);
      applyPlan(plan);
      toast('方案导入成功', 'info');
    } catch (err) {
      toast(`导入失败：${err.message}`, 'error');
    }
  };
  reader.readAsText(file);
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

// ---------------------------------------------------------------------------
// 总渲染与事件绑定
// ---------------------------------------------------------------------------
function onTopologySelect(movementId) {
  state.selectedMovementId = movementId;
  state.selectedPhaseId = null;
  renderTopology($('topology-svg-wrap'), state, onTopologySelect);
  renderProperty();
  renderTables($('tables-body'), state, tableCallbacks);
}

const tableCallbacks = {
  onChange(reason) {
    renderTopology($('topology-svg-wrap'), state, onTopologySelect);
    renderPhaseList();
    renderProperty();
    if (
      reason === 'movement-delete'
      || reason === 'movement-id'
      || reason === 'lost_time'
      || reason === 'strict-clearance'
    ) {
      renderTables($('tables-body'), state, tableCallbacks);
    }
  },
  onSelectMovement(movementId) {
    state.selectedMovementId = movementId;
    state.selectedPhaseId = null;
    renderTopology($('topology-svg-wrap'), state, onTopologySelect);
    renderProperty();
  },
};

function renderAll() {
  renderPhaseList();
  renderConstraints();
  renderProperty();
  renderTopology($('topology-svg-wrap'), state, onTopologySelect);
  renderTables($('tables-body'), state, tableCallbacks);
  renderAuditPanel($('audit-panel'), state);
  renderResults($('result-body'), state);
  renderTask();
}

function bindEvents() {
  $('btn-add-phase')?.addEventListener('click', addPhase);
  $('btn-add-movement')?.addEventListener('click', openMovementDialog);
  $('btn-add-movement-topology')?.addEventListener('click', openMovementDialog);
  $('btn-close-movement-modal')?.addEventListener('click', closeMovementDialog);
  $('btn-cancel-movement')?.addEventListener('click', closeMovementDialog);
  $('btn-confirm-movement')?.addEventListener('click', confirmMovementDialog);
  $('movement-direction')?.addEventListener('change', refreshMovementModal);
  $('movement-type')?.addEventListener('change', refreshMovementModal);
  $('movement-id')?.addEventListener('input', () => { movementIdTouched = true; });
  $('movement-modal')?.addEventListener('click', (event) => {
    if (event.target.id === 'movement-modal') closeMovementDialog();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !$('movement-modal')?.hidden) closeMovementDialog();
  });
  $('btn-new-constraint')?.addEventListener('click', () => {
    state.editingConstraintIndex = -1;
    state.constraintDraft = buildDefaultConstraintDraft();
    renderConstraints();
  });
  $('btn-reset-constraint')?.addEventListener('click', () => {
    state.editingConstraintIndex = -1;
    state.constraintDraft = buildDefaultConstraintDraft();
    renderConstraints();
  });
  $('btn-validate')?.addEventListener('click', validateCurrent);
  $('btn-solve')?.addEventListener('click', submitSolve);
  $('btn-cancel')?.addEventListener('click', cancelCurrent);
  $('btn-toggle-results')?.addEventListener('click', () => {
    const collapsed = document.body.classList.toggle('results-collapsed');
    const button = $('btn-toggle-results');
    if (button) button.textContent = collapsed ? '展开结果' : '折叠结果';
    setTimeout(() => {
      document.querySelectorAll('.chart').forEach((element) => {
        window.echarts?.getInstanceByDom(element)?.resize();
      });
    }, 80);
  });
  $('btn-save-plan')?.addEventListener('click', savePlan);
  $('btn-delete-plan')?.addEventListener('click', deletePlan);
  $('btn-export')?.addEventListener('click', exportPlan);
  $('btn-load-example')?.addEventListener('click', () => {
    applyPlan(buildDefaultPlan());
    toast('已载入默认四进口示例', 'info');
  });
  $('plan-select')?.addEventListener('change', (event) => {
    const name = event.target.value;
    if (name && state.plans[name]) applyPlan(state.plans[name]);
  });
  $('import-file')?.addEventListener('change', (event) => {
    const file = event.target.files?.[0];
    if (file) importPlanFile(file);
    event.target.value = '';
  });
  document.querySelectorAll('.view-nav button').forEach((button) => {
    button.addEventListener('click', () => {
      document.querySelectorAll('.view-nav button').forEach((item) => item.classList.remove('active'));
      document.querySelectorAll('.view').forEach((view) => view.classList.remove('active'));
      button.classList.add('active');
      state.activeView = button.dataset.view;
      $(`${state.activeView}-view`)?.classList.add('active');
      if (state.activeView === 'tables') renderTables($('tables-body'), state, tableCallbacks);
    });
  });
  document.querySelectorAll('.result-tabs button').forEach((button) => {
    button.addEventListener('click', () => {
      document.querySelectorAll('.result-tabs button').forEach((item) => item.classList.remove('active'));
      button.classList.add('active');
      state.activeResultTab = button.dataset.tab;
      renderResults($('result-body'), state);
    });
  });
}

function init() {
  const plan = buildDefaultPlan();
  state.intersection = plan.intersection;
  state.constraints = plan.constraints;
  state.solver = plan.solver;
  state.constraintDraft = buildDefaultConstraintDraft();
  loadPlans();
  renderSolverEditor();
  renderAll();
  bindEvents();
  checkHealth();
}

init();
