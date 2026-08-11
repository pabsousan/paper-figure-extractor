#!/usr/bin/env python3
"""Extract publication-quality figures and tables from scholarly PDFs.

The normal, no-argument mode is interactive. Automation-friendly arguments are
also available; run ``python extract_figures.py --help`` for details.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover - friendly startup error
    print(
        "PyMuPDF is required. Install it with:\n"
        "  python -m pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(2)


CAPTION_RE = re.compile(
    r"^\s*(?P<prefix>fig(?:ure)?|table)\s*\.?\s*"
    r"(?P<number>(?:s\s*)?\d+[a-z]?(?:\s*[-\u2013\u2014]\s*[a-z0-9]+)?)"
    r"(?=\s|[.:;)\]-]|$)",
    re.IGNORECASE,
)
YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
PANEL_RE = re.compile(r"^\s*[\(\[]?([A-Z])[\)\].:]?\s*$")
PANEL_REQUEST_RE = re.compile(
    r"^\s*fig(?:ure)?\s*\.?\s*((?:s\s*)?\d+)\s*([a-z])\s*$", re.IGNORECASE
)
SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class FoundItem:
    order: int
    page_index: int
    label: str
    match_key: str
    caption: str
    caption_rect: fitz.Rect
    panels: tuple[str, ...] = ()


@dataclass(frozen=True)
class Selection:
    item: FoundItem
    panel: str | None = None


@dataclass(frozen=True)
class PaperIdentity:
    name: str
    source: str
    doi: str | None = None


@dataclass(frozen=True)
class RegionQuality:
    kind: str  # "vector", "mixed", or "raster"
    description: str

    @property
    def is_true_vector(self) -> bool:
        return self.kind == "vector"


@dataclass(frozen=True)
class ExportJob:
    page_index: int
    item: FoundItem
    crop: fitz.Rect
    quality: RegionQuality
    formats: tuple[str, ...]
    note: str = ""


def clean_text(value: str) -> str:
    return " ".join(value.replace("\u00a0", " ").split())


def safe_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = SAFE_RE.sub("_", clean_text(value)).strip("._-")
    return value or "paper"


def item_key(value: str) -> str:
    value = value.lower().replace("figure", "fig")
    return re.sub(r"[^a-z0-9]", "", value)


def normalized_label(prefix: str, number: str) -> str:
    number = re.sub(r"\s+", "", number).replace("\u2013", "-").replace("\u2014", "-")
    if prefix.lower().startswith("table"):
        return f"Table {number.upper()}"
    return f"Fig.{number.upper()}"


def caption_panels(caption: str) -> tuple[str, ...]:
    """Return the contiguous A, B, C... panel sequence listed in a caption."""
    found = {match.upper() for match in re.findall(r"\(\s*([A-L])\s*\)", caption)}
    panels: list[str] = []
    for code in range(ord("A"), ord("L") + 1):
        letter = chr(code)
        if letter not in found:
            break
        panels.append(letter)
    return tuple(panels)


def infer_name_from_filename(pdf: Path) -> str:
    stem = safe_name(pdf.stem)
    years = list(YEAR_RE.finditer(stem))
    if not years:
        return stem
    year = years[-1]
    before = stem[: year.start()].rstrip("._- ")
    author_parts = [p for p in re.split(r"[_ .-]+", before) if p]
    author = author_parts[-1] if author_parts else "Paper"
    return safe_name(f"{author}_{year.group(1)}")


def find_doi(doc: fitz.Document) -> str | None:
    metadata_text = " ".join(str(value or "") for value in doc.metadata.values())
    page_text = " ".join(doc[index].get_text("text") for index in range(min(3, len(doc))))
    for match in DOI_RE.finditer(metadata_text + " " + page_text):
        doi = match.group(0).rstrip(".,;:)]}")
        # Reference lists sometimes appear unexpectedly early. Prefer the first
        # DOI because article PDFs normally print their own DOI near the title.
        return doi
    return None


def crossref_identity(doi: str, timeout: float = 5.0) -> PaperIdentity | None:
    encoded = urllib.parse.quote(doi, safe="")
    request = urllib.request.Request(
        f"https://api.crossref.org/works/{encoded}",
        headers={"User-Agent": "PaperFigureExtractor/2.0 (lab utility; Crossref metadata lookup)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            message = json.load(response).get("message", {})
    except (OSError, ValueError, urllib.error.URLError):
        return None
    authors = message.get("author") or []
    family = authors[0].get("family") if authors else None
    year = None
    for field in ("published-print", "published-online", "issued", "created"):
        parts = (message.get(field) or {}).get("date-parts") or []
        if parts and parts[0]:
            year = parts[0][0]
            break
    if family and year:
        return PaperIdentity(safe_name(f"{family}_{year}"), "Crossref", doi)
    return None


def first_author_family(author_text: str) -> str | None:
    text = clean_text(author_text)
    if not text:
        return None
    first = re.split(r"\s*(?:;|\band\b|\|)\s*", text, maxsplit=1, flags=re.IGNORECASE)[0]
    first = re.sub(r"\bet\s+al\.?$", "", first, flags=re.IGNORECASE).strip(" ,")
    if not first:
        return None
    if "," in first:
        before_comma, after_comma = (part.strip() for part in first.split(",", 1))
        # "Smith, John" is common in metadata; "John Smith, Jane Doe" is
        # common in visible bylines. Distinguish them conservatively.
        before_words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ'’-]+", before_comma)
        after_words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ'’-]+", after_comma)
        if len(before_words) == 1 or (len(after_words) == 1 and len(before_words) <= 2):
            return safe_name(before_words[0]) if before_words else None
        return safe_name(before_words[-1]) if before_words else None
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ'’-]+", first)
    return safe_name(words[-1]) if words else None


def metadata_year(doc: fitz.Document, pdf: Path) -> str | None:
    for field in (doc.metadata.get("creationDate", ""), doc.metadata.get("modDate", ""), pdf.stem):
        match = YEAR_RE.search(field or "")
        if match:
            return match.group(1)
    first_page_top = doc[0].get_text("text", clip=fitz.Rect(0, 0, doc[0].rect.width, doc[0].rect.height * 0.4))
    match = YEAR_RE.search(first_page_top)
    return match.group(1) if match else None


def first_page_byline_author(doc: fitz.Document) -> str | None:
    page = doc[0]
    data = page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)
    lines: list[tuple[float, float, str]] = []
    largest_size = 0.0
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = clean_text("".join(span.get("text", "") for span in spans))
            size = max((float(span.get("size", 0)) for span in spans), default=0.0)
            largest_size = max(largest_size, size)
            if text:
                lines.append((float(line["bbox"][1]), size, text))
    for y, size, text in lines:
        if y < page.rect.height * 0.04 or y > page.rect.height * 0.38:
            continue
        if largest_size and size >= largest_size * 0.92:
            continue  # likely the title
        lowered = text.lower()
        if any(word in lowered for word in ("abstract", "doi:", "http", "journal", "university")):
            continue
        author_signal = (
            "," in text
            or ";" in text
            or bool(re.search(r"\bet\s+al\.?", text, re.IGNORECASE))
            or len(re.findall(r"\b[A-Z]\.", text)) >= 2
        )
        if author_signal:
            author = first_author_family(text)
            if author:
                return author
    return None


def infer_paper_identity(doc: fitz.Document, pdf: Path, online: bool = True) -> PaperIdentity:
    doi = find_doi(doc)
    if doi and online:
        identity = crossref_identity(doi)
        if identity:
            return identity
    author = first_author_family(doc.metadata.get("author", ""))
    year = metadata_year(doc, pdf)
    if author and year:
        return PaperIdentity(safe_name(f"{author}_{year}"), "PDF metadata", doi)
    author = first_page_byline_author(doc)
    if author and year:
        return PaperIdentity(safe_name(f"{author}_{year}"), "first-page byline", doi)
    return PaperIdentity(infer_name_from_filename(pdf), "PDF filename", doi)


def iter_text_lines(page: fitz.Page) -> Iterable[tuple[fitz.Rect, str]]:
    data = page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = clean_text("".join(span.get("text", "") for span in spans))
            if text:
                yield fitz.Rect(line["bbox"]), text


def iter_text_spans(page: fitz.Page) -> Iterable[tuple[fitz.Rect, str, float]]:
    data = page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = clean_text(span.get("text", ""))
                if text:
                    yield fitz.Rect(span["bbox"]), text, float(span.get("size", 0))


def discover_items(doc: fitz.Document) -> list[FoundItem]:
    found: list[FoundItem] = []
    for page_index, page in enumerate(doc):
        lines = list(iter_text_lines(page))
        for i, (rect, text) in enumerate(lines):
            match = CAPTION_RE.match(text)
            if not match:
                continue
            # Read the complete caption, which is also the most reliable source
            # of panel count when labels are baked into a raster figure.
            caption_parts = [text]
            caption_rect = fitz.Rect(rect)
            for next_rect, next_text in lines[i + 1 : i + 40]:
                if CAPTION_RE.match(next_text):
                    break
                if DOI_RE.search(next_text) or next_text.lower().startswith(("http://", "https://")):
                    break
                caption_parts.append(next_text)
                caption_rect |= next_rect
            label = normalized_label(match.group("prefix"), match.group("number"))
            full_caption = clean_text(" ".join(caption_parts))
            found.append(
                FoundItem(
                    order=len(found) + 1,
                    page_index=page_index,
                    label=label,
                    match_key=item_key(label),
                    caption=full_caption,
                    caption_rect=caption_rect,
                    panels=caption_panels(full_caption),
                )
            )
    return found


def horizontal_zone(item: FoundItem, page_rect: fitz.Rect) -> fitz.Rect:
    """Return the usable page width; graphical bounds will tighten the crop."""
    margin = page_rect.width * 0.035
    return fitz.Rect(page_rect.x0 + margin, page_rect.y0, page_rect.x1 - margin, page_rect.y1)


def _graphical_rects(page: fitz.Page) -> list[fitz.Rect]:
    rects: list[fitz.Rect] = []
    for info in page.get_image_info():
        rect = fitz.Rect(info["bbox"])
        if rect.width > 3 and rect.height > 3:
            rects.append(rect)
    try:
        for drawing in page.get_drawings():
            rect = fitz.Rect(drawing["rect"])
            if rect.width > 1 or rect.height > 1:
                rects.append(rect)
    except Exception:
        pass
    return rects


def _nearest_graphics_group(
    rects: Sequence[fitz.Rect],
    zone: fitz.Rect,
    bottom: float,
    page_height: float,
    anchor: fitz.Rect,
) -> fitz.Rect | None:
    minimum_y = max(zone.y0, bottom - page_height * 0.72)
    candidates = [
        fitz.Rect(r)
        for r in rects
        if r.y0 >= minimum_y
        and r.y1 <= bottom + 4
        and r.x1 > zone.x0
        and r.x0 < zone.x1
    ]
    if not candidates:
        return None
    def seed_rank(rect: fitz.Rect) -> tuple[float, float, float]:
        overlap = min(rect.x1, anchor.x1) - max(rect.x0, anchor.x0)
        return (
            0.0 if overlap > 0 else 1.0,
            max(0.0, bottom - rect.y1),
            abs((rect.x0 + rect.x1) / 2 - (anchor.x0 + anchor.x1) / 2),
        )

    candidates.sort(key=seed_rank)
    group = fitz.Rect(candidates[0])
    # Grow upward through nearby/overlapping elements; this joins panels and axes
    # but usually stops before the preceding paragraph or figure.
    changed = True
    while changed:
        changed = False
        for rect in candidates:
            horizontal_overlap = min(group.x1, rect.x1) - max(group.x0, rect.x0)
            vertical_gap = group.y0 - rect.y1
            # A scientific multi-panel figure commonly has a 15-35 point gutter
            # between panels. Join across that gutter while still avoiding a
            # typical two-column article gutter.
            if horizontal_overlap > -36 and -group.height <= vertical_gap <= 45:
                old = tuple(group)
                group |= rect
                changed = changed or tuple(group) != old
    return group


def _whitespace_fallback(page: fitz.Page, zone: fitz.Rect, bottom: float) -> fitz.Rect:
    """Find the nearest ink block above a caption using a low-resolution render."""
    top_limit = max(page.rect.y0, bottom - page.rect.height * 0.58)
    search = fitz.Rect(zone.x0, top_limit, zone.x1, bottom)
    scale = 1.5
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=search, colorspace=fitz.csGRAY)
    samples = memoryview(pix.samples)
    width, height, stride = pix.width, pix.height, pix.stride
    active: list[bool] = []
    min_ink = max(2, int(width * 0.003))
    for y in range(height):
        row = samples[y * stride : y * stride + width]
        active.append(sum(value < 242 for value in row) >= min_ink)
    y = height - 1
    while y >= 0 and not active[y]:
        y -= 1
    content_bottom = y
    if content_bottom < 0:
        return fitz.Rect(zone.x0, max(top_limit, bottom - 180), zone.x1, bottom)
    blank_run = 0
    stop = 0
    for y in range(content_bottom, -1, -1):
        if active[y]:
            blank_run = 0
        else:
            blank_run += 1
            if blank_run >= int(10 * scale):
                stop = y + blank_run
                break
    top = search.y0 + stop / scale
    return fitz.Rect(zone.x0, top, zone.x1, bottom)


def estimate_crop(page: fitz.Page, item: FoundItem, include_caption: bool, margin: float) -> fitz.Rect:
    zone = horizontal_zone(item, page.rect)
    bottom = item.caption_rect.y1 if include_caption else item.caption_rect.y0 - 2
    group = _nearest_graphics_group(
        _graphical_rects(page), zone, bottom, page.rect.height, item.caption_rect
    )
    if group is None:
        crop = _whitespace_fallback(page, zone, bottom)
    else:
        # Include nearby labels, tick text, and panel letters around graphical content.
        crop = fitz.Rect(group)
        for rect, _ in iter_text_lines(page):
            near_x = rect.x1 >= crop.x0 - 24 and rect.x0 <= crop.x1 + 24
            near_y = rect.y1 >= crop.y0 - 24 and rect.y0 <= crop.y1 + 24
            if near_x and near_y and rect.y1 <= bottom:
                crop |= rect
        crop.x0 = max(zone.x0, crop.x0 - margin)
        crop.x1 = min(zone.x1, crop.x1 + margin)
        crop.y0 -= margin
        crop.y1 = bottom if include_caption else min(bottom, crop.y1 + margin)
    if include_caption:
        crop |= item.caption_rect
    crop &= page.rect
    if crop.is_empty or crop.width < 10 or crop.height < 10:
        raise ValueError(f"Could not determine a useful crop for {item.label}")
    return crop


def _clusters(values: Sequence[float], tolerance: float) -> list[float]:
    groups: list[list[float]] = []
    for value in sorted(values):
        if not groups or value - sum(groups[-1]) / len(groups[-1]) > tolerance:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [sum(group) / len(group) for group in groups]


def detect_panel_markers(page: fitz.Page, figure_crop: fitz.Rect) -> dict[str, fitz.Rect]:
    candidates: dict[str, list[tuple[fitz.Rect, float]]] = {}
    for rect, text, size in iter_text_spans(page):
        match = PANEL_RE.match(text)
        center = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
        if not match or not figure_crop.contains(center):
            continue
        letter = match.group(1)
        if letter > "L":  # Avoid most single-letter axis/unit labels.
            continue
        candidates.setdefault(letter, []).append((rect, size))

    markers: dict[str, fitz.Rect] = {}
    for letter, choices in candidates.items():
        # Panel markers are normally relatively large and near the top-left of
        # their panel. Prefer larger text, then the upper/left occurrence.
        choices.sort(key=lambda value: (-value[1], value[0].y0, value[0].x0))
        markers[letter] = fitz.Rect(choices[0][0])
    if "A" not in markers:
        return {}
    # Retain the contiguous A, B, C... run; unrelated single letters are noise.
    contiguous: dict[str, fitz.Rect] = {}
    for code in range(ord("A"), ord("L") + 1):
        letter = chr(code)
        if letter not in markers:
            break
        contiguous[letter] = markers[letter]
    return contiguous


def estimate_panel_crop(
    page: fitz.Page,
    figure_crop: fitz.Rect,
    panel: str,
    margin: float,
    expected_panels: Sequence[str] = (),
) -> tuple[fitz.Rect, list[str], str]:
    markers = detect_panel_markers(page, figure_crop)
    panel = panel.upper()
    if panel not in markers:
        if panel in expected_panels:
            print(
                f"Panel {panel} is listed in the caption but rasterized in the figure. "
                "A crop window will open."
            )
            chosen = choose_panel_crop(page, figure_crop, panel)
            if chosen is not None:
                return chosen, list(expected_panels), "interactive raster crop"
        detected = ", ".join(markers) if markers else "none"
        raise ValueError(
            f"panel {panel} was not found as PDF text (detected panels: {detected}). "
            "The interactive crop was cancelled or unavailable; use --crop for this paper."
        )
    if len(markers) < 2:
        raise ValueError(
            f"only panel marker {panel} was detected, so automatic panel boundaries are ambiguous; "
            "use --crop for this paper"
        )

    x_values = [rect.x0 for rect in markers.values()]
    y_values = [rect.y0 for rect in markers.values()]
    xs = _clusters(x_values, max(10.0, figure_crop.width * 0.10))
    ys = _clusters(y_values, max(10.0, figure_crop.height * 0.10))
    marker = markers[panel]
    marker_x = min(xs, key=lambda value: abs(value - marker.x0))
    marker_y = min(ys, key=lambda value: abs(value - marker.y0))
    x_index, y_index = xs.index(marker_x), ys.index(marker_y)

    # Panel letters mark the starts of cells, not their centers. Dividing the
    # complete figure into the detected row/column count avoids cutting a panel
    # in half (which midpoint-between-label algorithms do).
    x0 = figure_crop.x0 + figure_crop.width * x_index / len(xs)
    x1 = figure_crop.x0 + figure_crop.width * (x_index + 1) / len(xs)
    y0 = figure_crop.y0 + figure_crop.height * y_index / len(ys)
    y1 = figure_crop.y0 + figure_crop.height * (y_index + 1) / len(ys)
    crop = fitz.Rect(x0 - margin, y0 - margin, x1 + margin, y1 + margin) & figure_crop
    return crop, list(markers), "automatic PDF-text grid"


def crop_svg(page: fitz.Page, crop: fitz.Rect) -> str:
    """Create a clean cropped page, then convert its PDF instructions to SVG."""
    cropped_doc = fitz.open()
    target = cropped_doc.new_page(width=crop.width, height=crop.height)
    target.show_pdf_page(target.rect, page.parent, page.number, clip=crop, keep_proportion=False)
    svg = target.get_svg_image(text_as_path=True)
    cropped_doc.close()
    return svg


def region_quality(page: fitz.Page, crop: fitz.Rect) -> RegionQuality:
    raster_area = 0.0
    raster_count = 0
    for info in page.get_image_info():
        overlap = fitz.Rect(info["bbox"]) & crop
        if not overlap.is_empty:
            raster_count += 1
            raster_area += overlap.get_area()
    vector_count = 0
    try:
        for drawing in page.get_drawings():
            if not (fitz.Rect(drawing["rect"]) & crop).is_empty:
                vector_count += 1
    except Exception:
        pass
    raster_coverage = min(1.0, raster_area / max(crop.get_area(), 1.0))
    if raster_count and vector_count:
        return RegionQuality(
            "mixed", f"mixed vector/raster (raster covers about {raster_coverage:.0%})"
        )
    if raster_count:
        return RegionQuality(
            "raster",
            f"raster source (about {raster_coverage:.0%} of crop; SVG cannot add resolution)",
        )
    if vector_count:
        return RegionQuality("vector", "true vector source")
    return RegionQuality("vector", "text/vector source")


def formats_for_quality(requested: str, quality: RegionQuality) -> tuple[str, ...]:
    """Never create an SVG unless the selected PDF region is genuinely vector."""
    if requested == "png":
        return ("png",)
    if quality.is_true_vector:
        return ("svg", "png") if requested == "both" else ("svg",)
    # A raster wrapped in SVG is misleading. Fall back to the useful format.
    return ("png",)


def unique_base(output_dir: Path, paper_name: str, label: str) -> Path:
    base = output_dir / f"{safe_name(paper_name)}_{safe_name(label)}"
    candidate = base
    suffix = 2
    while any((candidate.parent / f"{candidate.name}{ext}").exists() for ext in (".png", ".svg")):
        candidate = output_dir / f"{base.name}_{suffix}"
        suffix += 1
    return candidate


def output_path(base: Path, extension: str) -> Path:
    """Append an extension without treating the dot in ``Fig.1`` as a suffix."""
    return base.parent / f"{base.name}{extension}"


def export_item(
    page: fitz.Page,
    item: FoundItem,
    crop: fitz.Rect,
    output_dir: Path,
    paper_name: str,
    output_formats: Sequence[str],
    dpi: int,
) -> list[Path]:
    base = unique_base(output_dir, paper_name, item.label)
    written: list[Path] = []
    if "svg" in output_formats:
        path = output_path(base, ".svg")
        path.write_text(crop_svg(page, crop), encoding="utf-8")
        written.append(path)
    if "png" in output_formats:
        path = output_path(base, ".png")
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        page.get_pixmap(matrix=matrix, clip=crop, alpha=False).save(path)
        written.append(path)
    return written


def print_inventory(items: Sequence[FoundItem]) -> None:
    if not items:
        print("No Figure/Fig./Table captions were detected.")
        return
    print("\nDetected items:")
    for item in items:
        preview = item.caption if len(item.caption) <= 100 else item.caption[:97] + "..."
        if item.panels:
            panel_summary = f"panels {item.panels[0]}-{item.panels[-1]} ({len(item.panels)})"
        elif item.label.startswith("Fig"):
            panel_summary = "panels not listed"
        else:
            panel_summary = "no panels"
        print(
            f"  {item.order:>3}. {item.label:<10} page {item.page_index + 1}, "
            f"{panel_summary}: {preview}"
        )


def parse_numbers(values: Sequence[str]) -> list[int]:
    result: list[int] = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part:
                result.append(int(part))
    return result


def choose_items(args: argparse.Namespace, items: Sequence[FoundItem]) -> list[Selection]:
    if args.all:
        return [Selection(item) for item in items]
    if args.order:
        wanted = set(parse_numbers(args.order))
        missing = wanted - {item.order for item in items}
        if missing:
            raise ValueError(f"Appearance order not found: {sorted(missing)}")
        return [Selection(item) for item in items if item.order in wanted]
    if args.select:
        requested = [part.strip() for value in args.select for part in value.split(",") if part.strip()]
        selected: list[Selection] = []
        missing: list[str] = []
        for label in requested:
            key = item_key(label)
            matches = [item for item in items if item.match_key == key]
            if matches:
                selected.extend(Selection(item) for item in matches)
                continue
            panel_match = PANEL_REQUEST_RE.match(label)
            if panel_match:
                number = re.sub(r"\s+", "", panel_match.group(1))
                base_key = item_key(f"Fig.{number}")
                base_matches = [item for item in items if item.match_key == base_key]
                if base_matches:
                    selected.extend(Selection(item, panel_match.group(2).upper()) for item in base_matches)
                    continue
            missing.append(label)
        if missing:
            raise ValueError(f"Caption label(s) not found: {', '.join(missing)}")
        return list(dict.fromkeys(selected))
    return []


def parse_manual_crop(value: str) -> tuple[int, fitz.Rect, str]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 6:
        raise argparse.ArgumentTypeError("crop must be PAGE,X0,Y0,X1,Y1,LABEL")
    try:
        page = int(parts[0])
        rect = fitz.Rect(*(float(n) for n in parts[1:5]))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("crop page/coordinates must be numeric") from exc
    if page < 1 or rect.is_empty:
        raise argparse.ArgumentTypeError("crop page must be >= 1 and rectangle must be non-empty")
    return page - 1, rect, parts[5]


def prompt_nonempty(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{prompt}{suffix}: ").strip()
        if value:
            return value
        if default:
            return default


def choose_output_folder(initial_dir: Path) -> Path | None:
    """Open the operating system's folder chooser; return None if unavailable/cancelled."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass
        root.update_idletasks()
        selected = filedialog.askdirectory(
            parent=root,
            title="Choose where extracted figures will be saved",
            initialdir=str(initial_dir),
            mustexist=True,
        )
        root.destroy()
        return Path(selected).expanduser().resolve() if selected else None
    except Exception:
        return None


