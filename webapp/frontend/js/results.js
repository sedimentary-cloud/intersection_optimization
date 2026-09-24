function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

function fmt(value, digits = 2) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : '—';
}

function mountChart(containerId, option) {
  const el = document.getElementById(containerId);
  if (!el || !window.echarts) return;
  const existing = window.echarts.getInstanceByDom(el);
  if (existing) {
    existing.__webResizeObserver?.disconnect();
    existing.dispose();
  }
  const chart = window.echarts.init(el, null, { renderer: 'canvas' });
  chart.setOption({
    textStyle: {
      fontSize: 13,
      fontFamily: '"Segoe UI", "Microsoft YaHei", "PingFang SC", Arial, sans-serif',
    },
    ...option,
  });
  if (typeof window.ResizeObserver === 'function') {
    const observer = new window.ResizeObserver(() => chart.resize());
    observer.observe(el);
    chart.__webResizeObserver = observer;
  } else {
    window.addEventListener('resize', () => chart.resize());
  }
}

function resultCards(result) {
  const cycle = Number(result.cycle);
  const phaseCount = result.selected?.length ?? 0;
  const slack = Number(result.cycle_slack);
  const slackClass = slack >= -1e-6 ? 'good' : 'bad';
  return `
    <div class="result-cards">
      <div class="result-card"><div class="k">最终周期</div><div class="v">${fmt(cycle)} s</div></div>
      <div class="result-card"><div class="k">选中相位数</div><div class="v">${phaseCount}</div></div>
      <div class="result-card"><div class="k">浪费服务</div><div class="v">${fmt(result.waste, 1)}</div></div>
      <div class="result-card"><div class="k">周期余量</div><div class="v ${slackClass}">${fmt(slack)} s</div></div>
      <div class="result-card"><div class="k">第一阶段周期</div><div class="v">${fmt(result.stage1_cycle)} s</div></div>
      <div class="result-card"><div class="k">no-good 回退</div><div class="v ${result.fallback_cuts > 0 ? 'warn' : ''}">${result.fallback_cuts}</div></div>
    </div>`;
}

function movementLabel(mid) {
  const approachNames = { N: '北', S: '南', E: '东', W: '西' };
  const movementNames = { left: '左转', thr: '直行', through: '直行' };
  const text = String(mid);
  if (text.includes('_')) {
    const [approach, movement] = text.split('_');
    return `${approachNames[approach] || approach} ${movementNames[movement] || movement}`;
  }
  return text;
}

function paperMovementColors(movements) {
  const palette = {
    N_left: '#2F5C8F', N_thr: '#8FB4D9',
    S_left: '#2E7D5B', S_thr: '#9CCFAD',
    E_left: '#C46A2E', E_thr: '#F0B98D',
    W_left: '#6A5A9E', W_thr: '#B3A6D1',
  };
  const fallback = ['#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f', '#edc948', '#b07aa1', '#ff9da7', '#9c755f', '#bab0ac'];
  return Object.fromEntries(movements.map((mid, index) => [mid, palette[mid] || fallback[index % fallback.length]]));
}

function buildMovementSchedule(intersection, result) {
  const order = (result.order && result.order.length ? result.order : result.selected) || [];
  if (!order.length) return null;
  const cycle = Number(result.cycle) || 0;
  const lostMap = new Map((intersection.lost_time || []).map(([from, to, seconds]) => [`${from}->${to}`, Number(seconds)]));
  const lBar = lostMap.size ? Math.max(...lostMap.values()) : 0;
  const hasRealOrder = Boolean(result.order && result.order.length);
  const intervals = [];
  const n = order.length;
  let cursor = 0;
  let clearanceTotal = 0;
  order.forEach((phase, index) => {
    const green = Math.max(0, Number(result.greens?.[phase] || 0));
    if (green > 1e-6) intervals.push({ kind: 'green', phase, start: cursor, end: cursor + green, duration: green });
    cursor += green;
    let clearance = 0;
    let next = null;
    if (n > 1) {
      next = order[(index + 1) % n];
      clearance = hasRealOrder ? (lostMap.get(`${phase}->${next}`) ?? lBar) : lBar;
    }
    if (clearance > 1e-6) {
      intervals.push({ kind: 'clearance', phase, next, start: cursor, end: cursor + clearance, duration: clearance });
      cursor += clearance;
      clearanceTotal += clearance;
    }
  });
  const slack = cycle - cursor;
  if (slack > 1e-6) intervals.push({ kind: 'slack', start: cursor, end: cycle, duration: slack });
  return { order, cycle, intervals, clearanceTotal };
}

