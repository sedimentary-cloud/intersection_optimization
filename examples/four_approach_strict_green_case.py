"""4 进口 x 2 流向案例：额外绿灯规则。

在“南北组（直行→搭接→左转）→ 东西组（直行→搭接→左转）”
顺序案例基础上，人为增加两条规则：

1. 所有被选中相位绿灯 >= 15s；
2. P5_N_THLT + P2_NS_LT >= 32s（两者都选中时生效）。

运行：
    /home/qktx/artery_milp/conda-envs/artery_milp/bin/python examples/four_approach_strict_green_case.py
"""
from __future__ import annotations

import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signal_timing import ConstraintSpec, LexicographicOptimizer, Trigger
from signal_timing.plotting import plot_movement_release_gantt

from four_approach_sequence_case import REFERENCE_ORDER, build_data


def main() -> None:
    warnings.simplefilter("ignore")
    data = build_data()

    # 规则 1：所有选中相位绿灯 >= 15s。
    # 直接提高 g_min 即可；半连续联动会自动实现“未选中为 0、选中 >=15s”。
    data.g_min = 15.0

    opt = LexicographicOptimizer(data, mip_rel_gap=0.001, time_limit=60.0)

    # 强制选中 P2_NS_LT，使 P5+P2 规则有意义。
    opt.add_constraint(ConstraintSpec(
        name="force_P2_NS_LT",
        coeffs={("y", "P2_NS_LT"): 1.0},
        sense=">=",
        rhs=1.0,
    ))

    # 规则 2：P5_N_THLT + P2_NS_LT >= 32s，仅当两者都选中时触发。
    opt.add_constraint(ConstraintSpec(
        name="P5_plus_P2_min_32",
        coeffs={("g", "P5_N_THLT"): 1.0, ("g", "P2_NS_LT"): 1.0},
        sense=">=",
        rhs=32.0,
        trigger=Trigger.all_of("P5_N_THLT", "P2_NS_LT"),
    ))

    result = opt.solve(
        reference_mode="hard",
        allow_cycle_reduction=True,
    )
    if result.order is None:
        raise RuntimeError("result.order 为 None；请确认 run_ordering=True")
    order = list(result.order)

    print("=" * 78)
    print("额外绿灯规则下的优化结果")
    print("=" * 78)
    print(f"最终周期 C = {result.cycle:.3f} s   （第一阶段 C* = {result.stage1_cycle:.3f} s）")
    print(f"选中相位 = {result.selected}")
    print(f"相位顺序 = {' → '.join(order)}")
    print(f"相位数 = {result.phase_count}")
    print(f"浪费服务 = {result.waste:.2f} veh/h·s")
    print(f"后验校验 = {'通过' if result.verification.get('passed') else '失败'}")
    print("-" * 78)
    print(f"{'相位':<12s} {'绿灯(s)':>10s}")
    for p in order:
        print(f"{p:<12s} {result.greens[p]:>10.3f}")
    print("-" * 78)
    min_green = min(result.greens[p] for p in order)
    sum_p5_p2 = result.greens.get("P5_N_THLT", 0.0) + result.greens.get("P2_NS_LT", 0.0)
    print(f"最小选中绿灯 = {min_green:.3f} s  （规则要求 >= 15s）")
    print(f"P5_N_THLT + P2_NS_LT = {sum_p5_p2:.3f} s  （规则要求 >= 32s）")
    print("=" * 78)

    out_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "output",
        "four_approach_strict_green_gantt.png",
    )
    plot_movement_release_gantt(
        data,
        result,
        cycles=1,
        save_path=out_path,
        show=False,
        title="4 进口 x 2 流向：额外绿灯规则下的方案",
    )
    print(f"甘特图已保存：{out_path}")


if __name__ == "__main__":
    main()
