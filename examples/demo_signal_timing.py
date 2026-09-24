"""框架使用示例。

运行：
    conda run -n artery_milp python examples/demo_signal_timing.py
"""
from __future__ import annotations

import os
import sys
import warnings

# 允许直接执行 examples/demo_signal_timing.py 时找到项目根目录下的包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signal_timing import (
    ConstraintSpec,
    IntersectionData,
    LexicographicOptimizer,
    Movement,
    Phase,
    Trigger,
)


def build_data() -> IntersectionData:
    # 3 个独立流向，3 个候选相位；清空时间故意做成不对称，便于观察排序层。
    movements = {mid: Movement(mid, 300.0) for mid in ("A", "B", "C")}
    phases = {
        "P1": Phase("P1", {"A": 1800.0}),
        "P2": Phase("P2", {"B": 1800.0}),
        "P3": Phase("P3", {"C": 1800.0}),
    }
    lost = {
        # 低清空时间顺序 P1 -> P2 -> P3 -> P1
        ("P1", "P2"): 2.0,
        ("P2", "P3"): 2.0,
        ("P3", "P1"): 2.0,
        # 其余方向为高清空时间
        ("P2", "P1"): 20.0,
        ("P1", "P3"): 20.0,
        ("P3", "P2"): 20.0,
    }
    return IntersectionData(
        movements=movements,
        phases=phases,
        lost_time=lost,
        g_min=15.0,
        c_min=30.0,
        c_max=180.0,
    )


def main() -> None:
    warnings.simplefilter("ignore")
    data = build_data()
    opt = LexicographicOptimizer(data, mip_rel_gap=0.001, time_limit=30.0)

    # 混合系数硬约束：必须显式用 AND 触发，表示"两个相位都选中时才谈均衡"。
    opt.add_constraint(
        ConstraintSpec(
            name="A_B_绿信比均衡",
            coeffs={("g", "P1"): 1.0, ("g", "P2"): -0.8},
            sense=">=",
            rhs=0.0,
            trigger=Trigger.all_of("P1", "P2"),
        )
    )

    # 软约束：C 相绿灯尽量不小于 20s，违反 1s 惩罚 800。
    opt.add_constraint(
        ConstraintSpec(
            name="C_相最小绿",
            coeffs={("g", "P3"): 1.0},
            sense=">=",
            rhs=20.0,
            trigger=Trigger.any_of("P3"),
            soft=True,
            penalty=800.0,
        )
    )

    result = opt.solve()

    print("=" * 72)
    print("最优周期 C =", round(result.cycle, 3), "s")
    print("选中相位 =", result.selected)
    print("绿灯配时 =", {k: round(v, 3) for k, v in result.greens.items()})
    print("相位顺序 =", result.order)
    print("浪费服务 =", round(result.waste, 3), "veh/h*s")
    print("软约束违反 =", {k: round(v, 3) for k, v in result.sigmas.items()})
    print("no-good cut 回退次数 =", result.fallback_cuts)
    print("后验校验 =", result.verification)
    print("字典序轨迹：")
    for r in result.rounds:
        print(
            f"  - {r.name:24s} obj={r.objective_value!s:>10s} "
            f"C={r.cycle!s:>8s} selected={r.selected}"
        )
    print("=" * 72)


if __name__ == "__main__":
    main()
