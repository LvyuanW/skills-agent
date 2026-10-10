# Located extraction

Call `python /absolute/skill/scripts/extract.py --help` or the relevant subcommand's `--help` for the interface. Helpers read the source without modifying it and print one JSON object; success exits 0, failures exit nonzero with an `error`. Select the next useful portion instead of extracting every attachment in advance.

All returned `source` paths are absolute. `--max-chars` defaults to 12000 Unicode characters of extracted text, excluding JSON framing and source metadata. `--offset` is a zero-based character offset in one record's extracted text; it is not a byte offset in the original file. Keep the source and options unchanged while following cursors. Extraction may normalize a format's representation as described below.

## JSON selection

```sh
python /absolute/skill/scripts/extract.py json /absolute/data.json --pointer '/users/0/name'
python /absolute/skill/scripts/extract.py json /absolute/data.json --pointer '/a~1b/~0key' --max-chars 1000
```

`--pointer` uses JSON Pointer: empty selects the root, `~1` means `/`, and `~0` means `~`. Array indices are zero-based. Missing members and malformed pointers fail. The result contains serialized JSON text for the selected value; it preserves values, not original whitespace or byte positions. If `truncated`, call again with the same pointer and `--offset` from `next.offset`. Concatenate returned `text` chunks before parsing a split JSON value. The parser loads the JSON file into memory; use a dedicated streaming reader for inputs too large to parse locally.

## CSV records

```sh
python /absolute/skill/scripts/extract.py csv /absolute/data.csv --start 1 --limit 20
python /absolute/skill/scripts/extract.py csv /absolute/data.csv --delimiter ';' --start 21 --limit 20
```

`--start` is a one-based logical CSV row, and `--limit` bounds rows, including any header row. Each record's `text` is a JSON array of field strings, retaining field order, Unicode, embedded line breaks, and empty fields. `location` gives the logical row and inclusive physical `line_start`/`line_end`. The default encoding is `utf-8-sig`; set `--encoding` explicitly for another known encoding. A delimiter is one character. No header, data type, or column meaning is inferred.

## DOCX body content

```sh
python /absolute/skill/scripts/extract.py docx /absolute/report.docx --start 1 --limit 30
```

Records follow `word/document.xml` body order: a direct paragraph or table cell is one record. Locations identify a body paragraph or a table/row/column; numbering is one-based and table columns refer to XML cells, not visual grid spans. Text joins stored runs, preserving tabs and line breaks. A cell's paragraphs are separated by newlines. Headers, footers, text embedded in images, comments, layout, and drawing positions are outside this extractor's scope. Nested table paragraphs appear within their containing cell's text.

## XLSX stored cells

```sh
python /absolute/skill/scripts/extract.py xlsx /absolute/book.xlsx --sheet 'Sales' --start 1 --limit 30
```

`--sheet` is the exact worksheet name; an unknown name fails and reports available sheets. One populated XML cell is one record, in stored order. `--start` counts populated cells, not worksheet rows. Locations identify the sheet, cell address such as `B7`, and worksheet part. Shared and inline strings are decoded; booleans become `true`/`false`. Other values remain their stored strings. A formula is reported separately with its cached value, which may be absent or stale; formulas are not calculated. Numeric dates remain stored serial numbers, styles and number formats are not applied, and merged-cell appearance is not reproduced.

## PDF page text

```sh
python /absolute/skill/scripts/inspect.py --module pypdf --executable pdftotext
python /absolute/skill/scripts/extract.py pdf /absolute/report.pdf --start 3 --limit 2
```

One page is one record; `--start` is the one-based PDF page number. `--reader auto` tries installed `pypdf`, then `PyPDF2`, then `pdftotext`; an explicit reader selects one of those. `pdftotext` has a configurable `--timeout` in seconds (default 30). Missing supported readers fail without installing anything. The Python readers reject encrypted files; password-protected files that `pdftotext` cannot open also fail. There is no password or decryption interface. Stored page text can be empty for scans or drawings; success does not prove that every visible word was extracted, and this helper does not perform OCR or visual inspection. Page numbers are source locations, not document-printed labels.

## Continue without losing content

CSV, DOCX, XLSX, and PDF results contain `records`, `truncated`, and `next`. A record's `partial` and `offset` describe whether its text was split. Use both fields in the continuation cursor:

```sh
python /absolute/skill/scripts/extract.py docx /absolute/report.docx --start 1 --limit 20 --max-chars 1000
# After inspecting a result with next={"start": 4, "offset": 250}:
python /absolute/skill/scripts/extract.py docx /absolute/report.docx --start 4 --offset 250 --limit 20 --max-chars 1000
```

A cursor may point into the same long record or to the next record. Concatenate pieces with the same source location to recover full text. `truncated: false` means the selected source has no further records; it does not claim that format features outside the extractor's scope were read. Re-read a changed source instead of applying a cursor from its earlier contents.
