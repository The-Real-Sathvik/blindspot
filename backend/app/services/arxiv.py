"""Sequential, paced arXiv Atom API client; metadata only, never PDF acquisition."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
import re
import time
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET


ARXIV_ENDPOINT = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
Transport = Callable[[str, Mapping[str, str], float], tuple[int, Mapping[str, str], bytes]]


class ArxivError(RuntimeError):
    pass


class ArxivResponseError(ArxivError):
    pass


class ArxivRequestError(ArxivError):
    pass


@dataclass(frozen=True)
class ArxivWork:
    canonical_id: str
    version: Optional[str]
    title: str
    authors: list[dict[str, str]]
    abstract: Optional[str]
    published_at: Optional[str]
    updated_at: Optional[str]
    categories: list[str]
    doi: Optional[str]
    abstract_url: str
    pdf_url: Optional[str]

    @property
    def source_metadata(self) -> dict:
        return {
            "canonical_arxiv_id": self.canonical_id,
            "version": self.version,
            "published_at": self.published_at,
            "updated_at": self.updated_at,
            "categories": self.categories,
            "abstract_url": self.abstract_url,
            "pdf_url": self.pdf_url,
            "source_doi": self.doi,
        }


def canonical_arxiv_id(value: str) -> tuple[str, Optional[str]]:
    match = re.search(r"(?:abs/)?([a-z\-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5})(v\d+)?$", value.strip(), re.IGNORECASE)
    if not match:
        raise ValueError("Invalid arXiv identifier.")
    return match.group(1), match.group(2)


def _transport(url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, Mapping[str, str], bytes]:
    request = Request(url, headers=dict(headers), method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:  # nosec B310: fixed HTTPS endpoint
            return response.status, dict(response.headers.items()), response.read()
    except HTTPError as error:
        return error.code, dict(error.headers.items()) if error.headers else {}, error.read()
    except URLError as error:
        raise ArxivRequestError("arXiv network request failed.") from error


class ArxivClient:
    source_name = "arxiv"

    def __init__(self, *, transport: Transport = _transport, timeout_seconds: float = 20.0, max_retries: int = 2, clock=time.monotonic, sleeper=time.sleep) -> None:
        self._transport, self._timeout, self._max_retries = transport, timeout_seconds, max_retries
        self._clock, self._sleeper, self._last_request = clock, sleeper, None
        self._cache: dict[tuple[str, int, int], tuple[float, list[ArxivWork]]] = {}

    def search(self, topic: str, *, limit: int = 10, start: int = 0) -> list[ArxivWork]:
        if not topic.strip() or not 1 <= limit <= 100 or start < 0:
            raise ValueError("topic, limit, or start is invalid")
        key = (topic.casefold(), limit, start)
        cached = self._cache.get(key)
        if cached and self._clock() - cached[0] < 300:
            return cached[1]
        query = urlencode({"search_query": f"all:{topic}", "start": start, "max_results": limit})
        body = self._get(f"{ARXIV_ENDPOINT}?{query}")
        works = parse_atom_feed(body)
        self._cache[key] = (self._clock(), works)
        return works

    def _get(self, url: str) -> bytes:
        for attempt in range(self._max_retries + 1):
            self._pace()
            try:
                status, headers, body = self._transport(url, {"User-Agent": "Blindspot/0.1 (metadata discovery; contact: local-user)"}, self._timeout)
            except ArxivRequestError:
                if attempt == self._max_retries:
                    raise
                self._sleeper(min(3 * (attempt + 1), 15))
                continue
            if status == 200:
                return body
            if status == 429 or 500 <= status < 600:
                if attempt < self._max_retries:
                    wait = headers.get("Retry-After") or "3"
                    try:
                        self._sleeper(max(3.0, min(float(wait), 30.0)))
                    except ValueError:
                        self._sleeper(3.0 * (attempt + 1))
                    continue
            raise ArxivRequestError(f"arXiv request failed with HTTP status {status}.")
        raise ArxivRequestError("arXiv request retry limit reached.")

    def _pace(self) -> None:
        now = self._clock()
        if self._last_request is not None and now - self._last_request < 3.0:
            self._sleeper(3.0 - (now - self._last_request))
        self._last_request = self._clock()


def parse_atom_feed(body: bytes) -> list[ArxivWork]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as error:
        raise ArxivResponseError("arXiv returned malformed XML.") from error
    works: list[ArxivWork] = []
    for entry in root.findall(f"{ATOM}entry"):
        identifier = _text(entry, f"{ATOM}id")
        title = _clean(_text(entry, f"{ATOM}title"))
        if not identifier or not title:
            continue
        try:
            canonical, version = canonical_arxiv_id(identifier)
        except ValueError:
            continue
        authors = [{"name": _clean(_text(author, f"{ATOM}name"))} for author in entry.findall(f"{ATOM}author") if _clean(_text(author, f"{ATOM}name"))]
        categories = [category.attrib["term"] for category in entry.findall(f"{ATOM}category") if category.attrib.get("term")]
        pdf_url = next((link.attrib.get("href") for link in entry.findall(f"{ATOM}link") if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf"), None)
        works.append(ArxivWork(canonical, version, title, authors, _clean(_text(entry, f"{ATOM}summary")) or None, _valid_date(_text(entry, f"{ATOM}published")), _valid_date(_text(entry, f"{ATOM}updated")), categories, _normalise_doi(_text(entry, f"{ARXIV}doi")), identifier, pdf_url))
    return works


def _text(element: ET.Element, path: str) -> str:
    return element.findtext(path, default="")


def _clean(value: str) -> str:
    return " ".join(value.split())


def _valid_date(value: str) -> Optional[str]:
    value = value.strip()
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def _normalise_doi(value: str) -> Optional[str]:
    value = value.strip().casefold()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if value.startswith(prefix): value = value[len(prefix):]
    return value or None
