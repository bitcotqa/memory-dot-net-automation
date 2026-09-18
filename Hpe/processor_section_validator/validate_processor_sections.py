"""
Cross-check ../input/hpe_quickspec_host_processors.csv against the actual
QuickSpecs PDFs it references.

For every CSV row:
  - `file_name` names a PDF that must exist in the local PDF folder (see
    PDF_DIR below). That PDF is opened.
  - `processor` and `processor_description` are each a Python-list-literal
    string of individual values. EVERY value is checked ONE AT A TIME (never
    as a group) against that PDF's Overview, Standard Features, and Technical
    Specifications pages only.

Section detection (primary): QuickSpecs PDFs repeat a running header on every
page --
    QuickSpecs
    <Product Name>
    <Section Name>
    Page N
-- so each page is tagged with the section name from its own header. Pages
tagged Overview / Standard Features / Technical Specifications form the
"target block" for that PDF; all other sections (Options, Memory, Storage,
Service and Support, Configuration Information, ...) are excluded from
matching but still searched so a wrong-section hit can be reported.

Fallback: if a PDF has no discernible per-page header (older/nonstandard
template), fall back to a heading-line / section-phrase text heuristic so the
row is still checked rather than silently skipped.

Per value, classification is one of:
  Matched     - found inside Overview / Standard Features / Technical Specifications
  Not Matched - found in the PDF, but only in some OTHER section (comment names
                which page/section it actually came from)
  Missing     - not found anywhere in the PDF at all (comment says so explicitly)

`processor` values (e.g. "E5-2620v3") are matched with a regex tolerant of
hyphen/space variation and glued suffixes (v3/v4, HE/L/P/W). `processor_description`
values are full spec strings (e.g. "E5-2687Wv4 3.0GHz 12 30MB 160W 9.6GT/s 2400");
PDFs render each field of a table row on its own line, so both sides are
whitespace-normalized and matched by exact substring, falling back to a
token-overlap check (all significant tokens found within a small window) to
tolerate line-wrap/spacing differences.

Usage:
    python validate_processor_sections.py [N]
    N - optional: only process the first N CSV rows (smoke-testing).

PDF_DIR defaults to a local "pdf/" folder next to this script. Override it by
setting the HPE_PDF_DIR environment variable to wherever you've downloaded
the QuickSpecs PDFs (they are not included in this repo - see README.md).

Outputs (in output/, one row per CSV row - same grain as the source file):
    output/host_processors_comparison_summary.csv - counts (matched/not_matched/missing)
                                                      plus which processor/description
                                                      values are Missing
    output/host_processors_comparison_detail.csv   - counts, the source processor /
                                                      processor_description values,
                                                      plus the actual Matched / Not
                                                      Matched / Missing values for
                                                      both columns (Not Matched entries
                                                      carry an inline comment saying
                                                      which section/page they were
                                                      actually found in)
"""

import ast
import csv
import os
import re
import sys
import unicodedata

import pandas as pd
import pymupdf

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HPE_DIR = os.path.dirname(BASE_DIR)
CSV_PATH = os.path.join(HPE_DIR, "input", "hpe_quickspec_host_processors.csv")
PDF_DIR = os.environ.get("HPE_PDF_DIR", os.path.join(BASE_DIR, "pdf"))
OUT_DIR = os.path.join(BASE_DIR, "output")
OUT_DETAIL = os.path.join(OUT_DIR, "host_processors_comparison_detail.csv")
OUT_SUMMARY = os.path.join(OUT_DIR, "host_processors_comparison_summary.csv")

