# images 技能提示词版本时间线

当前源位置：`skills_agent/builtin_skills/images/SKILL.md`。

## v1 · 2026-10-16 · 统一图片附加入口、大小限制与可回查地址

来源：当前工作区（未提交）。

- 发现的问题：历史原图持续进入后续请求，base64 请求体可能超限；已有图片与新生成图片缺少统一准备规范和发送地址记录。
- 本版本目标：凡附图必须使用 images 技能；每次请求最多5张，每张最多1,000,000字节；新图生成时控制大小，已有超大图由 Agent 压缩副本后附加；超过5张需要同时检查时合成编号拼图；保留原件、原始来源及发送快照 URL，并提供压缩、合图、生成、下载及附图命令。
- 本版本效果：新增独立 images.py，使用 Pillow 在内存编码合限后保存；prepare 压缩副本、compose 合成编号概览并保留来源与坐标映射、attach 复用 feedback 验证，save_bounded 支持新图生成。历史图片按引用重读。脚本定向测试与独立运行验收通过，实际模型持续遵循与任务图片质量仍需观察。

### 完整提示词快照

````markdown
---
name: images
description: >-
  Must use whenever attaching any image to a model request, including existing
  files, downloaded images, generated images, screenshots and rendered pages.
  Prepare at most five images per request, each at most 1 MB; compose needed
  images when there are more than five, and retain URLs for later inspection.
---

Use this skill for every image attachment. An image saved on disk is not yet model input. [scripts/images.py](scripts/images.py) prepares, composes and attaches images through the existing feedback protocol. Resolve its absolute path from this SKILL.md. Use Python 3.11 or newer; `prepare`, `compose` and `save_bounded` need Pillow from the project's dependencies, while `attach` uses only the standard library. The script runs from any working directory without importing the skills-agent package.

Each model request may contain **at most 5 images in total**, with **each encoded image file at most 1,000,000 bytes (1 MB)** before base64 encoding. Count all tool results and user attachments in that request. Select only images needed for the current decision. **If more than five are needed together, compose them into one or several labeled images within these same limits.** Do not split tool calls to bypass the request limit.

**Do not produce new image files larger than 1 MB.** Choose dimensions, format and quality for this limit from the start. Generate pixels in memory and use `save_bounded` below to check encoded bytes before saving. For existing oversized images, the agent must create a compressed copy with `prepare`, preserving the original; do not ask the user to do this or merely retry the oversized original. These helpers try compression at original dimensions first, then finite proportional resizing steps, and fail explicitly if they cannot fit. They write only a verified, bounded final encoding. Prefer JPEG/WebP for photographs and PNG for text/diagrams where useful; inspect whether the resulting detail is adequate.

For an existing image, prepare a separate copy and attach it in the same call:

```sh
python3 /absolute/builtin_skills/images/scripts/images.py prepare /absolute/workspace/original.png --output /absolute/workspace/prepared.jpg
```

`SOURCE` accepts a local path or `file://` URL. Output extension selects JPEG (`.jpg`/`.jpeg`), PNG or WebP. EXIF orientation is applied; animated inputs use the first frame and report that selection. The result records source URLs, original/oriented dimensions, sent dimensions, byte count and scale. Keep this metadata with findings, especially when converting coordinates back to an original.

When several views are needed together, compose a numbered grid. Any number of source paths may be supplied subject to a useful grid size; the helper returns **one attachment**:

```sh
python3 /absolute/builtin_skills/images/scripts/images.py compose \
  /absolute/workspace/view-1.png /absolute/workspace/view-2.png \
  /absolute/workspace/view-3.png /absolute/workspace/view-4.png \
  /absolute/workspace/view-5.png /absolute/workspace/view-6.png \
  --output /absolute/workspace/overview.jpg --columns 3
```

Observations map every visible number to its source URL, original dimensions, panel box and image box in the final sent image. Boxes use `[left, top, right, bottom]` pixel edges. Subtract the image-box origin and multiply by `scale_to_source` for coordinates in the EXIF-oriented source. A grid is an overview: if details are too small, select a relevant panel or crop its original and attach that view next. Do not claim unreadable details were inspected.

For already compliant images, attach up to five paths without recompressing. This also supports recorded snapshot URLs:

```sh
python3 /absolute/builtin_skills/images/scripts/images.py attach /absolute/workspace/prepared.jpg 'file:///absolute/workspace/detail.png' --label 'Current evidence'
```

For a known HTTP(S) image URL, use the [investigation fetch helper](../investigation/scripts/fetch.py) first:

```sh
python3 /absolute/builtin_skills/investigation/scripts/fetch.py 'https://example.org/original.png' --output-dir /absolute/workspace/downloads --extract none
```

