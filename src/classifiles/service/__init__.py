"""ClassifilesService and its parts. Import the service from here (or from `classifiles`)."""

import threading  # noqa: F401 - reachable as classifiles.service.threading (tests patch Thread)

from .. import llm  # noqa: F401 - reachable as classifiles.service.llm (tests patch the model client)
from .core import ClassifilesService
from .helpers import PROPOSALS_SQL, _learned_folder, _learned_key, _vec, _vec_bytes, _without_vectors

__all__ = ["PROPOSALS_SQL", "ClassifilesService", "_learned_folder", "_learned_key", "_vec", "_vec_bytes",
           "_without_vectors"]
