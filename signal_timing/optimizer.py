"""顶层接口 LexicographicOptimizer（设计文档 §11、§12）。

串联：
- 数据层数据；
- 约束描述/编译层；
- 字典序目标管线；
- 排序后处理；
- 结果后验校验与 no-good cut 回退。
"""
from __future__ import annotations

import warnings
from typing import Dict, List, Optional, Sequence, Tuple

from .audit import AuditReport, audit_constraint
from .compiler import CompiledRow, ConstraintCompiler, SlackSpec
from .constraints import ConstraintSpec, TriggerType
from .data import IntersectionData
from .exceptions import (
    CollapsedConstraintInfeasible,
    ConstraintAuditError,
    ConstraintError,
    InfeasibleError,
    OrderingError,
    PostCheckError,
)
from .ordering import OrderingPostProcessor, OrderFilter, OrderingResult
from .pipeline import LexicographicPipeline
from .result import OptimizationResult, Stage1Result
from .variables import VarKey, VarRegistry

_VERIFY_TOL = 1e-5


def _full_values_stage1(data: IntersectionData, values: Dict[VarKey, float]) -> Dict[VarKey, float]:
    full = dict(values)
    for pid in data.phase_ids:
        y = full.get(("y", pid), 0.0)
        full.setdefault(("y", pid), y)
        if y <= 0.5:
            full[("g", pid)] = 0.0
        else:
            full.setdefault(("g", pid), 0.0)
    full.setdefault(("C", ""), 0.0)
    return full


def _full_values_ordering(
    data: IntersectionData,
    selected: Sequence[str],
    ordering: OrderingResult,
) -> Dict[VarKey, float]:
    full: Dict[VarKey, float] = {}
    sel = set(selected)
    for pid in data.phase_ids:
        full[("y", pid)] = 1.0 if pid in sel else 0.0
        full[("g", pid)] = float(ordering.greens.get(pid, 0.0)) if pid in sel else 0.0
    full[("C", "")] = float(ordering.cycle)
    if ordering.values:
        full.update(ordering.values)
    return full


