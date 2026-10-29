"""The state every service module works with (declared once, so type checkers can follow the mixins)."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..background import Debounced
    from ..config import Settings
    from ..deciders import Decider
    from ..duplicates import Catalog
    from ..examples import ExampleIndex
    from ..image_index import ImageIndex
    from ..store import Store
    from ..taxonomy import Category


class ServiceBase:
    settings: Settings
    store: Store
    _decider: Decider | None
    _categories: list[Category] | None
    _index: ExampleIndex | None
    _image_index: ImageIndex | None
    _catalog: Catalog
    _llm_cancel: threading.Event
    _trained: tuple[str, Any] | None
    _trained_status: dict
    _train_lock: threading.Lock
    _retrainer: Debounced
    _auto_trained: bool
    _relearn_cache: tuple
    _labeller: Any
    _places: Any
    retrain_delay: float
