---
name: images
description: >-
  Must use whenever attaching any image to a model request, including existing
  files, downloaded images, generated images, screenshots and rendered pages.
  Prepare at most ten images per request, each at most 1 MB; compose needed
  images when there are more than ten, and retain URLs for later inspection.
---

Use this skill for every image attachment. **Declare images in the shell tool's `images` parameter.** Saving a file, printing its path or printing helper metadata does not replace that declaration. [scripts/images.py](scripts/images.py) prepares bounded files and composition metadata; it is not a second attachment step. Resolve its absolute path from this SKILL.md. Python 3.11 or newer and Pillow are required for preparation, composition and in-memory generation.

`images` is an array of `{ "path": "/absolute/host/image.png", "label": "optional description", "source_url": "optional original URL" }`. `path` also accepts a local `file://` URL, including a saved session snapshot URL. It refers to the **host filesystem**, not a container-only path; download HTTP(S) sources first. This parameter accepts images, not PDF or arbitrary file attachments.

Save generated images, screenshots and prepared copies in the **Image output directory** supplied in the system context, unless the user specified a delivery location. By default, the runtime keeps it at `~/.skills-agent/workspaces/<workspace-id>/images/<session-name>/`, alongside `sessions/`; child agents have their own subdirectory. Shell and sandbox commands receive its absolute path as `SKILLS_AGENT_IMAGE_DIR`. Shell command text can expand that variable, but `images.path` does not expand variables: supply the actual absolute path from context. In the examples, `/absolute/image-output` stands for that directory. Keep source images unchanged; scripts remain in the task workspace.

The runtime reads, validates and snapshots declared files **after the command finishes**. For a background `task`, it registers the declarations at launch and attaches the resulting images with the completion result, not the startup receipt. The files may therefore be created by the command. Explicit `images`, including `[]`, takes precedence over legacy stdout feedback. Stdout can contain progress logs, multiple JSON results or any other text without changing the declared images. Use `images: []` when preparing files without sending them.

Each model request may contain **at most 10 images in total**, with **each encoded image file at most 1,000,000 bytes (1 MB)** before base64 encoding. Count all tool results and user attachments in that request; separate tool calls do not increase the allowance. Select only images needed for the current decision. **If more than ten are needed together, compose them into one or several labeled images within the same limits.**

**Do not produce new image files larger than 1 MB.** Generate pixels in memory and use `save_bounded` to verify encoded bytes before writing. For existing oversized images, create a compressed copy with `prepare`, preserving the original. The helpers try bounded compression and proportional resizing, write only a verified final encoding, and fail explicitly if they cannot fit. Prefer JPEG/WebP for photos and PNG for text/diagrams where useful; inspect whether important detail remains readable.

## Existing or historical images

For a file already within the limit, declare it directly. `true` is sufficient when there is no preparation to run:

```json
{
  "name": "Inspect earlier image",
  "command": "true",
  "run_in_background": false,
  "images": [{"path": "file:///absolute/session/attachments/snapshot.png", "label": "Earlier result"}]
}
```

The runtime supplies the pixels and immutable snapshot URL in the same model request, retaining an optional original `source_url`. Save these references with visual findings. Every assistant response consumes that round's images, including a response that requests more tools; later requests retain their references, not their pixels. To inspect an older image again, declare its saved snapshot URL/path in a new `images` array. No upload or public hosting is required.

## Generate an image while printing logs

Use `edit_file` to save or adapt this script in the task workspace, for example as `/absolute/workspace/draw.py`, then run it through shell:

```python
import importlib.util
import json
from PIL import Image, ImageDraw

spec = importlib.util.spec_from_file_location("images", "/absolute/builtin_skills/images/scripts/images.py")
images = importlib.util.module_from_spec(spec)
spec.loader.exec_module(images)
canvas = Image.new("RGB", (1200, 700), "white")
ImageDraw.Draw(canvas).rectangle((100, 100, 1100, 600), outline="navy", width=5)
metadata = images.save_bounded(canvas, "/absolute/image-output/generated.png", label="Generated diagram")
print("Drawing complete")
print(json.dumps(metadata, ensure_ascii=False))
```

