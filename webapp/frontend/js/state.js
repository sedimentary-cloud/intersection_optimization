const listeners = new Set();

export const state = {
  activeView: 'topology',
  activeResultTab: 'timing',
  intersection: null,
  constraints: [],
  solver: null,
  selectedMovementId: null,
  selectedPhaseId: null,
  editingConstraintIndex: -1,
  constraintDraft: null,
  validation: null,
  audit: null,
  task: null,
  result: null,
  plans: {},
  resultCache: {},
  taskTimer: null,
};

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function notify(reason = 'update') {
  for (const fn of listeners) fn(state, reason);
}

export function setState(patch, reason = 'update') {
  Object.assign(state, patch);
  notify(reason);
}

export function deepClone(value) {
  return JSON.parse(JSON.stringify(value));
}
