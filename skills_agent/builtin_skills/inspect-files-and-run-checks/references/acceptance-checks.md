# File, text and JSON check commands

`scripts/check.py` compares saved data with conditions supplied on the command line. It does not infer task requirements or validate overall completion. Resolve the script's absolute path from the loaded skill directory. The examples are independent shell calls, so each defines its own variable.

## File existence and size

```sh
check='/absolute/skill/scripts/check.py'
python3 "$check" file result.json report.txt --min-bytes 1
```

Each path must be a regular file. `--min-bytes` is optional and must be a positive integer; without it, an empty regular file passes. The result contains a `regular_file` condition for each path and a `minimum_bytes` condition for existing files when requested. It does not parse file contents.

## Literal text

```sh
check='/absolute/skill/scripts/check.py'
python3 "$check" text result.txt --contains 'required literal text' --absent 'obsolete literal text'
```

At least one `--contains` or `--absent` is required. Flags may be repeated, and their values must be nonempty. Matching is literal and case-sensitive, without whitespace normalization. The helper reads the complete UTF-8 file into memory and rejects NUL bytes. A successful `contains` condition reports the first matching line and column, both one-based. These are text matches, not semantic or executable checks.

## JSON values and paths

```sh
check='/absolute/skill/scripts/check.py'
python3 "$check" json result.json --pointer /summary/processed --equals-json '12'
```

`--equals-json` is required and must contain valid JSON. The default empty pointer selects the entire document. RFC 6901 pointers use `/` between tokens, `~1` for a slash inside a key, `~0` for a tilde, and zero-based array indexes. For example, `/items/0/a~1b` selects key `a/b` in the first item. A missing key or array index is a failed condition, distinct from a present `null` value.

Shell quoting must preserve JSON string quotes:

```sh
check='/absolute/skill/scripts/check.py'
python3 "$check" json result.json --pointer /status --equals-json '"ready"'
```

Numbers compare by value, so `1` equals `1.0`; boolean `true` does not equal either. Objects must have the same keys and values. Array order matters. Nonstandard JSON constants such as `NaN` or `Infinity` are rejected. The helper reads the complete JSON file into memory.

## Result and exit status

The command prints one JSON object with `kind: "check"`, resolved source paths and `observations`. The observations contain:

- `checks`: individual conditions with `passed`, and the relevant expected value, actual value, path or missing-key diagnostic.
- `passed`: whether all supplied conditions passed.
- `scope`: a reminder that only the supplied conditions were checked.
- `error`: present for collection or input errors.

Long expected or actual values are returned as bounded excerpts with `truncated: true`; the comparison still uses the complete values. Exit status is 0 for all conditions passing, 1 for a mismatch, or 2 for an input or collection error. Failures also print a short diagnostic to stderr. The script does not edit the inspected files or generate expected values.
