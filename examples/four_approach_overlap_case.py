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
- 北进口需求不均衡：直行 650 veh/h，左转 240 veh/h；
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


def build_intersection_data(
    include_overlap_phases: bool = True,
) -> IntersectionData:
    """构造 4 进口 x 2 流向案例。

    include_overlap_phases=False 时只保留 4 个对称相位。
    """
    movement_demands = {
        "N_left": 240.0, "N_thr": 650.0,   # 北进口直行/左转不均衡
        "S_left": 170.0, "S_thr": 430.0,
        "E_left": 150.0, "E_thr": 470.0,   # 东进口直行偏大、左转偏小
        "W_left": 110.0, "W_thr": 350.0,   # 西进口整体需求较低
    }
    movements_by_id = {
        movement_id: Movement(movement_id, demand)
        for movement_id, demand in movement_demands.items()
    }

    through_saturation_flow = 1800.0   # 直行饱和流率 veh/h
    left_turn_saturation_flow = 1500.0  # 左转饱和流率 veh/h

    candidate_phases = {
        "P1_NS_TH": Phase(
            "P1_NS_TH",
            {"N_thr": through_saturation_flow, "S_thr": through_saturation_flow},
        ),
        "P2_NS_LT": Phase(
            "P2_NS_LT",
            {"N_left": left_turn_saturation_flow, "S_left": left_turn_saturation_flow},
        ),
        "P3_EW_TH": Phase(
            "P3_EW_TH",
            {"E_thr": through_saturation_flow, "W_thr": through_saturation_flow},
        ),
        "P4_EW_LT": Phase(
            "P4_EW_LT",
            {"E_left": left_turn_saturation_flow, "W_left": left_turn_saturation_flow},
        ),
    }

    if include_overlap_phases:
        candidate_phases["P5_N_THLT"] = Phase(
            "P5_N_THLT",
            {"N_thr": through_saturation_flow, "N_left": left_turn_saturation_flow},
        )
        candidate_phases["P6_S_THLT"] = Phase(
            "P6_S_THLT",
            {"S_thr": through_saturation_flow, "S_left": left_turn_saturation_flow},
        )

    # 同组相位间清空 3s，跨组 5s；所有有序对都定义，满足严格清空时间模式。
    phase_direction_groups = {
        "P1_NS_TH": "NS", "P2_NS_LT": "NS",
        "P5_N_THLT": "NS", "P6_S_THLT": "NS",
        "P3_EW_TH": "EW", "P4_EW_LT": "EW",
    }
    clearance_times = {}
    for from_phase_id in candidate_phases:
        for to_phase_id in candidate_phases:
            if from_phase_id == to_phase_id:
                continue
            same_direction_group = (
                phase_direction_groups[from_phase_id]
                == phase_direction_groups[to_phase_id]
            )
            clearance_times[(from_phase_id, to_phase_id)] = (
                3.0 if same_direction_group else 5.0
            )

    return IntersectionData(
        movements=movements_by_id,
        phases=candidate_phases,
        lost_time=clearance_times,
        g_min=11.0,
        c_min=40.0,
        c_max=180.0,
    )


def solve_intersection_case(include_overlap_phases: bool):
    """构造并求解一个案例，返回 (intersection_data, optimization_result)。"""
    intersection_data = build_intersection_data(
        include_overlap_phases=include_overlap_phases
    )
    optimizer = LexicographicOptimizer(
        intersection_data,
        mip_rel_gap=0.001,
        time_limit=60.0,
    )
    optimization_result = optimizer.solve(allow_cycle_reduction=True)
    return intersection_data, optimization_result


