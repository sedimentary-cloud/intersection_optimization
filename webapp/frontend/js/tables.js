function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

function formatNumber(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '';
  return String(Number(n.toFixed(6)));
}

export function renderTables(container, state, callbacks = {}) {
  const { intersection } = state;
  if (!intersection) {
    container.innerHTML = '<div class="empty-state">暂无路口数据</div>';
    return;
  }
  const phases = intersection.phases;
  const movements = intersection.movements;
  const lostValue = (from, to) => {
    const row = intersection.lost_time.find(([a, b]) => a === from && b === to);
    return row ? Number(row[2]) : '';
  };

  const movementRows = movements.map((movement) => `
    <tr>
      <td><input value="${escapeHtml(movement.id)}" data-table="movement" data-id="${escapeHtml(movement.id)}" data-field="id" /></td>
      <td><input type="number" min="0.001" step="1" value="${escapeHtml(formatNumber(movement.demand))}" data-table="movement" data-id="${escapeHtml(movement.id)}" data-field="demand" /></td>
      <td>${intersection.phases.filter((p) => Number(p.capacity?.[movement.id] || 0) > 0).map((p) => `<span class="status-pill ok">${escapeHtml(p.id)}</span>`).join(' ') || '<span class="status-pill bad">无</span>'}</td>
      <td><button type="button" class="mini-btn danger" data-table="movement-delete" data-id="${escapeHtml(movement.id)}">删除</button></td>
    </tr>`).join('');

  const capacityHead = movements.map((m) => `<th>${escapeHtml(m.id)}</th>`).join('');
  const capacityRows = phases.map((phase) => `
    <tr>
      <th>${escapeHtml(phase.id)}</th>
      ${movements.map((movement) => {
        const value = phase.capacity?.[movement.id] ?? '';
        return `<td><input type="number" min="0" step="10" value="${value === '' ? '' : escapeHtml(formatNumber(value))}" data-table="capacity" data-phase="${escapeHtml(phase.id)}" data-movement="${escapeHtml(movement.id)}" placeholder="—" /></td>`;
      }).join('')}
    </tr>`).join('');

  const lostMatrixHead = phases.map((phase) => `<th>${escapeHtml(phase.id)}</th>`).join('');
  const lostMatrixRows = phases.map((fromPhase) => `
    <tr>
      <th>${escapeHtml(fromPhase.id)}</th>
      ${phases.map((toPhase) => {
        if (fromPhase.id === toPhase.id) return '<td class="clearance-diagonal">—</td>';
        const value = lostValue(fromPhase.id, toPhase.id);
        const missing = value === '' && intersection.strict_clearance;
        return `<td><input type="number" min="0" step="0.5" value="${value === '' ? '' : escapeHtml(formatNumber(value))}" placeholder="${intersection.strict_clearance ? '必填' : '0'}" class="${missing ? 'missing' : ''}" data-table="lost-matrix" data-from="${escapeHtml(fromPhase.id)}" data-to="${escapeHtml(toPhase.id)}" /></td>`;
      }).join('')}
    </tr>`).join('');

  container.innerHTML = `
    <div class="table-block">
      <h3>流向数据</h3>
      <table class="data-table">
        <thead><tr><th>流向 id</th><th>需求 (veh/h)</th><th>服务相位</th><th></th></tr></thead>
        <tbody>${movementRows || '<tr><td colspan="4">暂无流向</td></tr>'}</tbody>
      </table>
    </div>
    <div class="table-block">
      <h3>相位-流向服务能力矩阵 <span class="muted">a_pm；空值表示 0 / 不服务</span></h3>
      <div class="matrix-wrap"><table class="data-table"><thead><tr><th>相位 \\ 流向</th>${capacityHead}</tr></thead><tbody>${capacityRows || '<tr><td>暂无相位</td></tr>'}</tbody></table></div>
    </div>
    <div class="table-block">
      <h3>加载参数</h3>
      <div class="form-grid">
        <label>最小绿灯 g_min (s)<input type="number" min="0.1" step="0.5" value="${escapeHtml(formatNumber(intersection.g_min))}" data-table="param" data-field="g_min" /></label>
        <label>周期下界 c_min (s)<input type="number" min="0.1" step="1" value="${escapeHtml(formatNumber(intersection.c_min))}" data-table="param" data-field="c_min" /></label>
        <label>周期上界 c_max (s)<input type="number" min="0.1" step="1" value="${escapeHtml(formatNumber(intersection.c_max))}" data-table="param" data-field="c_max" /></label>
        <label class="checkline"><input type="checkbox" ${intersection.strict_clearance ? 'checked' : ''} data-table="param" data-field="strict_clearance" /> 严格清空时间</label>
      </div>
    </div>
    <div class="table-block">
      <h3>相邻相位清空时间矩阵 <span class="muted">行 = 前序相位，列 = 后序相位；斜线为同相位，不需要清空</span></h3>
      <p class="muted">严格清空时间模式下，所有非对角单元都必须填写；空值会被算法包加载校验拒绝。</p>
      <div class="matrix-wrap">
        <table class="data-table clearance-matrix">
          <thead><tr><th>前序 \ 后序</th>${lostMatrixHead}</tr></thead>
          <tbody>${lostMatrixRows || '<tr><td>暂无相位</td></tr>'}</tbody>
        </table>
      </div>
    </div>`;

  if (callbacks.onSelectMovement) {
    container.querySelectorAll('tr').forEach((row) => {
      row.addEventListener('click', (event) => {
        if (event.target.matches('input, select, button')) return;
        const button = row.querySelector('[data-table="movement-delete"]');
        if (button) callbacks.onSelectMovement(button.dataset.id);
      });
    });
  }

  container.querySelectorAll('[data-table]').forEach((element) => {
    const type = element.dataset.table;
    if (type === 'lost-matrix') {
      element.addEventListener('change', () => {
        const { intersection } = state;
        const from = element.dataset.from;
        const to = element.dataset.to;
        const raw = element.value.trim();
        const index = intersection.lost_time.findIndex(([a, b]) => a === from && b === to);
        if (raw === '') {
          if (index >= 0) intersection.lost_time.splice(index, 1);
          element.classList.toggle('missing', intersection.strict_clearance);
        } else {
          const seconds = Number(raw);
          if (!(Number.isFinite(seconds) && seconds >= 0)) {
            element.value = index >= 0 ? formatNumber(intersection.lost_time[index][2]) : '';
            return;
          }
          if (index >= 0) intersection.lost_time[index][2] = seconds;
          else intersection.lost_time.push([from, to, seconds]);
          element.classList.remove('missing');
        }
        callbacks.onChange?.('lost-matrix');
      });
      return;
    }
    if (type === 'movement-delete') {
      element.addEventListener('click', () => {
        const id = element.dataset.id;
        state.intersection.movements = state.intersection.movements.filter((m) => m.id !== id);
        state.intersection.phases.forEach((phase) => { delete phase.capacity[id]; });
        state.intersection.lost_time = state.intersection.lost_time.filter(([a, b]) => a !== id && b !== id);
        if (state.selectedMovementId === id) state.selectedMovementId = null;
        callbacks.onChange?.('movement-delete');
      });
      return;
    }

    const handler = () => {
      const { intersection } = state;
      let changeReason = type;
      if (type === 'movement') {
        const id = element.dataset.id;
        const field = element.dataset.field;
        const movement = intersection.movements.find((m) => m.id === id);
        if (!movement) return;
        if (field === 'id') {
          changeReason = 'movement-id';
          const next = element.value.trim();
          if (!next) return;
          movement.id = next;
          for (const phase of intersection.phases) {
            if (Object.prototype.hasOwnProperty.call(phase.capacity, id)) {
              phase.capacity[next] = phase.capacity[id];
              delete phase.capacity[id];
            }
          }
          intersection.lost_time = intersection.lost_time.map(([a, b, t]) => [a === id ? next : a, b === id ? next : b, t]);
          if (state.selectedMovementId === id) state.selectedMovementId = next;
        } else {
          movement.demand = Number(element.value);
        }
      } else if (type === 'capacity') {
        const phase = intersection.phases.find((p) => p.id === element.dataset.phase);
        if (!phase) return;
        const movementId = element.dataset.movement;
        const value = Number(element.value);
        if (Number.isFinite(value) && value > 0) phase.capacity[movementId] = value;
        else delete phase.capacity[movementId];
      } else if (type === 'param') {
        const field = element.dataset.field;
        if (field === 'strict_clearance') {
          intersection[field] = element.checked;
          changeReason = 'strict-clearance';
        } else {
          intersection[field] = Number(element.value);
        }
      }
      callbacks.onChange?.(changeReason);
    };
    element.addEventListener('change', handler);
    if (element.tagName === 'INPUT' && element.type === 'number') element.addEventListener('input', handler);
  });
}
