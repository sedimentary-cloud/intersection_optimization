# 信号配时工具 Web 可视化层（非侵入式）

本目录是《信号配时工具Web可视化设计文档.md》的工程实现，位于算法包
`signal_timing` 之外。所有 Web 侧代码只调用算法包公开接口：

- 数据类：`IntersectionData` / `Movement` / `Phase`
- 约束：`ConstraintSpec` / `Trigger`
- 审计：`audit_constraint`
- 顶层入口：`LexicographicOptimizer.solve()`

没有修改、fork 或 monkey-patch `signal_timing`。

## 目录

```
webapp/
├── adapter/          # JSON <-> signal_timing 公开结构；唯一允许 import 算法包的 Web 侧包
│   ├── schemas.py
│   ├── serialize.py
│   ├── deserialize.py
│   ├── audit.py
│   └── runner.py
├── app/              # FastAPI 路由、内存任务表
│   ├── main.py
│   └── tasks.py
├── frontend/         # 原生 ES Module + 本地 ECharts，无 Node 构建链
│   ├── index.html
│   ├── css/
│   ├── js/
│   └── assets/echarts.min.js
├── tests/            # 适配层与 API 契约测试
├── requirements-web.txt
├── environment-web.yml
├── install_web_deps.sh
└── run_web.py
```

## 安装依赖

优先使用文档指定的 conda 环境：

```bash
cd /mnt/e/PythonProjects/intersection_milp/intersection_milp
ARTERY_MILP_PY=/home/qktx/artery_milp/conda-envs/artery_milp/bin/python \
  bash webapp/install_web_deps.sh
```

若该环境 site-packages 只读，上面的脚本会回退到项目本地
`/mnt/e/PythonProjects/intersection_milp/.webdeps`；`webapp/run_web.py`
启动时会自动把该目录加入 `sys.path`。

## 启动

```bash
cd /mnt/e/PythonProjects/intersection_milp/intersection_milp
PYTHONPATH=/mnt/e/PythonProjects/intersection_milp/.webdeps:. \
  /home/qktx/artery_milp/conda-envs/artery_milp/bin/python \
  -m webapp.run_web --host 127.0.0.1 --port 8000
```

浏览器打开 <http://127.0.0.1:8000/>。

> **单 worker 约束**：v1.0 任务表在进程内存中，`uvicorn --workers` 必须为 1；
> `run_web.py` 已固定为 1。

## 测试

```bash
cd /mnt/e/PythonProjects/intersection_milp/intersection_milp
PYTHONPATH=/mnt/e/PythonProjects/intersection_milp/.webdeps:. \
  /home/qktx/artery_milp/conda-envs/artery_milp/bin/python \
  -m unittest discover -s webapp/tests -t . -v
```

API 一览与设计文档 §4 对齐：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/validate` | 校验路口 + 约束，不求解 |
| POST | `/api/audit` | 单条约束子集语义审计 |
| POST | `/api/optimize` | 创建异步任务，返回 202 + task_id |
| GET | `/api/tasks/{id}` | 任务状态 |
| GET | `/api/tasks/{id}/result` | 任务结果 |
| DELETE | `/api/tasks/{id}` | 协作式取消 |
| GET | `/api/health` | 健康检查 |

## 配时图：算法包同款“泳道”结构

前端“配时图”页签已按算法包 `plot_movement_release_gantt` 的表达方式重构：

- 每个“进口 x 流向”一条泳道；
- 相位放行该流向时，在对应绿灯区间画柱子；
- 需求满足前为实心，需求满足后为描边/浅纹；
- 清空/全红时间为浅灰竖带，周期余量为斜纹浅带；
- 每条泳道在 `t_sat` 处画同色虚线和标记；
- y 轴标签展示该流向总绿灯时长与需求满足时刻。

## v1.0 边界

- 高成本求解段没有算法包回调钩子；当前实现会立即进入 `solving` 阶段，
  求解完成后通过 `result.rounds` 展示字典序轨迹。严格逐轮实时进度需要
  设计文档 §5.4 的“适配层复现管线调度”降级方案，可在后续版本补齐。
- `order_candidates` 仅包含算法包公开结果中的最终顺序；候选排序全量对比
  同样需要后续契约扩展或适配层自行枚举。
- 任务结果保存在内存中，TTL 30 分钟；进程重启丢失。
