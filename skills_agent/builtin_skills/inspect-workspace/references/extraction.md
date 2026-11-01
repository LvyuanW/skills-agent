# Read selected content with source locations

Read the section for the format you need. These helpers are useful when a decision requires selected records or source locations. You do not need to extract every attachment in advance.

Call `python /absolute/skill/scripts/extract.py --help` or a subcommand's `--help` for its interface. The helpers leave the source unchanged and print one JSON object. Success exits 0. Failure exits nonzero and includes an `error`.

All returned `source` paths are absolute. `--max-chars` defaults to 12000 Unicode characters of extracted text; JSON framing and source metadata do not count toward this limit. `--offset` is a zero-based character offset within one record's extracted text, not a byte offset in the original file.

Keep the source and options unchanged while following cursors. The sections below explain how extraction represents each format and what it does not cover.

## JSON selection

```sh
python /absolute/skill/scripts/extract.py json /absolute/data.json --pointer '/users/0/name'
python /absolute/skill/scripts/extract.py json /absolute/data.json --pointer '/a~1b/~0key' --max-chars 1000
```

`--pointer` uses JSON Pointer. An empty pointer selects the root, `~1` means `/`, and `~0` means `~`. Array indices are zero-based. Missing members and malformed pointers fail.

The result contains serialized JSON text for the selected value. Values are preserved, but original whitespace and byte positions are not. If `truncated` is true, call again with the same pointer and set `--offset` to `next.offset`. Join the returned `text` chunks before parsing a split JSON value.

The parser loads the JSON file into memory. Use a dedicated streaming reader for a file too large to parse locally.

## CSV records

```sh
python /absolute/skill/scripts/extract.py csv /absolute/data.csv --start 1 --limit 20
python /absolute/skill/scripts/extract.py csv /absolute/data.csv --delimiter ';' --start 21 --limit 20
```

`--start` is a one-based logical CSV row. `--limit` bounds the number of rows, including any header row. Each record's `text` is a JSON array of field strings. It retains field order, Unicode, embedded line breaks and empty fields. `location` gives the logical row and inclusive physical `line_start`/`line_end`.

The default encoding is `utf-8-sig`. Set `--encoding` explicitly when you know the file uses another encoding. A delimiter must be one character. The extractor does not infer headers, data types or column meanings.

## DOCX body content

```sh
python /absolute/skill/scripts/extract.py docx /absolute/report.docx --start 1 --limit 30
```

Records follow body order in `word/document.xml`. Each direct paragraph or table cell is one record. Locations identify a body paragraph or a table/row/column. Numbering is one-based; table columns refer to XML cells rather than visual grid spans.

The extractor joins stored text runs and preserves tabs and line breaks. Paragraphs within a cell are separated by newlines. Paragraphs in nested tables appear within their containing cell's text.

This extractor does not read headers, footers, text embedded in images, comments, layout or drawing positions.

## XLSX stored cells

```sh
python /absolute/skill/scripts/extract.py xlsx /absolute/book.xlsx --sheet 'Sales' --start 1 --limit 30
```

`--sheet` must match the worksheet name exactly. An unknown name fails and reports the available sheets. Each populated XML cell is one record, in stored order. `--start` counts populated cells, not worksheet rows. Locations identify the sheet, cell address such as `B7`, and worksheet part.

Shared and inline strings are decoded. Booleans become `true`/`false`; other values remain their stored strings. A formula is reported separately from its cached value. The cache may be absent or stale, and the extractor does not calculate formulas.

Numeric dates remain stored serial numbers. Styles and number formats are not applied, and merged-cell appearance is not reproduced.

## PDF page text

```sh
python /absolute/skill/scripts/probe_environment.py --module pypdf --executable pdftotext
python /absolute/skill/scripts/extract.py pdf /absolute/report.pdf --start 3 --limit 2
```

Each page is one record. `--start` is the one-based PDF page number, not the page label printed in the document.

`--reader auto` tries installed `pypdf`, then `PyPDF2`, then `pdftotext`. You can explicitly select one of these readers. For `pdftotext`, `--timeout` sets the time limit in seconds and defaults to 30. If no supported reader is installed, extraction fails without installing anything.

The Python readers reject encrypted files. Password-protected files that `pdftotext` cannot open also fail. There is no password or decryption interface.

Scans or drawings may have no stored page text. A successful command does not establish that every visible word was extracted. This helper does not perform OCR or visual inspection.

## Continue without losing content

CSV, DOCX, XLSX and PDF results contain `records`, `truncated` and `next`. A record's `partial` and `offset` describe split text. When continuing, use both `start` and `offset` from `next`:

```sh
python /absolute/skill/scripts/extract.py docx /absolute/report.docx --start 1 --limit 20 --max-chars 1000
# After inspecting a result with next={"start": 4, "offset": 250}:
python /absolute/skill/scripts/extract.py docx /absolute/report.docx --start 4 --offset 250 --limit 20 --max-chars 1000
```

A cursor may point into the same long record or to the next record. Join pieces with the same source location to recover the full text.

`truncated: false` means the selected source has no further records. It does not mean that the extractor read format features outside its stated scope. If the source changes, read it again instead of applying a cursor from the old contents.
