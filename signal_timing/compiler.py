"""约束编译层（设计文档 §6）。

把 ConstraintSpec 编译成线性行：
- 等式拆分；
- 按 §5.3 规则矩阵选择发射形式；
- big-M 按物理界自动推导，并加 5% + ε 余量；
- 软约束注册违反侧松弛变量；
- 第二阶段用固定 y 做触发坍缩。
"""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .constraints import ConstraintSpec, TriggerType
from .exceptions import CollapsedConstraintInfeasible, ConstraintError
from .variables import VarKey

BoundsProvider = Callable[[VarKey], Tuple[float, float]]


@dataclass
class CompiledRow:
    """编译后的线性行：sum(coeffs * x) sense rhs。"""

    name: str
    coeffs: Dict[VarKey, float]
    sense: str
    rhs: float
    big_m: float = 0.0
    trigger_kind: str = TriggerType.ALWAYS.value
    source: str = ""
    note: str = ""

    def lhs_value(self, values: Dict[VarKey, float]) -> float:
        total = 0.0
        for key, coeff in self.coeffs.items():
            if key not in values:
                raise ConstraintError(f"后验校验缺少变量 {key}")
            total += coeff * values[key]
        return total


@dataclass
class SlackSpec:
    """软约束松弛变量需求。"""

    key: VarKey
    lb: float
    ub: float
    penalty: float
    spec_name: str
    sign: str  # '+' = 上侧/<=违反；'-' = 下侧/>=违反
    base_name: str = ""


