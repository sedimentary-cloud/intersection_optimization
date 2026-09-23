"""测试公共数据/工具。"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from signal_timing import IntersectionData, Movement, Phase
from signal_timing.variables import VarKey, VarRegistry


def two_phase_data(
    *,
    demand_e: float = 600.0,
    demand_w: float = 600.0,
    capacity_e: float = 1800.0,
    capacity_w: float = 1800.0,
    lost_ew: float = 5.0,
    lost_we: float = 5.0,
    g_min: float = 15.0,
    c_min: float = 30.0,
    c_max: float = 180.0,
) -> IntersectionData:
    return IntersectionData(
        movements={
            "E": Movement("E", demand_e),
            "W": Movement("W", demand_w),
        },
        phases={
            "P1": Phase("P1", {"E": capacity_e}),
            "P2": Phase("P2", {"W": capacity_w}),
        },
        lost_time={("P1", "P2"): lost_ew, ("P2", "P1"): lost_we},
        g_min=g_min,
        c_min=c_min,
        c_max=c_max,
    )


def three_phase_asym_data(
    *,
    demand: float = 300.0,
    capacity: float = 1800.0,
    low: float = 2.0,
    high: float = 20.0,
    g_min: float = 15.0,
    c_min: float = 30.0,
    c_max: float = 180.0,
    low_cycle: Sequence[str] = ("P1", "P2", "P3"),
) -> IntersectionData:
    """3 相位，一组低清空时间形成低耗顺序，其余对为高清空时间。"""
    movements = {mid: Movement(mid, demand) for mid in ("A", "B", "C")}
    phases = {
        "P1": Phase("P1", {"A": capacity}),
        "P2": Phase("P2", {"B": capacity}),
        "P3": Phase("P3", {"C": capacity}),
    }
    lost: Dict[Tuple[str, str], float] = {}
    seq = list(low_cycle)
    for i, p in enumerate(seq):
        lost[(p, seq[(i + 1) % len(seq)])] = low
    for i, p in enumerate(seq):
        for j, q in enumerate(seq):
            if p == q:
                continue
            if (p, q) not in lost:
                lost[(p, q)] = high
    # 确保所有有序对都有定义
    for p in seq:
        for q in seq:
            if p != q:
                lost.setdefault((p, q), high)
    return IntersectionData(
        movements=movements,
        phases=phases,
        lost_time=lost,
        g_min=g_min,
        c_min=c_min,
        c_max=c_max,
    )


def compiler_registry(
    phases: Sequence[str] = ("P1", "P2"),
    *,
    c_max: float = 180.0,
    c_min: float = 30.0,
    with_C: bool = True,
) -> VarRegistry:
    reg = VarRegistry()
    for pid in phases:
        reg.register(("y", pid), 0.0, 1.0, integrality=1, kind="y")
        reg.register(("g", pid), 0.0, c_max, integrality=0, kind="g")
    if with_C:
        reg.register(("C", ""), c_min, c_max, integrality=0, kind="C")
    return reg


def four_approach_data(
    include_overlap: bool = True,
    reference_order=None,
    include_ew_overlap: bool = False,
) -> IntersectionData:
    """4 进口 x 2 流向案例：对称相位 + 南北直行左转搭接相位。"""
    demands = {
        "N_left": 240.0,
        "N_thr": 650.0,   # 北进口仍保持直行远大于左转的不均衡
        "S_left": 170.0,
        "S_thr": 430.0,
        "E_left": 150.0,
        "E_thr": 470.0,
        "W_left": 110.0,
        "W_thr": 350.0,
    }
    movements = {mid: Movement(mid, q) for mid, q in demands.items()}
    c_thr, c_left = 1800.0, 1500.0
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
    if include_ew_overlap:
        phases["P7_E_THLT"] = Phase(
            "P7_E_THLT", {"E_thr": c_thr, "E_left": c_left}
        )
        phases["P8_W_THLT"] = Phase(
            "P8_W_THLT", {"W_thr": c_thr, "W_left": c_left}
        )
    groups = {
        "P1_NS_TH": "NS",
        "P2_NS_LT": "NS",
        "P5_N_THLT": "NS",
        "P6_S_THLT": "NS",
        "P3_EW_TH": "EW",
        "P4_EW_LT": "EW",
        "P7_E_THLT": "EW",
        "P8_W_THLT": "EW",
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
        reference_order=reference_order,
    )
