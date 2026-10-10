# Editing across model turns

These examples show general file operations with an observation between calls. Each editing call returns control to the model, which can inspect the result and decide what change is needed next. A tool result does not end the user's task. Simple work can use one call, and related changes can share a call; no fixed number of turns or uniform splitting rule applies.

The examples assume the shell starts in the task's working directory and `python3` is available. In every editing call, replace `/absolute/path/from/skill-catalog/file-editing/scripts/edit.py` with the helper path resolved from the loaded SKILL.md. Each call sets its own `edit` variable; no shell variable persists from a previous call. To try the examples without touching task files, use a temporary directory as the shell call's working directory.

Output descriptions are illustrative. Inspect the actual JSON, stderr, and exit status returned by the tool. They are not records of a model run or a task-specific implementation recipe.

## Continue from a saved file

The following exchanges illustrate creation, insertion, and replacement using a small text file. The sample paragraphs stand for content the task actually requires; they are not prescribed milestones for building other artifacts.

Action: save the initial requested contents.

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" create document.txt <<'TEXT'
Section
Original paragraph.
TEXT
```

Observation: a successful result reports `operation=create`, `changed=true`, the saved path, and a numbered excerpt. If the target already exists, creation fails without overwriting it; read the existing file before deciding how to proceed.

Next decision: use the saved contents as the basis for any further requested change. For example, if another paragraph belongs before the existing one, its unique text is a usable anchor.

Action: insert that paragraph without reconstructing the file.

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" insert document.txt --anchor 'Original paragraph.' --position before <<'TEXT'
Additional paragraph.
TEXT
```

Observation: the result identifies the insertion location and shows nearby saved text. Inspect a targeted range if the excerpt is truncated or leaves the result uncertain.

Next decision: an insertion has been saved. If the existing paragraph also needs revision, replace its current exact text. If the requested change is already complete, no additional edit is needed.

Action: perform that replacement when it is required.

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" replace document.txt --old 'Original paragraph.' --new 'Updated paragraph.'
```

Observation: the result shows the changed region. A read of the saved file now yields:

```text
Section
Additional paragraph.
Updated paragraph.
```

The file survives interruption after each successful write. A subsequent turn reads it and continues from those contents, rather than regenerating completed changes from memory. The operation feedback establishes what was saved; task-level correctness may require other checks appropriate to the file and the requested outcome.

## Refine an ambiguous match

For a runnable example, first create a file containing a repeated value:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" create values.txt <<'TEXT'
First: value
Second: value
TEXT
```

Suppose the requested change affects only the first entry. A match on the repeated word is insufficient:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" replace values.txt --old 'value' --new 'updated'
```

Observation: a nonzero exit status and an ambiguous-match error; the file is unchanged. The word appears in both entries.

Next decision: inspect the relevant current contents before narrowing the match. Do not suppress the failure or reconstruct the entire file from a remembered copy.

```sh
nl -ba values.txt
```

Observation: the two numbered lines show that the complete first entry identifies the intended location.

Action: use that unique exact match.

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" replace values.txt --old 'First: value' --new 'First: updated'
```

Observation: the successful result shows the changed first entry and the preserved second entry. If the current contents no longer contain a unique match, the operation fails without modifying the file; inspect the new contents and choose a matching operation from them.
