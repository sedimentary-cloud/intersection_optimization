# 信号配时优化框架 · 实现说明

本目录是《信号配时优化框架设计文档.md》v1.0 的代码实现，求解后端为
`scipy.optimize.milp`（HiGHS）。

> 完整的算法原理、API、约束配置、性能指导和陷阱说明见
> [`算法库使用说明.md`](算法库使用说明.md)。

## 1. 环境检查

系统里存在与 `artery_milp` 项目配套的 conda 环境：

| 路径 | 状态 |
|---|---|
| `/home/qktx/artery_milp/conda-envs/artery_milp` | **Linux/WSL 可执行环境**：Python 3.11.16、scipy 1.17.1、numpy 2.4.6，`scipy.optimize.milp` 可用 |
| `/mnt/e/PythonProjects/artery_milp/conda-envs/artery_milp` | Windows 侧环境目录；在 WSL 下没有可直接调用的 `bin/python`，不作为运行环境 |

运行代码/测试请使用：

```bash
/home/qktx/artery_milp/conda-envs/artery_milp/bin/python
```

## 2. 文件结构

```
signal_timing/
  exceptions.py     统一异常
  variables.py      VarKey / 变量注册表（稀疏装配的列号管理）
  data.py           数据层：Movement / Phase / IntersectionData + 加载校验
  constraints.py    约束描述层：Trigger / ConstraintSpec
  audit.py          子集语义审计（§7）
  compiler.py       约束编译层：big-M、触发规则、软约束、触发坍缩（§6）
  model.py          模型层：基础约束 + scipy milp 装配/求解（§4/§10）
  pipeline.py       字典序目标管线（§8）
  ordering.py       排序后处理：枚举 + LP 重解（§9）
  optimizer.py      顶层 LexicographicOptimizer + 回退 + 后验校验（§11/§12）
  result.py         结果对象
tests/              unittest 测试套件
examples/demo_signal_timing.py  可运行示例
examples/plot_signal_gantt.py  甘特图示例
examples/four_approach_overlap_case.py  4 进口 x 2 流向 + 搭接相位复杂案例
```

## 3. 运行测试

```bash
cd /mnt/e/PythonProjects/intersection_milp
/home/qktx/artery_milp/conda-envs/artery_milp/bin/python -m unittest discover -s tests -t . -v
```

当前测试共 **61** 个，覆盖：

- 数据层：覆盖性、正需求/正能力、未知流线、时间参数、lost_time 引用、严格清空时间、静态可行性预检；
- 约束描述层：Trigger 三分类、ConstraintSpec 校验；
- 编译层：big-M 公式、ALWAYS/OR/AND × ≤/≥/== 发射矩阵、M≤0 直接发射、软约束松弛、触发坍缩、混合系数保护、OR 存在性特例；
- 审计层：§7.1 退化陷阱、AND 触发化解、等式陷阱、软约束豁免、非 g 变量人工复核；
- 排序层：真实清空时间、紧周期下的顺序选择、排序过滤器、单相位、缺清空时间报错；
- 集成：手算两相位算例、定点/自由周期一致性、周期回收、硬/软用户约束、对称性、单相位饱和极端、no-good cut 回退；
- 可视化：甘特区间构造、周期/清空时间正确性、需求满足时刻计算、两种甘特图 PNG 输出；
- 复杂案例：4 进口 x 2 流向、6 候选相位（4 对称 + 2 南北直行左转搭接），
  验证最小绿 >10s、需求满足、搭接相位不劣于纯对称方案。

## 4. 与设计文档的对应

| 设计文档 | 实现 |
|---|---|
| §3 数据层 | `data.py` |
| §4 变量/基础约束 | `variables.py`, `model.py` |
| §5 约束描述层 | `constraints.py` |
| §6 约束编译层 | `compiler.py` |
| §7 子集语义审计 | `audit.py` |
| §8 字典序目标管线 | `pipeline.py` |
| §9 排序后处理 | `ordering.py` |
| §10 求解器配置/数值工程 | `model.py`, `ordering.py` |
| §11 顶层 API | `optimizer.py` |
| §12 不可行/异常诊断 | `exceptions.py`, `optimizer.py` 后验校验 |
| §13 测试策略 | `tests/` |

