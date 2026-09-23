"""数据层（设计文档 §3）。

核心结构：
- Movement：流向；
- Phase：候选相位（基底容量字典）；
- IntersectionData：路口数据 + 加载时强制校验 + 派生量。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .exceptions import DataValidationError


@dataclass
class Movement:
    """一个流向。"""

    mid: str
    demand: float

    def __post_init__(self) -> None:
        if not isinstance(self.mid, str) or not self.mid:
            raise DataValidationError("Movement.mid 必须是非空字符串")
        self.demand = float(self.demand)
        if not (math.isfinite(self.demand) and self.demand > 0.0):
            raise DataValidationError(f"流向 {self.mid} 的需求必须 > 0，当前为 {self.demand}")


@dataclass
class Phase:
    """候选相位：capacity 仅列出 a_pm > 0 的项。"""

    pid: str
    capacity: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.pid, str) or not self.pid:
            raise DataValidationError("Phase.pid 必须是非空字符串")
        self.capacity = {str(k): float(v) for k, v in self.capacity.items()}

    def serves(self, mid: str) -> bool:
        return self.capacity.get(mid, 0.0) > 0.0


class IntersectionData:
    """单交叉口输入数据。

    参数
    ----
    movements : Dict[str, Movement]
    phases : Dict[str, Phase]
    lost_time : Dict[(str, str), float]
        相邻相位对 (前 -> 后) 的清空时间（秒）。strict_clearance=True 时，
        未定义的对在查询时直接报错。
    g_min : float
        被选相位最小绿灯（秒）。
    c_min, c_max : float
        周期下/上界（秒）。
    strict_clearance : bool
        严格清空时间模式。
    """

    def __init__(
        self,
        movements: Dict[str, Movement],
        phases: Dict[str, Phase],
        lost_time: Optional[Dict[Tuple[str, str], float]] = None,
        g_min: float = 15.0,
        c_min: float = 30.0,
        c_max: float = 180.0,
        *,
        strict_clearance: bool = True,
        static_precheck: bool = True,
        reference_order: Optional[Sequence[str]] = None,
    ) -> None:
        self.movements: Dict[str, Movement] = dict(movements)
        self.phases: Dict[str, Phase] = dict(phases)
        self.lost_time: Dict[Tuple[str, str], float] = {
            (str(a), str(b)): float(v) for (a, b), v in (lost_time or {}).items()
        }
        self.g_min = float(g_min)
        self.c_min = float(c_min)
        self.c_max = float(c_max)
        self.strict_clearance = bool(strict_clearance)
        self.static_precheck = bool(static_precheck)
        self.reference_order: Optional[Tuple[str, ...]] = None
        self._validate_reference_order(reference_order)
        self.validate()

    # ------------------------------------------------------------------ #
    # 校验
    # ------------------------------------------------------------------ #
    def _validate_reference_order(self, reference_order) -> None:
        """校验分层参考顺序。

        ``reference_order`` 可以是：
        - 扁平字符串序列：每个相位一层；
        - 嵌套序列：每个元素是一层，层内相位可互换。

        例如：``["P1", ("P5", "P6"), "P3", ("P2", "P4")]``

        要求所有候选相位恰好出现一次。
        """
        if reference_order is None:
            return
        groups = []
        for item in reference_order:
            if isinstance(item, str):
                group = (item,)
            else:
                group = tuple(str(p) for p in item)
            if not group:
                raise DataValidationError("reference_order 中存在空分组")
            groups.append(group)
        flat = [p for group in groups for p in group]
        if len(flat) != len(set(flat)):
            raise DataValidationError("reference_order 中存在重复相位")
        unknown = [p for p in flat if p not in self.phases]
        if unknown:
            raise DataValidationError(f"reference_order 引用了未知相位: {unknown}")
        missing = [p for p in self.phase_ids if p not in flat]
        if missing:
            raise DataValidationError(
                f"reference_order 必须包含全部候选相位；缺少: {missing}"
            )
        self.reference_order = tuple(groups)

    def validate(self) -> None:
        if not self.movements:
            raise DataValidationError("movements 不能为空")
        if not self.phases:
            raise DataValidationError("phases 不能为空")

        # 1. 数值合法性
        for mid, mov in self.movements.items():
            if mid != mov.mid:
                raise DataValidationError(f"movements 键 {mid!r} 与 Movement.mid {mov.mid!r} 不一致")
            if not (math.isfinite(mov.demand) and mov.demand > 0.0):
                raise DataValidationError(f"流向 {mid} 的需求必须 > 0，当前为 {mov.demand}")
        for pid, ph in self.phases.items():
            if pid != ph.pid:
                raise DataValidationError(f"phases 键 {pid!r} 与 Phase.pid {ph.pid!r} 不一致")
            if not ph.capacity:
                raise DataValidationError(f"相位 {pid} 的 capacity 不能为空")
            for mid, a in ph.capacity.items():
                if mid not in self.movements:
                    raise DataValidationError(f"相位 {pid} 引用了未知流向 {mid}")
                if not (math.isfinite(a) and a > 0.0):
                    raise DataValidationError(f"相位 {pid} 对流向 {mid} 的服务能力必须 > 0，当前为 {a}")

        # 2. 覆盖检查
        covered: Dict[str, List[str]] = {mid: [] for mid in self.movements}
        for pid, ph in self.phases.items():
            for mid in ph.capacity:
                covered[mid].append(pid)
        for mid, servers in covered.items():
            if not servers:
                raise DataValidationError(f"流向 {mid} 没有被任何候选相位服务，问题必然不可行")

        # 3. lost_time 引用检查
        for (i, j), ell in self.lost_time.items():
            if i not in self.phases:
                raise DataValidationError(f"lost_time 引用了未知相位 {i}")
            if j not in self.phases:
                raise DataValidationError(f"lost_time 引用了未知相位 {j}")
            if not (math.isfinite(ell) and ell >= 0.0):
                raise DataValidationError(f"lost_time[{i},{j}] 必须 >= 0，当前为 {ell}")

        # 4. 时间参数
        if not (math.isfinite(self.g_min) and self.g_min > 0.0):
            raise DataValidationError("g_min 必须 > 0")
        if not (math.isfinite(self.c_min) and math.isfinite(self.c_max)):
            raise DataValidationError("c_min / c_max 必须是有限数")
        if not (self.g_min < self.c_min <= self.c_max):
            raise DataValidationError(
                f"必须满足 g_min < c_min <= c_max，当前 g_min={self.g_min}, "
                f"c_min={self.c_min}, c_max={self.c_max}"
            )

        # 5. 静态可行性预检（必要非充分）
        if self.static_precheck:
            self._run_static_precheck()

    def _run_static_precheck(self) -> None:
        l_bar = self.l_bar
        tol = 1e-9
        if self.c_max < self.g_min + l_bar - tol:
            raise DataValidationError(
                "静态预检失败：c_max < g_min + l_bar，至少一个相位也无法安排。"
                f"g_min={self.g_min}, l_bar={l_bar}, c_max={self.c_max}"
            )
        for mid, mov in self.movements.items():
            max_a = self.max_capacity(mid)
            ratio = mov.demand / max_a
            if ratio >= 1.0 - tol:
                raise DataValidationError(
                    f"静态预检失败：流向 {mid} 的需求 {mov.demand} 已达到/超过其最大服务能力 "
                    f"{max_a}（流量比 {ratio:.3f} >= 1），任何周期都不可行"
                )
            if l_bar > 0.0:
                required_cycle = l_bar / (1.0 - ratio)
                if required_cycle > self.c_max + 1e-6:
                    raise DataValidationError(
                        f"静态预检失败：流向 {mid} 的关键流量比 {ratio:.3f} 与清空时间 "
                        f"{l_bar} 至少需要周期 {required_cycle:.2f}s，超过 c_max={self.c_max}s"
                    )

    # ------------------------------------------------------------------ #
    # 派生量
    # ------------------------------------------------------------------ #
    @property
    def movement_ids(self) -> List[str]:
        return list(self.movements.keys())

    @property
    def phase_ids(self) -> List[str]:
        return list(self.phases.keys())

    @property
    def l_bar(self) -> float:
        """保守清空时间上界：max lost_time，空字典时为 0。"""
        return max(self.lost_time.values(), default=0.0)

    @property
    def l_min(self) -> float:
        """乐观清空时间下界：min lost_time，空字典时为 0。

        仅用于定点周期模式的可行性回退：用 n*l_min 作为环约束的乐观清空时间，
        随后第二阶段枚举真实顺序并校验；若不满足则触发 no-good cut。
        """
        return min(self.lost_time.values(), default=0.0)

    def max_capacity(self, mid: str) -> float:
        if mid not in self.movements:
            raise DataValidationError(f"未知流向 {mid}")
        return max((ph.capacity.get(mid, 0.0) for ph in self.phases.values()), default=0.0)

    def service_phase_ids(self, mid: str) -> List[str]:
        return [pid for pid, ph in self.phases.items() if ph.serves(mid)]

    def clearance(self, i: str, j: str) -> float:
        """查询相位对清空时间；i == j 视为 0；严格模式下未定义即报错。"""
        if i == j:
            return 0.0
        key = (i, j)
        if key in self.lost_time:
            return self.lost_time[key]
        if self.strict_clearance:
            raise DataValidationError(
                f"lost_time 未定义相位对 ({i}, {j})；严格模式下禁止按 0 处理"
            )
        return 0.0

    def order_clearance(self, order: Iterable[str]) -> float:
        """计算一个（含首尾相接的）相位顺序的总清空时间。"""
        seq = list(order)
        n = len(seq)
        if n <= 1:
            return 0.0
        return sum(self.clearance(seq[k], seq[(k + 1) % n]) for k in range(n))

    def __repr__(self) -> str:  # pragma: no cover - 便于日志
        return (
            f"IntersectionData(movements={len(self.movements)}, phases={len(self.phases)}, "
            f"l_bar={self.l_bar}, g_min={self.g_min}, C=[{self.c_min},{self.c_max}])"
        )