Run it and declare the output once; do not invoke `images.py attach` afterward:

```json
{
  "name": "Draw and inspect diagram",
  "command": "python3 /absolute/workspace/draw.py",
  "run_in_background": false,
  "images": [{"path": "/absolute/image-output/generated.png", "label": "Generated diagram"}]
}
```

## Prepare several files, then declare the selected images

`prepare` accepts a local path or `file://` URL. It preserves the original, applies EXIF orientation and uses the first frame of animated inputs. Output extension selects JPEG, PNG or WebP. Keep source/sent dimensions, scale and byte counts when interpreting coordinates.

```json
{
  "name": "Prepare and inspect two views",
  "command": "python3 /absolute/builtin_skills/images/scripts/images.py prepare /absolute/workspace/original-a.png --output /absolute/image-output/view-a.jpg && python3 /absolute/builtin_skills/images/scripts/images.py prepare /absolute/workspace/original-b.png --output /absolute/image-output/view-b.jpg",
  "run_in_background": false,
  "images": [
    {"path": "/absolute/image-output/view-a.jpg", "label": "View A", "source_url": "file:///absolute/workspace/original-a.png"},
    {"path": "/absolute/image-output/view-b.jpg", "label": "View B", "source_url": "file:///absolute/workspace/original-b.png"}
  ]
}
```

Both preparation commands may print their own metadata. The `images` array alone selects the files to send.

For more than ten needed sources, compose a numbered grid and declare its single output:

```json
{
  "name": "Inspect eleven views together",
  "command": "python3 /absolute/builtin_skills/images/scripts/images.py compose /absolute/workspace/view-1.png /absolute/workspace/view-2.png /absolute/workspace/view-3.png /absolute/workspace/view-4.png /absolute/workspace/view-5.png /absolute/workspace/view-6.png /absolute/workspace/view-7.png /absolute/workspace/view-8.png /absolute/workspace/view-9.png /absolute/workspace/view-10.png /absolute/workspace/view-11.png --output /absolute/image-output/overview.jpg --columns 4",
  "run_in_background": false,
  "images": [{"path": "/absolute/image-output/overview.jpg", "label": "Eleven numbered views"}]
}
```

Composition metadata maps panel numbers to source URLs, dimensions, panel/image boxes and coordinate scales. Boxes are `[left, top, right, bottom]` pixel edges. Subtract the image-box origin and multiply by `scale_to_source` to recover EXIF-oriented source coordinates. A grid is an overview: if details are too small, crop the source and declare that view next.

For HTTP(S) images, use the [investigation fetch helper](../investigation/scripts/fetch.py), check successful status and `response_body_complete`, then prepare its actual returned `body_path`. Set the image declaration's `source_url` to the original HTTP(S) URL. The helpers do not upload images or install dependencies.

## Render or wait for an image

For local HTML/SVG, the [feedback render helper](../feedback/SKILL.md) creates a bounded screenshot; declare that output in the same shell call:

```json
{
  "name": "Render and inspect page",
  "command": "python3 /absolute/builtin_skills/feedback/scripts/feedback.py render /absolute/workspace/page.html --output /absolute/image-output/capture.png --width 500 --height 400",
  "run_in_background": false,
  "images": [{"path": "/absolute/image-output/capture.png", "label": "Page capture"}]
}
```

For a long computation, read the [background-tasks skill](../background-tasks/SKILL.md), then register the future image with the job:

```json
{
  "name": "Compute and inspect plot",
  "command": "python3 -u /absolute/workspace/analyze.py",
  "run_in_background": true,
  "background_lifecycle": "task",
  "timeout_seconds": 7200,
  "images": [{"path": "/absolute/image-output/plot.png", "label": "Final analysis plot"}]
}
```

The script must save its final bounded image before exiting. Wait for the actual completion result and check image-acquisition errors; launch success does not prove that the image exists or was attached. Saving or attaching an image also does not establish visual correctness: inspect the supplied pixels before making visual claims.