function satisfactionTime(intersection, plan, movementId) {
  const movement = intersection.movements.find((item) => item.id === movementId);
  if (!movement) return null;
  const demandVehicles = Number(movement.demand) * plan.cycle / 3600;
  const serviceIntervals = plan.intervals
    .filter((interval) => interval.kind === 'green' && interval.phase)
    .map((interval) => {
      const phase = intersection.phases.find((item) => item.id === interval.phase);
      const capacity = Number(phase?.capacity?.[movementId] || 0);
      return capacity > 0 ? { start: interval.start, end: interval.end, rate: capacity / 3600 } : null;
    })
    .filter(Boolean);
  if (!serviceIntervals.length) return null;

  const points = new Set([0, plan.cycle]);
  serviceIntervals.forEach((interval) => {
    points.add(interval.start);
    points.add(interval.end);
  });
  const sorted = [...points].sort((a, b) => a - b);
  let cumulative = 0;
  for (let i = 0; i < sorted.length - 1; i += 1) {
    const left = sorted[i];
    const right = sorted[i + 1];
    if (right <= left + 1e-12) continue;
    const probe = (left + right) / 2;
    const rate = serviceIntervals
      .filter((interval) => interval.start - 1e-9 <= probe && probe <= interval.end + 1e-9)
      .reduce((sum, interval) => sum + interval.rate, 0);
    if (rate <= 0) continue;
    const need = demandVehicles - cumulative;
    if (need <= 1e-9) return left;
    const dt = need / rate;
    if (left + dt <= right + 1e-9) return Math.max(0, Math.min(left + dt, plan.cycle));
    cumulative += rate * (right - left);
  }
  return cumulative + 1e-9 >= demandVehicles ? plan.cycle : null;
}

function movementReleaseRenderItem(params, api) {
  const kind = api.value(4);
  const color = api.value(3);
  const start = Number(api.value(0));
  const end = Number(api.value(1));
  const yIndex = api.value(2);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null;

  if (kind === 'clearance' || kind === 'slack') {
    const left = api.coord([start, 0]);
    const right = api.coord([end, 0]);
    const width = right[0] - left[0];
    if (width <= 0) return null;
    const rect = {
      x: left[0],
      y: params.coordSys.y,
      width,
      height: params.coordSys.height,
    };
    const children = [{
      type: 'rect',
      shape: rect,
      style: {
        fill: color,
        opacity: kind === 'clearance' ? 0.62 : 0.82,
      },
    }];
    const label = api.value(5);
    if (label && width > 10) {
      children.push({
        type: 'text',
        style: {
          text: label,
          x: rect.x + rect.width / 2,
          y: rect.y + 18,
          fill: '#555555',
          fontSize: 11,
          align: 'center',
          verticalAlign: 'middle',
          rotation: Math.PI / 2,
        },
      });
    }
    return { type: 'group', children };
  }

  if (kind === 'solid' || kind === 'hatch') {
    const left = api.coord([start, yIndex]);
    const right = api.coord([end, yIndex]);
    const width = right[0] - left[0];
    if (width <= 0) return null;
    const laneHeight = Math.max(12, api.size([0, 1])[1] * 0.56);
    const rect = {
      x: left[0],
      y: left[1] - laneHeight / 2,
      width,
      height: laneHeight,
    };
    const style = kind === 'solid'
      ? { fill: color, stroke: '#ffffff', lineWidth: 0.6 }
      : { fill: 'rgba(255,255,255,0.94)', stroke: color, lineWidth: 1.15, lineDash: [4, 2] };
    const children = [{ type: 'rect', shape: rect, style }];
    const label = api.value(5);
    if (label && width > 26) {
      children.push({
        type: 'text',
        style: {
          text: label,
          x: rect.x + rect.width / 2,
          y: rect.y + rect.height / 2,
          fill: '#111111',
          fontSize: 11,
          align: 'center',
          verticalAlign: 'middle',
          backgroundColor: 'rgba(255,255,255,0.72)',
          padding: [1, 3],
        },
      });
    }
    return { type: 'group', children };
  }

  if (kind === 'sat') {
    const point = api.coord([start, yIndex]);
    const laneHeight = Math.max(12, api.size([0, 1])[1] * 0.86);
    return {
      type: 'group',
      children: [
        {
          type: 'line',
          shape: {
            x1: point[0], y1: point[1] - laneHeight / 2,
            x2: point[0], y2: point[1] + laneHeight / 2,
          },
          style: { stroke: color, lineWidth: 1.2, lineDash: [4, 3] },
        },
        {
          type: 'circle',
          shape: { cx: point[0], cy: point[1], r: 3.4 },
          style: { fill: color, stroke: '#ffffff', lineWidth: 1 },
        },
      ],
    };
  }
  return null;
}

