# Inspect saved file versions

The runtime supplies the absolute history repository path in the system context. Pass that path to `scripts/history.py`; do not infer it from the current directory or use the project's own `.git`. Resolve the helper's absolute path from the file-editing skill. These commands use the existing shell and do not create checkpoints or change files.

| Command after `--repository REPO` | Result |
| --- | --- |
| `status` | Recording state, latest checkpoint, errors and exclusions. |
| `log --path RELATIVE_PATH --limit 10` | Recent checkpoints affecting a workspace-relative path. Omit `--path` for workspace history. |
| `diff BEFORE AFTER --path RELATIVE_PATH` | Changes between two checkpoints. Omit `--path` for all recorded changes. |
| `show REVISION RELATIVE_PATH` | The exact saved contents on stdout, including binary contents. |

Take checkpoint IDs from `log` or `status`. Paths are relative to the recorded workspace. For example, after selecting two actual IDs:

```sh
python3 /absolute/skill/scripts/history.py --repository /absolute/history.git diff BEFORE AFTER --path analysis.py
python3 /absolute/skill/scripts/history.py --repository /absolute/history.git show BEFORE analysis.py > /path/to/new-comparison-file.py
```

In these examples, replace `BEFORE` and `AFTER` with the selected checkpoint IDs. Choose a new destination for extracted content and check the command's exit status before using it. Compare the earlier content with the current file, then retain the useful current work while repairing the affected region. Restoring a file does not restore a running process, generated external state, or the conversation, and it does not establish that the old version was correct.

The runtime records ordinary files and symbolic links themselves without following links outside the workspace. It excludes its own state and logs, Git metadata, dependency directories and caches; `status` reports the configured exclusions. Empty directories and special files are not recorded. A task output is not excluded merely because the project's `.gitignore` lists it.

The initial checkpoint preserves the starting contents. Later checkpoints are created from observed changes, with a final scan when monitoring closes. Events may be grouped, and a rapidly overwritten or deleted intermediate version may never reach a checkpoint. A failed or stopped monitor is not evidence that later edits were saved. Use `status` and actual checkpoint contents to establish what is available.