class LexicographicOptimizer:
    """单交叉口信号配时优化器。"""

    def __init__(
        self,
        data: IntersectionData,
        *,
        eps_cycle: float = 0.01,
        eps_waste: float = 0.01,
        eps_margin: float = 0.01,
        delta_abs: Optional[float] = None,
        mip_rel_gap: float = 0.005,
        time_limit: float = 300.0,
        disp: bool = False,
        max_fallback: int = 20,
    ) -> None:
        self.data = data
        self.eps_cycle = float(eps_cycle)
        self.eps_waste = float(eps_waste)
        self.eps_margin = float(eps_margin)
        self.delta_abs = delta_abs
        self.mip_rel_gap = float(mip_rel_gap)
        self.time_limit = float(time_limit)
        self.disp = bool(disp)
        self.max_fallback = int(max_fallback)
        self.specs: List[ConstraintSpec] = []
        self.audit_reports: List[AuditReport] = []

    # ------------------------------------------------------------------ #
    # 约束声明
    # ------------------------------------------------------------------ #
    def add_constraint(self, spec: ConstraintSpec, *, run_audit: bool = True) -> None:
        if not isinstance(spec, ConstraintSpec):
            raise ConstraintError("add_constraint 需要 ConstraintSpec 实例")
        if any(s.name == spec.name for s in self.specs):
            raise ConstraintError(f"约束名重复: {spec.name}")
        if spec.has_mixed_coeffs and spec.trigger.kind != TriggerType.AND and not spec.confirm_mixed_trigger:
            raise ConstraintError(
                f"约束 {spec.name}: 混合系数 + {spec.trigger.kind.value} 触发未确认；"
                "请显式设置 confirm_mixed_trigger=True 并先用 audit() 检查子集语义。"
            )
        report: Optional[AuditReport] = None
        if run_audit:
            try:
                report = audit_constraint(spec, self.data.g_min, self.data.c_max)
            except ConstraintError:
                # 相位过多等审计不可用情况，不阻断声明，但记录告警
                warnings.warn(
                    f"约束 {spec.name} 的子集审计无法完成，已跳过；请手动检查。",
                    RuntimeWarning,
                    stacklevel=2,
                )
                report = None
            if report is not None:
                self.audit_reports.append(report)
                confirmed = spec.confirm_mixed_trigger or spec.confirm_audit
                if report.has_trap and not confirmed:
                    raise ConstraintAuditError(
                        f"约束 {spec.name} 存在恒违反的触发子集 {report.trap_subsets}；"
                        "请修改触发语义，或显式 confirm_audit=True / "
                        "confirm_mixed_trigger=True 后再声明。\n"
                        + report.as_table(),
                        report=report,
                    )
                if report.has_trap and confirmed:
                    warnings.warn(
                        f"约束 {spec.name} 已确认存在恒违反子集 {report.trap_subsets}，"
                        "模型可能不可行。",
                        RuntimeWarning,
                        stacklevel=2,
                    )
        self.specs.append(spec)

    def add_constraints(self, specs: Sequence[ConstraintSpec]) -> None:
        for spec in specs:
            self.add_constraint(spec)

    def audit(self, spec: Optional[ConstraintSpec] = None):
        """返回审计报告：传入 spec 则现场审计；否则返回已注册约束的报告列表。"""
        if spec is not None:
            return audit_constraint(spec, self.data.g_min, self.data.c_max)
        return list(self.audit_reports)

    # ------------------------------------------------------------------ #
    # 求解
    # ------------------------------------------------------------------ #
    def solve(
        self,
        fixed_cycle: Optional[float] = None,
        order_filter: Optional[OrderFilter] = None,
        *,
        run_ordering: bool = True,
        allow_cycle_reduction: bool = True,
        reference_order: Optional[Sequence[str]] = None,
        reference_mode: str = "prefer",
        reference_tolerance: float = 1e-3,
        enforce_zero_slack: bool = False,
        stage2_mode: str = "min_waste",
    ) -> OptimizationResult:
        if stage2_mode not in ("min_waste", "max_min_margin"):
            raise ValueError(
                "stage2_mode 必须是 'min_waste' 或 'max_min_margin'，"
                f"收到 {stage2_mode!r}"
            )
        pipeline = LexicographicPipeline(
            self.data,
            self.specs,
            eps_cycle=self.eps_cycle,
            eps_waste=self.eps_waste,
            eps_margin=self.eps_margin,
            delta_abs=self.delta_abs,
            mip_rel_gap=self.mip_rel_gap,
            time_limit=self.time_limit,
            disp=self.disp,
            stage2_mode=stage2_mode,
        )
        clearance_mode = "max"
        if fixed_cycle is not None:
            try:
                stage1 = pipeline.run(fixed_cycle=fixed_cycle, clearance_mode="max")
            except InfeasibleError as exc:
                # 定点周期模式：保守 l_bar 可能过紧；用乐观 l_min 重试可行性，
                # 真实清空时间由排序层校验，失败则走 no-good cut 回退。
                warnings.warn(
                    f"固定周期模式下保守清空时间不可行，改用乐观清空时间重试：{exc}",
                    RuntimeWarning,
                    stacklevel=2,
                )
                stage1 = pipeline.run(fixed_cycle=fixed_cycle, clearance_mode="min")
                clearance_mode = "min"
        else:
            stage1 = pipeline.run(clearance_mode="max")
        cycle_cap = float(stage1.cycle_cap if stage1.cycle_cap is not None else stage1.cycle)
        rounds = list(stage1.rounds)
        cuts: List[CompiledRow] = []
        fallback_cuts = 0
        last_selected = list(stage1.selected)

        effective_reference = (
            list(reference_order) if reference_order is not None else (
                list(self.data.reference_order) if self.data.reference_order is not None else None
            )
        )

        # max_min_margin 模式：先用真实清空时间求一个真正的最小周期上界，
        # 避免把第一阶段保守清空时间释放出来的周期空间全部让给裕量。
        ordering_cycle_upper = cycle_cap
        if (
            run_ordering
            and fixed_cycle is None
            and allow_cycle_reduction
            and stage2_mode == "max_min_margin"
        ):
            min_cycle_processor = OrderingPostProcessor(
                self.data,
                self.specs,
                time_limit=self.time_limit,
                disp=self.disp,
            )
            min_cycle_result = min_cycle_processor.process(
                stage1.selected,
                cycle_cap,
                cycle_fixed=False,
                order_filter=order_filter,
                reference_order=effective_reference,
                reference_mode=reference_mode,
                reference_tolerance=reference_tolerance,
                enforce_zero_slack=enforce_zero_slack,
                stage2_mode="min_cycle",
            )
            if min_cycle_result is not None:
                ordering_cycle_upper = (
                    min_cycle_result.cycle * (1.0 + 1e-6) + 1e-6
                )

        ordering_result: Optional[OrderingResult] = None
        if run_ordering:
            while True:
                processor = OrderingPostProcessor(
                    self.data,
                    self.specs,
                    time_limit=self.time_limit,
                    disp=self.disp,
                )
                if fixed_cycle is not None:
                    ordering_result = processor.process(
                        stage1.selected,
                        cycle_cap,
                        cycle_fixed=True,
                        order_filter=order_filter,
                        reference_order=effective_reference,
                        reference_mode=reference_mode,
                        reference_tolerance=reference_tolerance,
                        enforce_zero_slack=enforce_zero_slack,
                        stage2_mode=stage2_mode,
                    )
                elif allow_cycle_reduction:
                    # §9.5：第二阶段允许 C ∈ [c_min, C*]，回收保守清空时间损失
                    ordering_result = processor.process(
                        stage1.selected,
                        ordering_cycle_upper,
                        cycle_fixed=False,
                        order_filter=order_filter,
                        reference_order=effective_reference,
                        reference_mode=reference_mode,
                        reference_tolerance=reference_tolerance,
                        enforce_zero_slack=enforce_zero_slack,
                        stage2_mode=stage2_mode,
                    )
                else:
                    ordering_result = processor.process(
                        stage1.selected,
                        stage1.cycle,
                        cycle_fixed=True,
                        order_filter=order_filter,
                        reference_order=effective_reference,
                        reference_mode=reference_mode,
                        reference_tolerance=reference_tolerance,
                        enforce_zero_slack=enforce_zero_slack,
                        stage2_mode=stage2_mode,
                    )
                if ordering_result is not None:
                    break

                # ---- 回退：no-good cut ----
                if fallback_cuts >= self.max_fallback:
                    raise OrderingError(
                        f"排序回退超过上限 {self.max_fallback}，最后相位集 {stage1.selected}"
                    )
                S = list(dict.fromkeys(stage1.selected))
                cut = CompiledRow(
                    name=f"fallback_no_good[{','.join(S)}]",
                    coeffs={("y", pid): 1.0 for pid in S},
                    sense="<=",
                    rhs=float(len(S) - 1),
                    note="排序全部不可行，禁止该相位集",
                )
                cuts.append(cut)
                fallback_cuts += 1
                try:
                    if fixed_cycle is not None:
                        stage1 = pipeline.run(
                            fixed_cycle=fixed_cycle,
                            clearance_mode=clearance_mode,
                            extra_cuts=cuts,
                        )
                    else:
                        stage1 = pipeline.run(cycle_upper=cycle_cap, extra_cuts=cuts)
                except InfeasibleError as exc:
                    raise InfeasibleError(
                        f"排序回退第 {fallback_cuts} 次后第一阶段不可行：{exc}",
                        round_name="fallback",
                        locked_constraints={c.name: c.note for c in cuts},
                    ) from exc
                rounds.extend(stage1.rounds)
                if list(stage1.selected) == last_selected:
                    raise OrderingError(
                        "no-good cut 后仍返回相同相位集，可能是数值容差导致；请检查数据/约束。"
                    )
                last_selected = list(stage1.selected)

        # ------------------------------------------------------------------ #
        # 组装最终结果 + 后验校验
        # ------------------------------------------------------------------ #
        if ordering_result is not None:
            selected = list(stage1.selected)
            greens = dict(ordering_result.greens)
            cycle = float(ordering_result.cycle)
            waste = float(ordering_result.waste)
            order = list(ordering_result.order)
            sigmas = dict(ordering_result.sigmas)
            full_values = _full_values_ordering(self.data, selected, ordering_result)
            verification = self._verify_solution(full_values, selected, order=order)
            status = ordering_result.status
            message = ordering_result.message
        else:
            selected = list(stage1.selected)
            greens = dict(stage1.greens)
            cycle = float(stage1.cycle)
            waste = float(stage1.waste)
            order = None
            sigmas = dict(stage1.sigmas)
            full_values = _full_values_stage1(self.data, stage1.values)
            verification = self._verify_solution(full_values, selected, order=None)
            status = "optimal"
            message = "未运行排序后处理（run_ordering=False）"

        min_margin: Optional[float] = None
        if isinstance(verification, dict):
            service_margins = verification.get("service_margins") or {}
            if service_margins:
                min_margin = min(float(value) for value in service_margins.values())
        if min_margin is None and ordering_result is not None:
            min_margin = ordering_result.min_margin
        if min_margin is None:
            min_margin = getattr(stage1, "min_margin", None)

        return OptimizationResult(
            cycle=cycle,
            selected=selected,
            greens=greens,
            order=order,
            waste=waste,
            sigmas=sigmas,
            rounds=rounds,
            fallback_cuts=fallback_cuts,
            stage1_cycle=float(stage1.cycle),
            stage1_waste=float(stage1.waste),
            audit_reports=list(self.audit_reports),
            verification=verification,
            status=status,
            message=message,
            min_margin=min_margin,
            stage2_mode=stage2_mode,
        )

    # ------------------------------------------------------------------ #
    # 后验校验
    # ------------------------------------------------------------------ #
    def _verification_registry(self) -> VarRegistry:
        reg = VarRegistry()
        for pid in self.data.phase_ids:
            reg.register(("y", pid), 0.0, 1.0, kind="y")
            reg.register(("g", pid), 0.0, self.data.c_max, kind="g")
        reg.register(("C", ""), 0.0, self.data.c_max, kind="C")
        for spec in self.specs:
            if not spec.soft:
                continue
            ub = spec.slack_max if spec.slack_max is not None else self.data.c_max
            if spec.sense == "==":
                for sense in ("<=", ">="):
                    suffix = f"[{sense}]"
                    sign = "+" if sense == "<=" else "-"
                    key = ("sigma", f"{spec.name}{suffix}:{sign}")
                    reg.register(key, 0.0, ub, kind="sigma")
            else:
                sign = "+" if spec.sense == "<=" else "-"
                key = ("sigma", f"{spec.name}:{sign}")
                reg.register(key, 0.0, ub, kind="sigma")
        return reg

    def _verify_solution(
        self,
        values: Dict[VarKey, float],
        selected: Sequence[str],
        *,
        order: Optional[Sequence[str]],
    ) -> Dict[str, object]:
        d = self.data
        sel = set(selected)
        cycle = float(values.get(("C", ""), 0.0))
        greens = {pid: float(values.get(("g", pid), 0.0)) for pid in d.phase_ids}
        report: Dict[str, object] = {
            "selected": sorted(sel),
            "cycle": cycle,
            "order": list(order) if order is not None else None,
        }

        # 1. 环约束
        clearance = d.order_clearance(order) if order is not None else d.l_bar * len(sel)
        ring_slack = cycle - (sum(greens[p] for p in sel) + clearance)
        report["ring_slack"] = ring_slack
        if ring_slack < -_VERIFY_TOL:
            raise PostCheckError(
                f"后验校验失败：环约束违反 {ring_slack:.6g}s（cycle={cycle}, "
                f"sum_g={sum(greens[p] for p in sel):.6g}, clearance={clearance:.6g}）"
            )

        # 2. 服务能力
        margins: Dict[str, float] = {}
        for mid, mov in d.movements.items():
            service = sum(
                d.phases[pid].capacity.get(mid, 0.0) * greens[pid]
                for pid in d.phase_ids
                if pid in sel
            )
            margin = service - mov.demand * cycle
            margins[mid] = margin
            if margin < -_VERIFY_TOL:
                raise PostCheckError(
                    f"后验校验失败：流向 {mid} 服务不足 {margin:.6g} veh/h*s"
                )
        report["service_margins"] = margins

        # 3. 半连续/上下界
        for pid in d.phase_ids:
            g = greens[pid]
            if pid in sel:
                if g < d.g_min - _VERIFY_TOL or g > d.c_max + _VERIFY_TOL:
                    raise PostCheckError(f"后验校验失败：相位 {pid} 绿灯 {g} 越界")
            else:
                if abs(g) > _VERIFY_TOL:
                    raise PostCheckError(f"后验校验失败：未选中相位 {pid} 绿灯非零 {g}")

        # 4. 用户约束（触发坍缩后逐行代入）
        reg = self._verification_registry()
        compiler = ConstraintCompiler(reg.get_bound, d.c_max, default_slack_max=d.c_max)
        fixed_values: Dict[VarKey, float] = {}
        for pid in d.phase_ids:
            fixed_values[("y", pid)] = 1.0 if pid in sel else 0.0
            if pid not in sel:
                fixed_values[("g", pid)] = 0.0
        try:
            rows, _ = compiler.compile_all(self.specs, fixed_values=fixed_values)
        except CollapsedConstraintInfeasible as exc:
            raise PostCheckError(f"后验校验失败：用户约束在固定选择下恒违反：{exc}") from exc

        max_violation = 0.0
        worst_row = ""
        for row in rows:
            lhs = row.lhs_value(values)
            if row.sense == "<=":
                violation = lhs - row.rhs
            elif row.sense == ">=":
                violation = row.rhs - lhs
            else:
                violation = abs(lhs - row.rhs)
            if violation > max_violation:
                max_violation, worst_row = violation, row.name
            if violation > _VERIFY_TOL:
                raise PostCheckError(
                    f"后验校验失败：约束 {row.name} 违反 {violation:.6g} "
                    f"(lhs={lhs:.6g}, {row.sense} {row.rhs:.6g})"
                )
        report["max_user_constraint_violation"] = max_violation
        report["worst_user_constraint"] = worst_row
        report["num_user_rows"] = len(rows)
        report["passed"] = True
        return report