function buildMovementReleaseOption(intersection, result) {
  const plan = buildMovementSchedule(intersection, result);
  if (!plan) return null;
  const movements = intersection.movements.map((movement) => movement.id);
  const yIndex = Object.fromEntries(movements.map((mid, index) => [mid, index]));
  const colors = paperMovementColors(movements);
  const capacityOf = (phaseId, movementId) => {
    const phase = intersection.phases.find((item) => item.id === phaseId);
    return Number(phase?.capacity?.[movementId] || 0);
  };
  const satTimes = Object.fromEntries(movements.map((mid) => [mid, satisfactionTime(intersection, plan, mid)]));
  const data = [];

  plan.intervals.forEach((interval) => {
    if (interval.kind === 'clearance') {
      data.push([interval.start, interval.end, 0, '#D9D9D9', 'clearance', `${interval.duration.toFixed(0)}s`, `清空/全红 ${interval.duration.toFixed(1)}s`]);
      return;
    }
    if (interval.kind === 'slack') {
      data.push([interval.start, interval.end, 0, '#F5F5F5', 'slack', `余量 ${interval.duration.toFixed(1)}s`, `周期余量 ${interval.duration.toFixed(1)}s`]);
      return;
    }
    if (interval.kind !== 'green' || !interval.phase) return;
    movements.forEach((mid) => {
      const capacity = capacityOf(interval.phase, mid);
      if (capacity <= 0) return;
      const tSat = satTimes[mid];
      const solidEnd = tSat == null ? interval.end : Math.min(interval.end, tSat);
      if (solidEnd > interval.start + 1e-6) {
        const duration = solidEnd - interval.start;
        data.push([interval.start, solidEnd, yIndex[mid], colors[mid], 'solid', `${duration.toFixed(1)}s`, `${movementLabel(mid)} · ${duration.toFixed(1)}s（需求满足前）`]);
      }
      if (tSat != null && interval.end > tSat + 1e-6) {
        const hatchStart = Math.max(interval.start, tSat);
        const duration = interval.end - hatchStart;
        data.push([hatchStart, interval.end, yIndex[mid], colors[mid], 'hatch', `${duration.toFixed(1)}s`, `${movementLabel(mid)} · ${duration.toFixed(1)}s（满足后额外服务）`]);
      }
    });
  });

  movements.forEach((mid) => {
    const tSat = satTimes[mid];
    if (tSat == null) return;
    data.push([tSat, tSat, yIndex[mid], colors[mid], 'sat', '', `需求满足时刻 ${tSat.toFixed(2)}s`]);
  });

  // y 轴只显示“进口-流向”，时间信息通过柱段 tooltip 和图中标注表达。
  const laneLabels = movements.map((mid) => movementLabel(mid));
  const unsatisfied = movements.filter((mid) => satTimes[mid] == null);
  const orderText = plan.order.join(' → ');
  const summary = `C=${plan.cycle.toFixed(1)}s | 相位数=${plan.order.length} | 清空合计=${plan.clearanceTotal.toFixed(1)}s | 顺序: ${orderText}`;

  return {
    option: {
      animationDuration: 260,
      title: {
        text: '各进口–流向放行时段与需求满足时刻',
        subtext: summary,
        left: 'center',
        top: 2,
        textStyle: { fontSize: 16, color: '#172033' },
        subtextStyle: { fontSize: 12, color: '#64748b' },
      },
      tooltip: {
        formatter: (params) => {
          const item = Array.isArray(params) ? params[0]?.data : params?.data;
          if (!item) return '';
          return escapeHtml(item[6] || item[5] || '');
        },
      },
      grid: { left: 110, right: 28, top: 92, bottom: 50, containLabel: true },
      xAxis: {
        type: 'value',
        min: 0,
        max: plan.cycle,
        name: '时间 t (s)',
        nameLocation: 'middle',
        nameGap: 28,
        axisLabel: { fontSize: 13 },
        splitLine: { lineStyle: { color: '#e2e8f0' } },
      },
      yAxis: {
        type: 'category',
        data: laneLabels,
        inverse: true,
        axisTick: { show: false },
        axisLabel: {
          fontSize: 13,
          color: '#334155',
          interval: 0,
          showMinLabel: true,
          showMaxLabel: true,
          margin: 8,
        },
        splitLine: { show: true, lineStyle: { color: '#e8edf5' } },
      },
      series: [{
        type: 'custom',
        renderItem: movementReleaseRenderItem,
        data,
        encode: { x: [0, 1], y: 2 },
        z: 3,
      }],
    },
    unsatisfied,
    summary,
  };
}

