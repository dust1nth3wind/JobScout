"""Small deterministic normalization helpers shared by collectors and matching."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobscout.domain import WorkplaceType


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.ignored_depth:
            self.ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth and data.strip():
            self.parts.append(data.strip())


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    parser = _TextExtractor()
    parser.feed(value)
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def normalized_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9+#.]+", " ", value.lower()).strip()


GERMAN_MARKERS = {
    "und", "oder", "mit", "fur", "eine", "einen", "wir", "sie", "deine", "ihre",
    "kenntnisse", "erfahrung", "aufgaben", "anforderungen", "bewerbung", "arbeitsplatz",
}
ENGLISH_MARKERS = {
    "and", "or", "with", "for", "the", "you", "your", "we", "our", "skills",
    "experience", "responsibilities", "requirements", "apply", "workplace",
}


def infer_language(text: str) -> str:
    lowered = text.lower()
    tokens = normalized_text(text).split()
    german = sum(token in GERMAN_MARKERS for token in tokens) + sum(lowered.count(char) for char in "äöüß")
    english = sum(token in ENGLISH_MARKERS for token in tokens)
    if german >= 2 and german >= english + 1:
        return "de"
    if english >= 2 and english >= german + 1:
        return "en"
    return "unknown"


def infer_seniority(title: str) -> str:
    value = normalized_text(title)
    ordered = (
        ("intern", ("intern", "internship", "praktikant", "working student", "werkstudent")),
        ("executive", ("director", "vice president", "vp", "chief", "head of")),
        ("principal", ("principal", "staff")),
        ("lead", ("lead", "manager", "teamleiter")),
        ("senior", ("senior", "sr", "erfahren")),
        ("junior", ("junior", "jr", "entry level", "einsteiger")),
    )
    for seniority, markers in ordered:
        if any(re.search(rf"\b{re.escape(marker)}\b", value) for marker in markers):
            return seniority
    return "unknown"


COUNTRY_ALIASES = {
    "australia": "AU", "canada": "CA", "singapore": "SG",
    "new zealand": "NZ", "india": "IN", "japan": "JP",
    "united arab emirates": "AE", "uae": "AE",
    "austria": "AT", "osterreich": "AT", "österreich": "AT",
    "belgium": "BE", "belgien": "BE", "croatia": "HR", "cyprus": "CY",
    "czechia": "CZ", "czech republic": "CZ", "denmark": "DK", "danemark": "DK",
    "estonia": "EE", "finland": "FI", "france": "FR", "frankreich": "FR",
    "germany": "DE", "deutschland": "DE", "greece": "GR", "hungary": "HU",
    "iceland": "IS", "ireland": "IE", "italy": "IT", "italien": "IT",
    "latvia": "LV", "liechtenstein": "LI", "lithuania": "LT", "luxembourg": "LU",
    "malta": "MT", "netherlands": "NL", "niederlande": "NL", "norway": "NO",
    "poland": "PL", "polen": "PL", "portugal": "PT", "romania": "RO",
    "slovakia": "SK", "slovenia": "SI", "spain": "ES", "spanien": "ES",
    "sweden": "SE", "schweden": "SE", "switzerland": "CH", "schweiz": "CH",
    "united kingdom": "GB", "uk": "GB", "great britain": "GB",
    "united states": "US", "united states of america": "US", "usa": "US",
    "toronto": "CA", "ottawa": "CA", "calgary": "CA", "edmonton": "CA",
    "vancouver": "CA", "waterloo": "CA",
}


def countries_from_locations(locations: list[str], explicit: list[str] | None = None) -> list[str]:
    countries: set[str] = set()
    for value in explicit or []:
        value = value.strip()
        if re.fullmatch(r"[A-Za-z]{2}", value):
            countries.add(value.upper())
        elif value.lower() in COUNTRY_ALIASES:
            countries.add(COUNTRY_ALIASES[value.lower()])
    for location in locations:
        lowered = location.lower()
        for alias, code in COUNTRY_ALIASES.items():
            if re.search(rf"\b{re.escape(alias)}\b", lowered):
                countries.add(code)
    return sorted(countries)


def infer_workplace(*values: str) -> WorkplaceType:
    text = normalized_text(" ".join(values))
    if re.search(r"\bhybrid\b", text):
        return WorkplaceType.HYBRID
    if re.search(r"\b(remote|homeoffice|home office)\b", text):
        return WorkplaceType.REMOTE
    if re.search(r"\b(on site|onsite|vor ort)\b", text):
        return WorkplaceType.ONSITE
    return WorkplaceType.UNKNOWN


def canonicalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    tracking_keys = {"gh_src", "lever-source", "source", "ref", "referrer"}
    query = urlencode(
        [
            (key, item)
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in tracking_keys
        ]
    )
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), query, ""))


def job_fingerprint(company: str, title: str, locations: list[str]) -> str:
    raw = "|".join((normalized_text(company), normalized_text(title), *sorted(normalized_text(x) for x in locations)))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