TARGET_SECTIONS = {"overview", "standard features", "technical specifications"}
PAGE_LINE_RE = re.compile(r"^page\s+\d+$", re.IGNORECASE)
# A page header sometimes qualifies the section name with a product variant,
# e.g. "Standard Features (Server Blade)" or "Standard Features (DL380
# Generation 3 server)" on a QuickSpecs PDF that documents more than one form
# factor. That's still the Standard Features section - only the trailing
# "(...)" differs - so it must still count as a target section.
TARGET_SECTION_QUALIFIER_RE = re.compile(r"\s*\([^)]*\)\s*$")
# Some QuickSpecs "Supplement" documents (e.g. a Carrier Grade/NEBS
# addendum) have no Standard Features page at all - the per-SKU spec list
# instead lives under a "Recommended Support Services for <product>" header.
# Verified against the actual PDF text before adding this: that section
# contains full spec lines ("HPE BL460c Gen9 Intel(R) Xeon(R) E5-2620v3
# (2.4GHz/6-core/15MB/85W)"), i.e. it's standing in for Standard Features in
# this document, not generic warranty/support boilerplate.
TARGET_SECTION_ALIAS_RE = re.compile(r"^recommended support services for\b", re.IGNORECASE)


def is_target_section(section_name):
    stripped = TARGET_SECTION_QUALIFIER_RE.sub("", section_name).strip().lower()
    return stripped in TARGET_SECTIONS or bool(TARGET_SECTION_ALIAS_RE.match(stripped))

PROCESSOR_HEADINGS = {"processor", "processors"}
NEXT_SECTION_HEADINGS = {
    "configuration information", "options", "technical specifications",
    "popular configurations", "service and support", "warranty",
    "warranty and support", "summary of changes", "accessories",
    "ordering guidelines", "additional information", "environmental",
    "certifications", "related options and accessories",
    "frequently asked questions",
}
MAX_BLOCK_CHARS = 100000

# Some CSV file_name values were generated with a synthetic
# "<Product Title> QuickSpecs_<timestamp>.pdf" suffix that doesn't match the
# real file on disk, which instead carries an HPE document-ID suffix (e.g.
# "c04282694.pdf", "a00056112enw.pdf", "data sheet-PSN...WWEN.pdf"). Stripping
# known suffix patterns from both sides and comparing normalized titles
# resolves these unambiguously.
FILENAME_SUFFIX_PATTERNS = [
    r"\s*QuickSpecs_\d+$",
    r"[-\s]a\d{8}enw$",
    r"\s*c\d{8}$",
    r"\s*data sheet-PSN\d+WWEN$",
]


def normalize_filename_title(name):
    base = re.sub(r"\.pdf$", "", name, flags=re.IGNORECASE).strip()
    for pat in FILENAME_SUFFIX_PATTERNS:
        base = re.sub(pat, "", base, flags=re.IGNORECASE).strip()
    base = re.sub(r"[^a-z0-9]+", " ", base.lower()).strip()
    return re.sub(r"\s+", " ", base)


def build_filename_index(pdf_files_on_disk):
    index = {}
    for f in pdf_files_on_disk:
        index.setdefault(normalize_filename_title(f), []).append(f)
    return index


def resolve_file_name(csv_file_name, pdf_files_on_disk, filename_index):
    """Returns (resolved_file_name_or_None, method) where method is
    'exact', 'normalized', or None if unresolved."""
    if csv_file_name in pdf_files_on_disk:
        return csv_file_name, "exact"
    cands = filename_index.get(normalize_filename_title(csv_file_name), [])
    if len(cands) == 1:
        return cands[0], "normalized"
    return None, None


_pdf_cache = {}


def extract_pages(pdf_path):
    if pdf_path in _pdf_cache:
        return _pdf_cache[pdf_path]
    doc = pymupdf.open(pdf_path)
    pages = [page.get_text() for page in doc]
    doc.close()
    _pdf_cache[pdf_path] = pages
    return pages


VERSION_STAMP_RE = re.compile(r"(19|20)\d{2}")  # date/version boilerplate lines contain a bare year