function renderTimingTab(container, state) {
  const result = state.result;
  const intersection = state.intersection;
  const built = buildMovementReleaseOption(intersection, result);
  if (!built) {
    container.innerHTML = '<div class="empty-state">结果缺少选中相位或路口数据，无法绘制泳道图。</div>';
    return;
  }
  const order = result.order || result.selected || [];
  container.innerHTML = `
    ${resultCards(result)}
    <div class="timing-chart-head">
      <div class="legend">
        <span><i style="background:#7f7f7f"></i>需求满足前：实心</span>
        <span><i style="background:#ffffff;border:1px dashed #64748b"></i>需求满足后：额外服务</span>
        <span><i style="background:#d9d9d9"></i>清空/全红时间</span>
        <span><i style="background:#f5f5f5;border:1px solid #cbd5e1"></i>周期余量</span>
        <span><i style="background:#444444;height:2px;border-radius:0"></i>需求满足时刻 $t_{sat}$</span>
      </div>
      <span class="muted">${escapeHtml(built.summary)}</span>
    </div>
    ${built.unsatisfied.length ? `<div class="msg error">未满足流向：${built.unsatisfied.map(escapeHtml).join('、')}</div>` : ''}
    <div id="chart-movement-release" class="chart tall"></div>
    <details class="timing-details">
      <summary>相位配时明细</summary>
      <table class="data-table">
        <thead><tr><th>顺序</th><th>相位</th><th>绿灯 (s)</th><th>服务流向</th></tr></thead>
        <tbody>
          ${order.map((phase, index) => {
            const phaseData = intersection.phases.find((item) => item.id === phase);
            return `<tr><td>${index + 1}</td><td>${escapeHtml(phase)}</td><td>${fmt(result.greens?.[phase], 2)}</td><td>${escapeHtml(Object.keys(phaseData?.capacity || {}).join(', '))}</td></tr>`;
          }).join('')}
        </tbody>
      </table>
    </details>`;
  mountChart('chart-movement-release', built.option);
}

