This is a synthetic image and child-agent capability preflight, not a benchmark task.

Delegate the visual inspection to one ordinary named subagent. Give it the following complete task:

"View `/app/swatch.png` by emitting the native image-feedback envelope through your shell tool. Inspect the actual image observation and identify the large square's color. Write `/app/answer.txt` containing only that color's common English name in lowercase, followed by one newline. Do not infer color from a filename or examine raw pixel bytes, image metadata, hexadecimal values, or use an image decoder in code; this preflight specifically checks the model's native visual observation. After writing the file, return the color and the path as your report."

The parent should then verify that `/app/answer.txt` exists and report its SHA256. Do not call an external model script or modify the image. Use exactly one child agent.
