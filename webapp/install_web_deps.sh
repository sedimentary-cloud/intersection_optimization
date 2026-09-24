#!/usr/bin/env bash
# 使用配套 conda artery_milp 环境安装 Web 依赖。
# 如果环境 site-packages 不可写（例如只读挂载），自动回退到项目本地 .webdeps。
set -euo pipefail

ENV_PY="${ARTERY_MILP_PY:-/home/qktx/artery_milp/conda-envs/artery_milp/bin/python}"
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