function renderOrderTab(container, state) {
  const result = state.result;
  const order = result.order || [];
  const lost = Number(result.cycle_clearance || 0);
  const reference = state.solver?.reference_order;
  container.innerHTML = `
    ${resultCards(result)}
    <div class="two-col">
      <div class="result-card" style="padding:14px">
        <div class="k">最优相位顺序</div>
        <div style="font-size:18px;font-weight:800;margin:8px 0">${order.map(escapeHtml).join(' → ') || '—'}</div>
        <p class="muted">${escapeHtml(result.message || '')}</p>
        <table class="data-table">
          <tbody>
            <tr><th>环形清空时间合计</th><td>${fmt(lost)} s</td></tr>
            <tr><th>周期余量</th><td>${fmt(result.cycle_slack)} s</td></tr>
            <tr><th>参考顺序</th><td>${reference ? escapeHtml(JSON.stringify(reference)) : '未设置'}</td></tr>
          </tbody>
        </table>
      </div>
      <div class="result-card" style="padding:14px">
        <div class="k">求解状态</div>
        <div class="v">${escapeHtml(result.status || '—')}</div>
        <p class="muted">${escapeHtml(result.message || '')}</p>
      </div>
    </div>`;
}

function renderSaturationTab(container, state) {
  const rows = state.result?.lane_balance || [];
  container.innerHTML = `
    <div class="two-col">
      <div><h3 style="margin:4px 0 6px">需求 vs 供给 (veh/h)</h3><div id="chart-saturation" class="chart tall"></div></div>
      <div>
        <h3 style="margin:4px 0 6px">车道饱和度</h3>
        <table class="data-table">
          <thead><tr><th>流向</th><th>需求</th><th>供给</th><th>饱和度</th><th>状态</th></tr></thead>
          <tbody>
            ${rows.map((row) => {
              const ratio = row.ratio == null ? null : Number(row.ratio);
              const status = ratio == null ? '未知' : (ratio > 0.95 ? '接近饱和' : (ratio > 0.9 ? '偏高' : '正常'));
              const cls = ratio != null && ratio > 0.95 ? 'bad' : (ratio != null && ratio > 0.9 ? 'warn' : 'ok');
              return `<tr><td>${escapeHtml(row.lane)}</td><td>${fmt(row.demand)}</td><td>${fmt(row.supply)}</td><td>${ratio == null ? '—' : fmt(ratio, 3)}</td><td><span class="status-pill ${cls}">${status}</span></td></tr>`;
            }).join('') || '<tr><td colspan="5">暂无数据</td></tr>'}
          </tbody>
        </table>
      </div>
    </div>`;
  const lanes = rows.map((row) => row.lane);
  mountChart('chart-saturation', {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { top: 0 },
    grid: { left: 55, right: 20, top: 34, bottom: 60 },
    xAxis: { type: 'category', data: lanes, axisLabel: { rotate: 30 } },
    yAxis: { type: 'value', name: 'veh/h' },
    series: [
      { name: '需求', type: 'bar', data: rows.map((r) => r.demand), itemStyle: { color: '#f97316' } },
      { name: '供给', type: 'bar', data: rows.map((r) => r.supply), itemStyle: { color: '#2563eb' } },
    ],
  });
}

