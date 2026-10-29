"""Suggested file names: `<Subject>_<Type>_<Detail>.<ext>`, topic first, PascalCase parts.

    Telekom_Invoice_2025-03.pdf     subject = sender, type, detail = date of the document
    Econ1_Slides_Ch03.pdf           subject = course (from the subfolder), detail = chapter

Rules only (instant): clean the original name, find a date (text first, then name), a type from a
bilingual keyword table, and subject candidates (subfolder, sender in the text, words of the name).
The local language model can later pick the type and the subject among these candidates (llm.py);
it never writes names itself.

Only documents and scans/screenshots get suggestions; photos, videos, archives, installers and code
keep their names (camera names and versions carry meaning).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import PurePath

DOC_KINDS = {"pdf", "document", "presentation", "spreadsheet", "text"}
MAX_STEM = 60

# (English, German, keywords). Keywords are lowercase; short ones only match as whole words.
TYPES: list[tuple[str, str, tuple[str, ...]]] = [
    ("Invoice", "Rechnung", ("rechnung", "invoice", "faktura")),
    ("Receipt", "Quittung", ("quittung", "receipt", "kassenbon", "kaufbeleg", "zahlungsbeleg")),
    ("Statement", "Kontoauszug", ("kontoauszug", "account statement", "bank statement", "depotauszug")),
    ("Payslip", "Lohnabrechnung", ("lohnabrechnung", "gehaltsabrechnung", "entgeltabrechnung", "payslip", "pay slip")),
    ("Contract", "Vertrag", ("vertrag", "contract", "vereinbarung", "agreement")),
    ("Offer", "Angebot", ("angebot", "kostenvoranschlag", "quotation")),
    ("Order", "Bestellung", ("bestellbestätigung", "auftragsbestätigung", "bestellung", "order confirmation")),
    ("Insurance", "Versicherung", ("versicherung", "insurance", "versicherungsschein")),
    ("Tax", "Steuer", ("steuerbescheid", "steuererklärung", "steuer", "tax return", "tax")),
    ("Certificate", "Zeugnis", ("zeugnis", "zertifikat", "certificate", "bescheinigung", "urkunde", "transcript")),
    ("Application", "Bewerbung", ("bewerbung", "anschreiben", "cover letter", "application")),
    ("CV", "Lebenslauf", ("lebenslauf", "curriculum vitae", "resume", "cv")),
    ("Ticket", "Ticket", ("ticket", "fahrkarte", "boarding pass", "bordkarte")),
    ("Form", "Formular", ("formular", "antrag", "form")),
    ("Manual", "Anleitung", ("bedienungsanleitung", "anleitung", "handbuch", "manual", "user guide")),
    ("Exam", "Klausur", ("altklausur", "klausur", "prüfung", "pruefung", "exam")),
    ("Solution", "Loesung", ("musterlösung", "lösung", "loesung", "solution")),
    ("Exercise", "Uebung", ("übungsblatt", "aufgabenblatt", "übung", "uebung", "exercise", "assignment", "homework")),
    ("Slides", "Folien", ("vorlesungsfolien", "folien", "slides", "lecture")),
    ("Notes", "Notizen", ("mitschrift", "zusammenfassung", "notizen", "summary", "notes")),
    ("Thesis", "Abschlussarbeit", ("bachelorarbeit", "masterarbeit", "seminararbeit", "thesis")),
    ("Report", "Bericht", ("bericht", "protokoll", "report")),
    ("Letter", "Brief", ("sehr geehrte", "dear sir", "dear madam", "anschreiben", "letter")),
    ("Screenshot", "Screenshot", ()),
    ("Scan", "Scan", ()),
]
TYPE_WORDS = {en: (en, de) for en, de, _ in TYPES}
DATED = {"Invoice", "Receipt", "Statement", "Payslip", "Contract", "Offer", "Order", "Insurance", "Tax",
         "Certificate", "Ticket", "Letter", "Form", "Application", "Screenshot", "Scan"}

MONTHS = {m: i for i, names in enumerate([
    ("jan", "januar", "january", "jänner"), ("feb", "februar", "february"), ("mär", "mar", "märz", "maerz", "march"),
    ("apr", "april"), ("mai", "may"), ("jun", "juni", "june"), ("jul", "juli", "july"), ("aug", "august"),
    ("sep", "sept", "september"), ("okt", "oct", "oktober", "october"), ("nov", "november"),
    ("dez", "dec", "dezember", "december")], 1) for m in names}
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))

_DATE_PATTERNS = [  # (regex, groups: y, m, d)
    (re.compile(r"(?<!\d)((?:19|20)\d\d)[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)"), ("y", "m", "d")),
    (re.compile(r"(?<!\d)(0?[1-9]|[12]\d|3[01])[./](0?[1-9]|1[0-2])[./]((?:19|20)\d\d)(?!\d)"), ("d", "m", "y")),
    (re.compile(r"(?<!\d)(0?[1-9]|[12]\d|3[01])\.(0?[1-9]|1[0-2])\.(\d\d)(?![\d.])"), ("d", "m", "yy")),
    (re.compile(rf"(?<!\d)(0?[1-9]|[12]\d|3[01])\.?\s+({_MONTH})\.?\s+((?:19|20)\d\d)(?!\d)", re.I), ("d", "mon", "y")),
    (re.compile(rf"\b({_MONTH})\.?\s+(0?[1-9]|[12]\d|3[01]),?\s+((?:19|20)\d\d)(?!\d)", re.I), ("mon", "d", "y")),
    (re.compile(rf"\b({_MONTH})\.?\s+((?:19|20)\d\d)(?!\d)", re.I), ("mon", "y")),
    (re.compile(r"(?<!\d)((?:19|20)\d\d)[-_](0[1-9]|1[0-2])(?![\d])"), ("y", "m")),
]
_DATE_HINT = re.compile(r"(rechnungsdatum|datum|date|ausgestellt|issued|vom|dated)\W{0,3}$", re.I)

# removed from the whole name first (they contain separators), then word by word
_JUNK_SPANS = re.compile(
    r"\(\d+\)|\[\d+\]|copy of|"
    r"(?<![0-9a-z])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-z])"
    r"|(?<![0-9a-z])(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{10,}(?![0-9a-z])"
    r"|(?<!\d)\d{1,2}[.:]\d\d[.:]\d\d(?!\d)|(?<!\d)\d{9,}(?!\d)", re.I)
_JUNK_WORDS = {"kopie", "copy", "final", "finale", "neu", "new", "download", "downloaded", "untitled", "unbenannt",
               "img", "dsc", "pxl", "scan", "scanned", "screenshot", "bildschirmfoto", "document", "dokument", "file",
               "datei", "at", "um", "am", "von", "from", "screen", "shot"}
_SCREEN = re.compile(r"^(screenshot|bildschirmfoto|screen shot|capture)", re.I)
_SCANNAME = re.compile(r"^(scan|document|dokument)[ _-]?\d", re.I)
_COUNT_WORDS = ("sheet", "blatt", "chapter", "kapitel", "ch", "week", "woche", "lecture", "vorlesung", "vl",
                "part", "teil", "exercise", "uebung", "übung", "aufgabe", "task", "session", "unit", "lesson",
                "lektion", "termin", "tutorial", "tut")
_COUNT = re.compile(rf"(?<![A-Za-zÄÖÜäöüß])([A-Za-zÄÖÜäöüß]*?(?:{'|'.join(_COUNT_WORDS)}))[ _.-]?0*(\d{{1,3}})(?!\d)", re.I)
_DE_HINT = re.compile(r"rechnung|vertrag|quittung|kontoauszug|abrechnung|angebot|bestellung|versicherung|steuer|"
                      r"zeugnis|bescheinigung|bewerbung|lebenslauf|fahrkarte|formular|antrag|anleitung|handbuch|"
                      r"klausur|prüfung|lösung|loesung|übung|uebung|blatt|folien|vorlesung|mitschrift|zusammenfassung|"
                      r"notizen|bericht|protokoll|sehr geehrte|kassenbon|summe|datum|mwst|[äöüß]", re.I)
_EN_HINT = re.compile(r"invoice|contract|receipt|statement|payslip|agreement|certificate|application|exercise|"
                      r"solution|slides|lecture|notes|summary|report|manual|exam|dear ", re.I)
_GENERIC_FOLDERS = re.compile(r"^(\d+|misc|sonstiges|diverses|other|others|andere|archiv|archive|neu|new|"
                              r"inbox|unsorted|temp|tmp|alt|old|dokumente|documents|docs|unterlagen|files|dateien|"
                              r"scans|bilder|pictures|images|photos|fotos|videos|downloads|privat|private|personal)$",
                              re.I)
# the sender in the text says something for these; on a CV or thesis it's just a mail or university domain
SENDER_TYPES = {"Invoice", "Receipt", "Statement", "Payslip", "Contract", "Offer", "Order", "Insurance", "Tax",
                "Certificate", "Ticket", "Letter", "Form", None}
_SUFFIX = re.compile(r"\b([A-ZÄÖÜ][\w&.-]*(?:\s+[A-ZÄÖÜ&][\w&.-]*){0,2})\s+(GmbH|AG|KG|SE|UG|e\.\s?V\.|Inc\.?|Ltd\.?|LLC|"
                     r"GbR|OHG|mbH)\b")
_MAIL = re.compile(r"[\w.+-]+@([a-z0-9-]+)\.[a-z.]{2,}", re.I)
_WEB = re.compile(r"\bwww\.([a-z0-9-]+)\.[a-z]{2,}", re.I)
_FREE_MAIL = {"gmail", "googlemail", "gmx", "web", "outlook", "hotmail", "yahoo", "icloud", "live", "t-online",
              "freenet", "posteo", "mail", "protonmail", "aol", "example"}
_STOP_DE = {"der", "die", "das", "und", "ist", "nicht", "mit", "für", "sie", "wir", "ihre", "ihr", "sehr", "geehrte",
            "von", "zu", "den", "dem", "ein", "eine", "auf", "bitte", "vielen", "dank"}
_STOP_EN = {"the", "and", "is", "of", "to", "for", "your", "with", "dear", "this", "that", "please", "you", "are"}
_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})


@dataclass
class NameSuggestion:
    name: str                              # full file name with extension
    subject: str | None = None
    type: str | None = None                # English key of TYPES
    detail: str | None = None
    lang: str = "en"
    reason: str = ""
    subject_candidates: list[str] = field(default_factory=list)


def in_scope(kind: str, name: str, text: str = "", meta: dict | None = None) -> bool:
    """Documents, plus pictures that are screenshots or scans (with text found by OCR)."""
    if kind in DOC_KINDS:
        return True
    if kind != "image":
        return False
    meta = meta or {}
    stem = PurePath(name).stem
    if meta.get("looks_like") == "photo" and len(text.strip()) < 40:
        return False  # camera photo: its name carries date and order
    return (meta.get("looks_like") == "screenshot" or bool(_SCREEN.match(stem)) or bool(_SCANNAME.match(stem))
            or len(text.strip()) >= 40)


def language(text: str, name: str = "") -> str:
    """'de' or 'en': by common words of the text, else by document words in text and name."""
    s = text[:3000] + " " + name
    words = re.findall(r"[a-zäöüß]+", s.lower())
    de, en = sum(w in _STOP_DE for w in words), sum(w in _STOP_EN for w in words)
    if de == en:
        de, en = len(_DE_HINT.findall(s)), len(_EN_HINT.findall(s))
    return "de" if de > en else "en"


def _kw(k: str) -> re.Pattern:
    # short keywords (cv, tax, form) only as whole words; long ones also inside compounds (Mietvertrag)
    return re.compile(rf"\b{re.escape(k)}\b" if len(k) <= 4 else re.escape(k))


_KW = [(en, [(_kw(k), k) for k in kws]) for en, _, kws in TYPES]


def type_scores(name: str, text: str = "") -> dict[str, int]:
    """Keyword score per type: name hits weigh 3, text hits 1 (first 2000 characters)."""
    n, t = name.lower(), text[:2000].lower()
    scores = {}
    for en, pats in _KW:
        s = sum(3 for p, _ in pats if p.search(n)) + sum(1 for p, _ in pats if p.search(t))
        if s:
            scores[en] = s
    return scores


def detect_type(name: str, text: str = "") -> tuple[str | None, str]:
    """(English type key, where it was found: "'rechnung' in the name" / "… in the text")."""
    n, t = name.lower(), text[:2000].lower()
    scores = type_scores(name, text)
    if not scores:
        return None, ""
    best = max(scores, key=scores.get)
    if scores[best] < 2 and best == "Letter":
        return None, ""  # a greeting alone is weak
    pats = dict(_KW)[best]
    hit = next((k for p, k in pats if p.search(n)), None)
    if hit:
        return best, f"'{hit}' in the name"
    return best, f"'{next((k for p, k in pats if p.search(t)), best.lower())}' in the text"


def _norm(s: str) -> str:
    return s.translate(_TRANSLIT).lower()


def _similar(a: str, b: str) -> bool:
    """'Lebenslaeufe' ~ 'Lebenslauf', 'Rechnungen' ~ 'Rechnung': shared start of ≥ 80% of the shorter."""
    a, b = _norm(a), _norm(b)
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return n >= 5 and n >= 0.8 * min(len(a), len(b))


def type_of_word(word: str) -> str | None:
    """The type a folder or word stands for ('Lebensläufe' -> CV, 'Rechnungen' -> Invoice), if any."""
    for en, de, kws in TYPES:
        for k in (en, de, *kws):
            if " " not in k and len(k) >= 5 and _similar(word, k):
                return en
    return None


def _mk_date(parts: dict) -> tuple[str, int] | None:
    """(normalised date, precision 3 = day / 2 = month) or None if implausible."""
    try:
        y = int(parts["y"]) if "y" in parts else 2000 + int(parts["yy"])
        m = int(parts["m"]) if "m" in parts else MONTHS[parts["mon"].lower().rstrip(".")]
        if not 1990 <= y <= date.today().year + 1:
            return None
        if "d" in parts:
            d = date(y, m, int(parts["d"]))
            return d.isoformat(), 3
        date(y, m, 1)
        return f"{y:04d}-{m:02d}", 2
    except (ValueError, KeyError):
        return None


def find_dates(s: str) -> list[tuple[int, str, int, bool]]:
    """[(position, date, precision, after a date word like 'Rechnungsdatum')] in order of position."""
    out, taken = [], []
    for rx, groups in _DATE_PATTERNS:
        for m in rx.finditer(s):
            if any(a < m.end() and m.start() < b for a, b in taken):
                continue
            d = _mk_date(dict(zip(groups, m.groups())))
            if d:
                taken.append((m.start(), m.end()))
                out.append((m.start(), d[0], d[1], bool(_DATE_HINT.search(s[max(0, m.start() - 25):m.start()]))))
    return sorted(out)


def pick_date(name: str, text: str = "", created: str | None = None, dated: bool = True) -> tuple[str | None, str]:
    """The document's date and where it came from.

    dated (invoices, contracts, letters …): a date labelled in the text ('Rechnungsdatum: …'), else
    one in the file name, else the document's creation date (metadata), else the first in the text.
    Other documents (CV, notes, slides …): a date in the name, else the creation date. Their text
    dates are usually unrelated (a birth date, a deadline), so they are never used."""
    in_text = find_dates(text[:3000]) if dated else []
    hinted = [d for d in in_text if d[3]]
    if hinted:
        return hinted[0][1], "date labelled in the text"
    in_name = find_dates(name)  # a date someone put into the name beats an unlabelled one in the text
    if in_name:
        return in_name[0][1], "date in the name"
    if created:
        return created, "creation date (file metadata)"
    return (in_text[0][1], "first date in the text") if in_text else (None, "")


def count_token(stem: str) -> str | None:
    """'Lecture 3', 'Blatt_04', 'ch3' -> 'Lecture03', 'Blatt04', 'Ch03'."""
    m = _COUNT.search(stem)
    return f"{pascal_word(m.group(1))}{int(m.group(2)):02d}" if m else None


def pascal_word(w: str) -> str:
    if re.search(r"[a-zäöüß][A-ZÄÖÜ]", w) or (w.isupper() and len(w) <= 4):
        return w  # ProjectX, iPhone-style, acronyms (CV, AG) stay
    return w[:1].upper() + w[1:].lower()


def pascal(text: str, transliterate: bool = True) -> str:
    """'econ 1 summary' -> 'Econ1Summary'; numbers stick to the word before them."""
    if transliterate:
        text = text.translate(_TRANSLIT)
    words = re.findall(r"[A-Za-zÀ-ÿ]+|\d+", text)
    out = ""
    for w in words:
        out += w if w.isdigit() else pascal_word(w)
    return re.sub(r"[^A-Za-z0-9À-ÿ]", "", out)


def name_words(stem: str) -> list[str]:
    """Meaningful words of the original name: junk, dates, count tokens and type words removed."""
    s = _JUNK_SPANS.sub(" ", stem)
    for rx, _ in _DATE_PATTERNS:
        s = rx.sub(" ", s)
    s = _COUNT.sub(" ", s)
    s = re.sub(r"([a-zäöüß])([A-ZÄÖÜ])", r"\1 \2", s)  # camelCase
    words = [w for w in re.split(r"[\s_.,;()\[\]+-]+", s) if w]
    type_kw = {k for _, _, kws in TYPES for k in kws}
    keep = []
    for w in words:
        low = w.lower()
        if low in type_kw or low in _JUNK_WORDS or low in _STOP_DE or low in _STOP_EN or (w.isdigit() and len(w) > 2):
            continue
        if re.fullmatch(r"v\d+", low) or (w.isdigit() and not keep):  # versions; a leading counter ("3 Slides")
            continue
        if len(w) == 1 and not w.isdigit():
            continue
        keep.append(w)
    return keep


def clean_words(stem: str) -> list[str]:
    """All words of the original name except junk (copy markers, IDs, times, 'final', 'download' …)."""
    s = _JUNK_SPANS.sub(" ", stem)
    s = re.sub(r"([a-zäöüß])([A-ZÄÖÜ])", r"\1 \2", s)
    words = [w for w in re.split(r"[\s_.,;()\[\]+-]+", s) if w]
    return [w for w in words if w.lower() not in _JUNK_WORDS and not re.fullmatch(r"v\d+", w.lower())]


def suggest_cleanup(name: str) -> NameSuggestion | None:
    """Archives, installers, code and other files: only copy markers are removed ("x (1).zip" ->
    "x.zip"); everything else stays, since such names are often versions or identifiers."""
    p = PurePath(name)
    stem = p.stem
    for _ in range(3):
        stem = _COPY_MARKERS.sub("", stem).strip()
    if not stem or stem == p.stem:
        return None
    return NameSuggestion(stem + p.suffix, None, None, None, "en", "copy marker removed", [])


def sender(text: str) -> str | None:
    head = "\n".join(text.splitlines()[:25])[:2500]
    m = _SUFFIX.search(head)
    if m:
        type_kw = {k for _, _, kws in TYPES for k in kws}
        words = [w for w in m.group(1).split() if w.lower() not in type_kw]
        if words:
            return " ".join(words).strip(" .,-")
    for rx in (_MAIL, _WEB):
        for m in rx.finditer(head):
            dom = m.group(1).lower()
            if dom not in _FREE_MAIL:
                return dom
    return None


def topic_folder(subpath: str) -> str | None:
    """The deepest folder that names a topic: not a year, not generic ('Dokumente') and not a
    document type ('Lebensläufe', 'Rechnungen')."""
    for part in reversed([p for p in (subpath or "").split("/") if p]):
        part = re.sub(r"^\d{1,3}[ _.-]+", "", part)
        if part and not _GENERIC_FOLDERS.match(part) and not re.fullmatch(r"(19|20)\d\d([-_]\d\d)?", part) \
                and not type_of_word(part):
            return part
    return None


def subject_candidates(stem: str, text: str, subpath: str, source: str | None = None,
                       type_: str | None = None) -> list[tuple[str, str]]:
    """[(subject, where)] best first: subfolder, sender in the text (business documents only),
    words of the name, download site."""
    out: list[tuple[str, str]] = []
    folder = topic_folder(subpath)
    if folder:
        out.append((folder, "folder"))
    s = sender(text) if text and type_ in SENDER_TYPES else None
    if s:
        out.append((s, "sender in the text"))
    words = name_words(stem)
    if words:
        out.append((" ".join(words[:3]), "original name"))
    if source:
        dom = source.split(".")
        if len(dom) >= 2 and dom[-2] not in _FREE_MAIL:
            out.append((dom[-2], "download site"))
    seen, uniq = set(), []
    for subj, where in out:
        key = pascal(subj).lower()
        if key and key not in seen:
            seen.add(key)
            uniq.append((subj, where))
    return uniq


def compose(subject: str | None, type_: str | None, detail: str | None, ext: str, lang: str,
            transliterate: bool = True, min_parts: int = 2) -> str | None:
    parts = []
    if subject:
        parts.append(pascal(subject, transliterate))
    if type_:
        word = TYPE_WORDS[type_][1 if lang == "de" else 0]
        if not (parts and (word.lower() in parts[0].lower() or _similar(parts[0], word))):  # "Mietvertrag" says Vertrag
            parts.append(pascal(word, transliterate))
    if detail:
        parts.append(detail if re.fullmatch(r"\d{4}(-\d\d){0,2}", detail) else pascal(detail, transliterate))
    parts = [p for p in parts if p]
    if len(parts) < min_parts:
        return None
    stem = "_".join(parts)
    while len(stem) > MAX_STEM and len(parts[0]) > 8:  # shorten the subject first
        parts[0] = parts[0][:max(8, len(parts[0]) - (len(stem) - MAX_STEM))]
        stem = "_".join(parts)
    return stem[:MAX_STEM] + ext.lower()


def suggest(name: str, kind: str, text: str = "", meta: dict | None = None, subpath: str = "",
            transliterate: bool = True, type_: str | None = None, subject: str | None = None) -> NameSuggestion | None:
    """A suggested name, or None (out of scope, nothing better found, or already like this).
    type_ / subject: chosen by the language model, overriding the rules. The reason lists every
    part with its value and where it came from."""
    meta = meta or {}
    if not in_scope(kind, name, text, meta):
        return None
    p = PurePath(name)
    stem, ext = p.stem, p.suffix
    lang = language(text, stem)
    why = []

    # type: language model > keywords > the folder's type ('Lebensläufe') > picture kind
    if type_:
        t, t_where = type_, "chosen by the language model"
    else:
        t, t_where = detect_type(stem, text)
        folder_type = next((type_of_word(x) for x in reversed((subpath or "").split("/")) if x and type_of_word(x)), None)
        if t is None and folder_type:
            t, t_where = folder_type, "the folder it goes to"
        if t is None and kind == "image":
            t, t_where = ("Screenshot", "a screenshot") if (meta.get("looks_like") == "screenshot"
                                                         or _SCREEN.match(stem)) else ("Scan", "a picture with text")
    if t:
        why.append(f"type {TYPE_WORDS[t][1 if lang == 'de' else 0]}: {t_where}")

    cands = subject_candidates(stem, text, subpath, meta.get("source"), t)
    if subject:
        subj, s_where = subject, "chosen by the language model"
    elif cands:
        subj, s_where = cands[0]
    else:
        subj, s_where = None, ""
    if subj:
        why.append(f"subject {pascal(subj, transliterate)}: {s_where}")

    count = count_token(stem)
    dt, d_where = pick_date(stem, text, meta.get("created"), dated=t in DATED)
    if t in DATED or not count:
        detail, why_d = (dt, f"date {dt}: {d_where}") if dt else (count, f"number {count}: from the name")
    else:
        detail, why_d = count, f"number {count}: from the name"
    if detail:
        why.append(why_d)

    new = compose(subj, t, detail, ext, lang, transliterate)
    if not new:
        # too little for Subject_Type_Detail: clean up the original name in the same style instead,
        # keeping all its words (also type words like "assignment"), only junk removed
        words = clean_words(stem)
        if not words:
            return None
        new = compose(" ".join(words), None, detail, ext, lang, transliterate, min_parts=1)
        why = ["clean-up of the original name"] + ([why_d] if detail else [])
    if not new or new == name or new.lower() == name.lower():
        return None
    return NameSuggestion(new, subj, t, detail, lang, "; ".join(why), [c for c, _ in cands])


def valid_name(name: str) -> str:
    """A user-typed name made safe (raises ValueError if nothing is left). Keeps the user's style."""
    from .paths import _INVALID

    n = _INVALID.sub("", name.replace("/", "-").replace("\\", "-")).strip().rstrip(". ")
    if not n or n in (".", ".."):
        raise ValueError("Enter a file name")
    if len(n) > 200:
        raise ValueError("File name is too long")
    return n