def print_optimization_result(
    case_title: str,
    intersection_data: IntersectionData,
    optimization_result,
) -> None:
    """打印某个方案的关键结果和逐流向需求满足情况。"""
    if optimization_result.order is None:
        raise RuntimeError("optimization_result.order 为 None；请确认 run_ordering=True")
    phase_order = list(optimization_result.order)

    print("=" * 78)
    print(case_title)
    print("=" * 78)
    print(
        f"最终周期 C = {optimization_result.cycle:.3f} s   "
        f"（第一阶段保守 C* = {optimization_result.stage1_cycle:.3f} s）"
    )
    print(f"选中相位   = {optimization_result.selected}")
    print(f"相位顺序   = {' → '.join(phase_order)}")
    print(f"相位数     = {optimization_result.phase_count}")
    print(f"浪费服务   = {optimization_result.waste:.2f} veh/h·s")
    print(
        f"后验校验   = "
        f"{'通过' if optimization_result.verification.get('passed') else '失败'}"
    )
    print("-" * 78)
    print(f"{'相位':<12s} {'绿灯(s)':>10s} {'服务流向':<28s}")
    for phase_id in optimization_result.selected:
        served_movement_ids = ", ".join(
            intersection_data.phases[phase_id].capacity.keys()
        )
        print(
            f"{phase_id:<12s} "
            f"{optimization_result.greens[phase_id]:>10.2f} "
            f"{served_movement_ids:<28s}"
        )
    print("-" * 78)
    print(
        f"{'流向':<10s} {'需求(veh)':>12s} {'提供(veh)':>12s} "
        f"{'余量(veh)':>12s} {'状态':>6s}"
    )
    for movement_id in intersection_data.movements:
        cycle_demand_veh = (
            intersection_data.movements[movement_id].demand
            * optimization_result.cycle
            / 3600.0
        )
        provided_service_veh = (
            sum(
                intersection_data.phases[phase_id].capacity.get(movement_id, 0.0)
                * optimization_result.greens[phase_id]
                for phase_id in phase_order
            )
            / 3600.0
        )
        demand_margin_veh = provided_service_veh - cycle_demand_veh
        demand_satisfied = demand_margin_veh >= -1e-6
        print(
            f"{movement_id:<10s} {cycle_demand_veh:>12.3f} "
            f"{provided_service_veh:>12.3f} {demand_margin_veh:>+12.3f} "
            f"{'满足' if demand_satisfied else '不足':>6s}"
        )
    print("=" * 78)
    print()


def main() -> None:
    warnings.simplefilter("ignore")

    data_with_overlap, result_with_overlap = solve_intersection_case(
        include_overlap_phases=True
    )
    data_symmetric_only, result_symmetric_only = solve_intersection_case(
        include_overlap_phases=False
    )

    print_optimization_result(
        "方案 A：4 个对称相位 + 2 个南北直行左转搭接相位",
        data_with_overlap,
        result_with_overlap,
    )
    print_optimization_result(
        "方案 B：对照组，仅 4 个对称相位",
        data_symmetric_only,
        result_symmetric_only,
    )

    cycle_reduction_seconds = (
        result_symmetric_only.cycle - result_with_overlap.cycle
    )
    cycle_reduction_percent = (
        cycle_reduction_seconds / result_symmetric_only.cycle * 100.0
    )
    print(
        f"对照结论：加入南北直行左转搭接相位后，周期从 "
        f"{result_symmetric_only.cycle:.2f}s 降至 "
        f"{result_with_overlap.cycle:.2f}s，"
        f"降幅 {cycle_reduction_seconds:.2f}s"
        f"（{cycle_reduction_percent:.1f}%）。"
    )

    movement_satisfaction_times = compute_satisfaction_times(
        data_with_overlap,
        result_with_overlap,
    )
    print("各进口-流向需求满足时刻：")
    for movement_id, satisfaction_time_seconds in movement_satisfaction_times.items():
        if satisfaction_time_seconds is None:
            print(f"  {movement_id:8s} 未满足")
        else:
            print(
                f"  {movement_id:8s} "
                f"t_sat = {satisfaction_time_seconds:.2f} s"
            )
    print()

    gantt_output_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "output",
        "four_approach_movement_gantt.png",
    )
    plot_movement_release_gantt(
        data_with_overlap,
        result_with_overlap,
        cycles=1,
        save_path=gantt_output_path,
        show=False,
        title="4 进口 x 2 流向：含南北直行左转搭接相位的优化方案",
    )
    print(f"新版流向甘特图已保存：{gantt_output_path}")


if __name__ == "__main__":
    main()
