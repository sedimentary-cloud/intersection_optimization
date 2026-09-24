import { buildDefaultConstraintDraft } from './defaults.js';
import { deepClone } from './state.js';

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

function constraintSummary(constraint) {
  const terms = (constraint.coeffs || []).map(([kind, name, value]) => `${Number(value).toFixed(3)}·${kind}${name ? ':' + name : ''}`).join(' + ');
  const trigger = constraint.trigger?.kind === 'always' ? 'always'
    : `${constraint.trigger?.kind}(${(constraint.trigger?.phases || []).join(', ')})`;
  return `${terms} ${constraint.sense} ${constraint.rhs} · ${trigger}`;
}

export function renderConstraintList(container, state, callbacks = {}) {
  const constraints = state.constraints || [];
  if (!constraints.length) {
    container.innerHTML = '<div class="hint">暂无用户约束。可新建“绿信比均衡”“最小绿”等约束。</div>';
    return;
  }
  container.innerHTML = constraints.map((constraint, index) => `
    <div class="list-item ${state.editingConstraintIndex === index ? 'active' : ''}" data-index="${index}">
      <div>
        <div>${escapeHtml(constraint.name)}</div>
        <small>${escapeHtml(constraintSummary(constraint))}${constraint.soft ? ' · 软约束' : ''}</small>
      </div>
      <div style="display:flex;gap:5px;align-items:center">
        <button class="mini-btn" data-action="audit" data-index="${index}">审计</button>
        <span class="delete-link" data-action="delete" data-index="${index}">删除</span>
      </div>
    </div>`).join('');

  container.querySelectorAll('.list-item').forEach((row) => {
    row.addEventListener('click', (event) => {
      const index = Number(row.dataset.index);
      const action = event.target.dataset.action;
      if (action === 'delete') {
        callbacks.onDeleteConstraint?.(index);
        event.stopPropagation();
        return;
      }
      if (action === 'audit') {
        callbacks.onAuditConstraint?.(index);
        event.stopPropagation();
        return;
      }
      callbacks.onEditConstraint?.(index);
    });
  });
}