# --- photos and videos --------------------------------------------------------------------
#
#     <original core>_<Title>.<ext>
#     IMG_20250314_103344.jpg  ->  IMG_20250314_103344_Strand_Barcelona.jpg
#
# The core is the original name, cleaned (copy markers, spaces); a meaningless core (hash, bare
# number) is replaced by the date taken. The title has at most two parts, in this order: the topic
# folder, what the picture shows (labels.py), the town from GPS (places.py); the source app only
# when nothing else is known. Words already in the core are not repeated.

MEDIA_KINDS = {"image", "video"}
MAX_CORE = 45
_COPY_MARKERS = re.compile(r"\s*\(\d+\)$|[\s_-]+(kopie|copy)(\s*\d+)?$|^(copy of|kopie von)\s+", re.I)
_MEANINGLESS = re.compile(r"(?=.*[0-9a-f])[0-9a-f-]{12,}|\d{6,}|[0-9a-f]{8}-?[0-9a-f]{4}-?.*", re.I)
_APPS = [
    ("WhatsApp", re.compile(r"-WA\d{3,}|whatsapp", re.I)),
    ("Signal", re.compile(r"^signal[-_ ]", re.I)),
    ("Telegram", re.compile(r"telegram", re.I)),
    ("Bildschirmaufnahme", re.compile(r"screen[ _-]?recording|bildschirmaufnahme|screenrecord", re.I)),
]


