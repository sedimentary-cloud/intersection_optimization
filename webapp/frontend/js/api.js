async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  let body = null;
  try {
    body = await response.json();
  } catch (err) {
    body = null;
  }
  if (!response.ok) {
    const error = new Error(body?.error?.message || body?.detail || `HTTP ${response.status}`);
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body;
}

export const api = {
  health: () => requestJson('/api/health'),
  validate: (payload) => requestJson('/api/validate', { method: 'POST', body: JSON.stringify(payload) }),
  audit: (payload) => requestJson('/api/audit', { method: 'POST', body: JSON.stringify(payload) }),
  optimize: (payload) => requestJson('/api/optimize', { method: 'POST', body: JSON.stringify(payload) }),
  task: (taskId) => requestJson(`/api/tasks/${encodeURIComponent(taskId)}`),
  result: (taskId) => requestJson(`/api/tasks/${encodeURIComponent(taskId)}/result`),
  cancel: (taskId) => requestJson(`/api/tasks/${encodeURIComponent(taskId)}`, { method: 'DELETE' }),
};
