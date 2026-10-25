# Capturing and locating source evidence

Use only the operation needed for the current question. Simple lookups can remain ordinary reads. The scripts use the standard library, accept absolute paths and run from any working directory. They do not create a task plan, require a separate evidence document or decide whether a conclusion is true.

## Direct lookup and batches

Locate a quotation in an existing local file without preparing a capture or request document:

```sh
python3 /absolute/investigation/scripts/source.py locate \
  --file /project/input/deployment.yaml --quote 'initialDelaySeconds: 5'
```

This is read-only. It returns the consumed file's SHA-256, precise positions, neighboring text and ambiguity status. `retention.snapshot_saved:false` means the original bytes were **not** retained; no replayable `binding` is created. A hash identifies the observation but cannot recover an edited file. Add `--store /project/evidence` when later replay is useful: the same consumed bytes are captured, and the returned `saved_result` can be passed directly to `verify --bindings`. Existing snapshots also accept the shortcut `locate --source source.json --quote '...'`.

For a more precise selector, use `--request -` and pipe the existing selection JSON. This works with either `--file` or `--source`; a request file still works. For example:

```sh
python3 /absolute/investigation/scripts/source.py locate --file /project/input/report.json --request - <<'JSON'
{"format":"json","pointer":"/summary/count"}
JSON
```

For many quotations or several sources, one batch shares acquisition and the preview budget:

```sh
python3 /absolute/investigation/scripts/source.py locate --batch - --store /project/evidence <<'JSON'
[
  {"file":"/project/input/deployment.yaml","quote":"initialDelaySeconds: 5","claim_id":"probe-delay"},
  {"file":"/project/input/pod.log","quote":"cache warm-up complete","claim_id":"warm-up"},
  {"file":"/project/input/pod.log","quote":"OOMKilled","context":1}
]
JSON
```

Each item supplies exactly one `file` (current local bytes) or `source` (an existing `source.json`), followed by the same selector fields used for a single lookup. CSV/JSON/JSONL selectors work here too. Relative paths in `--batch FILE` are relative to that JSON file; stdin paths are relative to the invoking working directory. No directory scan or source inference occurs. Omit `--store` when saving snapshots adds no value.

A batch accepts up to 100 items and 2 MiB of request JSON. Each distinct local file is read once, so its quotations share one consumed version; with `--store`, it is also captured only once. Existing captured views are reused after their integrity check. This does not make several sources one atomic snapshot. `--max-bytes` still bounds a file/view; `--max-total-bytes` bounds the unique selected views retained by the batch and defaults to `--max-bytes` (64 MiB). A request that exceeds the remaining budget reports an error instead of returning partial file contents. `--max-chars` is shared across all excerpt/context previews, not multiplied by the number of requests.

Results retain input order and include `index`, per-item status and source identity. Missing or ambiguous matches keep the overall status unresolved; input/acquisition errors return exit 2 while preserving other results. Multiple matches and whitespace candidates never become exact bindings automatically. A saved batch result can be passed directly to `verify`; any unresolved member keeps verification unresolved. `verify --bindings -` also accepts piped JSON. A one-off selection request is bounded to 64 KiB whether supplied as a file or stdin.

## Capture and reuse

For a known URL, keep the existing acquisition interface:

```sh
python3 /absolute/investigation/scripts/fetch.py 'https://example.org/source' --output-dir /absolute/evidence --max-chars 8000
```

The result identifies a fresh directory containing `body.bin`, `metadata.json` and, when available, `extracted.txt`. Metadata preserves requested/final URLs, redirect hops, HTTP status and headers, UTC retrieval time, SHA-256 and `response_body_complete`. Failed transfers retain their acquired bytes; HTTP error bodies are preserved with failure status. Retrieval time is not publication time, and a hash does not establish source authority.

`--extract auto` handles declared HTML, JSON and text; use `none` for bytes only or an explicit type/encoding when the server metadata is known to be wrong. `--max-chars` bounds only the preview. `--max-extract-bytes` bounds extraction memory; `--max-body-bytes` defaults to 64 MiB and an exceeded limit preserves a prefix with failure. HTML extraction omits scripts/styles/templates and normalizes whitespace; it does not establish rendered or JavaScript content. JSON extraction validates without reformatting. The response body remains the original acquired representation.

