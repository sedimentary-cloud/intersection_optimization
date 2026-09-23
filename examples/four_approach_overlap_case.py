"""复杂案例：4 进口 x 2 流向 + 南北直行左转搭接相位。

场景：
- 4 个进口 N/E/S/W，每个进口有 左转 + 直行，共 8 个流向；
- 候选相位：
    P1_NS_TH : 南北直行对称放行
    P2_NS_LT : 南北左转对称放行
    P3_EW_TH : 东西直行对称放行
    P4_EW_LT : 东西左转对称放行
    P5_N_THLT: 北进口直行 + 左转搭接（单进口同时放行直行与左转）
    P6_S_THLT: 南进口直行 + 左转搭接
- 北进口需求不均衡：直行 600 veh/h，左转 250 veh/h；
- 所有被选相位最小绿灯 11 s（> 10 s）。
- 目标：字典序最小化周期（第一目标）。

运行：
    /home/qktx/artery_milp/conda-envs/artery_milp/bin/python examples/four_approach_overlap_case.py
"""
from __future__ import annotations

import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signal_timing import (
    IntersectionData,
    LexicographicOptimizer,
    Movement,
    Phase,
)
from signal_timing.plotting import (
    compute_satisfaction_times,
    plot_movement_release_gantt,
)


def build_data(include_overlap: bool = True) -> IntersectionData:
    """构造 4 进口 x 2 流向案例；include_overlap=False 时只保留 4 个对称相位。"""
    demands = {
        "N_left": 240.0, "N_thr": 650.0,   # 北进口直行/左转不均衡
        "S_left": 170.0, "S_thr": 430.0,
        "E_left": 150.0, "E_thr": 470.0,   # 东进口直行偏大、左转偏小
        "W_left": 110.0, "W_thr": 350.0,   # 西进口整体需求较低
    }
    movements = {mid: Movement(mid, q) for mid, q in demands.items()}
    c_thr, c_left = 1800.0, 1500.0  # 直行/左转饱和流率 veh/h

    phases = {
        "P1_NS_TH": Phase("P1_NS_TH", {"N_thr": c_thr, "S_thr": c_thr}),
        "P2_NS_LT": Phase("P2_NS_LT", {"N_left": c_left, "S_left": c_left}),
        "P3_EW_TH": Phase("P3_EW_TH", {"E_thr": c_thr, "W_thr": c_thr}),
        "P4_EW_LT": Phase("P4_EW_LT", {"E_left": c_left, "W_left": c_left}),
    }
    if include_overlap:
        phases["P5_N_THLT"] = Phase(
            "P5_N_THLT", {"N_thr": c_thr, "N_left": c_left}
        )
        phases["P6_S_THLT"] = Phase(
            "P6_S_THLT", {"S_thr": c_thr, "S_left": c_left}
        )

    # 同组相位间清空 3s，跨组 5s；所有有序对都定义，满足严格清空时间模式。
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
    )


def solve_case(include_overlap: bool):
    data = build_data(include_overlap=include_overlap)
    opt = LexicographicOptimizer(data, mip_rel_gap=0.001, time_limit=60.0)
    result = opt.solve(allow_cycle_reduction=True)
    return data, result


def print_result(title, data, result):
    if result.order is None:
        raise RuntimeError("result.order 为 None；请确认 run_ordering=True")
    order = list(result.order)

    print("=" * 78)
    print(title)
    print("=" * 78)
    print(f"最终周期 C = {result.cycle:.3f} s   （第一阶段保守 C* = {result.stage1_cycle:.3f} s）")
    print(f"选中相位   = {result.selected}")
    print(f"相位顺序   = {' → '.join(order)}")
    print(f"相位数     = {result.phase_count}")
    print(f"浪费服务   = {result.waste:.2f} veh/h·s")
    print(f"后验校验   = {'通过' if result.verification.get('passed') else '失败'}")
    print("-" * 78)
    print(f"{'相位':<12s} {'绿灯(s)':>10s} {'服务流向':<28s}")
    for p in result.selected:
        served = ", ".join(data.phases[p].capacity.keys())
        print(f"{p:<12s} {result.greens[p]:>10.2f} {served:<28s}")
    print("-" * 78)
    print(f"{'流向':<10s} {'需求(veh)':>12s} {'提供(veh)':>12s} {'余量(veh)':>12s} {'状态':>6s}")
    for mid in data.movements:
        demand = data.movements[mid].demand * result.cycle / 3600.0
        provided = (
            sum(
                data.phases[p].capacity.get(mid, 0.0) * result.greens[p]
                for p in order
            )
            / 3600.0
        )
        margin = provided - demand
        print(
            f"{mid:<10s} {demand:>12.3f} {provided:>12.3f} {margin:>+12.3f} "
            f"{'满足' if margin >= -1e-6 else '不足':>6s}"
        )
    print("=" * 78)
    print()


def main():
    warnings.simplefilter("ignore")

    data_with, result_with = solve_case(include_overlap=True)
    data_sym, result_sym = solve_case(include_overlap=False)

    print_result("方案 A：4 个对称相位 + 2 个南北直行左转搭接相位", data_with, result_with)
    print_result("方案 B：对照组，仅 4 个对称相位", data_sym, result_sym)

    cycle_gap = result_sym.cycle - result_with.cycle
    print(
        f"对照结论：加入南北直行左转搭接相位后，周期从 "
        f"{result_sym.cycle:.2f}s 降至 {result_with.cycle:.2f}s，"
        f"降幅 {cycle_gap:.2f}s（{cycle_gap / result_sym.cycle * 100:.1f}%）。"
    )

    sat_times = compute_satisfaction_times(data_with, result_with)
    print("各进口-流向需求满足时刻：")
    for mid, t_sat in sat_times.items():
        print(f"  {mid:8s} t_sat = {t_sat:.2f} s" if t_sat is not None else f"  {mid:8s} 未满足")
    print()

    out_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "output",
        "four_approach_movement_gantt.png",
    )
    plot_movement_release_gantt(
        data_with,
        result_with,
        cycles=1,
        save_path=out_path,
        show=False,
        title="4 进口 x 2 流向：含南北直行左转搭接相位的优化方案",
    )
    print(f"新版流向甘特图已保存：{out_path}")


if __name__ == "__main__":
    main()