## 5. 实现中的几个明确约定

1. **big-M 只按物理界推导**：`g, C∈[0,c_max]`、`y∈[0,1]`，并统一加
   `1.05*M + 1e-6` 余量；M≤0 时直接发射，不加松弛。
2. **混合系数保护**：混合系数 + 非 AND 触发必须显式
   `confirm_mixed_trigger=True`；审计发现"恒违反"触发子集时默认拒绝注册，
   如确属用户意图可用 `confirm_audit=True` 显式确认。
3. **空集恒违反不算陷阱**：模型内置 `Σy ≥ 1`，空集的恒违反由"至少一相"
   语义兜底；软约束的恒违反只意味着惩罚，也不视为硬陷阱。
4. **定点周期模式的保守性回退**：设计文档第一阶段用 `l_bar = max ℓ`，
   在定点周期模式下可能过紧。实现先按 `l_bar` 求解；若不可行，再用
   `l_min = min ℓ` 做乐观可行性重试，真实清空时间由第二阶段 LP 校验，
   校验失败则触发 no-good cut 回退。自由模式仍严格使用 `l_bar`。
5. **周期回收开关**：按 §9.5，自由模式第二阶段默认允许
   `C ∈ [c_min, C*]`（`allow_cycle_reduction=True`）。若希望最终周期严格
   等于第一阶段锁定周期，可调用 `solve(allow_cycle_reduction=False)`。
6. **δ_abs 默认值**：取"全部流向各 1 秒最大服务量之和"
   `Σ_m max_p a_pm`。

## 6. 快速示例

```python
from signal_timing import (
    IntersectionData, Movement, Phase,
    LexicographicOptimizer, ConstraintSpec, Trigger,
)

data = IntersectionData(
    movements={"E": Movement("E", 600), "W": Movement("W", 600)},
    phases={
        "P1": Phase("P1", {"E": 1800}),
        "P2": Phase("P2", {"W": 1800}),
    },
    lost_time={("P1", "P2"): 5, ("P2", "P1"): 5},
    g_min=15,
)

opt = LexicographicOptimizer(data, mip_rel_gap=0.001)
opt.add_constraint(ConstraintSpec(
    name="东西直行绿信比均衡",
    coeffs={("g", "P1"): 1.0, ("g", "P2"): -0.8},
    sense=">=", rhs=0.0,
    trigger=Trigger.all_of("P1", "P2"),
))

res = opt.solve()
print(res.cycle, res.selected, res.greens, res.order, res.waste)
```

更完整的示例见 `examples/demo_signal_timing.py`。


## 7. 可视化

新增 `signal_timing/plotting.py`，一行调用绘制"甘特图 + 需求满足情况"：

```python
from signal_timing.plotting import plot_signal_timing_gantt

fig = plot_signal_timing_gantt(
    data,
    result,                 # LexicographicOptimizer.solve() 的返回值
    cycles=2,               # 甘特图展示 2 个周期
    save_path="outputs/signal_timing_gantt.png",
    show=False,
)
```

图中包含三个面板：

1. **相位配时甘特图**（上）：
   - 每个相位一条泳道，绿色条为该相位绿灯时长；
   - 灰色区间为相邻相位清空/全红时间；
   - 斜线区间为周期余量；
   - 红色虚线为周期边界，可重复多个周期展示周期性。
2. **周期内累计服务能力 vs 需求**（左下）：
   - 每个流向一条实线（累计服务）和一条虚线（累计需求）；
   - 服务曲线在相位绿灯期间上升，周期末若不低于需求线即满足。
3. **周期末需求满足情况**（右下）：
   - 每个流向的"需求 vs 提供服务"柱状图；
   - 标注余量 `+x.x veh` 或缺额 `-x.x veh`，绿色为满足、红色为不足。

示例：

```bash
cd /mnt/e/PythonProjects/intersection_milp
/home/qktx/artery_milp/conda-envs/artery_milp/bin/python examples/plot_signal_gantt.py
# 输出 examples/output/signal_timing_gantt.png
```


