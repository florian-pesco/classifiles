from __future__ import annotations

from .base import Decider
from .keyword import KeywordDecider


def make_decider(backend: str, **kwargs) -> Decider:
    if backend == "laya":
        from .laya import LayaDecider

        return LayaDecider(model=kwargs.get("laya_model", "convaiinnovations/laya"))
    if backend == "keyword":
        return KeywordDecider()
    raise ValueError(f"Unknown backend: {backend!r} (expected 'laya' or 'keyword')")


__all__ = ["Decider", "KeywordDecider", "make_decider"]