Check successful status and `response_body_complete`, then use its actual returned `body_path` as SOURCE (the following path illustrates that returned value):

```sh
python3 /absolute/builtin_skills/images/scripts/images.py prepare /absolute/workspace/downloads/fetch-result/body.bin --output /absolute/workspace/prepared.jpg --source-url 'https://example.org/original.png'
```

For `attach` or `compose`, optional `--source-url` repeats once per source in the same order. Local sources already have file URLs; no upload or public hosting is required. Do not put image bytes or base64 in text.

For new generated images, import the same helper and keep pixels in memory until the bounded save. Print only its returned envelope:

```sh
python3 - <<'PY'
import importlib.util
import json
from PIL import Image, ImageDraw

spec = importlib.util.spec_from_file_location("images", "/absolute/builtin_skills/images/scripts/images.py")
images = importlib.util.module_from_spec(spec)
spec.loader.exec_module(images)
canvas = Image.new("RGB", (1200, 700), "white")
draw = ImageDraw.Draw(canvas)
draw.rectangle((100, 100, 1100, 600), outline="navy", width=5)
result = images.save_bounded(canvas, "/absolute/workspace/generated.png", label="Generated diagram")
print(json.dumps(result, ensure_ascii=False))
PY
```

For local HTML/SVG, use the [feedback render helper](../feedback/SKILL.md) with a bounded viewport:

```sh
python3 /absolute/builtin_skills/feedback/scripts/feedback.py render /absolute/workspace/page.html --output /absolute/workspace/capture.png --width 500 --height 400
```

Rendering uses installed Chrome/Chromium and rejects oversized captures. Reduce viewport or render the relevant crop when needed. Other image tools must likewise respect the size limit; a tool that cannot bound its output needs an in-memory or bounded acquisition path before saving the new image.

Return the helper's **single JSON envelope as the entire shell stdout**, with no extra printed text or formatter. Successful `prepare` and `compose` already attach their output; do not attach it again. Each attachment includes its local URL. The runtime saves an immutable session snapshot and includes the sent snapshot's URL alongside the pixels in the **same model request**, retaining any original `source_url`. Save these addresses and the observations with visual findings. File URLs are local references, not public links.

Historical images become references instead of being reattached forever. To inspect an older image, read its recorded snapshot URL/path and use `attach`, `prepare` or a new crop within the current limits. Remote source URLs may change or expire; saved snapshots identify the pixels actually sent. Attachment success alone does not establish visual correctness: inspect the returned pixels before making visual claims.
````


## v2 · 2026-10-17 · 统一通过 shell.images 选择图片

来源：工作区未提交；`skills_agent/builtin_skills/images/SKILL.md`。

- 发现的问题：生成、日志与重复 attach 会使 stdout 变成多个 JSON，无法获得图片。
- 本版本目标：统一声明式附图，提供生成、组合准备、历史 URL、多图合成和后台任务的完整范例。
- 本版本效果：技能与工具契约保持一致，脚本可打印普通日志和多个结果；技能格式与 JSON 示例校验通过，实际模型遵循待观察。

### 完整原文

````markdown
---
name: images
description: >-
  Must use whenever attaching any image to a model request, including existing
  files, downloaded images, generated images, screenshots and rendered pages.
  Prepare at most five images per request, each at most 1 MB; compose needed
  images when there are more than five, and retain URLs for later inspection.
---

Use this skill for every image attachment. **Declare images in the shell tool's `images` parameter.** Saving a file, printing its path or printing helper metadata does not replace that declaration. [scripts/images.py](scripts/images.py) prepares bounded files and composition metadata; it is not a second attachment step. Resolve its absolute path from this SKILL.md. Python 3.11 or newer and Pillow are required for preparation, composition and in-memory generation.

`images` is an array of `{ "path": "/absolute/host/image.png", "label": "optional description", "source_url": "optional original URL" }`. `path` also accepts a local `file://` URL, including a saved session snapshot URL. It refers to the **host filesystem**, not a container-only path; download HTTP(S) sources first. This parameter accepts images, not PDF or arbitrary file attachments.

The runtime reads, validates and snapshots declared files **after the command finishes**. For a background `task`, it registers the declarations at launch and attaches the resulting images with the completion result, not the startup receipt. The files may therefore be created by the command. Explicit `images`, including `[]`, takes precedence over legacy stdout feedback. Stdout can contain progress logs, multiple JSON results or any other text without changing the declared images. Use `images: []` when preparing files without sending them.

Each model request may contain **at most 5 images in total**, with **each encoded image file at most 1,000,000 bytes (1 MB)** before base64 encoding. Count all tool results and user attachments in that request; separate tool calls do not increase the allowance. Select only images needed for the current decision. **If more than five are needed together, compose them into one or several labeled images within the same limits.**

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