def choose_panel_crop(
    page: fitz.Page, figure_crop: fitz.Rect, panel: str
) -> fitz.Rect | None:
    """Let the user drag a crop around a rasterized panel in a graphical window."""
    root = None
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk

        root = tk.Tk()
        root.title(f"Select {panel} - drag a rectangle around the complete panel")
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass
        root.update_idletasks()
        available_w = max(640, root.winfo_screenwidth() - 120)
        available_h = max(480, root.winfo_screenheight() - 210)
        scale = min(
            2.0,
            available_w / max(figure_crop.width, 1),
            available_h / max(figure_crop.height, 1),
        )
        pix = page.get_pixmap(
            matrix=fitz.Matrix(scale, scale),
            clip=figure_crop,
            alpha=False,
        )
        encoded = base64.b64encode(pix.tobytes("png")).decode("ascii")
        photo = tk.PhotoImage(data=encoded, format="png")

        instruction = ttk.Label(
            root,
            text=(
                f"Panel {panel} is part of a raster image. "
                "Drag from one corner to the opposite corner around the complete panel, "
                "then click Use selection."
            ),
            wraplength=min(pix.width, 950),
            justify="left",
            padding=(10, 8),
        )
        instruction.pack(fill="x")
        canvas = tk.Canvas(
            root,
            width=pix.width,
            height=pix.height,
            cursor="crosshair",
            highlightthickness=0,
        )
        canvas.pack()
        canvas.create_image(0, 0, image=photo, anchor="nw")

        state: dict[str, object] = {"start": None, "end": None, "rectangle": None}
        result: list[fitz.Rect] = []

        def clamp(x: float, y: float) -> tuple[float, float]:
            return max(0, min(pix.width, x)), max(0, min(pix.height, y))

        def on_press(event: object) -> None:
            x, y = clamp(float(event.x), float(event.y))  # type: ignore[attr-defined]
            state["start"] = (x, y)
            state["end"] = (x, y)
            if state["rectangle"] is not None:
                canvas.delete(state["rectangle"])
            state["rectangle"] = canvas.create_rectangle(
                x, y, x, y, outline="#e41a1c", width=3
            )

        def on_drag(event: object) -> None:
            if state["start"] is None:
                return
            x, y = clamp(float(event.x), float(event.y))  # type: ignore[attr-defined]
            state["end"] = (x, y)
            x0, y0 = state["start"]  # type: ignore[misc]
            canvas.coords(state["rectangle"], x0, y0, x, y)

        def reset() -> None:
            if state["rectangle"] is not None:
                canvas.delete(state["rectangle"])
            state.update({"start": None, "end": None, "rectangle": None})

        def accept() -> None:
            if state["start"] is None or state["end"] is None:
                messagebox.showinfo("Select a panel", "Drag a rectangle around the panel first.")
                return
            x0, y0 = state["start"]  # type: ignore[misc]
            x1, y1 = state["end"]  # type: ignore[misc]
            x0, x1 = sorted((x0, x1))
            y0, y1 = sorted((y0, y1))
            if x1 - x0 < 20 or y1 - y0 < 20:
                messagebox.showinfo("Selection too small", "Please select the complete panel.")
                return
            result.append(
                fitz.Rect(
                    figure_crop.x0 + x0 / scale,
                    figure_crop.y0 + y0 / scale,
                    figure_crop.x0 + x1 / scale,
                    figure_crop.y0 + y1 / scale,
                )
            )
            root.destroy()

        buttons = ttk.Frame(root, padding=(10, 8))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Use selection", command=accept).pack(side="right")
        ttk.Button(buttons, text="Reset", command=reset).pack(side="right", padx=(0, 8))
        ttk.Button(buttons, text="Cancel", command=root.destroy).pack(side="left")
        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_drag)
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        root.mainloop()
        return result[0] if result else None
    except Exception as exc:
        print(f"Could not open the panel crop window: {exc}", file=sys.stderr)
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass
        return None


