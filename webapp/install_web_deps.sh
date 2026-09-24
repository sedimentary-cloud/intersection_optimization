#!/usr/bin/env bash
# 使用 artery_milp conda 环境安装 Web 依赖。
# 如果当前环境 site-packages 不可写（例如只读挂载），自动回退到项目本地 .webdeps。
set -euo pipefail

if [[ -n "${ARTERY_MILP_PY:-}" ]]; then
  ENV_PY="${ARTERY_MILP_PY}"
elif [[ -n "${CONDA_PREFIX:-}" && -x "${CONDA_PREFIX}/bin/python" ]]; then
  ENV_PY="${CONDA_PREFIX}/bin/python"
else
  ENV_PY="$(command -v python3 || command -v python)"
fi

WEBAPP_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${WEBAPP_DIR}/../.." && pwd)"
TARGET="${WEB_DEPS_TARGET:-${PROJECT_ROOT}/.webdeps}"
REQ="${WEBAPP_DIR}/requirements-web.txt"

"${ENV_PY}" -m pip install --no-input -r "${REQ}" || {
  echo "[webapp] 全局环境不可写，改为 --target ${TARGET}" >&2
  mkdir -p "${TARGET}"
  "${ENV_PY}" -m pip install --no-input --target "${TARGET}" -r "${REQ}"
  echo "[webapp] 已安装到 ${TARGET}；run_web.py 会自动加入 sys.path"
}
