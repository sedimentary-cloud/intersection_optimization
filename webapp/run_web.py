"""开发/生产启动脚本。

用法::

    /home/qktx/artery_milp/conda-envs/artery_milp/bin/python -m webapp.run_web --port 8000

脚本会优先把项目根目录与项目本地依赖目录 ``.webdeps`` 加入 ``sys.path``，
因此在 conda 环境 site-packages 不可写时也能使用 ``pip --target`` 安装的依赖。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _bootstrap_path() -> Path:
    webapp_dir = Path(__file__).resolve().parent
    repo_root = webapp_dir.parent
    project_root = repo_root.parent
    for candidate in (project_root / ".webdeps", repo_root):
        path = str(candidate)
        if candidate.exists() and path not in sys.path:
            sys.path.insert(0, path)
    return repo_root


def main() -> None:
    parser = argparse.ArgumentParser(description="信号配时优化工具 Web 服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="开发模式自动重载")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    _bootstrap_path()
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - 环境缺失提示
        raise SystemExit(
            "缺少 Web 依赖。请使用 conda artery_milp 环境执行：\n"
            "  /home/qktx/artery_milp/conda-envs/artery_milp/bin/python -m pip "
            "install -r webapp/requirements-web.txt\n"
            "若 site-packages 不可写，可加 --target ../.webdeps，然后重新运行本脚本。"
        ) from exc

    uvicorn.run(
        "webapp.app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
        workers=1,  # 任务表在进程内存中，v1.0 必须单 worker
    )


if __name__ == "__main__":
    main()