Adapt this script in the task workspace, for example as `/absolute/workspace/draw.py`:

```python
import importlib.util
import json
from PIL import Image, ImageDraw

spec = importlib.util.spec_from_file_location("images", "/absolute/builtin_skills/images/scripts/images.py")
images = importlib.util.module_from_spec(spec)
spec.loader.exec_module(images)
canvas = Image.new("RGB", (1200, 700), "white")
ImageDraw.Draw(canvas).rectangle((100, 100, 1100, 600), outline="navy", width=5)
metadata = images.save_bounded(canvas, "/absolute/workspace/generated.png", label="Generated diagram")
print("Drawing complete")
print(json.dumps(metadata, ensure_ascii=False))
```

Run it and declare the output once; do not invoke `images.py attach` afterward:

```json
{
  "name": "Draw and inspect diagram",
  "command": "python3 /absolute/workspace/draw.py",
  "run_in_background": false,
  "images": [{"path": "/absolute/workspace/generated.png", "label": "Generated diagram"}]
}
```

## Prepare several files, then declare the selected images

`prepare` accepts a local path or `file://` URL. It preserves the original, applies EXIF orientation and uses the first frame of animated inputs. Output extension selects JPEG, PNG or WebP. Keep source/sent dimensions, scale and byte counts when interpreting coordinates.

```json
{
  "name": "Prepare and inspect two views",
  "command": "python3 /absolute/builtin_skills/images/scripts/images.py prepare /absolute/workspace/original-a.png --output /absolute/workspace/view-a.jpg && python3 /absolute/builtin_skills/images/scripts/images.py prepare /absolute/workspace/original-b.png --output /absolute/workspace/view-b.jpg",
  "run_in_background": false,
  "images": [
    {"path": "/absolute/workspace/view-a.jpg", "label": "View A", "source_url": "file:///absolute/workspace/original-a.png"},
    {"path": "/absolute/workspace/view-b.jpg", "label": "View B", "source_url": "file:///absolute/workspace/original-b.png"}
  ]
}
```

Both preparation commands may print their own metadata. The `images` array alone selects the files to send.

For more than five needed sources, compose a numbered grid and declare its single output:

```json
{
  "name": "Inspect six views together",
  "command": "python3 /absolute/builtin_skills/images/scripts/images.py compose /absolute/workspace/view-1.png /absolute/workspace/view-2.png /absolute/workspace/view-3.png /absolute/workspace/view-4.png /absolute/workspace/view-5.png /absolute/workspace/view-6.png --output /absolute/workspace/overview.jpg --columns 3",
  "run_in_background": false,
  "images": [{"path": "/absolute/workspace/overview.jpg", "label": "Six numbered views"}]
}
```

Composition metadata maps panel numbers to source URLs, dimensions, panel/image boxes and coordinate scales. Boxes are `[left, top, right, bottom]` pixel edges. Subtract the image-box origin and multiply by `scale_to_source` to recover EXIF-oriented source coordinates. A grid is an overview: if details are too small, crop the source and declare that view next.

For HTTP(S) images, use the [investigation fetch helper](../investigation/scripts/fetch.py), check successful status and `response_body_complete`, then prepare its actual returned `body_path`. Set the image declaration's `source_url` to the original HTTP(S) URL. The helpers do not upload images or install dependencies.

## Render or wait for an image

For local HTML/SVG, the [feedback render helper](../feedback/SKILL.md) creates a bounded screenshot; declare that output in the same shell call:

```json
{
  "name": "Render and inspect page",
  "command": "python3 /absolute/builtin_skills/feedback/scripts/feedback.py render /absolute/workspace/page.html --output /absolute/workspace/capture.png --width 500 --height 400",
  "run_in_background": false,
  "images": [{"path": "/absolute/workspace/capture.png", "label": "Page capture"}]
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
  "images": [{"path": "/absolute/workspace/plot.png", "label": "Final analysis plot"}]
}
```

The script must save its final bounded image before exiting. Wait for the actual completion result and check image-acquisition errors; launch success does not prove that the image exists or was attached. Saving or attaching an image also does not establish visual correctness: inspect the supplied pixels before making visual claims.
````

### 同步更新的 helper 帮助原文

来源：`skills_agent/builtin_skills/images/scripts/images.py`；输出协议保留兼容，只更新模型可能读取的帮助指引。

```text
Prepare bounded image files and metadata; declare their paths in shell.images.

save_bounded: Save a bounded Pillow image and return preparation metadata in a legacy feedback envelope.
prepare: compress a separate copy; declare its output in shell.images
compose: create a numbered grid; declare its output in shell.images
attach: legacy image feedback metadata; prefer shell.images
```


## v3 · 2026-10-17 · 图片上限统一为10张

