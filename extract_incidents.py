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

# The date suffix pattern at the end of incident titles
DATE_SUFFIX = re.compile(
    r"-\s+"
    r"((?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2}[,.]?\s*\d{4})"
)

# The "(Source)" marker that ends every incident
SOURCE_MARKER = "(Source)"


def clean_title(title: str) -> str:
    """Remove page numbers, stray digits, and other artifacts from titles."""
    # Remove leading page numbers like "5\n", "12\n", "5 \n", etc.
    title = re.sub(r"^\d{1,3}\s*\n", "", title)
    # Remove trailing page numbers
    title = re.sub(r"\n\d{1,3}\s*$", "", title)
    # Remove standalone page numbers that appear mid-title from page breaks
    title = re.sub(r"\n\d{1,3}\n", "\n", title)
    # Collapse internal newlines into spaces (titles can wrap)
    title = re.sub(r"\s*\n\s*", " ", title)
    # Collapse multiple spaces
    title = re.sub(r" {2,}", " ", title)
    # Remove section header prefixes that might have bled in
    title = re.sub(r"^EMPLOYEE PERSONAL ENRICHMENT INCIDENTS\s*", "", title)
    return title.strip()


def find_incidents(text: str) -> list[dict]:
    """
    Parse the extracted text and return a list of incidents.

    Each incident dict has:
        title       – cleaned incident headline
        body        – the descriptive paragraphs (between title and Source)
        date        – date string from the title
        full_text   – raw text of the entire incident block
    """

    # -- Step 1: Find all title-date positions --------------------------------
    title_locations = []
    for m in DATE_SUFFIX.finditer(text):
        date_str = m.group(1).strip()
        date_end_pos = m.end()

        # Walk backwards to find where this title block starts.
        # Titles begin after a blank line, after a prior "(Source)", 
        # or after a known section header.
        search_from = m.start()
        prev_source = text.rfind(SOURCE_MARKER, 0, search_from)
        prev_blank = text.rfind("\n\n", 0, search_from)
        
        # Also look for known section headers that precede incidents
        section_headers = [
            "EMPLOYEE PERSONAL ENRICHMENT INCIDENTS",
            "Examples Of Where Implementing ECMR",
        ]
        prev_header = -1
        for hdr in section_headers:
            pos = text.rfind(hdr, 0, search_from)
            if pos != -1:
                # Position after the header line
                end_of_header = text.find("\n", pos)
                if end_of_header != -1 and end_of_header > prev_header:
                    prev_header = end_of_header + 1

        boundary = max(prev_source, prev_blank, prev_header)
        if boundary == -1:
            title_start = 0
        elif prev_header >= prev_source and prev_header >= prev_blank:
            title_start = prev_header
        elif prev_source > prev_blank:
            title_start = prev_source + len(SOURCE_MARKER)
        else:
            title_start = prev_blank + 2

        # Skip whitespace
        while title_start < search_from and text[title_start] in (" ", "\n", "\t", "\r"):
            title_start += 1

        raw_title = text[title_start:date_end_pos]
        title_locations.append({
            "title_start": title_start,
            "title_end": date_end_pos,
            "raw_title": raw_title,
            "date": date_str,
        })

    # -- Step 2: Find all (Source) end positions ------------------------------
    source_positions = []
    idx = 0
    while True:
        pos = text.find(SOURCE_MARKER, idx)
        if pos == -1:
            break
        source_positions.append((pos, pos + len(SOURCE_MARKER)))
        idx = pos + 1

    # -- Step 3: Pair each title with its next (Source) -----------------------
    incidents = []
    used_sources = set()

    for tl in title_locations:
        # Find the first unused (Source) after this title
        matched = None
        for sp_start, sp_end in source_positions:
            if sp_start > tl["title_end"] and sp_start not in used_sources:
                matched = (sp_start, sp_end)
                used_sources.add(sp_start)
                break

        if matched is None:
            continue

        raw_title = tl["raw_title"]
        cleaned_title = clean_title(raw_title)
        body = text[tl["title_end"]:matched[0]].strip()
        full = text[tl["title_start"]:matched[1]].strip()

        incidents.append({
            "title": cleaned_title,
            "body": body,
            "date": tl["date"],
            "full_text": full,
        })

    # -- Step 4: Filter out non-incident matches ------------------------------
    # Real incidents have titles with keywords about crimes/legal outcomes
    # and their titles are a reasonable length (not entire document preambles).
    title_keywords = re.compile(
        r"(?:embezzl|fraud|stol|steal|sentenced|guilty|charged|theft|"
        r"kickback|scheme|conspir|misappropriat|indicted|arrested|"
        r"pleads?|convicted|stealing|pocketing|accused|admits?|"
        r"fired|spent|murder|robbery|negligent|awarded)",
        re.IGNORECASE,
    )

    filtered = []
    for inc in incidents:
        title = inc["title"]
        # Skip if the title itself is too long (likely captured preamble)
        if len(title) > 300:
            continue
        # Skip if the title doesn't contain crime/legal keywords
        if not title_keywords.search(title):
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

    slug = sanitize_filename(title)
    filename = f"{index:03d}_{slug}.md"
    filepath = os.path.join(output_dir, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        f.write(f"**Date:** {date}\n\n")
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
        f.write("| # | Date | Title |\n")
        f.write("|---|------|-------|\n")
        for i, inc in enumerate(incidents, start=1):
            slug = sanitize_filename(inc["title"])
            fname = f"{i:03d}_{slug}.md"
            short_title = inc["title"][:120]
            f.write(f"| {i} | {inc['date']} | [{short_title}]({fname}) |\n")
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
        fp = write_incident_markdown(inc, output_dir, i)
        short = inc["title"][:95]
        print(f"  [{i:3d}] {short}")

    index_path = write_index(incidents, output_dir)

    print(f"\n{'='*70}")
    print(f"Done! {len(incidents)} incidents extracted.")
    print(f"  Output directory : {output_dir}/")
    print(f"  Index file       : {index_path}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
