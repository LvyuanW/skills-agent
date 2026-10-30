---
name: images
description: >-
  Prepare and attach images for model inspection. Required whenever a model
  request includes an image, whether existing, downloaded, generated, or rendered.
  Keep each image within 1 MB and each request within ten images; preserve source
  URLs. Does not handle PDF or other file attachments or provide an image-generation model.
---

Use this skill whenever you attach an image to a model request, including a screenshot, rendered page, generated image, or existing file. Use its preparation helpers when those files need compression or composition. A task that merely mentions images does not require this workflow. This skill does not read PDFs as documents or provide an image-generation model.

**Declare each attachment in the shell tool's `images` parameter.** Saving a file or printing its path or metadata does not attach it. [scripts/images.py](scripts/images.py) prepares files within the limits and supplies composition metadata. Running it is not a separate attachment step. Resolve its absolute path from this SKILL.md. Preparation, composition, and generation in memory require Python 3.11 or newer and Pillow.

`images` is an array of `{ "path": "/absolute/host/image.png", "label": "optional description", "source_url": "optional original URL" }`. The `path` must point to a file on the host. It may be an absolute path or a local `file://` URL, including a saved session snapshot URL. A container-only path cannot be read from the host. Download HTTP(S) sources before declaring them. This parameter accepts image files; it does not accept PDFs or other file attachments.

Save generated images, screenshots and prepared copies in the **Image output directory** supplied in the system context, unless the user specified a delivery location. By default, the runtime keeps it at `~/.skills-agent/workspaces/<workspace-id>/images/<session-name>/`, alongside `sessions/`; child agents have their own subdirectory. Shell and sandbox commands receive its absolute path as `SKILLS_AGENT_IMAGE_DIR`. Shell command text can expand that variable, but `images.path` does not expand variables: supply the actual absolute path from context. In the examples, `/absolute/image-output` stands for that directory. Keep source images unchanged; scripts remain in the task workspace.

The runtime reads, validates, and snapshots the declared files **after the command finishes**, so the command may create them. For a background `task`, the runtime registers the paths at launch and attaches images to the completion result. The startup receipt has no image payload.

An explicit `images` field takes precedence over legacy stdout feedback, including when it is `[]`. Progress logs, JSON results, or other stdout text do not change those declarations. Use `images: []` when preparing files without sending them.

Each model request may contain **at most 10 images in total**. Each encoded image file must be **at most 1,000,000 bytes (1 MB)** before base64 encoding. Count both tool results and user attachments in the request. Splitting attachments across tool calls does not increase the limit.

Select images that help with the current decision. **If more than ten source images are needed together, compose them into one or more labeled images that satisfy the same limits.**

**Do not create new image files larger than 1 MB.** Generate pixels in memory and use `save_bounded` to check the encoded size before writing. If an existing image is oversized, use `prepare` to make a compressed copy and preserve the original.

The helpers try compression and proportional resizing. They write the file only after checking its encoded size, and report failure if it cannot fit. JPEG or WebP often suit photos; PNG can suit text and diagrams. Check that the chosen format and size preserve the details you need.

## Existing or historical images

Declare a file directly if it already meets the limits. Use `true` as the command when no preparation is needed:

```json
{
  "name": "Inspect earlier image",
  "command": "true",
  "run_in_background": false,
  "images": [{"path": "file:///absolute/session/attachments/snapshot.png", "label": "Earlier result"}]
}
```

The runtime supplies the pixels and an immutable snapshot URL in the same model request. It also keeps the original `source_url` when provided. Save these references with the visual findings so the image can be retrieved later.

Images are visible for one assistant response, including a response that requests more tools. Later requests retain the references but not the pixels. To inspect an older image again, declare its saved snapshot URL or path in a new `images` array. No upload or public hosting is required.

## Generate an image while printing logs

Save or adapt this script in the task workspace, for example as `/absolute/workspace/draw.py`, then run it through shell. Use `edit_file` when the script is part of the project or deliverable. A temporary analysis script may be created and run through shell within the user's allowed write locations:

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

`prepare` accepts a local path or `file://` URL. It preserves the original, applies EXIF orientation, and uses the first frame of an animated image. The output extension selects JPEG, PNG, or WebP. Retain the original and sent dimensions, scale, and byte counts when interpreting coordinates.

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

Composition metadata records each panel's source URL, dimensions, panel and image boxes, and coordinate scale. Boxes use `[left, top, right, bottom]` pixel edges. To recover EXIF-oriented source coordinates, subtract the image-box origin and multiply by `scale_to_source`. If a grid makes important details too small, crop the source and attach that view next.

For an HTTP(S) image, use the [investigation fetch helper](../investigation/scripts/fetch.py). Check the response status and `response_body_complete`, then prepare the file at the returned `body_path`. Set `source_url` to the original HTTP(S) URL. These helpers do not upload images or install dependencies.

## Render or wait for an image

To inspect local HTML or SVG, use the [check-results render helper](../check-results/SKILL.md) to create a screenshot within the size limit. Declare the output in the same shell call:

```json
{
  "name": "Render and inspect page",
  "command": "python3 /absolute/builtin_skills/check-results/scripts/feedback.py render /absolute/workspace/page.html --output /absolute/image-output/capture.png --width 500 --height 400",
  "run_in_background": false,
  "images": [{"path": "/absolute/image-output/capture.png", "label": "Page capture"}]
}
```

If a computation needs to continue after its shell call returns, read the [background-tasks skill](../background-tasks/SKILL.md). Register the future image with the job:

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

The script must save its final image within the size limit before exiting. Read the actual completion result and check for image acquisition errors. A successful launch does not prove that the image exists or was attached. Inspect the supplied pixels before making claims about appearance; saving or attaching the file alone does not verify it.