def guided_setup(args: argparse.Namespace, items: Sequence[FoundItem], identity: PaperIdentity) -> None:
    print_inventory(items)
    print(f"\nPaper name detected automatically: {identity.name} ({identity.source})")
    if not items:
        print("Use --crop PAGE,X0,Y0,X1,Y1,LABEL for a manual extraction.")
        raise SystemExit(1)
    print("\nExtract: [A]ll, by [L]abel, or by appearance [O]rder?")
    mode = prompt_nonempty("Choice", "A").lower()
    if mode.startswith("a"):
        args.all = True
    elif mode.startswith("l"):
        args.select = [prompt_nonempty("Labels, comma-separated (e.g. Figure 1, Figure 1A, Table 2)")]
    elif mode.startswith("o"):
        args.order = [prompt_nonempty("Appearance numbers, comma-separated (e.g. 1,3,5)")]
    else:
        raise ValueError("Please choose A, L, or O")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract sharp figures/tables from papers as vector SVG and/or high-DPI PNG.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("pdf", nargs="?", type=Path, help="paper PDF (omit for guided mode)")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--all", action="store_true", help="extract all detected figures and tables")
    selection.add_argument(
        "--select",
        nargs="+",
        metavar="LABEL",
        help='select full items or panels, e.g. "Figure 1" "Figure 1A" "Table 2"',
    )
    selection.add_argument("--order", nargs="+", metavar="N", help="select by appearance order, e.g. 1 3 5")
    parser.add_argument("--list", action="store_true", help="list detected items without extracting")
    parser.add_argument("--paper-name", help="override automatically detected FirstAuthor_Year")
    parser.add_argument("--offline", action="store_true", help="skip DOI/Crossref metadata lookup")
    parser.add_argument("--output-dir", type=Path, help="destination directory")
    parser.add_argument(
        "--choose-output",
        action="store_true",
        help="open a folder-selection window for the destination",
    )
    parser.add_argument("--format", choices=("both", "svg", "png"), default="both")
    parser.add_argument("--dpi", type=int, default=600, help="PNG resolution (SVG is vector only if its PDF source is)")
    parser.add_argument("--include-caption", action="store_true", help="include caption text in each crop")
    parser.add_argument("--margin", type=float, default=6.0, help="crop padding in PDF points")
    parser.add_argument(
        "--crop",
        action="append",
        type=parse_manual_crop,
        metavar="PAGE,X0,Y0,X1,Y1,LABEL",
        help="manual PDF-point crop; may be repeated",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    interactive = args.pdf is None
    if interactive:
        args.pdf = Path(prompt_nonempty("Path to the paper PDF").strip('"'))
    pdf = args.pdf.expanduser().resolve()
    if not pdf.is_file():
        parser.error(f"PDF not found: {pdf}")
    if pdf.suffix.lower() != ".pdf":
        parser.error(f"Expected a .pdf file: {pdf}")
    if args.dpi < 72 or args.dpi > 2400:
        parser.error("--dpi must be between 72 and 2400")

    try:
        doc = fitz.open(pdf)
    except Exception as exc:
        parser.error(f"Could not open PDF: {exc}")
    if doc.needs_pass:
        parser.error("This PDF is password-protected")

    items = discover_items(doc)
    if args.paper_name:
        identity = PaperIdentity(safe_name(args.paper_name), "command-line override")
    else:
        identity = infer_paper_identity(doc, pdf, online=not args.offline)
    args.paper_name = identity.name
    if args.list:
        print_inventory(items)
        print(f"\nPaper name: {identity.name} ({identity.source})")
        if identity.doi:
            print(f"DOI: {identity.doi}")
        return 0

    if interactive and not args.crop:
        try:
            guided_setup(args, items, identity)
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return 130

    try:
        selected = choose_items(args, items)
    except ValueError as exc:
        parser.error(str(exc))
    if not selected and not args.crop:
        parser.error("choose --all, --select, --order, --list, or --crop")

    if args.output_dir is None and (interactive or args.choose_output):
        print("\nChoose the destination folder in the window that opens...")
        chosen_folder = choose_output_folder(pdf.parent)
        if chosen_folder:
            args.output_dir = chosen_folder
            print(f"Selected output folder: {chosen_folder}")
        elif args.choose_output and not interactive:
            parser.error("no output folder was selected")
        else:
            print("No folder was selected.")
            typed_folder = input(
                "Enter an output folder path, or press Enter to use the PDF folder: "
            ).strip().strip('"')
            if typed_folder:
                args.output_dir = Path(typed_folder).expanduser()

    destination_base = (args.output_dir or pdf.parent).resolve()
    jobs: list[ExportJob] = []
    failures: list[str] = []

    if not interactive:
        print(f"Paper name: {identity.name} ({identity.source})")

    # Resolve crops and quality first. This lets folder organization use the
    # number of files that will actually be written after SVG filtering.
    for selection in selected:
        item = selection.item
        page = doc[item.page_index]
        try:
            full_crop = estimate_crop(
                page, item, args.include_caption and selection.panel is None, args.margin
            )
            export_item_record = item
            crop = full_crop
            panel_note = ""
            if selection.panel:
                crop, detected_panels, panel_method = estimate_panel_crop(
                    page,
                    full_crop,
                    selection.panel,
                    args.margin,
                    item.panels,
                )
                panel_label = f"{item.label}{selection.panel}"
                export_item_record = FoundItem(
                    item.order,
                    item.page_index,
                    panel_label,
                    item_key(panel_label),
                    item.caption,
                    item.caption_rect,
                    item.panels,
                )
                panel_note = (
                    f"; panels: {', '.join(detected_panels)}; "
                    f"panel crop: {panel_method}"
                )
            quality = region_quality(page, crop)
            formats = formats_for_quality(args.format, quality)
            jobs.append(
                ExportJob(
                    item.page_index,
                    export_item_record,
                    crop,
                    quality,
                    formats,
                    panel_note,
                )
            )
        except Exception as exc:
            requested_label = f"{item.label}{selection.panel or ''}"
            failures.append(f"{requested_label} on page {item.page_index + 1}: {exc}")

    for page_index, rect, label in args.crop or []:
        if page_index >= len(doc):
            failures.append(f"{label}: page {page_index + 1} does not exist")
            continue
        try:
            rect &= doc[page_index].rect
            manual = FoundItem(0, page_index, label, item_key(label), label, rect)
            quality = region_quality(doc[page_index], rect)
            jobs.append(
                ExportJob(
                    page_index,
                    manual,
                    rect,
                    quality,
                    formats_for_quality(args.format, quality),
                    "; manual crop",
                )
            )
        except Exception as exc:
            failures.append(f"{label} on page {page_index + 1}: {exc}")

    actual_file_count = sum(len(job.formats) for job in jobs)
    paper_folder_name = f"{args.paper_name}_figures"
    if actual_file_count > 1 and destination_base.name.casefold() != paper_folder_name.casefold():
        output_dir = destination_base / paper_folder_name
    else:
        output_dir = destination_base
    if jobs:
        output_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for job in jobs:
        page = doc[job.page_index]
        try:
            paths = export_item(
                page,
                job.item,
                job.crop,
                output_dir,
                args.paper_name,
                job.formats,
                args.dpi,
            )
            written.extend(paths)
            svg_note = ""
            if args.format in {"both", "svg"} and not job.quality.is_true_vector:
                svg_note = "; SVG skipped - source is not truly vector; PNG saved instead"
            print(
                f"Extracted {job.item.label} (page {job.page_index + 1}) "
                f"- {job.quality.description}{job.note}{svg_note}"
            )
        except Exception as exc:
            failures.append(f"{job.item.label} on page {job.page_index + 1}: {exc}")

    print(f"\nCreated {len(written)} file(s) in: {output_dir}")
    if failures:
        print("\nCould not extract:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        print("Try --list, or use --crop for an unusual page layout.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
