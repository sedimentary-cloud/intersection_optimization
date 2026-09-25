"""目标管线层（设计文档 §8）。

字典序三轮：
1. min C（给定周期模式跳过）；
2. 锁定 C <= C*(1+eps_cycle)，min 浪费；
3. 再锁定 浪费 <= W*(1+eps_waste)+delta_abs，min 相位数。
软约束惩罚项在每一轮目标中常驻。
"""
from __future__ import annotations

import math
import warnings
from typing import Dict, List, Optional, Sequence

from .compiler import CompiledRow
from .constraints import ConstraintSpec
from .data import IntersectionData
from .exceptions import InfeasibleError
from .model import MILPSolution, Stage1Model
from .result import RoundTrace, Stage1Result
from .variables import VarKey


class LexicographicPipeline:
    def __init__(
        self,
        data: IntersectionData,
        specs: Optional[Sequence[ConstraintSpec]] = None,
        *,
        eps_cycle: float = 0.01,
        eps_waste: float = 0.01,
        eps_margin: float = 0.01,
        delta_abs: Optional[float] = None,
        mip_rel_gap: float = 0.005,
        time_limit: float = 300.0,
        disp: bool = False,
        stage2_mode: str = "min_waste",
    ) -> None:
        self.data = data
        self.specs = list(specs or [])
        self.eps_cycle = float(eps_cycle)
        self.eps_waste = float(eps_waste)
        self.eps_margin = float(eps_margin)
        self.mip_rel_gap = float(mip_rel_gap)
        self.time_limit = float(time_limit)
        self.disp = bool(disp)
        if stage2_mode not in ("min_waste", "max_min_margin"):
            raise ValueError(
                "stage2_mode 必须是 'min_waste' 或 'max_min_margin'，"
                f"收到 {stage2_mode!r}"
            )
        self.stage2_mode = str(stage2_mode)
        if self.eps_cycle < 0 or self.eps_waste < 0 or self.eps_margin < 0:
            raise ValueError("eps_cycle / eps_waste / eps_margin 必须 >= 0")
        if self.eps_cycle < self.mip_rel_gap:
            raise ValueError(
                f"eps_cycle={self.eps_cycle} 小于 mip_rel_gap={self.mip_rel_gap}，"
                "字典序锁定会不可行；设计文档要求 eps 显著大于 mip_rel_gap"
            )
        if self.eps_cycle < 5.0 * self.mip_rel_gap:
            warnings.warn(
                f"eps_cycle={self.eps_cycle} 未达到 mip_rel_gap={self.mip_rel_gap} 的 5 倍，"
                "建议增大 eps_cycle 或减小 mip_rel_gap。",
                RuntimeWarning,
                stacklevel=2,
            )
        if delta_abs is None:
            # 默认取"全部流向 1 秒绿灯的服务量"量级
            delta_abs = sum(data.max_capacity(mid) for mid in data.movement_ids)
        self.delta_abs = float(delta_abs)

    # ------------------------------------------------------------------ #
    # 目标系数
    # ------------------------------------------------------------------ #
    def waste_coeffs(self) -> Dict[VarKey, float]:
        coeffs: Dict[VarKey, float] = {}
        for pid, ph in self.data.phases.items():
            coeffs[("g", pid)] = float(sum(ph.capacity.values()))
        coeffs[("C", "")] = -float(sum(mov.demand for mov in self.data.movements.values()))
        return coeffs

    @staticmethod
    def phase_count_coeffs(data: IntersectionData) -> Dict[VarKey, float]:
        return {("y", pid): 1.0 for pid in data.phase_ids}

    def waste_lock_row(self, w_star: float) -> CompiledRow:
        w_safe = max(float(w_star), 0.0)
        rhs = w_safe * (1.0 + self.eps_waste) + self.delta_abs
        return CompiledRow(
            name="lock_waste",
            coeffs=self.waste_coeffs(),
            sense="<=",
            rhs=rhs,
            note=f"锁定 waste <= {w_safe:.6g} * (1+{self.eps_waste}) + {self.delta_abs:.6g}",
        )

    def margin_lock_rhs(self, z_star: float) -> float:
        """max-min margin 模式第三阶段的 z 下界。"""
        tolerance = max(1e-6, abs(float(z_star)) * self.eps_margin)
        return float(z_star) - tolerance

    # ------------------------------------------------------------------ #
    # 求解
    # ------------------------------------------------------------------ #
    def run(
        self,
        *,
        fixed_cycle: Optional[float] = None,
        cycle_upper: Optional[float] = None,
        clearance_mode: str = "max",
        extra_cuts: Optional[Sequence] = None,
    ) -> Stage1Result:
        """运行字典序管线。

        fixed_cycle 给出时固定周期并跳过第 1 轮；
        cycle_upper 给出时周期上界为 cycle_upper（用于回退时保持 C*），也跳过第 1 轮。
        clearance_mode='max' 使用保守 l_bar；'min' 使用乐观 l_min
        （仅作为定点周期模式不可行时的回退，后续由排序层校验真实清空时间）。
        """
        if fixed_cycle is not None and cycle_upper is not None:
            raise ValueError("fixed_cycle 与 cycle_upper 不能同时给出")
        if clearance_mode not in ("max", "min"):
            raise ValueError("clearance_mode 必须是 'max' 或 'min'")
        ring_clearance = self.data.l_bar if clearance_mode == "max" else self.data.l_min

        model = Stage1Model(
            self.data,
            self.specs,
            fixed_cycle=fixed_cycle,
            cycle_upper=cycle_upper,
            ring_clearance=ring_clearance,
            extra_cuts=extra_cuts,
        )
        rounds: List[RoundTrace] = []
        locked: Dict[str, str] = {}

        # ---- 第 1 轮 / 给定周期 ----
        if fixed_cycle is not None:
            c_star = float(fixed_cycle)
            rounds.append(
                RoundTrace(
                    name="round0_cycle_given",
                    objective_name="fixed_cycle",
                    objective_value=c_star,
                    status="fixed",
                    message=f"用户给定周期 C={c_star}",
                    cycle=c_star,
                    locked={},
                )
            )
        elif cycle_upper is not None:
            c_star = float(cycle_upper)
            rounds.append(
                RoundTrace(
                    name="round0_cycle_upper",
                    objective_name="cycle_upper",
                    objective_value=c_star,
                    status="inherited",
                    message=f"回退沿用第一阶段锁定周期 C*={c_star}",
                    cycle=c_star,
                    locked={},
                )
            )
        else:
            sol1 = self._solve_round(model, {("C", ""): 1.0}, "round1_min_cycle")
            c_star = model.cycle_value(sol1.values)
            rounds.append(self._trace(model, sol1, "round1_min_cycle", "min C"))
            lock_rhs = c_star * (1.0 + self.eps_cycle)
            model.add_upper_bound_row(("C", ""), lock_rhs, "lock_cycle")
            locked["lock_cycle"] = f"C <= {lock_rhs:.6g} (C*={c_star:.6g})"

        # ---- 第 2 轮：min 浪费 / max 最紧张流向裕量 ----
        if self.stage2_mode == "min_waste":
            sol2 = self._solve_round(
                model, self.waste_coeffs(), "round2_min_waste", locked=locked
            )
            w_star = model.waste_value(sol2.values)
            rounds.append(
                self._trace(model, sol2, "round2_min_waste", "min waste", cycle=c_star)
            )
            waste_lock = self.waste_lock_row(w_star)
            model.add_row(waste_lock)
            locked[waste_lock.name] = waste_lock.note
            stage2_value: Optional[float] = w_star
        else:
            z_key = model.register_min_margin()
            model.add_min_margin_rows(z_key)
            sol2 = self._solve_round(
                model,
                {z_key: -1.0},
                "round2_max_min_margin",
                locked=locked,
            )
            z_star = float(sol2.values.get(z_key, 0.0))
            rounds.append(
                self._trace(
                    model,
                    sol2,
                    "round2_max_min_margin",
                    "max min margin",
                    cycle=c_star,
                    objective_value=z_star,
                )
            )
            margin_lock = model.add_min_margin_lock(
                z_star,
                tolerance=max(1e-6, abs(z_star) * self.eps_margin),
            )
            locked[margin_lock.name] = margin_lock.note
            stage2_value = z_star

        # ---- 第 3 轮：min 相位数 ----
        sol3 = self._solve_round(
            model,
            self.phase_count_coeffs(self.data),
            "round3_min_phase_count",
            locked=locked,
        )
        rounds.append(self._trace(model, sol3, "round3_min_phase_count", "min phase count", cycle=c_star))

        selected = model.selected_phases(sol3.values)
        greens = model.selected_greens(sol3.values)
        cycle = model.cycle_value(sol3.values)
        waste = model.waste_value(sol3.values)
        min_margin = model.min_margin_value(sol3.values)
        sigmas = self._aggregate_sigmas(model, sol3.values)

        return Stage1Result(
            cycle=cycle,
            selected=selected,
            greens=greens,
            waste=waste,
            sigmas=sigmas,
            rounds=rounds,
            values=dict(sol3.values),
            cycle_cap=c_star,
            model=model,
            min_margin=min_margin,
            stage2_mode=self.stage2_mode,
        )

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    def _solve_round(
        self,
        model: Stage1Model,
        objective: Dict[VarKey, float],
        round_name: str,
        *,
        locked: Optional[Dict[str, str]] = None,
    ) -> MILPSolution:
        try:
            return model.solve(
                objective,
                time_limit=self.time_limit,
                mip_rel_gap=self.mip_rel_gap,
                disp=self.disp,
            )
        except InfeasibleError as exc:
            raise InfeasibleError(
                f"{round_name} 不可行：{exc}",
                round_name=round_name,
                locked_constraints=locked or {},
            ) from exc

    @staticmethod
    def _trace(
        model: Stage1Model,
        sol: MILPSolution,
        name: str,
        objective_name: str,
        *,
        cycle: Optional[float] = None,
        objective_value: Optional[float] = None,
    ) -> RoundTrace:
        return RoundTrace(
            name=name,
            objective_name=objective_name,
            objective_value=float(sol.fun) if objective_value is None else float(objective_value),
            status="optimal" if sol.status == 0 else "time_limit",
            message=sol.message,
            cycle=model.cycle_value(sol.values) if cycle is None else cycle,
            waste=model.waste_value(sol.values),
            selected=model.selected_phases(sol.values),
            mip_gap=sol.mip_gap,
        )

    @staticmethod
    def _aggregate_sigmas(model: Stage1Model, values: Dict[VarKey, float]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for slack in model.slack_specs:
            name = slack.base_name or slack.spec_name
            out[name] = out.get(name, 0.0) + float(values.get(slack.key, 0.0))
        return out
