function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

function movementGroup(id) {
  const first = String(id).trim().toUpperCase()[0];
  return ['N', 'S', 'E', 'W'].includes(first) ? first : 'OTHER';
}

function movementType(id) {
  const text = String(id).toLowerCase();
  if (text.includes('left') || text.includes('左')) return 'left';
  if (text.includes('thr') || text.includes('straight') || text.includes('直')) return 'through';
  return 'other';
}

function buildLaneSvg({ id, demand, group, type, index, count }, servingIds) {
  const served = servingIds.has(id);
  const selected = servingIds.__selected === id;
  const color = selected ? '#7c3aed' : (served ? '#16a34a' : '#64748b');
  const width = selected || served ? 10 : 7;
  const label = `${escapeHtml(id)} · ${Math.round(demand)}`;
  const mid = count > 1 ? (index - (count - 1) / 2) : 0;
  if (group === 'N' || group === 'S') {
    const y1 = group === 'N' ? 48 : 552;
    const y2 = group === 'N' ? 218 : 382;
    const x = 380 + mid * 44;
    const textY = group === 'N' ? y1 - 6 : y2 + 14;
    return `
      <g class="lane-hit ${served ? 'phase-serving' : ''} ${selected ? 'selected' : ''}" data-movement="${escapeHtml(id)}">
        <line x1="${x}" y1="${y1}" x2="${x}" y2="${y2}" stroke="${color}" stroke-width="${width}" />
        <text x="${x + 6}" y="${textY}" text-anchor="start" fill="${served ? '#166534' : '#334155'}">${label}</text>
      </g>`;
  }
  if (group === 'E' || group === 'W') {
    const x1 = group === 'W' ? 52 : 708;
    const x2 = group === 'W' ? 302 : 458;
    const y = 300 + mid * 44;
    const textY = y - 7;
    const anchor = group === 'W' ? 'start' : 'end';
    const textX = group === 'W' ? x1 + 8 : x2 - 8;
    return `
      <g class="lane-hit ${served ? 'phase-serving' : ''} ${selected ? 'selected' : ''}" data-movement="${escapeHtml(id)}">
        <line x1="${x1}" y1="${y}" x2="${x2}" y2="${y}" stroke="${color}" stroke-width="${width}" />
        <text x="${textX}" y="${textY}" text-anchor="${anchor}" fill="${served ? '#166534' : '#334155'}">${label}</text>
      </g>`;
  }
  return '';
}

export function renderTopology(container, state, onSelectMovement) {
  const { intersection, selectedMovementId, selectedPhaseId } = state;
  if (!intersection) {
    container.innerHTML = '<div class="empty-state">暂无路口数据</div>';
    return;
  }
  const phasesById = Object.fromEntries(intersection.phases.map((p) => [p.id, p]));
  const serving = new Set();
  if (selectedPhaseId && phasesById[selectedPhaseId]) {
    Object.keys(phasesById[selectedPhaseId].capacity || {}).forEach((id) => serving.add(id));
  }
  serving.__selected = selectedMovementId;

  const groups = { N: [], S: [], E: [], W: [], OTHER: [] };
  for (const movement of intersection.movements) {
    const group = movementGroup(movement.id);
    groups[group].push(movement);
  }
  const laneGroups = Object.entries(groups)
    .flatMap(([group, movements]) => movements.map((movement, index) => ({
      ...movement, group, type: movementType(movement.id), index, count: movements.length,
    })));

  const selectedPhase = selectedPhaseId && phasesById[selectedPhaseId] ? selectedPhaseId : '未选择相位';
  const svg = `
    <svg viewBox="0 0 760 600" role="img" aria-label="四进口路口拓扑">
      <defs>
        <pattern id="grid" width="24" height="24" patternUnits="userSpaceOnUse">
          <path d="M 24 0 L 0 0 0 24" fill="none" stroke="#eef2f7" stroke-width="1" />
        </pattern>
      </defs>
      <rect x="0" y="0" width="760" height="600" fill="url(#grid)" />
      <rect x="300" y="30" width="160" height="540" rx="10" fill="#e5e7eb" />
      <rect x="40" y="220" width="680" height="160" rx="10" fill="#e5e7eb" />
      <rect x="300" y="220" width="160" height="160" fill="#f1f5f9" stroke="#cbd5e1" />
      <line x1="300" y1="300" x2="460" y2="300" stroke="#cbd5e1" stroke-dasharray="6 5" />
      <line x1="380" y1="220" x2="380" y2="380" stroke="#cbd5e1" stroke-dasharray="6 5" />
      <circle cx="380" cy="300" r="42" fill="#ffffff" stroke="#cbd5e1" />
      <text x="380" y="296" text-anchor="middle" font-size="11" fill="#64748b">当前相位</text>
      <text x="380" y="316" text-anchor="middle" font-size="13" font-weight="700" fill="#1d4ed8">${escapeHtml(selectedPhase)}</text>
      ${laneGroups.map((lane) => buildLaneSvg(lane, serving)).join('')}
    </svg>`;
  container.innerHTML = svg;
  container.querySelectorAll('.lane-hit').forEach((node) => {
    node.addEventListener('click', () => onSelectMovement(node.dataset.movement));
  });

  const legend = document.getElementById('topology-legend');
  if (legend) {
    legend.innerHTML = `
      <span><i style="background:#64748b"></i>普通车道</span>
      <span><i style="background:#16a34a"></i>当前相位服务</span>
      <span><i style="background:#7c3aed"></i>已选车道</span>`;
  }
}