def detect_section_for_page(page_text):
    """Read the page's own running header/footer and return its section name
    (original case), or None if no header/footer pattern is present.

    Two layouts are seen across ~25 years of QuickSpecs templates:
      - modern (top header): QuickSpecs / <Product> / <Section> / Page N
      - older (bottom footer): QuickSpecs / <Product...> / <Section> /
        <doc-id/version/date stamp> / Page N
    Both put "Page N" alone on its own line, with the section name either
    directly above it (modern) or one line above a date/version stamp
    (older) - so find the LAST standalone "Page N" line on the page (there
    should only be one), then walk upward skipping any boilerplate
    date/version line(s) to find the section name.
    """
    lines = [l.strip() for l in page_text.split("\n") if l.strip()]
    page_idx = None
    for i, l in enumerate(lines):
        if PAGE_LINE_RE.match(l):
            page_idx = i
    if page_idx is None or page_idx == 0:
        return None

    j = page_idx - 1
    steps = 0
    while j >= 0 and steps < 3:
        candidate = lines[j]
        if VERSION_STAMP_RE.search(candidate):
            j -= 1
            steps += 1
            continue
        if candidate and candidate.lower() != "quickspecs":
            return candidate
        return None
    return None


class PdfIndex:
    """Wraps one PDF's pages with per-page section labels and offset maps for
    both raw and normalized concatenated text, so a match position can be
    traced back to the page/section it came from."""

    def __init__(self, pages):
        self.pages = pages
        self.sections = [detect_section_for_page(p) for p in pages]
        self.has_headers = any(s is not None for s in self.sections)

        # raw full text + per-page start offsets
        self.full_text, self.raw_offsets = self._join_with_offsets(pages)

        # normalized full text + per-page start offsets (normalize per page
        # first so offsets still line up with page boundaries)
        norm_pages = [normalize_text(p) for p in pages]
        self.full_text_norm, self.norm_offsets = self._join_with_offsets(norm_pages, sep=" ")

        if self.has_headers:
            target_pages = [p for p, s in zip(pages, self.sections)
                             if s and is_target_section(s)]
            self.method = "header"
        else:
            target_pages = None
            self.method = "fallback"

        if target_pages:
            self.target_block = "\n".join(target_pages)[:MAX_BLOCK_CHARS]
        else:
            self.target_block = self._fallback_block()
            if not self.target_block:
                self.method = "none"

        self.target_block_norm = normalize_text(self.target_block)

    @staticmethod
    def _join_with_offsets(chunks, sep="\n"):
        text_parts = []
        offsets = []
        pos = 0
        for chunk in chunks:
            offsets.append(pos)
            text_parts.append(chunk)
            pos += len(chunk) + len(sep)
        return sep.join(text_parts), offsets

    def _fallback_block(self):
        lines = self.full_text.split("\n")
        block = ""
        start_line = None
        for i, l in enumerate(lines):
            if l.strip().lower() in PROCESSOR_HEADINGS:
                start_line = i
                break
        if start_line is not None:
            end_line = len(lines)
            for i in range(start_line + 1, len(lines)):
                if lines[i].strip().lower() in NEXT_SECTION_HEADINGS:
                    end_line = i
                    break
            block += "\n".join(lines[start_line:end_line])

        lower = self.full_text.lower()
        covered = []
        for phrase in ("overview", "standard features", "technical specifications"):
            search_from = 0
            while True:
                start = lower.find(phrase, search_from)
                if start == -1:
                    break
                search_from = start + len(phrase)
                if any(s <= start < e for s, e in covered):
                    continue
                tail_lines = self.full_text[start:].split("\n")
                end_line = len(tail_lines)
                for i, l in enumerate(tail_lines):
                    if i == 0:
                        continue
                    if l.strip().lower() in NEXT_SECTION_HEADINGS:
                        end_line = i
                        break
                segment = "\n".join(tail_lines[:end_line])
                block += "\n" + segment
                covered.append((start, start + len(segment)))
        return block.strip()[:MAX_BLOCK_CHARS]

    def _page_for_offset(self, offset, offsets):
        page_idx = 0
        for i, off in enumerate(offsets):
            if off <= offset:
                page_idx = i
            else:
                break
        return page_idx

    def locate(self, offset, normalized=False):
        offsets = self.norm_offsets if normalized else self.raw_offsets
        page_idx = self._page_for_offset(offset, offsets)
        section = self.sections[page_idx] if page_idx < len(self.sections) else None
        return page_idx + 1, (section or "Unlabeled section")


