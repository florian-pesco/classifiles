"""Request bodies of the REST API."""

from __future__ import annotations

from pydantic import BaseModel


class ScanRequest(BaseModel):
    inbox: str | None = None
    max_files: int | None = None  # None = settings default; 0 = all files
    only_new: bool = False


class ReviewRequest(BaseModel):
    action: str  # accept | reject | reset
    folder: str | None = None
    subpath: str | None = None  # folder inside the category; "" = top level; None = keep/suggest
    name: str | None = None  # new file name; "" = keep the original; None = unchanged
    use_suggested_name: bool | None = None


class BulkReviewRequest(BaseModel):
    ids: list[int]
    action: str  # accept | reject | reset
    folder: str | None = None
    subpath: str | None = None
    use_suggested_name: bool | None = None  # True: use the suggested names; False: keep the originals


class SettingsPatch(BaseModel):
    inboxes: list[str] | None = None
    threshold: float | None = None
    max_files: int | None = None  # 0 = no limit
    backend: str | None = None
    laya_model: str | None = None
    samples_per_folder: int | None = None
    examples_per_category: int | None = None
    content_per_category: int | None = None
    gpu_memory_fraction: float | None = None
    cpu_threads: int | None = None
    ocr_pdf_pages: int | None = None
    ocr_images: bool | None = None
    use_download_source: bool | None = None
    second_opinion: bool | None = None
    image_similarity: bool | None = None
    image_weight: float | None = None
    images_per_category: int | None = None
    detect_duplicates: bool | None = None
    duplicates_in_sorted: bool | None = None
    llm_model: str | None = None
    llm_max_files: int | None = None
    relearn_in_scan: bool | None = None
    trained_weight: float | None = None
    name_suggestions: bool | None = None
    name_llm: bool | None = None
    use_suggested_names: bool | None = None
    name_transliterate: bool | None = None
    name_media: bool | None = None
    name_media_labels: bool | None = None
    name_media_place: bool | None = None


class CategoriesUpdate(BaseModel):
    roots: list[dict]
    categories: list[dict]  # per-category settings: path + name/description/enabled/action
    delete: dict | None = None


class RulesUpdate(BaseModel):
    rules: list[dict]


class ScopeRequest(BaseModel):
    scan_id: int | None = None


class AcceptSuggestedRequest(BaseModel):
    scan_id: int | None = None
    top_level: bool = False  # True: ignore subfolder suggestions, put files in the category folder
