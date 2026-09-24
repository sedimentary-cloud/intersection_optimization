"""4 进口 x 2 流向 + 南北搭接 + 指定相位顺序约束。

顺序要求：
南北组：直行 → 搭接 → 左转；然后东西组：直行 → 搭接 → 左转。

具体到相位池：
1. 南北直行 ``P1_NS_TH``；
2. 南北搭接 ``P5_N_THLT`` / ``P6_S_THLT``（层内可互换）；
3. 南北左转 ``P2_NS_LT``；
4. 东西直行 ``P3_EW_TH``；
5. 东西搭接 ``P7_E_THLT`` / ``P8_W_THLT``（层内可互换）；
6. 东西左转 ``P4_EW_LT``。

实现方式：
- 给 ``IntersectionData`` 提供一个覆盖全部候选相位的分层 ``reference_order``；
- 第二阶段用 ``reference_mode='hard'`` 严格按参考层级枚举顺序；
- 由于当前最优解不主动选择 ``P2_NS_LT``，为了让南北左转出现在顺序中，
  额外加一条硬约束 ``y(P2_NS_LT) >= 1`` 强制选中 P2。

运行：
    conda run -n artery_milp python examples/four_approach_sequence_case.py
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
    make_reference_order_filter,
)
from signal_timing.plotting import plot_movement_release_gantt


REFERENCE_ORDER = [
    # 南北组：直行 → 搭接 → 左转
    ("P1_NS_TH",),                  # 南北直行
    ("P5_N_THLT", "P6_S_THLT"),     # 南北搭接，层内可互换
    ("P2_NS_LT",),                  # 南北左转
    # 东西组：直行 → 搭接 → 左转
    ("P3_EW_TH",),                  # 东西直行
    ("P7_E_THLT", "P8_W_THLT"),     # 东西搭接，层内可互换
    ("P4_EW_LT",),                  # 东西左转
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
        "P7_E_THLT": Phase("P7_E_THLT", {"E_thr": c_thr, "E_left": c_left}),
        "P8_W_THLT": Phase("P8_W_THLT", {"W_thr": c_thr, "W_left": c_left}),
    }
    groups = {
        "P1_NS_TH": "NS", "P2_NS_LT": "NS",
        "P5_N_THLT": "NS", "P6_S_THLT": "NS",
        "P3_EW_TH": "EW", "P4_EW_LT": "EW",
        "P7_E_THLT": "EW", "P8_W_THLT": "EW",
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
        reference_mode="hard",     # 严格按“南北组 → 东西组”的参考层级
        allow_cycle_reduction=True,
    )
    if result.order is None:
        raise RuntimeError("result.order 为 None；请确认 run_ordering=True")

    order = list(result.order)

    print("=" * 78)
    print("含指定相位顺序约束的优化结果")
    print("=" * 78)
    print(f"最终周期 C = {result.cycle:.3f} s   （第一阶段 C* = {result.stage1_cycle:.3f} s）")
    print(f"选中相位 = {result.selected}")
    print(f"相位顺序 = {' → '.join(order)}")
    print(f"相位数 = {result.phase_count}")
    print(f"浪费服务 = {result.waste:.2f} veh/h·s")
    print(f"后验校验 = {'通过' if result.verification.get('passed') else '失败'}")
    print("-" * 78)
    print(f"{'相位':<12s} {'绿灯(s)':>10s} {'服务流向':<28s}")
    for p in result.selected:
        served = ", ".join(data.phases[p].capacity.keys())
        print(f"{p:<12s} {result.greens[p]:>10.2f} {served:<28s}")
    print("=" * 78)

    # 检查顺序是否满足分层参考顺序
    ok_tier = make_reference_order_filter(REFERENCE_ORDER)(tuple(order))
    print(f"顺序规则检查：满足分层参考顺序={ok_tier}")
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