`fetch.py` does not search, log in, execute browsers or install dependencies. It sends no authorization, cookies or custom headers, does not inherit proxy credentials, and rejects credential-bearing URLs/redirects. Its timeout spans redirects and body transfer, although OS DNS lookup may outlast socket timeouts. For unsupported access, use an appropriate authorized route or state the limitation.

For a local source or an existing fetch record:

```sh
python3 /absolute/investigation/scripts/source.py capture /absolute/input/log.txt --store /absolute/evidence
python3 /absolute/investigation/scripts/source.py import-fetch /absolute/evidence/source-OLD/metadata.json --store /absolute/evidence
```

Both return a `source` path to a new `source.json`; they never overwrite an earlier version. Local capture copies the consumed bytes and checks file identity before/after the read; this is not an atomic filesystem snapshot. The default byte budget is 64 MiB. An oversized file fails rather than silently losing its tail. `capture --prefix-bytes N` explicitly captures a prefix and records its scope/completeness; choose a size that does not split a UTF-8 character if text selection will follow. An observed change during capture fails and requires a new read.

`import-fetch` performs no network request. It checks the saved body against its recorded size/hash, copies the body and metadata, and regenerates a derived view through the existing fetch extractor. It does not trust an independently edited `extracted.txt`. The derived view has its own hash, raw-parent hash, transformation description and extractor hash. An HTTP error or partial response remains marked as such, even when capture succeeds. `--max-view-bytes` defaults to 5 MiB; extraction failure leaves the raw snapshot available and is recorded separately.

## Source and location identity

`source.json` version 1 records:

- `source_id`, `captured_at`, and `origin`: original path and resolved path, or request/final URLs and original retrieval metadata.
- `acquisition`: completeness, scope and any HTTP/acquisition/extraction errors.
- `objects.raw`: relative snapshot path, byte count, SHA-256 and representation.
- Optional `objects.text`: a separate derived-view path/hash and its transformation/provenance.

All positions are interpreted in a particular hash-identified snapshot/view. A locator never silently follows an edited origin or maps an old line number into a new file. Raw and derived objects are both checked when reading a derived view. A hash mismatch fails; the helper does not update the recorded hash to accept changed content.

## Locate selected content

For an already captured source, an existing JSON request file remains supported:

```sh
python3 /absolute/investigation/scripts/source.py locate --source /absolute/evidence/source-ID/source.json --request /absolute/selection.json
```

`format` defaults to `text`; `view` defaults to `text` when a derived view exists, otherwise `raw`. Override with `"view":"raw"` when the actual response bytes are decodable text. `encoding` accepts `utf-8-sig` (default; consumes an initial BOM) or `utf-8`. `context` defaults to two neighboring lines/records and accepts 0–20. An optional `claim_id` associates the result with an existing conclusion; it does not assert that the conclusion is supported.

### Text and ordinary logs

```json
{"quote":"initialDelaySeconds: 5","context":2,"claim_id":"probe-timing"}
```

Exact search returns all matches up to `--max-matches` (default 20, maximum 100). Multiple occurrences produce `ambiguous` and no binding; narrow the range after inspecting the contexts:

```json
{"quote":"initialDelaySeconds: 5","range":{"start_line":45,"end_line":52}}
```

For direct selection, omit `quote` and supply `range`. Lines and columns are one-based; end columns are exclusive. Without columns the selected lines include their original line endings. `offsets:[start,end]` is an alternative zero-based, end-exclusive Unicode-character span, not a byte span. Returned `byte_start`/`byte_end` additionally locate the original encoded fragment within the selected hash-identified view, accounting for a consumed UTF-8 BOM. They are zero-based and end-exclusive; byte-range input is not needed. CR, LF and CRLF are retained. An explicit range and offsets cannot be combined.

`"match":"whitespace"` allows runs of ASCII space/tab/CR/LF to differ during discovery. It returns original fragments as `normalized_candidate`, even for one match, and creates no exact binding. Select the relevant original range to retain its actual text. No case folding, punctuation rewriting, Unicode compatibility normalization or unit conversion is performed. Empty/whitespace-only searches are rejected; quotes are limited to 4096 characters.

Plain log timestamps are not unique record IDs. Use explicit line/character locations; the helper does not infer multiline event boundaries or causal relationships.

### JSON and JSONL

```json
{"format":"json","pointer":"/root_causes/2/evidence/0"}
```

