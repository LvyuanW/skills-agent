# Editing across model turns

Read these examples when you need to continue from a saved edit or resolve an ambiguous match. Each call returns control to the model so it can inspect the result and choose the next action. A tool result does not end the user's task. Simple work can use one call, and related changes can share a call. You do not need a fixed number of turns or a separate call for every small change.

The examples assume that the shell starts in the task's working directory and `python3` is available. Replace `/absolute/path/from/skill-catalog/file-editing/scripts/edit.py` with the helper's path, resolved from the loaded SKILL.md. Each call sets its own `edit` variable because shell variables do not persist between calls. To try an example without changing task files, run it with a temporary directory as the shell working directory.

The output descriptions illustrate what to expect. Inspect the actual JSON, stderr and exit status returned by the tool. These examples are not records of a model run or a required implementation plan.

## Continue from a saved file

This example creates a small text file, inserts a paragraph and replaces another paragraph. The sample text stands for content a task actually requires. These steps are not required milestones for other artifacts.

Action: save the initial requested contents.

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" create document.txt <<'TEXT'
Section
Original paragraph.
TEXT
```

Observation: success reports `operation=create`, `changed=true`, the saved path and a numbered excerpt. If the target already exists, creation fails without overwriting it. Read that file before deciding how to proceed.

Next decision: base further requested changes on the saved contents. To add a paragraph before the existing one, use the existing paragraph's unique text as the anchor.

Action: insert that paragraph without reconstructing the file.

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" insert document.txt --anchor 'Original paragraph.' --position before <<'TEXT'
Additional paragraph.
TEXT
```

Observation: the result identifies the insertion location and shows nearby saved text. Inspect a targeted range if the excerpt is truncated or leaves the result uncertain.

Next decision: the insertion is saved. If the existing paragraph also needs revision, replace its current exact text. Stop editing if the requested change is already complete.

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

After each successful write, the file is saved even if the task is interrupted. A later turn can read it and continue from the saved contents. It should not rebuild completed changes from memory. The operation feedback shows what was saved. Whether that content meets the task may require other checks suited to the file and the requested outcome.

## Refine an ambiguous match

For a runnable example, first create a file containing a repeated value:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" create values.txt <<'TEXT'
First: value
Second: value
TEXT
```

Suppose the user wants to change only the first entry. Matching just the repeated word is ambiguous:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" replace values.txt --old 'value' --new 'updated'
```

Observation: the command returns a nonzero exit status and an ambiguous-match error. The file is unchanged because the word appears in both entries.

Next decision: read the relevant current contents and choose a more specific match. Do not suppress the error or rebuild the file from memory.

```sh
nl -ba values.txt
```

Observation: the two numbered lines show that the complete first entry is a unique match for the intended location.

Action: use that unique exact match.

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" replace values.txt --old 'First: value' --new 'First: updated'
```

Observation: success shows the changed first entry and the unchanged second entry. If the file no longer contains a unique match, the operation fails without modifying it. Read the current contents and choose the edit from what is actually saved.
