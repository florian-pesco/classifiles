"""Loading models from the local Hugging Face cache: download once if missing, then stay offline."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

log = logging.getLogger(__name__)


def load_pretrained[T](load: Callable[..., T], name: str, what: str = "model", **kwargs) -> T:
    """load(name, **kwargs) from the cache only; if that fails, download once. Afterwards the
    process stays offline (HF_HUB_OFFLINE), so nothing touches the network again."""
    try:
        obj = load(name, local_files_only=True, **kwargs)
    except Exception:  # noqa: BLE001 - not in the cache: each loader raises its own error type
        log.info("Downloading %s %s (one time)", what, name)
        obj = load(name, **kwargs)
    os.environ["HF_HUB_OFFLINE"] = "1"
    return obj