JSON Pointer uses an empty string for the root, `~1` for `/`, `~0` for `~` and zero-based array indices. Missing is distinct from null. Duplicate object keys, nonstandard numeric constants and malformed pointers fail. Decimal values retain precision. Returned `parsed_json_value` text is serialization of a selected value, **not a verbatim span of the original JSON**. For a literal quote of JSON syntax, use text selection. Context is the selected value's containing object/array, bounded by the preview budget.

```json
{"format":"jsonl","line":12,"pointer":"/message","context":1}
```

JSONL `line` is a one-based physical line; the selected line is parsed strictly. Neighboring raw lines are context, not proof that the rest of the log is valid JSONL. Blank or malformed selected lines fail. Equal timestamps do not merge records.

### CSV and TSV

```json
{"format":"csv","row":3,"column":2,"delimiter":",","context":1}
```

Rows and columns are one-based. Rows count logical records, including any header; no header or column meaning is inferred. Omit `column` to select the whole row. `format:"tsv"` defaults the delimiter to a tab. `delimiter` must be one character. Parsing uses standard double-quote escaping and is strict about malformed quoted fields.

An explicit key may replace `row`:

```json
{"format":"csv","key":{"column":1,"equals":"payment-api"},"column":3}
```

Keys compare decoded field strings exactly; header rows participate unless excluded by choosing a known row instead. `row` and `key` cannot be combined. Repeated keys return `ambiguous` with no binding, even when the returned match count is limited. Key selection scans the captured table to establish uniqueness, retains only the bounded candidates, and pins a uniquely selected record's physical/logical location in the binding. Missing key columns do not match; a matched record missing the selected value column is an error.

The locator contains logical `row` plus physical `line_start`/`line_end`, so a quoted field with embedded newlines remains one record. `parsed_csv_value` is JSON serialization of a field string or row array; it retains empty fields and decoded embedded newlines, but is not the original quoting syntax. Context supplies the surrounding raw records. Row selection parses only the necessary records; it makes no claim about a later unvisited tail. Key selection needs the complete captured table to detect duplicate keys.

## Results, bounded output and replay

`locate` returns `status`, source/view identity, scoped search metadata, selected locations, excerpt/context previews, and `semantic_support:"not_evaluated"`. Keep these limitations separate:

- `source.acquisition.complete`: whether the acquired source is complete in its recorded scope.
- `search.complete` and `search.scope`: whether discovery completed within the selected snapshot range. Reaching a match limit makes the match count a lower bound.
- `preview.truncated`: whether returned text was shortened. `--max-chars` defaults to 12000 characters shared by excerpt/context fields; framing, hashes and locators are additional bounded metadata. A shorter preview does not change the full selected-content hash.

Only `resolved` produces a `binding`. It stores source manifest and raw/view hashes, a replayable selector, selected-content hash, representation and optional claim ID. A located quotation only establishes its source in that version. Models still judge whether the surrounding evidence supports the conclusion's scope and causal strength.

If reuse warrants saving a record, save the complete `locate` JSON output as-is, or just its `binding`, and later call:

```sh
python3 /absolute/investigation/scripts/source.py verify --bindings /absolute/saved-location.json
```

The file can also contain an array of up to 100 results/bindings. Verification replays locations and checks the original manifest, raw/view hashes and selected-content hashes. Ambiguous/normalized discovery output has no binding and cannot verify as an exact citation. Changes to a snapshot or source manifest fail; they do not silently update old evidence. A new read/capture produces a new version.

Default verification concerns the captured historical version. `--check-origin` additionally compares a full local file capture with its current origin. A changed/missing/uncheckable origin yields `historical_only` while retaining the valid old snapshot. HTTP freshness requires another fetch, and a captured byte prefix is not a full-file fingerprint; those origins report `not_checked` when asked. No network refresh or source mutation occurs during verification.

Exit codes: 0 means the requested capture or unique location resolved **in its stated scope**; 1 means missing/ambiguous/candidate or unresolved/current-version checks; 2 means an input, acquisition or integrity error. A successful capture of an HTTP error body does not make it a successful HTTP response. No exit code means that a conclusion is true or the user's whole task is complete.

## Other formats

Direct PDF, OCR, image and Office interpretation is outside this helper. Use the workspace skill's located extraction or another suitable reader, retain the original hash, page/object location, method and limitations, and treat its text as a derived representation. A source capture can retain binary bytes, but that does not make them text evidence. PDF extraction may miss visible content; this tool cannot turn extracted/OCR text into a guarantee of verbatim agreement with the original page. Use visual inspection when that distinction affects the conclusion.
