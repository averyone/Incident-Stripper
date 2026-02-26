#!/usr/bin/env python3
"""
Extract individual insider threat incidents from a PDF report into separate Markdown files.

Each incident follows this pattern:
  - TITLE LINE(S): A bold headline ending with a date like "- September 30, 2025"
  - BODY: One or more paragraphs describing the incident
  - TERMINATOR: The text "(Source)" marks the end of an incident

Usage:
    python extract_incidents.py <input.pdf> [output_directory]

If no output directory is specified, files are written to ./extracted_incidents/
"""

import bisect
import re
import os
import sys
import unicodedata
import pdfplumber


# ---------------------------------------------------------------------------
# 1.  TEXT EXTRACTION
# ---------------------------------------------------------------------------

def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract all text from a PDF, preserving page order."""
    full_text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"
    return full_text


# ---------------------------------------------------------------------------
# 2.  INCIDENT PARSING
# ---------------------------------------------------------------------------

# Full date suffix: handles hyphens, en-dashes, em-dashes; optional comma after month name
DATE_SUFFIX = re.compile(
    r"[-–—]\s+"
    r"((?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r",?\s+\d{1,2}[,.]?\s*\d{4})"
)

# Year-only date patterns: "- 2010", "– 2010", "— 2010" (end of line), or "(2005)"
DATE_SUFFIX_YEAR_ONLY = re.compile(
    r"[-–—]\s+((?:19|20)\d{2})\s*$"
    r"|"
    r"\(((?:19|20)\d{2})\)",
    re.MULTILINE,
)

# "(Source)" marker — allow spaces inside parens, case-insensitive
SOURCE_MARKER_RE = re.compile(r"\(\s*Source\s*\)", re.IGNORECASE)

# ALL-CAPS section header lines (e.g. "BANKING / FINANCIAL INSTITUTIONS")
# Includes Unicode curly apostrophe \u2019 and en/em-dashes for PDFs with smart quotes.
SECTION_HEADER_RE = re.compile(r"^([A-Z][A-Z0-9 /\(\)\-&.,'\u2019\u2013\u2014]+)$", re.MULTILINE)

# Keywords that real incident titles contain
TITLE_KEYWORDS = re.compile(
    r"(?:embezzl|fraud|stol|steal|sentenced|guilty|charged|theft|"
    r"kickback|scheme|conspir|misappropriat|indicted|arrested|"
    r"pleads?|convicted|stealing|pocketing|accused|admits?|"
    r"fired|spent|murder|robbery|negligent|awarded|"
    r"brib|extort|launder|wire\s+fraud|bank\s+fraud|mail\s+fraud|"
    r"defraud|misus|divert|corrup|hack|leak|espionage|sabotage|"
    r"threaten|harass|assault|manslaughter)",
    re.IGNORECASE,
)


def find_section_headers(text: str) -> list[tuple[int, str]]:
    """Return sorted list of (position, header_text) for ALL-CAPS section headers."""
    headers = []
    for m in SECTION_HEADER_RE.finditer(text):
        header = m.group(1).strip()
        if len(header) >= 5:
            headers.append((m.start(), header))
    return headers


def find_end_boundary(text: str) -> int:
    """
    Find the position where real incident content ends.
    Everything after this point (e-magazine promos, source-listings) is excluded.
    These markers appear immediately after the last real (Source) in every test file.
    """
    end_boundary = len(text)
    for marker in [
        "WORKPLACE VIOLENCE (WPV) INSIDER THREAT INCIDENTS E-MAGAZINE",
        "SOURCES FOR INSIDER THREAT INCIDENT POSTINGS",
    ]:
        pos = text.find(marker)
        if pos != -1:
            end_boundary = min(end_boundary, pos)
    return end_boundary


def clean_title(title: str) -> str:
    """Remove page numbers, section headers, and other artifacts from titles."""
    # Remove leading page numbers like "5\n", "12\n"
    title = re.sub(r"^\d{1,3}\s*\n", "", title)
    # Remove trailing page numbers
    title = re.sub(r"\n\d{1,3}\s*$", "", title)
    # Remove standalone page numbers mid-title from page breaks
    title = re.sub(r"\n\d{1,3}\n", "\n", title)
    # Strip leading ALL-CAPS section header lines that bled in (newline-separated)
    _hdr_line = re.compile(r"^[A-Z][A-Z0-9 /\(\)\-&.,'\u2019\u2013\u2014]{4,}$")
    lines = title.splitlines()
    while lines and _hdr_line.match(lines[0].strip()):
        lines.pop(0)
    title = "\n".join(lines)
    # Collapse internal newlines into spaces (titles can wrap)
    title = re.sub(r"\s*\n\s*", " ", title)
    # Strip inline leading ALL-CAPS section header prefix (same-line bleed-in)
    title = re.sub(
        r"^[A-Z][A-Z0-9 /\(\)\-&.,'\u2019\u2013\u2014]{4,}\s+(?=[A-Z])", "", title
    )
    # Collapse multiple spaces
    title = re.sub(r" {2,}", " ", title)
    return title.strip()


def find_incidents(text: str) -> list[dict]:
    """
    Parse the extracted text and return a list of incidents.

    Each incident dict has:
        title       – cleaned incident headline
        body        – the descriptive paragraphs (between title and Source)
        date        – date string from the title
        category    – section category (e.g. "BANKING / FINANCIAL INSTITUTIONS")
        full_text   – raw text of the entire incident block
    """

    # -- Preprocessing: find end-of-incidents boundary -----------------------
    end_boundary = find_end_boundary(text)

    # -- Step A: Find all (Source) positions (within incident section only) --
    raw_source_list = []
    for m in SOURCE_MARKER_RE.finditer(text, 0, end_boundary):
        raw_source_list.append([m.start(), m.end()])

    # Deduplicate consecutive Source markers within ~20 chars of each other
    source_positions = []
    for sp in raw_source_list:
        if source_positions and sp[0] - source_positions[-1][1] < 20:
            source_positions[-1][1] = sp[1]  # extend end of last entry
        else:
            source_positions.append(sp)

    source_starts = [sp[0] for sp in source_positions]

    # -- Step B: Build a title-location finder using the source list ---------
    def build_title_location(m_start: int, m_end: int, date_str: str) -> dict:
        """Walk backward from a date match to determine where the title starts."""
        search_from = m_start

        # Rightmost Source marker before this date match (binary search)
        idx = bisect.bisect_left(source_starts, search_from) - 1
        if idx >= 0:
            prev_source, prev_source_end = source_positions[idx]
        else:
            prev_source, prev_source_end = -1, -1

        prev_blank = text.rfind("\n\n", 0, search_from)

        if max(prev_source, prev_blank) == -1:
            title_start = 0
        elif prev_source > prev_blank:
            title_start = prev_source_end
        else:
            title_start = prev_blank + 2

        # Skip leading whitespace
        while title_start < search_from and text[title_start] in (" ", "\n", "\t", "\r"):
            title_start += 1

        # Forward-scan: if there are intermediate blank lines or ALL-CAPS section
        # headers between title_start and the date match, advance past the last one.
        # This handles empty sections ("No Incidents To Report") that precede the
        # real incident title in the same document span.
        last_sep = title_start
        scan_pos = title_start
        while True:
            bp = text.find("\n\n", scan_pos, search_from)
            if bp == -1:
                break
            last_sep = bp + 2
            scan_pos = bp + 1
        for m2 in SECTION_HEADER_RE.finditer(text, title_start, search_from):
            line_end = text.find("\n", m2.end())
            candidate = (line_end + 1) if (0 <= line_end < search_from) else m2.end()
            if candidate > last_sep:
                last_sep = candidate
        if last_sep > title_start:
            title_start = last_sep
            while title_start < search_from and text[title_start] in (" ", "\n", "\t", "\r"):
                title_start += 1

        return {
            "title_start": title_start,
            "title_end": m_end,
            "raw_title": text[title_start:m_end],
            "date": date_str,
        }

    # -- Step 1: Find all title-date positions --------------------------------

    title_locations = []
    full_date_spans = []  # (match_start, match_end) for each full-date match

    # 1a: Full month-name dates
    for m in DATE_SUFFIX.finditer(text, 0, end_boundary):
        date_str = " ".join(m.group(1).split())  # normalize internal whitespace/newlines
        loc = build_title_location(m.start(), m.end(), date_str)
        title_locations.append(loc)
        full_date_spans.append((m.start(), m.end()))

    # 1b: Year-only dates — skip if the match overlaps a full-date span
    for m in DATE_SUFFIX_YEAR_ONLY.finditer(text, 0, end_boundary):
        if any(fs <= m.start() <= fe for fs, fe in full_date_spans):
            continue
        date_str = (m.group(1) or m.group(2)).strip()
        loc = build_title_location(m.start(), m.end(), date_str)
        # Pre-filter: only keep if the inferred title contains crime keywords
        if not TITLE_KEYWORDS.search(loc["raw_title"]):
            continue
        title_locations.append(loc)

    # Sort by position in document
    title_locations.sort(key=lambda x: x["title_start"])

    # -- Step 2: Build section header list for category tracking -------------
    section_headers = find_section_headers(text)

    # -- Step 3: Pair each title with its next (Source) ----------------------
    incidents = []
    used_sources = set()

    for tl in title_locations:
        # Find the first unused (Source) that comes after this title
        matched = None
        for sp in source_positions:
            sp_start = sp[0]
            sp_end = sp[1]
            if sp_start > tl["title_end"] and sp_start not in used_sources:
                matched = (sp_start, sp_end)
                used_sources.add(sp_start)
                break

        if matched is None:
            continue

        # Most recent section header before this title's start position
        category = ""
        for hdr_pos, hdr_text in section_headers:
            if hdr_pos < tl["title_start"]:
                category = hdr_text
            else:
                break

        cleaned_title = clean_title(tl["raw_title"])
        body = text[tl["title_end"]:matched[0]].strip()
        full = text[tl["title_start"]:matched[1]].strip()

        incidents.append({
            "title": cleaned_title,
            "body": body,
            "date": tl["date"],
            "category": category,
            "full_text": full,
        })

    # -- Step 4: Filter out non-incident matches -----------------------------
    filtered = []
    for inc in incidents:
        title = inc["title"]
        # Skip titles that are too long (likely captured document preamble)
        if len(title) > 300:
            continue
        # Skip titles without crime/legal keywords
        if not TITLE_KEYWORDS.search(title):
            continue
        filtered.append(inc)

    return filtered


# ---------------------------------------------------------------------------
# 3.  MARKDOWN OUTPUT
# ---------------------------------------------------------------------------

def sanitize_filename(name: str, max_len: int = 80) -> str:
    """Turn an incident title into a safe, readable filename slug."""
    name = unicodedata.normalize("NFKD", name)
    name = re.sub(r'[\\/*?:"<>|$\'\n\r,&]+', " ", name)
    name = re.sub(r"\s+", "_", name).strip("_")
    if len(name) > max_len:
        name = name[:max_len].rsplit("_", 1)[0]
    return name


def write_incident_markdown(incident: dict, output_dir: str, index: int) -> str:
    """Write one incident as a Markdown file. Returns the filepath."""
    title = incident["title"]
    body = incident["body"]
    date = incident["date"]
    category = incident.get("category", "")

    slug = sanitize_filename(title)
    filename = f"{index:03d}_{slug}.md"
    filepath = os.path.join(output_dir, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        f.write(f"**Date:** {date}\n\n")
        if category:
            f.write(f"**Category:** {category}\n\n")
        f.write("---\n\n")

        # Normalize body into clean paragraphs
        paragraphs = re.split(r"\n{2,}", body)
        for para in paragraphs:
            cleaned = " ".join(para.split())
            if cleaned:
                f.write(f"{cleaned}\n\n")

    return filepath


def write_index(incidents: list[dict], output_dir: str) -> str:
    """Write a combined index Markdown file."""
    index_path = os.path.join(output_dir, "000_INDEX.md")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write("# Insider Threat Incidents – Extracted Index\n\n")
        f.write(f"**Total incidents extracted:** {len(incidents)}\n\n")
        f.write("| # | Date | Category | Title |\n")
        f.write("|---|------|----------|-------|\n")
        for i, inc in enumerate(incidents, start=1):
            slug = sanitize_filename(inc["title"])
            fname = f"{i:03d}_{slug}.md"
            short_title = inc["title"][:120]
            cat = inc.get("category", "")
            f.write(f"| {i} | {inc['date']} | {cat} | [{short_title}]({fname}) |\n")
    return index_path


# ---------------------------------------------------------------------------
# 4.  MAIN
# ---------------------------------------------------------------------------

def main():
    # Default to the uploaded file if no argument given
    if len(sys.argv) < 2:
        pdf_path = "/mnt/user-data/uploads/insider-threat-incidents-report-on-employee-personal-enrichment_11-4-25_CLEANED.pdf"
    else:
        pdf_path = sys.argv[1]

    output_dir = sys.argv[2] if len(sys.argv) >= 3 else "./extracted_incidents"
    os.makedirs(output_dir, exist_ok=True)

    print(f"Reading PDF: {pdf_path}")
    text = extract_text_from_pdf(pdf_path)
    print(f"Extracted {len(text):,} characters of text.\n")

    incidents = find_incidents(text)
    print(f"Found {len(incidents)} incidents.\n")

    if not incidents:
        print("No incidents found. Check that the PDF contains the expected format.")
        return

    for i, inc in enumerate(incidents, start=1):
        short = inc["title"][:95]
        print(f"  [{i:3d}] {short}")

    index_path = write_index(incidents, output_dir)
    for i, inc in enumerate(incidents, start=1):
        write_incident_markdown(inc, output_dir, i)

    print(f"\n{'='*70}")
    print(f"Done! {len(incidents)} incidents extracted.")
    print(f"  Output directory : {output_dir}/")
    print(f"  Index file       : {index_path}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
