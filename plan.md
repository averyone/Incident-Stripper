# Plan: Fix incident extraction in extract_incidents.py

## Problem Summary

After analyzing all 3 test PDFs against the current parser, I identified 6 issues causing missed incidents. The most critical is the Source marker mismatch — the parser finds essentially **zero** incidents from the test files.

---

## Issues Found (ordered by severity)

### 1. CRITICAL: Source marker never matches (impacts ~100% of incidents)

The parser uses `SOURCE_MARKER = "(Source)"` but the PDFs contain spaces inside the parens:

| Variant | Jan 2022 | Apr 2023 | Apr 2025 |
|---------|----------|----------|----------|
| `(Source)` exact | **0** | **0** | **1** |
| `(Source )` trailing space | 12 | 25 | 35 |
| `( Source )` both spaces | 36 | 39 | 67 |
| `( Source)` leading space | 1 | 0 | 0 |

**Fix**: Replace literal string match with a regex: `\(\s*Source\s*\)`

### 2. MODERATE: ACFE/Resources section produces false-positive Source markers (~5 per file)

The ACFE "Report to the Nations" section has `(Source)` links that are not incident terminators (e.g. "Key Findings From Report / Infographic ... (Source)"). These would be incorrectly consumed as incident terminators.

**Fix**: Define section boundaries. Stop incident extraction before the ACFE/resource pages begin, or detect and skip non-incident "(Source)" markers by checking that they follow a real incident body.

### 3. MODERATE: Section headers referenced in code don't exist in any test PDF

The parser's `section_headers` list contains:
- `"EMPLOYEE PERSONAL ENRICHMENT INCIDENTS"` — not found in any test file
- `"Examples Of Where Implementing ECMR"` — not found in any test file

Meanwhile, the actual PDFs have ~30+ section headers like:
- `"U.S. GOVERNMENT"`
- `"BANKING / FINANCIAL INSTITUTIONS"`
- `"LAW ENFORCEMENT / PRISONS / FIRST RESPONDERS"`
- `"EMBEZZLEMENT / FINANCIAL THEFT / FRAUD / ..."` etc.

These headers can bleed into title detection when looking backward for title start boundaries.

**Fix**: Replace hardcoded headers with a detection heuristic (all-caps lines with no date suffix), or just rely on blank-line and Source-marker boundaries which work well in practice.

### 4. MODERATE: Year-only date formats missed (3-5 incidents per file)

Some historical incidents end with:
- `(2005)` / `(2010)` / `(2016)` etc. — year in parentheses
- `- 2010` — just a year after the dash

The `DATE_SUFFIX` regex requires a full month name.

**Fix**: Add an alternate date pattern for year-only matches.

### 5. LOW: Double (Source) markers on single incident

At least one incident has `(Source )  (Source )` — two adjacent source markers. This wastes one Source marker and could misalign subsequent title-to-source pairing.

**Fix**: When finding Source positions, collapse consecutive markers that are within ~20 characters of each other.

### 6. LOW: En-dash/em-dash before dates

12 en-dashes (–) and 4 em-dashes (—) appear in the Apr 2025 PDF. The date regex only matches hyphen-minus (`-`).

**Fix**: Update `DATE_SUFFIX` regex to match `[-–—]` instead of just `-`.

---

## New Feature: Category/Section Tracking

Add a `category` field to each incident dict by detecting ALL-CAPS section headers and tracking the current section as incidents are parsed.

---

## Implementation Steps

### Step 1: Fix Source marker matching
- Replace `SOURCE_MARKER = "(Source)"` literal with regex `\(\s*Source\s*\)`
- Update `find_incidents()` Step 2 to use `re.finditer` instead of `str.find`
- Add deduplication for consecutive Source markers within ~20 chars

### Step 2: Fix date pattern matching
- Update `DATE_SUFFIX` to use `[-–—]\s+` instead of `-\s+`
- Add a second pattern `DATE_SUFFIX_YEAR_ONLY` for `[-–—]\s+(\d{4})` and `\((\d{4})\)` endings
- Merge both sets of title locations, deduplicating overlaps

### Step 3: Fix section header handling
- Remove the hardcoded `section_headers` list
- Add a function to detect ALL-CAPS section headers from the text
- Track current category as incidents are parsed
- Add `category` field to incident dicts

### Step 4: Add section boundary awareness
- Detect the ACFE/resource section (starts with "ASSOCIATION OF CERTIFIED FRAUD EXAMINERS") and exclude Source markers from that region
- Or alternatively, skip Source markers that aren't preceded by incident body text

### Step 5: Update Markdown output
- Add `**Category:** {category}` line to each incident's markdown file
- Add a Category column to the index table

### Step 6: Remove dead code
- Remove the `section_headers` list with its nonexistent entries
- Clean up the `clean_title` function's `EMPLOYEE PERSONAL ENRICHMENT` regex since it doesn't apply

### Step 7: Test against all 3 PDFs
- Run the updated parser against all 3 test PDFs
- Verify incident counts match expected totals (Jan 2022: ~43+6, Apr 2023: ~54+23, Apr 2025: ~86+51)
- Spot-check a few incidents for correct title, body, date, and category extraction
