"""ClassifilesService: the public facade. UIs (CLI, web API, a future LifeOS) only talk to this.

The methods live in focused modules (one mixin each); this class only combines them and holds the state."""

from __future__ import annotations

import threading
from typing import Any

from .. import background
from ..classifier import Classifier
from ..config import Settings
from ..deciders import Decider, make_decider
from ..deciders.examples import ExampleDecider
from ..duplicates import Catalog
from ..examples import ExampleIndex
from ..image_index import ImageIndex
from ..rules import load_rules
from ..store import Store
from ..taxonomy import Category
from .applying import ApplyingMixin
from .categories import CategoriesMixin
from .learning import LearningMixin
from .llm_steps import LlmStepsMixin
from .names import NamesMixin
from .reviewing import ReviewingMixin
from .scanning import ScanningMixin


class ClassifilesService(CategoriesMixin, ScanningMixin, ReviewingMixin, NamesMixin, ApplyingMixin, LearningMixin,
                         LlmStepsMixin):
    def __init__(self, settings: Settings | None = None, decider: Decider | None = None, store: Store | None = None):
        self.settings = settings or Settings.load()
        self._decider = decider
        self._categories: list[Category] | None = None
        self._index: ExampleIndex | None = None
        self._llm_cancel = threading.Event()
        self.store = store or Store(self.settings.db_path)
        self._catalog = Catalog(self.store)
        self._image_index: ImageIndex | None = None
        self._trained: tuple[str, Any] | None = None  # (embedding model, TrainedModel | None), loaded lazily
        self._trained_status: dict = {"state": "idle"}
        self._train_lock = threading.Lock()
        self._retrainer = background.Debounced(self.retrain, "classifiles-retrain")
        self._auto_trained = False
        self._relearn_cache: tuple = (None, {})  # (signature, {proposal id: stored candidates})
        self.retrain_delay = 30.0  # seconds after an apply/undo before retraining (debounced)
        self._labeller = None
        self._places = None

    def decider(self) -> Decider:
        if self._decider is None:
            if self.settings.backend == "examples":
                images = self.image_index() if self.settings.image_similarity else None
                trained = self.trained_model() if self.settings.trained_weight > 0 else None
                self._decider = ExampleDecider(self.example_index(), images, self.settings.image_weight,
                                               trained, self.settings.trained_weight)
                if trained is None and self.settings.trained_weight > 0 and not self._auto_trained:
                    self._auto_trained = True  # first use with an index but no model yet: train once
                    self.schedule_retrain(0)
            else:
                self._decider = make_decider(self.settings.backend, laya_model=self.settings.laya_model)
        return self._decider

    def reset_decider(self) -> None:
        self._decider = None

    def classifier(self, inbox: str | None = None) -> Classifier:
        cats = self.categories(refresh=True, also_exclude=inbox)
        return Classifier(cats, self.decider(), load_rules(self.settings.rules))
