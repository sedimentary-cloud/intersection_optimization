"""适配层：JSON -> signal_timing 公开数据结构。"""
from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

from signal_timing import (
    ConstraintSpec,
    IntersectionData,
    LexicographicOptimizer,
    Movement,
    Phase,
    Trigger,
)

from .schemas import (
    ConstraintPayload,
    IntersectionPayload,
    TriggerPayload,
)

OrderFilter = Callable[[tuple[str, ...]], bool]

_REFERENCE_MODE_MAP = {
    "off": "none",
    "none": "none",
    "hard": "hard",
    "soft": "soft",
    "prefer": "prefer",
}


def build_intersection(payload: IntersectionPayload) -> IntersectionData:
    """按算法包公开构造函数装配 IntersectionData；不复制其校验规则。"""
    if not payload.movements:
        raise ValueError("intersection.movements 不能为空")
    if not payload.phases:
        raise ValueError("intersection.phases 不能为空")

    movements = {
        movement.id: Movement(mid=movement.id, demand=movement.demand)
        for movement in payload.movements
    }
    phases = {
        phase.id: Phase(pid=phase.id, capacity=dict(phase.capacity))
        for phase in payload.phases
    }
    lost_time = {
        (from_id, to_id): float(seconds)
        for from_id, to_id, seconds in payload.lost_time
    }
    return IntersectionData(
        movements=movements,
        phases=phases,
        lost_time=lost_time,
        g_min=payload.g_min,
        c_min=payload.c_min,
        c_max=payload.c_max,
        strict_clearance=payload.strict_clearance,
        static_precheck=payload.static_precheck,
        reference_order=payload.reference_order,
    )


def build_trigger(payload: TriggerPayload) -> Trigger:
    """JSON 触发对象 -> 算法包 Trigger。"""
    if payload.kind == "always":
        return Trigger.always()
    if payload.kind == "or":
        return Trigger.any_of(*payload.phases)
    if payload.kind == "and":
        return Trigger.all_of(*payload.phases)
    raise ValueError(f"未知 trigger.kind: {payload.kind!r}")


def build_constraint(payload: ConstraintPayload) -> ConstraintSpec:
    """JSON 约束 -> 算法包 ConstraintSpec。

    校验完全交给 ``ConstraintSpec.__post_init__``；这里只做机械翻译。
    """
    coeffs = {}
    for kind, name, value in payload.coeffs:
        key = (str(kind), str(name))
        coeffs[key] = float(value)
    return ConstraintSpec(
        name=payload.name,
        coeffs=coeffs,
        sense=payload.sense,
        rhs=float(payload.rhs),
        trigger=build_trigger(payload.trigger),
        soft=payload.soft,
        penalty=float(payload.penalty),
        slack_max=payload.slack_max,
        confirm_mixed_trigger=payload.confirm_mixed_trigger,
        confirm_audit=payload.confirm_audit,
        or_existential=payload.or_existential,
    )


def build_constraints(payloads: Sequence[ConstraintPayload]) -> list[ConstraintSpec]:
    """批量翻译。"""
    return [build_constraint(p) for p in payloads]


def build_optimizer(
    data: IntersectionData,
    specs: Sequence[ConstraintSpec],
    solver_config: Any,
) -> LexicographicOptimizer:
    """按 JSON solver 配置构造 LexicographicOptimizer 并注册约束。"""
    optimizer = LexicographicOptimizer(
        data,
        eps_cycle=float(solver_config.eps_cycle),
        eps_waste=float(solver_config.eps_waste),
        delta_abs=solver_config.waste_abs_tol,
        mip_rel_gap=float(solver_config.mip_rel_gap),
        time_limit=float(solver_config.time_limit),
        disp=bool(solver_config.disp),
        max_fallback=int(solver_config.max_fallback),
    )
    optimizer.add_constraints(specs)
    return optimizer


def reference_mode_to_ordering_mode(mode: str) -> str:
    """前端/接口命名 -> OrderingPostProcessor.reference_mode。"""
    if mode not in _REFERENCE_MODE_MAP:
        raise ValueError(f"未知 reference_order_mode: {mode!r}")
    return _REFERENCE_MODE_MAP[mode]


def compile_order_rules(
    rules: Sequence[dict[str, Any]],
    *,
    phase_ids: Sequence[str],
) -> Optional[OrderFilter]:
    """把声明式 order_rules 编译成 order_filter 谓词。

    规则类型：
    - ``precedence``: ``before`` 必须排在 ``after`` 之前（两者都选中时）。
    - ``adjacent``: ``a`` 与 ``b`` 必须相邻（两者都选中时）。
    - ``forbidden_adjacent``: ``a`` 与 ``b`` 禁止相邻（两者都选中时）。
    """
    if not rules:
        return None

    known = set(phase_ids)
    predicates: list[Callable[[tuple[str, ...]], bool]] = []

    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError(f"order_rules[{index}] 必须是对象")
        rule_type = str(rule.get("type", "")).strip()
        if rule_type == "precedence":
            before = str(rule.get("before", "")).strip()
            after = str(rule.get("after", "")).strip()
            if before not in known or after not in known:
                raise ValueError(
                    f"order_rules[{index}] 引用了未知相位: before={before!r}, after={after!r}"
                )

            def precedence(order, before=before, after=after):
                if before not in order or after not in order:
                    return True
                return order.index(before) < order.index(after)

            predicates.append(precedence)
        elif rule_type == "adjacent":
            a = str(rule.get("a", "")).strip()
            b = str(rule.get("b", "")).strip()
            if a not in known or b not in known:
                raise ValueError(f"order_rules[{index}] 引用了未知相位: a={a!r}, b={b!r}")

            def adjacent(order, a=a, b=b):
                if a not in order or b not in order:
                    return True
                return abs(order.index(a) - order.index(b)) == 1

            predicates.append(adjacent)
        elif rule_type == "forbidden_adjacent":
            a = str(rule.get("a", "")).strip()
            b = str(rule.get("b", "")).strip()
            if a not in known or b not in known:
                raise ValueError(f"order_rules[{index}] 引用了未知相位: a={a!r}, b={b!r}")

            def forbidden_adjacent(order, a=a, b=b):
                if a not in order or b not in order:
                    return True
                return abs(order.index(a) - order.index(b)) != 1

            predicates.append(forbidden_adjacent)
        else:
            raise ValueError(f"order_rules[{index}] 未知规则类型: {rule_type!r}")

    def order_filter(order: tuple[str, ...]) -> bool:
        return all(predicate(order) for predicate in predicates)

    return order_filter
