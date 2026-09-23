"""4 进口 x 2 流向 + 南北搭接 + 指定相位顺序约束。

顺序要求：
1. 第一相位必须是南北直行 ``P1_NS_TH``；
2. 然后是南北直行左转搭接相位 ``P5_N_THLT`` 或 ``P6_S_THLT``；
3. 最后两个相位必须是两个左转相位 ``P2_NS_LT`` 和 ``P4_EW_LT``（两者内部顺序任意）。
   如果存在东西直行相位 ``P3_EW_TH``，它应排在搭接相位之后、左转之前。

实现方式：
- 给 ``IntersectionData`` 提供一个覆盖全部候选相位的 ``reference_order``；
- 第二阶段用 ``reference_mode='prefer'``：先尝试与参考顺序一致的顺序，
  不可行再回退到全枚举；
- 由于当前最优解不主动选择 ``P2_NS_LT``，为了让"两个左转"都出现在顺序中，
  额外加一条硬约束 ``y(P2_NS_LT) >= 1`` 强制选中 P2。

运行：
    /home/qktx/artery_milp/conda-envs/artery_milp/bin/python examples/four_approach_sequence_case.py
"""
from __future__ import annotations

import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signal_timing import (
    ConstraintSpec,
    IntersectionData,
    LexicographicOptimizer,
    Movement,
    Phase,
)
from signal_timing.plotting import plot_movement_release_gantt


REFERENCE_ORDER = [
    ("P1_NS_TH",),                  # 1. 南北直行
    ("P5_N_THLT", "P6_S_THLT"),     # 2. 两个南北搭接相位，层内可互换
    ("P3_EW_TH",),                  # 3. 东西直行
    ("P2_NS_LT", "P4_EW_LT"),       # 4. 两个左转相位，层内可互换
]


def build_data() -> IntersectionData:
    demands = {
        "N_left": 240.0, "N_thr": 650.0,
        "S_left": 170.0, "S_thr": 430.0,
        "E_left": 150.0, "E_thr": 470.0,
        "W_left": 110.0, "W_thr": 350.0,
    }
    movements = {mid: Movement(mid, q) for mid, q in demands.items()}
    c_thr, c_left = 1800.0, 1500.0
    phases = {
        "P1_NS_TH": Phase("P1_NS_TH", {"N_thr": c_thr, "S_thr": c_thr}),
        "P2_NS_LT": Phase("P2_NS_LT", {"N_left": c_left, "S_left": c_left}),
        "P3_EW_TH": Phase("P3_EW_TH", {"E_thr": c_thr, "W_thr": c_thr}),
        "P4_EW_LT": Phase("P4_EW_LT", {"E_left": c_left, "W_left": c_left}),
        "P5_N_THLT": Phase("P5_N_THLT", {"N_thr": c_thr, "N_left": c_left}),
        "P6_S_THLT": Phase("P6_S_THLT", {"S_thr": c_thr, "S_left": c_left}),
    }
    groups = {
        "P1_NS_TH": "NS", "P2_NS_LT": "NS",
        "P5_N_THLT": "NS", "P6_S_THLT": "NS",
        "P3_EW_TH": "EW", "P4_EW_LT": "EW",
    }
    lost = {}
    for i in phases:
        for j in phases:
            if i != j:
                lost[(i, j)] = 3.0 if groups[i] == groups[j] else 5.0
    return IntersectionData(
        movements=movements,
        phases=phases,
        lost_time=lost,
        g_min=11.0,
        c_min=40.0,
        c_max=180.0,
        reference_order=REFERENCE_ORDER,
    )


def main() -> None:
    warnings.simplefilter("ignore")
    data = build_data()
    opt = LexicographicOptimizer(data, mip_rel_gap=0.001, time_limit=60.0)

    # 当前最优解不会主动选 P2_NS_LT；为了出现"两个左转"的最后两个相位，强制选中 P2。
    opt.add_constraint(
        ConstraintSpec(
            name="force_P2_NS_LT",
            coeffs={("y", "P2_NS_LT"): 1.0},
            sense=">=",
            rhs=1.0,
        )
    )

    result = opt.solve(
        reference_mode="prefer",   # 先尝试参考顺序，不可行再回退
        allow_cycle_reduction=True,
    )

    print("=" * 78)
    print("含指定相位顺序约束的优化结果")
    print("=" * 78)
    print(f"最终周期 C = {result.cycle:.3f} s   （第一阶段 C* = {result.stage1_cycle:.3f} s）")
    print(f"选中相位 = {result.selected}")
    print(f"相位顺序 = {' → '.join(result.order) if result.order else None}")
    print(f"相位数 = {result.phase_count}")
    print(f"浪费服务 = {result.waste:.2f} veh/h·s")
    print(f"后验校验 = {'通过' if result.verification.get('passed') else '失败'}")
    print("-" * 78)
    print(f"{'相位':<12s} {'绿灯(s)':>10s} {'服务流向':<28s}")
    for p in result.selected:
        served = ", ".join(data.phases[p].capacity.keys())
        print(f"{p:<12s} {result.greens[p]:>10.2f} {served:<28s}")
    print("=" * 78)

    # 检查顺序是否满足规则
    order = list(result.order)
    ok_first = order[0] == "P1_NS_TH"
    ok_last = set(order[-2:]) == {"P2_NS_LT", "P4_EW_LT"}
    print(f"顺序规则检查：首相位南北直行={ok_first}，最后两个为左转={ok_last}")
    print(
        "参考顺序："
        + " → ".join(
            "[" + ", ".join(group) + "]" for group in REFERENCE_ORDER
        )
    )

    out_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "output",
        "four_approach_sequence_gantt.png",
    )
    plot_movement_release_gantt(
        data,
        result,
        cycles=1,
        save_path=out_path,
        show=False,
        title="4 进口 x 2 流向：指定相位顺序约束下的优化方案",
    )
    print(f"甘特图已保存：{out_path}")


if __name__ == "__main__":
    main()
