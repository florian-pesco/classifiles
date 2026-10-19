"""classifiles: classify files from inbox folders into a folder taxonomy using a local decision model.

Typical use as a library::

    from classifiles import ClassifilesService, Settings

    svc = ClassifilesService(Settings(roots=[{"path": "/mnt/d/Archive"}], threshold=0.7))
    scan_id, proposals = svc.scan("~/Downloads")
    svc.accept_suggested(scan_id)
    svc.apply(scan_id)
"""

from .config import Settings
from .service import ClassifilesService

__all__ = ["ClassifilesService", "Settings"]
__version__ = "0.1.0"