def token_pattern(token):
    """Build a regex for a processor code that tolerates the PDF inserting a
    space/hyphen where the CSV token glued characters together that the PDF
    keeps apart. Two kinds of seam show up in practice, and both are needed
    (one doesn't subsume the other):
      - a version/suffix seam between a LETTER and a following letter+digit
        group, e.g. CSV "E5-2650Lv2" vs PDF "E5-2650L v2" - the space sits
        between "L" and "v2", not inside "v2".
      - a digit/letter class seam anywhere else, e.g. CSV "21364EV7" vs PDF
        "21364 EV7", or CSV "D920" vs PDF "D 920".
    Order matters: the specific v-digit seam must be inserted before the
    generic digit<->letter pass, otherwise the generic pass claims the
    boundary right before the trailing digit (between "v" and "2") instead of
    the boundary that's actually flexible in these PDFs (between "L" and "v").

    A separate seam at the END of the token: some spec tables render the SKU
    with the word "Processor" glued directly onto it with no space at all
    (e.g. "Gold 5415+Processor", "Gold  5222Processor" - the table's row
    label bleeding into the SKU cell). The normal trailing boundary check
    (no letter/digit immediately after the token) would reject that, so it's
    also satisfied when the token is immediately followed by "Processor".

    A fourth, unrelated seam: HPE renders a trademark glyph (®/™/©) glued
    directly onto a brand word with no preceding space, right where the
    CSV's hyphen sits, e.g. CSV "EPYC-9845" vs PDF "AMD EPYC™ 9845". The
    optional trademark class is inserted at the old hyphen position using
    the literal symbol characters (not a \\u escape) specifically so it's
    immune to the two digit/letter passes below, which only look for
    [A-Za-z0-9] neighbors.
    """
    escaped = re.escape(token.strip())
    escaped = escaped.replace(r"\-", "[®™©]?[\\s-]*")
    escaped = re.sub(r"(?<=\w)v(\d)", r"[\\s-]*v\1", escaped)
    escaped = re.sub(r"(?<=[0-9])(?=[A-Za-z])", r"[\\s-]*", escaped)
    escaped = re.sub(r"(?<=[A-Za-z])(?=[0-9])", r"[\\s-]*", escaped)
    return re.compile(r"(?<![A-Za-z0-9])" + escaped + r"(?:(?![A-Za-z0-9])|(?=Processor))", re.IGNORECASE)


# Some QuickSpecs PDFs describe two SKUs of the same generation as one
# combined family reference, e.g. "Intel Xeon E7-4800/8800 v2 processors"
# instead of listing "E7-4800 v2" and "E7-8800 v2" as separate lines, or
# combine two generations of the SAME family number instead, e.g. "Intel
# E5-2600 v3/v4 Processor Family" instead of separate "E5-2600v3"/
# "E5-2600v4" lines. If the token's own literal pattern isn't found, also
# accept it appearing as one side of either kind of slash-combined pair.
COMBINED_FAMILY_TOKEN_RE = re.compile(r"^([A-Za-z0-9]+-)(\d+)((?:\s?[vV]\d+)?)$")