## 8. 复杂案例：4 进口 x 2 流向 + 南北直行左转搭接

运行：

```bash
cd /mnt/e/PythonProjects/intersection_milp
/home/qktx/artery_milp/conda-envs/artery_milp/bin/python examples/four_approach_overlap_case.py
```

场景：

- 4 个进口 N/E/S/W，每个进口有左转 + 直行，共 8 个流向；
- 候选相位 6 个：
  - `P1_NS_TH`：南北直行对称放行；
  - `P2_NS_LT`：南北左转对称放行；
  - `P3_EW_TH`：东西直行对称放行；
  - `P4_EW_LT`：东西左转对称放行；
  - `P5_N_THLT`：北进口直行 + 左转搭接；
  - `P6_S_THLT`：南进口直行 + 左转搭接；
- 各流向需求错落有致（veh/h）：

| 进口 | 直行 | 左转 |
|---|---:|---:|
| N | 650 | 240 |
| S | 430 | 170 |
| E | 470 | 150 |
| W | 350 | 110 |

  其中北进口直行/左转仍明显不均衡（650 : 240 ≈ 2.71 : 1）。
- `g_min=11s`（>10s），`c_min=40s`，`c_max=180s`；
- 同组相位清空时间 3s，跨组 5s。

优化结果：

```text
最终周期 C = 115.541 s
第一阶段保守 C* = 152.027 s
选中相位 = P1_NS_TH, P3_EW_TH, P4_EW_LT, P5_N_THLT, P6_S_THLT
相位顺序 = P1_NS_TH → P5_N_THLT → P3_EW_TH → P4_EW_LT → P6_S_THLT
相位数 = 5
浪费服务 = 31581.08 veh/h·s
```

对照：只用 4 个对称相位时，周期为 135.849s；加入南北直行左转搭接相位后
周期降至 115.541s，降幅 20.31s（约 14.9%）。

输出新版流向甘特图：

```text
examples/output/four_approach_movement_gantt.png
```


## 9. 新版甘特图：按“进口-流向”泳道（论文风格）

新增 `plot_movement_release_gantt()`，只使用一个绘图区域，默认只画 1 个周期：

```python
from signal_timing.plotting import (
    plot_movement_release_gantt,
    compute_satisfaction_times,
)

sat = compute_satisfaction_times(data, result)
plot_movement_release_gantt(
    data, result,
    cycles=1,
    save_path="outputs/movement_gantt.png",
)
```

表达规则：

- 每个“进口 x 流向”一条横向泳道（本案例 8 条）；
- 若某相位放行了该流向，就在该相位绿灯区间画一段柱子；
- `satisfaction_time()` / `compute_satisfaction_times()` 计算该流向累计服务能力
  首次达到周期需求的时刻 `t_sat`；
- `t_sat` 之前柱子为实心颜色，`t_sat` 之后为细密斜条纹；
- **浅灰竖带表示相邻相位间的清空/全红时间**，顶部标注每个清空区间时长；
- **每条泳道在 `t_sat` 处画同色竖虚线**，并在 y 轴标签直接标出 `t_sat`；
- **每条泳道标注时间长度**：y 轴标签给出该流向一个周期内的总绿灯时长，
  柱子内部再标注每段实心/斜条纹的持续时间；
- 论文风格：按进口分组配色、去掉上/右边框、底部横排图例、300 dpi 输出；
- 背景加入淡灰色横向泳道分隔线，以及较密的纵向网格（主刻度 20s、次刻度 5s）。

复杂案例输出：

```text
examples/output/four_approach_movement_gantt.png
```

复杂案例的 `t_sat` 结果：

```text
N_left   t_sat = 35.99 s
N_thr    t_sat = 44.72 s
S_left   t_sat = 112.54 s
S_thr    t_sat = 112.54 s
E_left   t_sat = 94.45 s
E_thr    t_sat = 79.89 s
W_left   t_sat = 91.36 s
W_thr    t_sat = 72.19 s
```
