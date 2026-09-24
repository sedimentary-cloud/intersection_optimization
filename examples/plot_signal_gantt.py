"""绘制信号配时甘特图 + 需求满足情况。

运行：
    conda run -n artery_milp python examples/plot_signal_gantt.py
输出：
    examples/output/signal_timing_gantt.png
"""
from __future__ import annotations

import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signal_timing import IntersectionData, LexicographicOptimizer, Movement, Phase
from signal_timing.plotting import plot_signal_timing_gantt


def build_data() -> IntersectionData:
    # 三个流向需求相同、清空时间相同；选取 480 veh/h 使最优周期恰好
    # 被 "3 个最小绿 + 3 段清空时间" 填满：C = 3*15 + 3*2 = 51s。
    movements = {mid: Movement(mid, 480.0) for mid in ("A", "B", "C")}
    phases = {
        "P1": Phase("P1", {"A": 1800.0}),
        "P2": Phase("P2", {"B": 1800.0}),
        "P3": Phase("P3", {"C": 1800.0}),
    }
    lost = {
        ("P1", "P2"): 2.0,
        ("P2", "P3"): 2.0,
        ("P3", "P1"): 2.0,
        ("P2", "P1"): 2.0,
        ("P1", "P3"): 2.0,
        ("P3", "P2"): 2.0,
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
    opt = LexicographicOptimizer(data, mip_rel_gap=0.001)
    result = opt.solve()

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "signal_timing_gantt.png")
    fig = plot_signal_timing_gantt(
        data,
        result,
        cycles=2,
        save_path=out_path,
        show=False,
    )
    print("配时结果：")
    print(f"  C={result.cycle:.2f}s, selected={result.selected}, order={result.order}")
    print(f"  greens={result.greens}, waste={result.waste:.2f}")
    print(f"甘特图已保存：{out_path}")
    print(f"图片对象：{fig}")


if __name__ == "__main__":
    main()