def source_app(stem: str) -> str | None:
    return next((app for app, rx in _APPS if rx.search(stem)), None)


def media_core(stem: str) -> str:
    """The original name, cleaned: copy markers removed, spaces -> '_', invalid characters removed."""
    from .paths import _INVALID

    core = stem
    for _ in range(3):  # "x (1) - Kopie"
        core = _COPY_MARKERS.sub("", core).strip()
    core = _INVALID.sub("", core)
    core = re.sub(r"\s+", "_", core)
    core = re.sub(r"_{2,}", "_", core).strip("_.- ")
    return core


def _is_meaningless(core: str) -> bool:
    """A hash, UUID or bare counter/timestamp without a readable date."""
    if not core:
        return True
    if find_dates(core):
        return False
    return bool(_MEANINGLESS.fullmatch(core)) or bool(re.fullmatch(r"\d+", core))


def _taken_date(meta: dict) -> str | None:
    m = re.match(r"(\d{4})[:-](\d\d)[:-](\d\d)", str(meta.get("taken") or ""))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m and 1990 <= int(m.group(1)) else None


def suggest_media(name: str, kind: str, meta: dict | None = None, subpath: str = "", hints: dict | None = None,
                  transliterate: bool = True) -> NameSuggestion | None:
    """Name suggestion for a photo or video. hints: {"label", "label_share", "place", "app"}."""
    meta, hints = meta or {}, hints or {}
    if kind not in MEDIA_KINDS:
        return None
    p = PurePath(name)
    stem, ext = p.stem, p.suffix
    why = []
    core = media_core(stem)
    if _is_meaningless(core):
        taken = _taken_date(meta)
        why.append(f"core {taken}: date taken (the original name says nothing)" if taken
                   else "original name says nothing: dropped")
        core = taken or ""
    else:
        if core != stem:
            why.append(f"core {core}: original name, cleaned")
        else:
            why.append(f"core {core}: original name")
    if len(core) > MAX_CORE:
        core = core[:MAX_CORE].rstrip("_-. ")

    in_core = _norm(core).replace("_", "").replace("-", "")
    title, sources = [], []
    candidates = [(topic_folder(subpath), "topic folder"),
                  (hints.get("label"), f"picture recognition ({hints.get('label_share', 0):.2f})"),
                  (hints.get("place"), "GPS")]
    for value, where in candidates:
        if not value or len(title) >= 2:
            continue
        word = pascal(value, transliterate)
        if word and _norm(word) not in in_core and word not in title:
            title.append(word)
            sources.append(f"{word}: {where}")
    if not title and hints.get("app") and _norm(hints["app"]) not in in_core:
        title.append(hints["app"])
        sources.append(f"{hints['app']}: source app")
    why += [f"title {s}" for s in sources]

    stem_new = "_".join(x for x in [core, *title] if x)[:MAX_STEM]
    if not stem_new:
        return None
    new = stem_new + ext.lower()
    if new == name:
        return None
    return NameSuggestion(new, core or None, None, None, "de", "; ".join(why), [])
