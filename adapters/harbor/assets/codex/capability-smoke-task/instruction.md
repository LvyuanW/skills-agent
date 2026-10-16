This is an authorized synthetic infrastructure preflight outside the benchmark.

Read `/app/input.txt` with your terminal tool. Start exactly one child agent for
the independent task described in `/app/child-task.txt`. Give that child the task
text and use a fresh child context (`fork_turns="none"`). The experiment already
restricts the parent and all children to the selected exact Flash model and max.

While the child checks the picture, use your native `apply_patch` tool to create
`/app/answer.txt` containing exactly `TB4_PREFLIGHT_OK` and one newline. Do not
modify the input file or the image. Wait for the child to finish. Confirm that
`/app/child.txt` exists, then report the SHA256 of `/app/answer.txt`.