来源：工作区未提交；`skills_agent/builtin_skills/images/SKILL.md`。

- 发现的问题：原上限为每个请求5张图片，用户要求扩展为10张。
- 本版本目标：统一运行时、工具说明与技能的10图上限；每图1 MB及历史图片引用规则不变。
- 本版本效果：允许同一请求携带至多10张图片；超出时使用合图，实际模型表现待观察。

### 完整原文

````markdown
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

Adapt this script in the task workspace, for example as `/absolute/workspace/draw.py`:

```python
import importlib.util
import json
from PIL import Image, ImageDraw

spec = importlib.util.spec_from_file_location("images", "/absolute/builtin_skills/images/scripts/images.py")
images = importlib.util.module_from_spec(spec)
spec.loader.exec_module(images)
canvas = Image.new("RGB", (1200, 700), "white")
ImageDraw.Draw(canvas).rectangle((100, 100, 1100, 600), outline="navy", width=5)
metadata = images.save_bounded(canvas, "/absolute/workspace/generated.png", label="Generated diagram")
print("Drawing complete")
print(json.dumps(metadata, ensure_ascii=False))
```

Run it and declare the output once; do not invoke `images.py attach` afterward:

```json
{
  "name": "Draw and inspect diagram",
  "command": "python3 /absolute/workspace/draw.py",
  "run_in_background": false,
  "images": [{"path": "/absolute/workspace/generated.png", "label": "Generated diagram"}]
}
```

## Prepare several files, then declare the selected images

`prepare` accepts a local path or `file://` URL. It preserves the original, applies EXIF orientation and uses the first frame of animated inputs. Output extension selects JPEG, PNG or WebP. Keep source/sent dimensions, scale and byte counts when interpreting coordinates.

```json
{
  "name": "Prepare and inspect two views",
  "command": "python3 /absolute/builtin_skills/images/scripts/images.py prepare /absolute/workspace/original-a.png --output /absolute/workspace/view-a.jpg && python3 /absolute/builtin_skills/images/scripts/images.py prepare /absolute/workspace/original-b.png --output /absolute/workspace/view-b.jpg",
  "run_in_background": false,
  "images": [
    {"path": "/absolute/workspace/view-a.jpg", "label": "View A", "source_url": "file:///absolute/workspace/original-a.png"},
    {"path": "/absolute/workspace/view-b.jpg", "label": "View B", "source_url": "file:///absolute/workspace/original-b.png"}
  ]
}
```

Both preparation commands may print their own metadata. The `images` array alone selects the files to send.

For more than ten needed sources, compose a numbered grid and declare its single output:

```json
{
  "name": "Inspect eleven views together",
  "command": "python3 /absolute/builtin_skills/images/scripts/images.py compose /absolute/workspace/view-1.png /absolute/workspace/view-2.png /absolute/workspace/view-3.png /absolute/workspace/view-4.png /absolute/workspace/view-5.png /absolute/workspace/view-6.png /absolute/workspace/view-7.png /absolute/workspace/view-8.png /absolute/workspace/view-9.png /absolute/workspace/view-10.png /absolute/workspace/view-11.png --output /absolute/workspace/overview.jpg --columns 4",
  "run_in_background": false,
  "images": [{"path": "/absolute/workspace/overview.jpg", "label": "Eleven numbered views"}]
}
```

Composition metadata maps panel numbers to source URLs, dimensions, panel/image boxes and coordinate scales. Boxes are `[left, top, right, bottom]` pixel edges. Subtract the image-box origin and multiply by `scale_to_source` to recover EXIF-oriented source coordinates. A grid is an overview: if details are too small, crop the source and declare that view next.

For HTTP(S) images, use the [investigation fetch helper](../investigation/scripts/fetch.py), check successful status and `response_body_complete`, then prepare its actual returned `body_path`. Set the image declaration's `source_url` to the original HTTP(S) URL. The helpers do not upload images or install dependencies.

## Render or wait for an image

For local HTML/SVG, the [feedback render helper](../feedback/SKILL.md) creates a bounded screenshot; declare that output in the same shell call:

```json
{
  "name": "Render and inspect page",
  "command": "python3 /absolute/builtin_skills/feedback/scripts/feedback.py render /absolute/workspace/page.html --output /absolute/workspace/capture.png --width 500 --height 400",
  "run_in_background": false,
  "images": [{"path": "/absolute/workspace/capture.png", "label": "Page capture"}]
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
  "images": [{"path": "/absolute/workspace/plot.png", "label": "Final analysis plot"}]
}
```

The script must save its final bounded image before exiting. Wait for the actual completion result and check image-acquisition errors; launch success does not prove that the image exists or was attached. Saving or attaching an image also does not establish visual correctness: inspect the supplied pixels before making visual claims.
````