class ConstraintCompiler:
    """把 ConstraintSpec 编译到变量注册表上。

    参数
    ----
    bound_provider : Callable[[VarKey], (lb, ub)]
        用于检查变量是否已注册。
    c_max : float
        big-M 推导使用的物理上界（设计文档 §6.2 要求 g、C ∈ [0, c_max]）。
    default_slack_max : Optional[float]
        未显式给出 slack_max 时的默认值；通常等于 c_max。
    """

    def __init__(
        self,
        bound_provider: BoundsProvider,
        c_max: float,
        *,
        default_slack_max: Optional[float] = None,
        m_margin: float = 1.05,
        m_epsilon: float = 1e-6,
    ) -> None:
        self._bound_provider = bound_provider
        self.c_max = float(c_max)
        self.default_slack_max = float(default_slack_max if default_slack_max is not None else c_max)
        self.m_margin = float(m_margin)
        self.m_epsilon = float(m_epsilon)

    # ------------------------------------------------------------------ #
    # 界
    # ------------------------------------------------------------------ #
    def _model_bound(self, key: VarKey) -> Tuple[float, float]:
        try:
            return self._bound_provider(key)
        except KeyError as exc:
            raise ConstraintError(f"约束引用了未注册变量 {key}") from exc

    def _m_bound(self, key: VarKey) -> Tuple[float, float]:
        """big-M 推导使用的物理界（设计文档 §6.2）。"""
        kind = key[0]
        if kind in ("g", "C"):
            return 0.0, self.c_max
        if kind == "y":
            return 0.0, 1.0
        if kind == "sigma":
            lb, ub = self._model_bound(key)
            return lb, ub
        # 其他扩展变量按实际注册界处理
        return self._model_bound(key)

    def _check_registered(self, spec: ConstraintSpec) -> None:
        for key in spec.coeffs:
            self._model_bound(key)
        for pid in spec.trigger.phases:
            self._model_bound(("y", pid))

    # ------------------------------------------------------------------ #
    # big-M
    # ------------------------------------------------------------------ #
    def raw_big_m(self, spec: ConstraintSpec, sense: str) -> float:
        """按物理界推导未加余量的 M。"""
        if sense not in ("<=", ">="):
            raise ConstraintError(f"raw_big_m 只接受 <= / >=，收到 {sense}")
        max_val = 0.0
        min_val = 0.0
        for key, a in spec.coeffs.items():
            lb, ub = self._m_bound(key)
            if a > 0:
                max_val += a * ub
                min_val += a * lb
            else:
                max_val += a * lb
                min_val += a * ub
        if sense == "<=":
            return max_val - spec.rhs
        return spec.rhs - min_val

    def big_m(self, spec: ConstraintSpec, sense: str) -> float:
        """加 5% + ε 余量；若 raw <= 0，返回 0 表示无需松弛。"""
        raw = self.raw_big_m(spec, sense)
        if raw <= 0.0:
            return 0.0
        return self.m_margin * raw + self.m_epsilon

    # ------------------------------------------------------------------ #
    # 编译入口
    # ------------------------------------------------------------------ #
    def compile(
        self,
        spec: ConstraintSpec,
        fixed_values: Optional[Dict[VarKey, float]] = None,
    ) -> Tuple[List[CompiledRow], List[SlackSpec]]:
        """编译单条约束。

        fixed_values 非 None 时执行触发坍缩：触发未激活 -> 丢弃；
        激活 -> 发射不含 M/y 的普通行。字典中出现的变量会被替换为常数。
        """
        if fixed_values is not None:
            return self._compile_collapsed(spec, fixed_values)

        self._check_registered(spec)
        self._check_mixed_trigger(spec)
        if spec.sense == "==":
            rows_l, slacks_l = self._compile_direction(spec, "<=", suffix="[<=]")
            rows_g, slacks_g = self._compile_direction(spec, ">=", suffix="[>=]")
            return rows_l + rows_g, slacks_l + slacks_g
        rows, slacks = self._compile_direction(spec, spec.sense, suffix="")
        return rows, slacks

    def compile_all(
        self,
        specs: Sequence[ConstraintSpec],
        fixed_values: Optional[Dict[VarKey, float]] = None,
    ) -> Tuple[List[CompiledRow], List[SlackSpec]]:
        rows: List[CompiledRow] = []
        slacks: List[SlackSpec] = []
        for spec in specs:
            r, s = self.compile(spec, fixed_values=fixed_values)
            rows.extend(r)
            slacks.extend(s)
        return rows, slacks

    # ------------------------------------------------------------------ #
    # 内部：混合系数保护
    # ------------------------------------------------------------------ #
    def _check_mixed_trigger(self, spec: ConstraintSpec) -> None:
        if spec.has_mixed_coeffs and spec.trigger.kind != TriggerType.AND:
            if not spec.confirm_mixed_trigger:
                raise ConstraintError(
                    f"约束 {spec.name} 是混合系数（既有正又有负）但触发为 "
                    f"{spec.trigger.kind.value}。设计文档要求混合系数默认使用 AND 触发；"
                    f"若确实要保留语义，请显式设置 confirm_mixed_trigger=True。"
                )
            warnings.warn(
                f"约束 {spec.name}: 混合系数 + {spec.trigger.kind.value} 触发已显式确认，"
                f"请务必用子集审计确认没有恒违反子集。",
                RuntimeWarning,
                stacklevel=3,
            )

    # ------------------------------------------------------------------ #
    # 内部：正向编译
    # ------------------------------------------------------------------ #
    def _sigma_spec(self, spec: ConstraintSpec, sense: str, suffix: str) -> SlackSpec:
        sign = "+" if sense == "<=" else "-"
        key: VarKey = ("sigma", f"{spec.name}{suffix}:{sign}")
        ub = spec.slack_max if spec.slack_max is not None else self.default_slack_max
        return SlackSpec(
            key=key,
            lb=0.0,
            ub=float(ub),
            penalty=float(spec.penalty),
            spec_name=spec.name + suffix,
            sign=sign,
            base_name=spec.name,
        )

    @staticmethod
    def _add_coeff(coeffs: Dict[VarKey, float], key: VarKey, value: float) -> None:
        new = coeffs.get(key, 0.0) + value
        if abs(new) <= 1e-15:
            coeffs.pop(key, None)
        else:
            coeffs[key] = new

    def _compile_direction(
        self,
        spec: ConstraintSpec,
        sense: str,
        *,
        suffix: str = "",
    ) -> Tuple[List[CompiledRow], List[SlackSpec]]:
        if sense not in ("<=", ">="):
            raise ConstraintError(f"_compile_direction 只接受 <= / >=，收到 {sense}")

        base = dict(spec.coeffs)
        name = f"{spec.name}{suffix}"
        slacks: List[SlackSpec] = []
        if spec.soft:
            slack = self._sigma_spec(spec, sense, suffix)
            slacks.append(slack)
            # 违反方向：<= 用 a^T x - sigma <= b；>= 用 a^T x + sigma >= b
            self._add_coeff(base, slack.key, -1.0 if sense == "<=" else 1.0)

        raw_m = self.raw_big_m(spec, sense)
        m_used = self.big_m(spec, sense)
        trigger_kind = spec.trigger.kind

        # M <= 0：约束在任何触发状态下都自动成立，直接发射（免费紧化）。
        if m_used <= 0.0:
            row = CompiledRow(
                name=name,
                coeffs=dict(base),
                sense=sense,
                rhs=float(spec.rhs),
                big_m=0.0,
                trigger_kind=trigger_kind.value,
                source=spec.name,
                note="M<=0，约束恒成立，无需松弛",
            )
            return [row], slacks

        if trigger_kind == TriggerType.ALWAYS:
            row = CompiledRow(
                name=name,
                coeffs=dict(base),
                sense=sense,
                rhs=float(spec.rhs),
                big_m=0.0,
                trigger_kind=trigger_kind.value,
                source=spec.name,
                note="ALWAYS，直接发射",
            )
            return [row], slacks

        S = list(spec.trigger.phases)
        rows: List[CompiledRow] = []

        if trigger_kind == TriggerType.OR:
            # 特例：<= 且 b >= 0 时，全不选状态 0 <= b 自动满足，
            # 因此直接发射就等价于 OR 语义（设计文档 §5.3）。
            if sense == "<=" and spec.rhs >= 0.0:
                row = CompiledRow(
                    name=name,
                    coeffs=dict(base),
                    sense=sense,
                    rhs=float(spec.rhs),
                    big_m=0.0,
                    trigger_kind=trigger_kind.value,
                    source=spec.name,
                    note="OR + (<=, b>=0)：关闭态自动满足，直接发射",
                )
                return [row], slacks

            # 显式声明的"至少选一个"语义（设计文档 §5.3 OR 特例）
            if sense == ">=" and getattr(spec, "or_existential", False):
                row = CompiledRow(
                    name=name,
                    coeffs=dict(base),
                    sense=sense,
                    rhs=float(spec.rhs),
                    big_m=0.0,
                    trigger_kind=trigger_kind.value,
                    source=spec.name,
                    note="OR + (>=) 显式声明存在性语义，直接发射",
                )
                return [row], slacks

            for pid in S:
                coeffs = dict(base)
                if sense == "<=":
                    self._add_coeff(coeffs, ("y", pid), m_used)
                    rhs = spec.rhs + m_used
                else:
                    self._add_coeff(coeffs, ("y", pid), -m_used)
                    rhs = spec.rhs - m_used
                rows.append(
                    CompiledRow(
                        name=f"{name}[OR:{pid}]",
                        coeffs=coeffs,
                        sense=sense,
                        rhs=float(rhs),
                        big_m=m_used,
                        trigger_kind=trigger_kind.value,
                        source=spec.name,
                        note=f"OR 逐相位复制，触发相位 {pid}",
                    )
                )
            return rows, slacks

        # AND 触发
        coeffs = dict(base)
        k = len(S)
        if sense == "<=":
            for pid in S:
                self._add_coeff(coeffs, ("y", pid), m_used)
            rhs = spec.rhs + m_used * k
        else:
            for pid in S:
                self._add_coeff(coeffs, ("y", pid), -m_used)
            rhs = spec.rhs - m_used * k
        rows.append(
            CompiledRow(
                name=f"{name}[AND]",
                coeffs=coeffs,
                sense=sense,
                rhs=float(rhs),
                big_m=m_used,
                trigger_kind=trigger_kind.value,
                source=spec.name,
                note=f"AND 触发，|S|={k}",
            )
        )
        return rows, slacks

    # ------------------------------------------------------------------ #
    # 内部：触发坍缩（第二阶段）
    # ------------------------------------------------------------------ #
    def _compile_collapsed(
        self,
        spec: ConstraintSpec,
        fixed_values: Dict[VarKey, float],
    ) -> Tuple[List[CompiledRow], List[SlackSpec]]:
        selected = {
            key[1] for key, val in fixed_values.items() if key[0] == "y" and val > 0.5
        }
        if not spec.trigger.is_active(selected):
            return [], []

        # 替换固定变量
        coeffs: Dict[VarKey, float] = {}
        rhs = float(spec.rhs)
        for key, a in spec.coeffs.items():
            if key in fixed_values:
                rhs -= a * fixed_values[key]
            else:
                self._model_bound(key)  # 未固定变量必须已注册
                self._add_coeff(coeffs, key, a)

        # 触发相位也应当固定；否则第二阶段模型里没有 y 变量
        for pid in spec.trigger.phases:
            ykey = ("y", pid)
            if ykey not in fixed_values:
                raise ConstraintError(
                    f"触发坍缩缺少触发相位 {pid} 的固定值；无法在第二阶段发射约束 {spec.name}"
                )

        rows: List[CompiledRow] = []
        slacks: List[SlackSpec] = []

        directions = ["<=", ">="] if spec.sense == "==" else [spec.sense]
        for sense in directions:
            suffix = "[<=]" if (spec.sense == "==" and sense == "<=") else (
                "[>=]" if (spec.sense == "==" and sense == ">=") else ""
            )
            body = dict(coeffs)
            local_slacks: List[SlackSpec] = []
            if spec.soft:
                slack = self._sigma_spec(spec, sense, suffix)
                local_slacks.append(slack)
                self._add_coeff(body, slack.key, -1.0 if sense == "<=" else 1.0)
            if not body:
                # 常数行：直接判断
                ok = (0.0 <= rhs + 1e-9) if sense == "<=" else (0.0 >= rhs - 1e-9)
                if not ok:
                    raise CollapsedConstraintInfeasible(
                        f"约束 {spec.name} 在触发坍缩后为恒违反常数行: 0 {sense} {rhs}"
                    )
                continue
            rows.append(
                CompiledRow(
                    name=f"{spec.name}{suffix}[collapsed]",
                    coeffs=body,
                    sense=sense,
                    rhs=rhs,
                    big_m=0.0,
                    trigger_kind=spec.trigger.kind.value,
                    source=spec.name,
                    note="触发坍缩：直接发射，不含 M/y",
                )
            )
            slacks.extend(local_slacks)
        return rows, slacks