function renderSigmaTab(container, state) {
  const sigmas = state.result?.sigmas || {};
  const keys = Object.keys(sigmas);
  container.innerHTML = keys.length
    ? `<div class="two-col"><div><h3 style="margin:4px 0 6px">软约束违反量</h3><div id="chart-sigma" class="chart"></div></div>
       <div><table class="data-table"><thead><tr><th>约束键</th><th>违反量</th></tr></thead><tbody>${keys.map((key) => `<tr><td>${escapeHtml(key)}</td><td>${fmt(sigmas[key], 4)}</td></tr>`).join('')}</tbody></table></div></div>`
    : '<div class="empty-state">当前结果没有软约束 σ 归档。</div>';
  if (!keys.length) return;
  mountChart('chart-sigma', {
    tooltip: { trigger: 'axis' },
    grid: { left: 120, right: 24, top: 20, bottom: 30 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: keys, axisLabel: { width: 115, overflow: 'truncate' } },
    series: [{ type: 'bar', data: keys.map((key) => sigmas[key]), itemStyle: { color: '#d97706' } }],
  });
}

function renderRoundsTab(container, state) {
  const rounds = state.result?.rounds || [];
  container.innerHTML = `
    <table class="data-table">
      <thead><tr><th>轮次</th><th>目标</th><th>目标值</th><th>状态</th><th>C</th><th>浪费</th><th>选中相位</th><th>锁定</th><th>说明</th></tr></thead>
      <tbody>
        ${rounds.map((round) => `
          <tr>
            <td>${escapeHtml(round.name)}</td>
            <td>${escapeHtml(round.objective_name)}</td>
            <td>${fmt(round.objective_value, 4)}</td>
            <td>${escapeHtml(round.status)}</td>
            <td>${fmt(round.cycle)}</td>
            <td>${fmt(round.waste, 1)}</td>
            <td>${escapeHtml((round.selected || []).join(', '))}</td>
            <td><code>${escapeHtml(Object.keys(round.locked || {}).join(', ') || '—')}</code></td>
            <td>${escapeHtml(round.message)}</td>
          </tr>`).join('') || '<tr><td colspan="9">暂无轮次数据</td></tr>'}
      </tbody>
    </table>`;
}

function renderCompareTab(container, state) {
  const candidates = state.result?.order_candidates || [];
  container.innerHTML = `
    <table class="data-table">
      <thead><tr><th>#</th><th>相位顺序</th><th>清空时间合计</th><th>浪费服务</th><th>说明</th></tr></thead>
      <tbody>
        ${candidates.map((candidate, index) => `
          <tr>
            <td>${index + 1}</td>
            <td>${(candidate.order || []).map(escapeHtml).join(' → ')}</td>
            <td>${fmt(candidate.lost_total)} s</td>
            <td>${fmt(candidate.waste, 1)}</td>
            <td>${escapeHtml(candidate.note || '')}</td>
          </tr>`).join('') || '<tr><td colspan="5">算法包公开结果未暴露候选排序列表。</td></tr>'}
      </tbody>
    </table>`;
}

export function renderResults(container, state) {
  const result = state.result;
  if (!result) {
    container.innerHTML = '<div class="empty-state">提交求解任务后，结果将在这里渲染。</div>';
    return;
  }
  const summary = document.getElementById('result-summary');
  if (summary) summary.textContent = `C=${fmt(result.cycle)}s · ${(result.selected || []).length} 相位 · waste=${fmt(result.waste, 1)}`;
  const status = document.getElementById('result-status');
  if (status) {
    status.textContent = result.status === 'optimal' ? '求解成功' : String(result.status || '已返回');
    status.className = `badge ${result.status === 'optimal' ? 'badge-ok' : 'badge-warn'}`;
  }

  container.innerHTML = '<div class="empty-state">渲染中…</div>';
  const tab = state.activeResultTab || 'timing';
  if (tab === 'timing') renderTimingTab(container, state);
  else if (tab === 'order') renderOrderTab(container, state);
  else if (tab === 'saturation') renderSaturationTab(container, state);
  else if (tab === 'sigma') renderSigmaTab(container, state);
  else if (tab === 'rounds') renderRoundsTab(container, state);
  else renderCompareTab(container, state);
}

// 供前端单测/SSR 冒烟测试使用；不影响生产渲染。
export { buildMovementReleaseOption, movementReleaseRenderItem, buildMovementSchedule, satisfactionTime };
