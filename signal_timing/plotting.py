"""信号配时方案可视化（甘特图 + 需求满足情况）。

设计目标：
- 甘特图：按 ``result.order`` 展示每个相位的绿灯区间、相邻相位清空/全红区间、
  周期余量以及周期边界；可重复多个周期展示周期性。
- 需求满足：周期内各流向的累计服务能力曲线 vs 累计需求曲线，以及周期末
  "需求 vs 提供" 柱状图。

用法：
    from signal_timing.plotting import plot_signal_timing_gantt
    fig = plot_signal_timing_gantt(data, result, cycles=2,
                                   save_path="outputs/gantt.png")
"""
from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# matplotlib 缓存目录在受限环境下可能不可写，必须在 import pyplot 之前设置。
_MPL_CONFIG_DIR = os.path.join(tempfile.gettempdir(), "mplconfig_signal_timing")
os.makedirs(_MPL_CONFIG_DIR, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", _MPL_CONFIG_DIR)

import matplotlib  # noqa: E402

if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
    matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from matplotlib.ticker import MultipleLocator  # noqa: E402

from .data import IntersectionData  # noqa: E402
from .result import OptimizationResult  # noqa: E402

_TOL = 1e-6

# 常见中文字体候选（Windows/WSL、Linux）
_CJK_FONT_CANDIDATES = [
    "/mnt/c/Windows/Fonts/msyh.ttc",
    "/mnt/c/Windows/Fonts/msyhbd.ttc",
    "/mnt/c/Windows/Fonts/simhei.ttf",
    "/mnt/c/Windows/Fonts/SimHei.ttf",
    "/mnt/c/Windows/Fonts/simsun.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]

_FONT_READY = False


def configure_chinese_font() -> bool:
    """尝试加载一个可用的中文字体；返回是否成功。

    该函数是幂等的，可重复调用。
    """
    global _FONT_READY
    if _FONT_READY:
        return True
    for path in _CJK_FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            font_manager.fontManager.addfont(path)
            prop = font_manager.FontProperties(fname=path)
            name = prop.get_name()
            matplotlib.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            matplotlib.rcParams["font.family"] = "sans-serif"
            matplotlib.rcParams["axes.unicode_minus"] = False
            _FONT_READY = True
            return True
        except Exception:  # pragma: no cover - 字体环境差异
            continue
    # 没有中文字体时仍继续，中文可能显示为方框
    return False


@dataclass
class ScheduleInterval:
    """一个周期内的一个时间区间。"""

    kind: str          # 'green' | 'clearance' | 'slack'
    label: str
    start: float
    end: float
    phase: Optional[str] = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class SchedulePlan:
    """用于绘图的一个周期配时方案。"""

    order: List[str]
    greens: Dict[str, float]
    cycle: float
    intervals: List[ScheduleInterval]
    clearance_total: float


def build_schedule(data: IntersectionData, result: OptimizationResult) -> SchedulePlan:
    """从优化结果构造一个周期的甘特区间。

    若 ``result.order`` 存在（排序后处理已运行），使用真实相邻清空时间；
    否则使用设计文档第一阶段的保守值 ``l_bar``。
    """
    order = list(result.order) if result.order else list(result.selected)
    if not order:
        raise ValueError("result 中没有选中相位，无法绘制甘特图")
    missing = [p for p in order if p not in data.phases]
    if missing:
        raise ValueError(f"result.order 含未知相位: {missing}")

    greens = {p: max(0.0, float(result.greens.get(p, 0.0))) for p in order}
    cycle = float(result.cycle)
    n = len(order)
    intervals: List[ScheduleInterval] = []
    t = 0.0
    clearance_total = 0.0
    for i, p in enumerate(order):
        g = greens[p]
        if g > _TOL:
            intervals.append(ScheduleInterval("green", p, t, t + g, phase=p))
        t += g
        if n > 1:
            nxt = order[(i + 1) % n]
            clr = (
                data.clearance(p, nxt)
                if result.order is not None
                else data.l_bar
            )
        else:
            clr = 0.0
        if clr > _TOL:
            intervals.append(
                ScheduleInterval("clearance", f"{p}→{nxt}", t, t + clr, phase=p)
            )
            t += clr
            clearance_total += clr
    slack = cycle - t
    if slack > _TOL:
        intervals.append(ScheduleInterval("slack", "周期余量", t, cycle))
    return SchedulePlan(
        order=order,
        greens=greens,
        cycle=cycle,
        intervals=intervals,
        clearance_total=clearance_total,
    )


def satisfaction_time(data: IntersectionData, plan: SchedulePlan, mid: str) -> Optional[float]:
    """计算某个流向在周期内累计服务能力首次达到其周期需求的时间。

    返回相对周期起点的秒数；若一个周期内无法满足则返回 ``None``。
    """
    if mid not in data.movements:
        raise ValueError(f"未知流向 {mid}")
    demand_veh = data.movements[mid].demand * plan.cycle / 3600.0
    intervals = []
    for iv in plan.intervals:
        if iv.kind != "green" or iv.phase is None:
            continue
        a = data.phases[iv.phase].capacity.get(mid, 0.0)
        if a > 0.0:
            intervals.append((iv.start, iv.end, a / 3600.0))
    if not intervals:
        return None

    points = {0.0, plan.cycle}
    for start, end, _ in intervals:
        points.add(start)
        points.add(end)
    points = sorted(points)

    cumulative = 0.0
    for left, right in zip(points[:-1], points[1:]):
        if right <= left + 1e-12:
            continue
        probe = 0.5 * (left + right)
        rate = sum(
            r for s, e, r in intervals if s - 1e-9 <= probe <= e + 1e-9
        )
        if rate <= 0.0:
            continue
        need = demand_veh - cumulative
        if need <= 1e-9:
            return left
        dt = need / rate
        if left + dt <= right + 1e-9:
            return max(0.0, min(left + dt, plan.cycle))
        cumulative += rate * (right - left)
    if cumulative + 1e-9 >= demand_veh:
        return plan.cycle
    return None


def compute_satisfaction_times(
    data: IntersectionData, result: OptimizationResult
) -> Dict[str, Optional[float]]:
    """返回 {流向: 需求满足时刻(s)}；一个周期内不满足则为 None。"""
    plan = build_schedule(data, result)
    return {mid: satisfaction_time(data, plan, mid) for mid in data.movements}


def _movement_label(mid: str) -> str:
    """把 N_left / N_thr 之类的 id 转成中文/可读标签。"""
    approach_names = {"N": "北", "S": "南", "E": "东", "W": "西"}
    movement_names = {"left": "左转", "thr": "直行", "through": "直行"}
    if "_" in mid:
        approach, movement = mid.split("_", 1)
        return f"{approach_names.get(approach, approach)} {movement_names.get(movement, movement)}"
    return mid


# --------------------------------------------------------------------------- #
# 绘图内部工具
# --------------------------------------------------------------------------- #
def _phase_colors(order: Sequence[str]) -> Dict[str, tuple]:
    base = plt.cm.Set3(np.linspace(0, 1, max(len(order), 3)))
    return {p: base[i % len(base)] for i, p in enumerate(order)}


def _draw_gantt(ax, data: IntersectionData, plan: SchedulePlan, cycles: int) -> None:
    order = plan.order
    n = len(order)
    ypos = {p: n - 1 - i for i, p in enumerate(order)}
    colors = _phase_colors(order)

    for k in range(cycles):
        offset = k * plan.cycle
        if k % 2 == 1:
            ax.axvspan(offset, offset + plan.cycle, color="0.95", zorder=0)
        for iv in plan.intervals:
            start, end = offset + iv.start, offset + iv.end
            if iv.kind == "clearance":
                ax.axvspan(start, end, color="0.75", alpha=0.55, zorder=1)
            elif iv.kind == "slack":
                ax.axvspan(start, end, facecolor="white", edgecolor="0.6",
                           hatch="///", alpha=0.65, zorder=1)
            elif iv.kind == "green" and iv.phase is not None:
                width = iv.duration
                y = ypos[iv.phase]
                ax.barh(
                    y,
                    width,
                    left=start,
                    height=0.52,
                    color=colors[iv.phase],
                    edgecolor="black",
                    linewidth=0.6,
                    zorder=3,
                )
                if width > 0.055 * plan.cycle:
                    ax.text(
                        start + width / 2.0,
                        y,
                        f"{width:.1f}s",
                        ha="center",
                        va="center",
                        fontsize=8,
                        zorder=4,
                    )
        # 周期边界
        ax.axvline(offset + plan.cycle, color="crimson", linestyle="--",
                   linewidth=1.0, zorder=5)
        ax.text(
            offset + plan.cycle / 2.0,
            n - 0.32,
            f"第 {k + 1} 周期",
            ha="center",
            va="bottom",
            color="crimson",
            fontsize=9,
            zorder=6,
        )

    labels = []
    for p in order:
        served = "、".join(data.phases[p].capacity.keys())
        labels.append(f"{p}\n({served})")
    ax.set_yticks([ypos[p] for p in order])
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_ylim(-0.7, n - 0.15)
    ax.set_xlim(0, cycles * plan.cycle)
    ax.set_xlabel("时间 (s)", fontsize=10)
    ax.set_title("相位配时甘特图（绿灯 + 清空/全红 + 周期余量）", fontsize=11)
    ax.grid(axis="x", alpha=0.25, linestyle=":")

    handles = [
        Patch(facecolor=colors[order[0]], edgecolor="black", label="绿灯"),
        Patch(facecolor="0.75", alpha=0.55, label="清空/全红"),
        Patch(facecolor="white", edgecolor="0.6", hatch="///", label="周期余量"),
        Line2D([0], [0], color="crimson", linestyle="--", label="周期边界"),
    ]
    ax.legend(handles=handles, loc="upper right", fontsize=8, ncol=2, framealpha=0.9)


def _cumulative_curves(
    data: IntersectionData,
    plan: SchedulePlan,
    mid: str,
    *,
    cycles: int = 1,
    n_points: int = 3001,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """返回某个流向在若干周期内的 (时间, 累计服务, 累计需求)。"""
    total_time = plan.cycle * cycles
    t = np.linspace(0.0, total_time, n_points)
    rate = np.zeros_like(t)  # veh/s
    for k in range(cycles):
        offset = k * plan.cycle
        for iv in plan.intervals:
            if iv.kind != "green" or iv.phase is None:
                continue
            a = data.phases[iv.phase].capacity.get(mid, 0.0)
            if a <= 0.0:
                continue
            mask = (t >= offset + iv.start) & (t < offset + iv.end)
            rate[mask] = a / 3600.0
    dt = t[1] - t[0]
    service = np.empty_like(t)
    service[0] = 0.0
    service[1:] = np.cumsum(0.5 * (rate[:-1] + rate[1:]) * dt)
    demand = data.movements[mid].demand / 3600.0 * t
    return t, service, demand


def _movement_colors(mids: Sequence[str]) -> Dict[str, tuple]:
    cmap = plt.cm.tab10
    return {m: cmap(i % 10) for i, m in enumerate(mids)}


def _draw_demand_curves(ax, data: IntersectionData, plan: SchedulePlan) -> None:
    mids = list(data.movements.keys())
    colors = _movement_colors(mids)
    for mid in mids:
        t, service, demand = _cumulative_curves(data, plan, mid, cycles=1)
        color = colors[mid]
        ax.plot(t, service, color=color, linewidth=2.0, label=f"{mid} 累计服务")
        ax.plot(t, demand, color=color, linewidth=1.2, linestyle="--",
                label=f"{mid} 累计需求")
    ax.axvline(plan.cycle, color="crimson", linestyle="--", linewidth=1.0)
    ax.set_xlim(0, plan.cycle)
    ax.set_xlabel("时间 (s)", fontsize=10)
    ax.set_ylabel("累计车辆数 (veh)", fontsize=10)
    ax.set_title("周期内累计服务能力 vs 需求", fontsize=11)
    ax.grid(alpha=0.25, linestyle=":")
    ax.legend(fontsize=7, ncol=2, loc="upper left", framealpha=0.9)


def _draw_satisfaction_bars(ax, data: IntersectionData, plan: SchedulePlan) -> None:
    mids = list(data.movements.keys())
    demand_veh = np.array([data.movements[m].demand * plan.cycle / 3600.0 for m in mids])
    provided_veh = np.array(
        [
            sum(
                data.phases[p].capacity.get(m, 0.0) * plan.greens[p]
                for p in plan.order
            )
            / 3600.0
            for m in mids
        ]
    )
    margins = provided_veh - demand_veh

    x = np.arange(len(mids))
    width = 0.34
    ax.bar(x - width / 2.0, demand_veh, width, color="#9ecae1",
           edgecolor="black", linewidth=0.5, label="需求")
    bar_colors = ["#2ca02c" if m >= -1e-6 else "#d62728" for m in margins]
    bars = ax.bar(x + width / 2.0, provided_veh, width, color=bar_colors,
                  edgecolor="black", linewidth=0.5, label="提供服务")
    for i, (xi, bar, margin) in enumerate(zip(x, bars, margins)):
        status = "满足" if margin >= -1e-6 else "不足"
        ax.text(
            xi + width / 2.0,
            bar.get_height(),
            f"{status}\n{margin:+.1f}",
            ha="center",
            va="bottom",
            fontsize=8,
            color="#1a7f37" if margin >= -1e-6 else "#b30000",
        )
    ax.set_xticks(x)
    ax.set_xticklabels(mids)
    ax.set_ylabel("车辆数/周期 (veh)", fontsize=10)
    ax.set_title("周期末需求满足情况", fontsize=11)
    ax.grid(axis="y", alpha=0.25, linestyle=":")
    ax.legend(fontsize=8, loc="upper left")


# --------------------------------------------------------------------------- #
# 对外接口
# --------------------------------------------------------------------------- #
def plot_signal_timing_gantt(
    data: IntersectionData,
    result: OptimizationResult,
    *,
    cycles: int = 2,
    save_path: Optional[str] = None,
    show: bool = False,
    figsize: Tuple[float, float] = (14.0, 8.5),
    dpi: int = 150,
    title: Optional[str] = None,
):
    """绘制信号配时甘特图 + 需求满足情况。

    参数
    ----
    data : IntersectionData
    result : OptimizationResult
        ``LexicographicOptimizer.solve`` 的返回值。
    cycles : int
        甘特图重复多少个周期（需求曲线和柱状图固定展示 1 个周期）。
    save_path : str, optional
        保存图片路径；不存在则自动创建目录。
    show : bool
        是否调用 ``plt.show()``（WSL 无显示环境时保持 False）。
    """
    configure_chinese_font()
    plan = build_schedule(data, result)

    fig = plt.figure(figsize=figsize, constrained_layout=True)
    gs = fig.add_gridspec(2, 2, height_ratios=[1.15, 1.0], hspace=0.28, wspace=0.22)
    ax_gantt = fig.add_subplot(gs[0, :])
    ax_demand = fig.add_subplot(gs[1, 0])
    ax_bars = fig.add_subplot(gs[1, 1])

    _draw_gantt(ax_gantt, data, plan, cycles=max(1, int(cycles)))
    _draw_demand_curves(ax_demand, data, plan)
    _draw_satisfaction_bars(ax_bars, data, plan)

    sigma_text = ""
    if result.sigmas:
        parts = [f"{k}={v:.2f}" for k, v in result.sigmas.items() if abs(v) > 1e-9]
        if parts:
            sigma_text = " | 软约束违反: " + ", ".join(parts)
    summary = (
        f"C={plan.cycle:.1f}s | 选中 {len(plan.order)} 相位 | 顺序: "
        f"{' → '.join(plan.order)} | 清空合计 {plan.clearance_total:.1f}s | "
        f"浪费 {result.waste:.1f} veh/h·s{sigma_text}"
    )
    fig.suptitle(
        (title + "\n" if title else "") + "信号配时方案与需求满足情况\n" + summary,
        fontsize=13,
    )

    if save_path:
        directory = os.path.dirname(os.path.abspath(save_path))
        os.makedirs(directory, exist_ok=True)
        fig.savefig(save_path, dpi=dpi, bbox_inches="tight")
    if show:
        plt.show()
    return fig

def _add_duration_label(ax, start: float, end: float, y: float, cycle: float) -> None:
    """在柱子内部标注该段的持续时间（秒）。"""
    width = end - start
    if width < max(2.0, 0.035 * cycle):
        return
    ax.text(
        (start + end) / 2.0,
        y,
        f"{width:.1f}s",
        ha="center",
        va="center",
        fontsize=6.5,
        color="#111111",
        zorder=6,
        bbox=dict(
            boxstyle="round,pad=0.10",
            facecolor="white",
            edgecolor="none",
            alpha=0.78,
        ),
    )


def _paper_movement_colors(movements: Sequence[str]) -> Dict[str, str]:
    """论文风格配色：按进口分组，左转/直行用同色系深浅区分。"""
    palette = {
        # 北：蓝
        "N_left": "#2F5C8F", "N_thr": "#8FB4D9",
        # 南：绿
        "S_left": "#2E7D5B", "S_thr": "#9CCFAD",
        # 东：橙
        "E_left": "#C46A2E", "E_thr": "#F0B98D",
        # 西：紫
        "W_left": "#6A5A9E", "W_thr": "#B3A6D1",
    }
    fallback = plt.cm.tab20(np.linspace(0, 1, max(len(movements), 1)))
    return {
        mid: palette.get(mid, fallback[i % len(fallback)])
        for i, mid in enumerate(movements)
    }


def plot_movement_release_gantt(
    data: IntersectionData,
    result: OptimizationResult,
    *,
    cycles: int = 1,
    save_path: Optional[str] = None,
    show: bool = False,
    figsize: Tuple[float, float] = (13.5, 6.8),
    dpi: int = 300,
    title: Optional[str] = None,
):
    """论文风格甘特图：每个进口-流向一条泳道，只使用一个绘图区域。

    表达规则：
    - 每个进口 x 流向一条泳道；
    - 相位放行该流向时，在对应绿灯区间画一段柱子；
    - 实心部分表示该流向周期需求尚未完全满足；
    - 细密斜条纹部分表示需求已满足后的额外服务能力；
    - 浅灰竖带表示相邻相位之间的清空/全红时间，并在顶部标注时长；
    - 每条泳道在 t_sat 处画同色竖虚线，表示该流向需求被完全满足的时刻。

    默认只绘制 1 个周期，适合论文插图。
    """
    configure_chinese_font()
    plan = build_schedule(data, result)
    movements = list(data.movements.keys())
    n = len(movements)
    ypos = {mid: n - 1 - i for i, mid in enumerate(movements)}
    colors = _paper_movement_colors(movements)
    sat_times = {mid: satisfaction_time(data, plan, mid) for mid in movements}
    unsatisfied = [mid for mid, t in sat_times.items() if t is None]
    clearance_intervals = [iv for iv in plan.intervals if iv.kind == "clearance"]
    slack_intervals = [iv for iv in plan.intervals if iv.kind == "slack"]

    # 每个流向在一个周期内的总绿灯时长（可能由多个相位共同服务）
    green_totals = {mid: 0.0 for mid in movements}
    for iv in plan.intervals:
        if iv.kind != "green" or iv.phase is None:
            continue
        for mid in movements:
            if data.phases[iv.phase].capacity.get(mid, 0.0) > 0.0:
                green_totals[mid] += iv.duration

    paper_style = {
        "font.size": 9,
        "axes.titlesize": 11,
        "axes.labelsize": 9.5,
        "xtick.labelsize": 8.5,
        "ytick.labelsize": 8.5,
        "legend.fontsize": 8,
        "axes.linewidth": 0.8,
        "xtick.major.width": 0.8,
        "ytick.major.width": 0.8,
        "xtick.direction": "out",
        "ytick.direction": "out",
        "figure.dpi": dpi,
    }

    with plt.rc_context(paper_style):
        fig, ax = plt.subplots(figsize=figsize)
        ax.set_axisbelow(True)

        # ---- 每个周期：清空时间、周期余量、放行柱子和周期边界 ----
        cycles = max(1, int(cycles))
        for k in range(cycles):
            offset = k * plan.cycle

            # 清空时间：浅灰竖带 + 顶部时长标注
            for iv in clearance_intervals:
                start, end = offset + iv.start, offset + iv.end
                ax.axvspan(start, end, facecolor="#D9D9D9", edgecolor="none",
                           alpha=0.55, zorder=0)
                ax.text(
                    (start + end) / 2.0,
                    n - 0.08,
                    f"{iv.duration:.0f}s",
                    ha="center",
                    va="bottom",
                    rotation=90,
                    fontsize=6.5,
                    color="#555555",
                    zorder=6,
                )

            # 周期余量：浅色斜纹带 + 时长标注
            for iv in slack_intervals:
                start, end = offset + iv.start, offset + iv.end
                ax.axvspan(start, end, facecolor="#F5F5F5",
                           edgecolor="#BFBFBF", hatch="///",
                           alpha=0.9, zorder=0)
                if iv.duration > 0.03 * plan.cycle:
                    ax.text(
                        (start + end) / 2.0,
                        n - 0.08,
                        f"余量 {iv.duration:.1f}s",
                        ha="center",
                        va="bottom",
                        rotation=90,
                        fontsize=6.0,
                        color="#666666",
                        zorder=6,
                    )

            # 放行时段柱子
            for iv in plan.intervals:
                if iv.kind != "green" or iv.phase is None:
                    continue
                for mid in movements:
                    capacity = data.phases[iv.phase].capacity.get(mid, 0.0)
                    if capacity <= 0.0:
                        continue
                    start = offset + iv.start
                    end = offset + iv.end
                    t_sat = sat_times[mid]
                    t_global = None if t_sat is None else offset + t_sat

                    # 需求满足前：实心
                    if t_global is None or start < t_global - _TOL:
                        solid_end = end if t_global is None else min(end, t_global)
                        if solid_end > start + _TOL:
                            ax.barh(
                                ypos[mid],
                                solid_end - start,
                                left=start,
                                height=0.58,
                                color=colors[mid],
                                edgecolor="white",
                                linewidth=0.35,
                                zorder=3,
                            )
                            _add_duration_label(
                                ax, start, solid_end, ypos[mid], plan.cycle
                            )
                    # 需求满足后：细密斜条纹
                    if t_global is not None and end > t_global + _TOL:
                        hatch_start = max(start, t_global)
                        if end > hatch_start + _TOL:
                            ax.barh(
                                ypos[mid],
                                end - hatch_start,
                                left=hatch_start,
                                height=0.58,
                                facecolor="white",
                                edgecolor=colors[mid],
                                hatch="////",
                                linewidth=0.35,
                                zorder=3,
                            )
                            _add_duration_label(
                                ax, hatch_start, end, ypos[mid], plan.cycle
                            )
            ax.axvline(offset + plan.cycle, color="#B22222",
                       linestyle="--", linewidth=0.9, zorder=2)

        # ---- 每个泳道的需求满足竖虚线 ----
        for mid in movements:
            t_sat = sat_times[mid]
            if t_sat is None:
                continue
            y = ypos[mid]
            color = colors[mid]
            ax.vlines(
                t_sat,
                y - 0.42,
                y + 0.42,
                colors=color,
                linestyles="--",
                linewidth=1.0,
                zorder=5,
            )
            ax.plot(
                [t_sat],
                [y],
                marker="D",
                markersize=3.0,
                markerfacecolor=color,
                markeredgecolor="white",
                markeredgewidth=0.4,
                zorder=6,
            )

        # ---- 坐标轴与标签 ----
        labels = []
        for mid in movements:
            base = _movement_label(mid)
            t_sat = sat_times[mid]
            if t_sat is None:
                labels.append(
                    f"{base}\n绿 {green_totals[mid]:.1f}s | 未满足"
                )
            else:
                labels.append(
                    f"{base}\n绿 {green_totals[mid]:.1f}s | "
                    f"$t_{{sat}}$ {t_sat:.1f}s"
                )
        ax.set_yticks([ypos[mid] for mid in movements])
        ax.set_yticklabels(labels, fontsize=8.5)
        ax.set_ylim(-0.7, n + 0.45)
        ax.set_xlim(0, cycles * plan.cycle)
        ax.set_xlabel("时间 $t$ (s)", fontsize=9.5)
        ax.set_ylabel("进口–流向", fontsize=9.5)

        # 背景网格：横向泳道分隔线 + 较密的纵向主/次刻度线
        ax.xaxis.set_major_locator(MultipleLocator(20))
        ax.xaxis.set_minor_locator(MultipleLocator(5))
        ax.grid(which="major", axis="x", color="#C8C8C8",
                linestyle="-", linewidth=0.6, alpha=0.55)
        ax.grid(which="minor", axis="x", color="#E6E6E6",
                linestyle=":", linewidth=0.5, alpha=0.85)
        for y_sep in np.arange(-0.5, n, 1.0):
            ax.axhline(y_sep, color="#E0E0E0", linewidth=0.6,
                       alpha=0.9, zorder=0.5)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        subtitle = (
            f"$C$ = {plan.cycle:.1f}s | 相位数 = {len(plan.order)} | "
            f"清空合计 = {plan.clearance_total:.1f}s | "
            f"顺序: {'→'.join(plan.order)}"
        )
        ax.set_title(
            ((title + "\n") if title else "")
            + f"各进口–流向放行时段与需求满足时刻\n{subtitle}",
            fontsize=10.5,
            pad=10,
        )

        # ---- 图例放在坐标轴下方，保持图面干净 ----
        handles = [
            Patch(facecolor="#7F7F7F", edgecolor="white", label="需求满足前：实心"),
            Patch(facecolor="white", edgecolor="black", hatch="////",
                  label="需求满足后：细密斜条纹"),
            Patch(facecolor="#D9D9D9", edgecolor="none", alpha=0.8,
                  label="清空/全红时间"),
            Patch(facecolor="#F5F5F5", edgecolor="#BFBFBF", hatch="///",
                  label="周期余量"),
            Line2D([0], [0], color="#444444", linestyle="--", linewidth=1.0,
                   marker="D", markersize=3.5, markerfacecolor="#444444",
                   markeredgecolor="white", label="需求满足时刻 $t_{sat}$"),
        ]
        ax.legend(
            handles=handles,
            loc="upper center",
            bbox_to_anchor=(0.5, -0.14),
            ncol=4,
            frameon=False,
            handlelength=2.2,
            columnspacing=1.6,
        )
        if unsatisfied:
            ax.text(
                0.99,
                0.02,
                "未满足流向：" + ", ".join(unsatisfied),
                transform=ax.transAxes,
                ha="right",
                va="bottom",
                color="#B22222",
                fontsize=8,
            )
        fig.tight_layout()
        if save_path:
            directory = os.path.dirname(os.path.abspath(save_path))
            os.makedirs(directory, exist_ok=True)
            fig.savefig(save_path, dpi=dpi, bbox_inches="tight")
        if show:
            plt.show()
    return fig