def combined_family_pattern(token):
    m = COMBINED_FAMILY_TOKEN_RE.match(token.strip())
    if not m:
        return None
    prefix, number, suffix = m.groups()
    prefix_pat = re.escape(prefix.rstrip("-"))
    suffix = suffix.strip()
    version_match = re.match(r"[vV](\d+)$", suffix) if suffix else None
    if version_match:
        version_digit = re.escape(version_match.group(1))
        # The version half can itself be slash-combined, e.g. "E5-2600 v3/v4"
        # for both "E5-2600v3" and "E5-2600v4". Each item in that list can
        # carry its own "v" (only the first one always does), so a leading
        # run of complete "vN/" groups is consumed before our target digit,
        # and a trailing run of "/vN" groups is consumed after it.
        suffix_pat = (
            r"[\s-]*(?:[vV][\s-]*\d+[\s-]*/[\s-]*)*[vV]?[\s-]*" + version_digit +
            r"(?:[\s-]*/[\s-]*[vV]?[\s-]*\d+)*"
        )
    else:
        suffix_pat = ""
    return re.compile(
        r"(?<![A-Za-z0-9])" + prefix_pat + r"[\s-]*(?:\d+\s*/\s*)?" + re.escape(number) +
        r"(?:\s*/\s*\d+)?" + suffix_pat + r"(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


def classify_processor_token(token, pdf_index):
    pat = token_pattern(token)
    combined_pat = combined_family_pattern(token)

    m = pat.search(pdf_index.target_block) if pdf_index.target_block else None
    if not m and combined_pat and pdf_index.target_block:
        m = combined_pat.search(pdf_index.target_block)
    if m:
        ctx = pdf_index.target_block[max(0, m.start() - 60):m.end() + 60].replace("\n", " ").strip()
        return "Matched", ctx, ""

    m = pat.search(pdf_index.full_text)
    if not m and combined_pat:
        m = combined_pat.search(pdf_index.full_text)
    if m:
        ctx = pdf_index.full_text[max(0, m.start() - 60):m.end() + 60].replace("\n", " ").strip()
        page_num, section = pdf_index.locate(m.start())
        if _is_changelog_note(ctx, section):
            return "Matched", ctx, f"Accepted as a changelog note (found in 'Summary of Changes', page {page_num})."
        comment = (f"Value '{token}' was NOT found in Overview / Standard Features / "
                   f"Technical Specifications. It only appears in the '{section}' "
                   f"section on PDF page {page_num}.")
        return "Not Matched", ctx, comment

    comment = f"Value '{token}' was not found anywhere in the PDF ({len(pdf_index.pages)} pages checked)."
    return "Missing", "", comment


def normalize_text(text):
    text = unicodedata.normalize("NFKD", text)
    text = text.replace("�", " ").replace("", "")
    text = re.sub(r"[^\w.]+", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


# Unit-abbreviation words the CSV's processor_description strings often carry
# attached to a number (e.g. "3.1 GHz", "36 MB", "205 W", "3200 MT/s"), but
# that PDF spec TABLES usually don't repeat per cell - the unit lives once in
# the column header instead (e.g. the cell just says "3.1", "36", "205",
# "3200"). Requiring these tokens to co-occur with the number caused real
# matches to be misreported as Missing, so they're not required - the number
# itself still has to match.
UNIT_WORDS = {"ghz", "mhz", "khz", "hz", "mb", "gb", "kb", "tb", "mt", "gt", "w"}


def significant_tokens(desc_norm):
    toks = desc_norm.split(" ")
    return [t for t in toks if t and t not in UNIT_WORDS and (any(c.isdigit() for c in t) or len(t) >= 3)]


# PDFs sometimes glue a footnote-reference digit straight onto the word
# "processor" with no space (e.g. "Processor1"), while the CSV description
# keeps them apart ("Processor 1"). Collapsing just this specific seam and
# re-trying an EXACT substring match recovers those cases. This is
# deliberately narrow (scoped to the literal word "processor", not any
# word+digit) - a lone digit elsewhere is sometimes the only real
# differentiator between similar table rows (e.g. CSV "Model 2" is a
# corrupted/truncated version of the PDF's "Model 2222", not the same value),
# so it must still be required verbatim there.
FOOTNOTE_DIGIT_RE = re.compile(r"\bprocessor\s(\d)(?=\s|$)")


def glue_footnote_digits(desc_norm):
    return FOOTNOTE_DIGIT_RE.sub(r"processor\1", desc_norm)


# A value is sometimes not a spec-table line at all but a changelog sentence
# the CSV picked up from the "Summary of Changes" page (e.g. "AMD Opteron
# 6128 processor was added"). That's not a real spec mismatch - the SKU's
# actual spec line is checked (and matched) separately - so a changelog
# sentence, or a bare SKU code that only appears inside one, found ONLY in
# Summary of Changes is an expected companion entry, not something to flag.
CHANGELOG_PHRASE_RE = re.compile(
    r"\bwas added\b|\bwere added\b|\bhas been added\b|\bhave been added\b",
    re.IGNORECASE,
)


def _is_changelog_note(text, section):
    return bool(section) and section.strip().lower() == "summary of changes" and CHANGELOG_PHRASE_RE.search(text)


def find_token_window(tokens, haystack_norm, window_chars=250):
    """Returns the start offset of a window in haystack_norm where every token
    in `tokens` occurs, or None if no such window exists."""
    if not tokens:
        return None
    first = tokens[0]
    haystack_len = len(haystack_norm)
    start = 0
    while True:
        idx = haystack_norm.find(first, start)
        if idx == -1:
            return None
        lo = max(0, idx - window_chars)
        hi = min(haystack_len, idx + window_chars)
        window = haystack_norm[lo:hi]
        if all((f" {t} " in f" {window} ") for t in tokens):
            return idx
        start = idx + 1


def classify_description(desc, pdf_index):
    desc_norm = normalize_text(desc)
    if not desc_norm:
        return "Missing", "", f"Value '{desc}' is empty after normalization."
    tokens = significant_tokens(desc_norm)
    if not tokens:
        return "Missing", "", f"Value '{desc}' has no significant tokens to search for."
    desc_glued = glue_footnote_digits(desc_norm)

    tb_norm = pdf_index.target_block_norm
    if desc_norm in tb_norm:
        idx = tb_norm.find(desc_norm)
        return "Matched", tb_norm[max(0, idx - 40):idx + len(desc_norm) + 40], ""
    if desc_glued != desc_norm and desc_glued in tb_norm:
        idx = tb_norm.find(desc_glued)
        return "Matched", tb_norm[max(0, idx - 40):idx + len(desc_glued) + 40], ""
    win_idx = find_token_window(tokens, tb_norm)
    if win_idx is not None:
        return "Matched", "(token-overlap match in target sections)", ""

    ft_norm = pdf_index.full_text_norm
    if desc_norm in ft_norm:
        idx = ft_norm.find(desc_norm)
        page_num, section = pdf_index.locate(idx, normalized=True)
        ctx = ft_norm[max(0, idx - 40):idx + len(desc_norm) + 40]
        if _is_changelog_note(desc, section):
            return "Matched", ctx, f"Accepted as a changelog note (found in 'Summary of Changes', page {page_num})."
        comment = (f"Description was NOT found in Overview / Standard Features / "
                   f"Technical Specifications. It only appears in the '{section}' "
                   f"section on PDF page {page_num}.")
        return "Not Matched", ctx, comment
    if desc_glued != desc_norm and desc_glued in ft_norm:
        idx = ft_norm.find(desc_glued)
        page_num, section = pdf_index.locate(idx, normalized=True)
        ctx = ft_norm[max(0, idx - 40):idx + len(desc_glued) + 40]
        if _is_changelog_note(desc, section):
            return "Matched", ctx, f"Accepted as a changelog note (found in 'Summary of Changes', page {page_num})."
        comment = (f"Description was NOT found in Overview / Standard Features / "
                   f"Technical Specifications. It only appears in the '{section}' "
                   f"section on PDF page {page_num}.")
        return "Not Matched", ctx, comment

    win_idx = find_token_window(tokens, ft_norm)
    if win_idx is not None:
        page_num, section = pdf_index.locate(win_idx, normalized=True)
        if _is_changelog_note(desc, section):
            return "Matched", "(token-overlap match elsewhere in PDF)", f"Accepted as a changelog note (found in 'Summary of Changes', page {page_num})."
        comment = (f"Description was NOT found in Overview / Standard Features / "
                   f"Technical Specifications. Its key details only appear together "
                   f"in the '{section}' section on PDF page {page_num}.")
        return "Not Matched", "(token-overlap match elsewhere in PDF)", comment

    comment = f"Description was not found anywhere in the PDF ({len(pdf_index.pages)} pages checked)."
    return "Missing", "", comment


# Some CSV cells use a curly/smart quote (U+2018/U+2019/U+201C/U+201D) as the
# Python-list-literal delimiter itself (e.g. "[‘21264’]") instead of a
# straight quote, which is a SyntaxError for ast.literal_eval - the whole cell
# then silently parses as no data at all. Only quotes sitting where a
# delimiter belongs (right after "[" or ", ", or right before "]" or ",") are
# swapped; a curly quote used as normal punctuation inside an already-valid
# string (e.g. "Intel's" mid-word) is left untouched.
CURLY_QUOTES = set("‘’“”")
_OPEN_QUOTE_RE = re.compile(r"([\[,]\s*)([‘“])")
_CLOSE_QUOTE_RE = re.compile(r"([’”])(\s*[\],])")


def _straighten_delimiter_quotes(cell):
    cell = _OPEN_QUOTE_RE.sub(lambda m: m.group(1) + ("'" if m.group(2) == "‘" else '"'), cell)
    cell = _CLOSE_QUOTE_RE.sub(lambda m: ("'" if m.group(1) == "’" else '"') + m.group(2), cell)
    return cell


def parse_list_cell(cell):
    if pd.isna(cell):
        return None
    try:
        val = ast.literal_eval(cell)
    except (ValueError, SyntaxError):
        if any(c in CURLY_QUOTES for c in cell):
            try:
                val = ast.literal_eval(_straighten_delimiter_quotes(cell))
            except (ValueError, SyntaxError):
                return None
        else:
            return None
    if isinstance(val, list):
        return [str(v).strip() for v in val if str(v).strip()]
    return [str(val).strip()]


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None

    if not os.path.isfile(CSV_PATH):
        sys.exit(f"CSV not found: {CSV_PATH}")
    if not os.path.isdir(PDF_DIR):
        sys.exit(f"PDF folder not found: {PDF_DIR} (set HPE_PDF_DIR to override - see README.md)")

    os.makedirs(OUT_DIR, exist_ok=True)

    df = pd.read_csv(CSV_PATH)
    if limit:
        df = df.head(limit)
    pdf_files_on_disk = set(os.listdir(PDF_DIR))
    filename_index = build_filename_index(pdf_files_on_disk)

    detail_rows = []
    summary_rows = []
    _pdf_index_cache = {}

    for idx, row in df.iterrows():
        csv_row_num = idx + 2
        csv_file_name = str(row["file_name"]).strip() if not pd.isna(row["file_name"]) else ""

        file_name, resolve_method = (None, None)
        if csv_file_name:
            file_name, resolve_method = resolve_file_name(csv_file_name, pdf_files_on_disk, filename_index)

        source_processor = str(row["processor"]) if not pd.isna(row["processor"]) else ""
        source_processor_description = str(row["processor_description"]) if not pd.isna(row["processor_description"]) else ""

        if not file_name:
            skip_row = {
                "csv_row": csv_row_num, "csv_file_name": csv_file_name, "resolved_file_name": "",
                "file_match_method": "", "status": "SKIPPED - file_name not found in PDF_DIR (no unambiguous match)",
                "section_method": "", "matched": 0, "not_matched": 0, "missing": 0,
                "total_items": 0,
            }
            summary_rows.append(dict(skip_row))
            detail_rows.append({**skip_row, "processor": source_processor, "processor_description": source_processor_description})
            continue

        processors = parse_list_cell(row["processor"]) or []
        descriptions = parse_list_cell(row["processor_description"]) or []
        if not processors and not descriptions:
            skip_row = {
                "csv_row": csv_row_num, "csv_file_name": csv_file_name, "resolved_file_name": file_name,
                "file_match_method": resolve_method, "status": "NO PROCESSOR DATA IN CSV ROW",
                "section_method": "", "matched": 0, "not_matched": 0, "missing": 0,
                "total_items": 0,
            }
            summary_rows.append(dict(skip_row))
            detail_rows.append({**skip_row, "processor": source_processor, "processor_description": source_processor_description})
            continue

        pdf_path = os.path.join(PDF_DIR, file_name)
        try:
            if pdf_path not in _pdf_index_cache:
                _pdf_index_cache[pdf_path] = PdfIndex(extract_pages(pdf_path))
            pdf_index = _pdf_index_cache[pdf_path]
        except Exception as e:
            skip_row = {
                "csv_row": csv_row_num, "csv_file_name": csv_file_name, "resolved_file_name": file_name,
                "file_match_method": resolve_method, "status": f"SKIPPED - could not open/read PDF ({e})",
                "section_method": "", "matched": 0, "not_matched": 0, "missing": 0,
                "total_items": 0,
            }
            summary_rows.append(dict(skip_row))
            detail_rows.append({**skip_row, "processor": source_processor, "processor_description": source_processor_description})
            continue

        counts = {"Matched": 0, "Not Matched": 0, "Missing": 0}
        proc_matched, proc_not_matched, proc_missing = [], [], []
        desc_matched, desc_not_matched, desc_missing = [], [], []

        for token in processors:
            status, _ctx, comment = classify_processor_token(token, pdf_index)
            counts[status] += 1
            if status == "Matched":
                proc_matched.append(f"{token} — {comment}" if comment else token)
            elif status == "Not Matched":
                proc_not_matched.append(f"{token} — {comment}")
            else:
                proc_missing.append(token)

        for desc in descriptions:
            status, _ctx, comment = classify_description(desc, pdf_index)
            counts[status] += 1
            if status == "Matched":
                desc_matched.append(f"{desc} — {comment}" if comment else desc)
            elif status == "Not Matched":
                desc_not_matched.append(f"{desc} — {comment}")
            else:
                desc_missing.append(desc)

        summary_rows.append({
            "csv_row": csv_row_num, "csv_file_name": csv_file_name, "resolved_file_name": file_name,
            "file_match_method": resolve_method, "status": "OK",
            "section_method": pdf_index.method,
            "matched": counts["Matched"], "not_matched": counts["Not Matched"],
            "missing": counts["Missing"],
            "total_items": len(processors) + len(descriptions),
            "missing_processor_values": " | ".join(proc_missing),
            "missing_processor_description_values": " | ".join(desc_missing),
        })

        detail_rows.append({
            "csv_row": csv_row_num, "csv_file_name": csv_file_name, "resolved_file_name": file_name,
            "file_match_method": resolve_method, "status": "OK",
            "section_method": pdf_index.method,
            "matched": counts["Matched"], "not_matched": counts["Not Matched"],
            "missing": counts["Missing"],
            "total_items": len(processors) + len(descriptions),
            "processor": source_processor,
            "processor_description": source_processor_description,
            "processor_matched_values": " | ".join(proc_matched),
            "processor_not_matched_values": " | ".join(proc_not_matched),
            "processor_missing_values": " | ".join(proc_missing),
            "processor_description_matched_values": " | ".join(desc_matched),
            "processor_description_not_matched_values": " | ".join(desc_not_matched),
            "processor_description_missing_values": " | ".join(desc_missing),
        })

    with open(OUT_DETAIL, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "csv_row", "csv_file_name", "resolved_file_name", "file_match_method", "status", "section_method",
            "matched", "not_matched", "missing", "total_items",
            "processor", "processor_description",
            "processor_matched_values", "processor_not_matched_values", "processor_missing_values",
            "processor_description_matched_values", "processor_description_not_matched_values", "processor_description_missing_values",
        ])
        writer.writeheader()
        writer.writerows(detail_rows)

    with open(OUT_SUMMARY, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "csv_row", "csv_file_name", "resolved_file_name", "file_match_method", "status", "section_method",
            "matched", "not_matched", "missing", "total_items",
            "missing_processor_values", "missing_processor_description_values",
        ])
        writer.writeheader()
        writer.writerows(summary_rows)

    ok_rows = [r for r in summary_rows if r.get("status") == "OK"]
    total = sum(r["total_items"] for r in ok_rows)
    m = sum(r["matched"] for r in ok_rows)
    nm = sum(r["not_matched"] for r in ok_rows)
    mi = sum(r["missing"] for r in ok_rows)
    skipped = sum(1 for r in summary_rows if r.get("status") != "OK")

    print(f"Processed {len(df)} CSV row(s) ({skipped} skipped - see summary).")
    print(f"Checked {total} individual values (processor + processor_description):")
    print(f"  Matched:     {m}")
    print(f"  Not Matched: {nm}")
    print(f"  Missing:     {mi}")
    print(f"\nDetail:  {OUT_DETAIL}")
    print(f"Summary: {OUT_SUMMARY}")


if __name__ == "__main__":
    main()
