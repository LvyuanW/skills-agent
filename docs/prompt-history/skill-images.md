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