export function renderConstraintEditor(container, state, callbacks = {}) {
  const phases = state.intersection?.phases || [];
  const draft = state.constraintDraft || buildDefaultConstraintDraft();
  state.constraintDraft = draft;

  // 关键修复：如果默认 draft 或旧数据里的 g/y 变量名为空，
  // 在渲染前同步为当前第一个相位，避免向后端发送 ('g', '')。
  if (!Array.isArray(draft.coeffs) || !draft.coeffs.length) {
    draft.coeffs = [['g', phases[0]?.id || '', 1]];
  } else {
    draft.coeffs = draft.coeffs.map(([kind = 'g', name = '', value = 1]) => {
      if (kind === 'C') return ['C', '', Number(value) || 0];
      const validName = phases.some((phase) => phase.id === name) ? name : (phases[0]?.id || '');
      return [kind, validName, Number(value) || 0];
    });
  }

  const phaseOptions = (selected) => phases.map((phase) => `
    <option value="${escapeHtml(phase.id)}" ${phase.id === selected ? 'selected' : ''}>${escapeHtml(phase.id)}</option>`).join('');

  const coeffRows = draft.coeffs.map(([kind = 'g', name = '', value = 1], index) => {
    const variableControl = kind === 'C'
      ? `<select data-coeff="${index}" data-field="name"><option value="">C（周期）</option></select>`
      : `<select data-coeff="${index}" data-field="name">${phaseOptions(name)}</select>`;
    return `
      <div class="coeff-row">
        <select data-coeff="${index}" data-field="kind">
          <option value="g" ${kind === 'g' ? 'selected' : ''}>g</option>
          <option value="y" ${kind === 'y' ? 'selected' : ''}>y</option>
          <option value="C" ${kind === 'C' ? 'selected' : ''}>C</option>
        </select>
        ${variableControl}
        <input type="number" step="0.1" value="${escapeHtml(value)}" data-coeff="${index}" data-field="value" />
        <button type="button" data-action="remove-coeff" data-index="${index}" title="删除系数">×</button>
      </div>`;
  }).join('');

  const triggerPhases = phases.map((phase) => `
    <label><input type="checkbox" data-trigger-phase="${escapeHtml(phase.id)}" ${(draft.trigger?.phases || []).includes(phase.id) ? 'checked' : ''} /> ${escapeHtml(phase.id)}</label>`).join('');

  container.innerHTML = `
    <div class="form-row">
      <label>约束名称<input id="constraint-name" value="${escapeHtml(draft.name)}" placeholder="例如：东西直行绿信比均衡" /></label>
    </div>
    <div class="form-row">
      <label>线性表达式 coeffs</label>
      <div id="coeff-rows">${coeffRows || '<div class="hint">至少需要一个系数</div>'}</div>
      <button type="button" class="mini-btn" data-action="add-coeff">+ 系数</button>
    </div>
    <div class="form-grid">
      <label>sense<select id="constraint-sense">
        <option value="<=" ${draft.sense === '<=' ? 'selected' : ''}>&le;</option>
        <option value=">=" ${draft.sense === '>=' ? 'selected' : ''}>&ge;</option>
        <option value="==" ${draft.sense === '==' ? 'selected' : ''}>=</option>
      </select></label>
      <label>rhs<input id="constraint-rhs" type="number" step="0.1" value="${escapeHtml(draft.rhs)}" /></label>
    </div>
    <div class="form-row">
      <label>trigger.kind<select id="constraint-trigger-kind">
        <option value="always" ${draft.trigger?.kind === 'always' ? 'selected' : ''}>always</option>
        <option value="or" ${draft.trigger?.kind === 'or' ? 'selected' : ''}>or</option>
        <option value="and" ${draft.trigger?.kind === 'and' ? 'selected' : ''}>and</option>
      </select></label>
      <div class="trigger-phases">${triggerPhases || '<span class="hint">先添加相位</span>'}</div>
    </div>
    <div class="form-grid">
      <label class="checkline"><input id="constraint-soft" type="checkbox" ${draft.soft ? 'checked' : ''} /> 软约束</label>
      <label>penalty<input id="constraint-penalty" type="number" min="0" step="1" value="${escapeHtml(draft.penalty)}" /></label>
      <label>slack_max<input id="constraint-slack" type="number" min="0" step="1" value="${draft.slack_max ?? ''}" /></label>
      <label class="checkline"><input id="constraint-confirm-mixed" type="checkbox" ${draft.confirm_mixed_trigger ? 'checked' : ''} /> 确认混合触发</label>
      <label class="checkline"><input id="constraint-confirm-audit" type="checkbox" ${draft.confirm_audit ? 'checked' : ''} /> 确认审计陷阱</label>
    </div>
    <div class="button-row">
      <button type="button" class="btn btn-primary" data-action="save-constraint">${state.editingConstraintIndex >= 0 ? '更新约束' : '添加约束'}</button>
      <button type="button" class="btn btn-secondary" data-action="audit-draft">审计当前</button>
      <button type="button" class="btn btn-ghost" data-action="clear-constraint">清空</button>
    </div>`;

  const readBasicFields = () => {
    draft.name = container.querySelector('#constraint-name')?.value.trim() || '';
    draft.sense = container.querySelector('#constraint-sense')?.value || '>=';
    draft.rhs = Number(container.querySelector('#constraint-rhs')?.value || 0);
    draft.trigger.kind = container.querySelector('#constraint-trigger-kind')?.value || 'always';
    draft.soft = Boolean(container.querySelector('#constraint-soft')?.checked);
    draft.penalty = Number(container.querySelector('#constraint-penalty')?.value || 0);
    const slackValue = container.querySelector('#constraint-slack')?.value;
    draft.slack_max = slackValue === '' ? null : Number(slackValue);
    draft.confirm_mixed_trigger = Boolean(container.querySelector('#constraint-confirm-mixed')?.checked);
    draft.confirm_audit = Boolean(container.querySelector('#constraint-confirm-audit')?.checked);
    if (draft.trigger.kind === 'always') draft.trigger.phases = [];
  };

  const readCoeffsFromDom = () => {
    const rows = container.querySelectorAll('.coeff-row');
    if (!rows.length) return;
    draft.coeffs = Array.from(rows).map((row) => {
      const kind = row.querySelector('[data-field="kind"]')?.value || 'g';
      const name = row.querySelector('[data-field="name"]')?.value || '';
      const value = Number(row.querySelector('[data-field="value"]')?.value || 1);
      return [kind, kind === 'C' ? '' : name, value];
    });
  };

  const readForm = () => {
    readBasicFields();
    readCoeffsFromDom();
  };

  const formIsComplete = () => draft.coeffs.every(([kind, name]) => kind === 'C' || Boolean(name));

  container.querySelectorAll('#constraint-name, #constraint-rhs, #constraint-penalty, #constraint-slack, #constraint-sense, #constraint-soft, #constraint-confirm-mixed, #constraint-confirm-audit').forEach((element) => {
    element.addEventListener('change', readBasicFields);
    element.addEventListener('input', readBasicFields);
  });

  container.querySelector('#constraint-trigger-kind')?.addEventListener('change', (event) => {
    draft.trigger.kind = event.target.value;
    if (draft.trigger.kind === 'always') draft.trigger.phases = [];
    callbacks.onDraftStructuralChange?.();
  });

  container.querySelectorAll('[data-trigger-phase]').forEach((checkbox) => {
    checkbox.addEventListener('change', () => {
      draft.trigger.phases = Array.from(container.querySelectorAll('[data-trigger-phase]:checked'))
        .map((item) => item.dataset.triggerPhase);
      callbacks.onDraftChange?.();
    });
  });

  container.querySelectorAll('[data-coeff]').forEach((element) => {
    element.addEventListener('change', () => {
      readBasicFields();
      const index = Number(element.dataset.coeff);
      const field = element.dataset.field;
      const existing = draft.coeffs[index] || ['g', '', 1];
      if (field === 'kind') {
        draft.coeffs[index] = [element.value, element.value === 'C' ? '' : (phases[0]?.id || ''), Number(existing[2] || 1)];
        callbacks.onDraftStructuralChange?.();
      } else if (field === 'name') {
        draft.coeffs[index] = [existing[0], element.value, Number(existing[2] || 1)];
        callbacks.onDraftChange?.();
      } else {
        draft.coeffs[index] = [existing[0], existing[1], Number(element.value)];
        callbacks.onDraftChange?.();
      }
    });
  });

  container.querySelectorAll('[data-action]').forEach((button) => {
    const action = button.dataset.action;
    if (action === 'remove-coeff') {
      button.addEventListener('click', () => {
        readForm();
        draft.coeffs.splice(Number(button.dataset.index), 1);
        if (!draft.coeffs.length) draft.coeffs.push(['g', phases[0]?.id || '', 1]);
        callbacks.onDraftStructuralChange?.();
      });
    } else if (action === 'add-coeff') {
      button.addEventListener('click', () => {
        readForm();
        draft.coeffs.push(['g', phases[0]?.id || '', 1]);
        callbacks.onDraftStructuralChange?.();
      });
    } else if (action === 'save-constraint') {
      button.addEventListener('click', () => {
        readForm();
        if (!formIsComplete()) {
          callbacks.onValidationError?.('每个 g/y 变量都必须选择一个相位；若不需要该变量请删除系数行。');
          return;
        }
        callbacks.onSaveConstraint?.(deepClone(draft));
      });
    } else if (action === 'audit-draft') {
      button.addEventListener('click', () => {
        readForm();
        if (!formIsComplete()) {
          callbacks.onValidationError?.('每个 g/y 变量都必须选择一个相位；若不需要该变量请删除系数行。');
          return;
        }
        callbacks.onAuditDraft?.(deepClone(draft));
      });
    } else if (action === 'clear-constraint') {
      button.addEventListener('click', () => callbacks.onClearConstraint?.());
    }
  });
}

