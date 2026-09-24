"""变量注册表：统一管理 VarKey -> 列号、界与整数性。

设计文档 §4.1 / §10.2：
- 变量键统一为 ``(kind, name)`` 二元组；
- 所有变量必须先在注册表中注册，装配期遇到未注册变量直接报错；
- 约束矩阵按稀疏方式装配，禁止稠密化。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

VarKey = Tuple[str, str]


@dataclass
class VarInfo:
    key: VarKey
    lb: float
    ub: float
    integrality: int = 0  # 1 = integer/binary, 0 = continuous
    kind: str = ""


class VarRegistry:
    """按注册顺序分配列号的变量注册表。"""

    def __init__(self) -> None:
        self._keys: List[VarKey] = []
        self._index: Dict[VarKey, int] = {}
        self._info: Dict[VarKey, VarInfo] = {}

    # ------------------------------------------------------------------ #
    # 注册与查询
    # ------------------------------------------------------------------ #
    def register(
        self,
        key: VarKey,
        lb: float,
        ub: float,
        integrality: int = 0,
        *,
        replace: bool = False,
        kind: Optional[str] = None,
    ) -> int:
        """注册变量；返回列号。重复注册默认报错，replace=True 时更新界。"""
        if lb > ub:
            raise ValueError(f"变量 {key} 的界非法: lb={lb} > ub={ub}")
        if key in self._index:
            if not replace:
                raise ValueError(f"变量 {key} 已注册")
            info = self._info[key]
            info.lb, info.ub, info.integrality = float(lb), float(ub), int(integrality)
            if kind is not None:
                info.kind = kind
            return self._index[key]
        idx = len(self._keys)
        self._keys.append(key)
        self._index[key] = idx
        self._info[key] = VarInfo(
            key=key,
            lb=float(lb),
            ub=float(ub),
            integrality=int(integrality),
            kind=kind if kind is not None else key[0],
        )
        return idx

    def index(self, key: VarKey) -> int:
        try:
            return self._index[key]
        except KeyError as exc:  # noqa: PERF203
            raise KeyError(f"变量 {key} 未注册") from exc

    def contains(self, key: VarKey) -> bool:
        return key in self._index

    def __contains__(self, key: VarKey) -> bool:  # pragma: no cover - trivial
        return key in self._index

    def get_info(self, key: VarKey) -> VarInfo:
        try:
            return self._info[key]
        except KeyError as exc:  # noqa: PERF203
            raise KeyError(f"变量 {key} 未注册") from exc

    def get_bound(self, key: VarKey) -> Tuple[float, float]:
        info = self.get_info(key)
        return info.lb, info.ub

    @property
    def keys(self) -> List[VarKey]:
        return list(self._keys)

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self._keys)

    def bounds_arrays(self):
        lb = [self._info[k].lb for k in self._keys]
        ub = [self._info[k].ub for k in self._keys]
        return lb, ub

    def integrality_array(self):
        return [self._info[k].integrality for k in self._keys]

    def subset_registry(self, keys: Iterable[VarKey], *, copy_info: bool = True) -> "VarRegistry":
        """创建一个子注册表，用于第二阶段固定变量后的 LP 重解。"""
        sub = VarRegistry()
        for key in keys:
            info = self.get_info(key)
            sub.register(
                key,
                info.lb,
                info.ub,
                info.integrality,
                kind=info.kind,
            )
        return sub
