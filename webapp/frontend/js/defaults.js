export function buildDefaultIntersection() {
  const movements = [
    { id: 'N_left', demand: 240 },
    { id: 'N_thr', demand: 650 },
    { id: 'S_left', demand: 170 },
    { id: 'S_thr', demand: 430 },
    { id: 'E_left', demand: 150 },
    { id: 'E_thr', demand: 470 },
    { id: 'W_left', demand: 110 },
    { id: 'W_thr', demand: 350 },
  ];
  const capacity = {
    P1_NS_TH: { N_thr: 1800, S_thr: 1800 },
    P2_NS_LT: { N_left: 1500, S_left: 1500 },
    P3_EW_TH: { E_thr: 1800, W_thr: 1800 },
    P4_EW_LT: { E_left: 1500, W_left: 1500 },
  };
  const phases = Object.entries(capacity).map(([id, cap]) => ({ id, capacity: { ...cap } }));
  const groups = {
    P1_NS_TH: 'NS', P2_NS_LT: 'NS', P3_EW_TH: 'EW', P4_EW_LT: 'EW',
  };
  const lostTime = [];
  for (const from of phases) {
    for (const to of phases) {
      if (from.id === to.id) continue;
      lostTime.push([from.id, to.id, groups[from.id] === groups[to.id] ? 3 : 5]);
    }
  }
  return {
    movements,
    phases,
    lost_time: lostTime,
    g_min: 11,
    c_min: 40,
    c_max: 180,
    strict_clearance: true,
    static_precheck: true,
    reference_order: null,
  };
}

export function buildDefaultSolver() {
  return {
    fixed_cycle: null,
    eps_cycle: 0.01,
    eps_waste: 0.01,
    waste_abs_tol: null,
    time_limit: 60,
    mip_rel_gap: 0.001,
    reference_order: null,
    reference_order_mode: 'off',
    order_rules: [],
    run_ordering: true,
    allow_cycle_reduction: true,
    enforce_zero_slack: false,
    stage2_mode: 'min_waste',
    disp: false,
    max_fallback: 20,
  };
}

export function buildDefaultConstraintDraft() {
  return {
    name: '',
    coeffs: [['g', '', 1]],
    sense: '>=',
    rhs: 0,
    trigger: { kind: 'always', phases: [] },
    soft: false,
    penalty: 0,
    slack_max: null,
    confirm_mixed_trigger: false,
    confirm_audit: false,
    or_existential: false,
  };
}

export function buildDefaultPlan() {
  return {
    intersection: buildDefaultIntersection(),
    constraints: [],
    solver: buildDefaultSolver(),
  };
}