export function renderAuditPanel(container, state) {
  const audit = state.audit;
  if (!audit) {
    container.innerHTML = '<div class="hint">约束保存/点击“审计”后显示子集语义审计。</div>';
    return;
  }
  const entries = audit.entries || [];
  const warnings = audit.warnings || [];
  const rows = entries.map((entry) => `
    <tr class="${entry.trap ? 'audit-entry trap' : ''}">
      <td>${entry.subset?.length ? `{${entry.subset.map(escapeHtml).join(', ')}}` : '∅'}</td>
      <td><span class="status-pill ${entry.trap ? 'bad' : (entry.status === '恒满足' ? 'ok' : 'warn')}">${escapeHtml(entry.status)}</span></td>
      <td>${entry.active ? '是' : '否'}</td>
      <td>${entry.trap ? '是' : '否'}</td>
      <td>[${Number(entry.lo ?? 0).toFixed(2)}, ${Number(entry.hi ?? 0).toFixed(2)}]</td>
    </tr>`).join('');
  container.innerHTML = `
    <div class="section-head" style="margin:0 0 6px"><span>${escapeHtml(audit.constraint || '审计报告')}</span>${audit.trap_subsets?.length ? '<span class="badge badge-error">发现陷阱</span>' : '<span class="badge badge-ok">无硬陷阱</span>'}</div>
    ${warnings.map((text) => `<div class="msg warn">${escapeHtml(text)}</div>`).join('')}
    <table class="audit-table">
      <thead><tr><th>子集</th><th>状态</th><th>触发</th><th>陷阱</th><th>区间</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="5">无子集（可能不含 g 变量）</td></tr>'}</tbody>
    </table>`;
}
