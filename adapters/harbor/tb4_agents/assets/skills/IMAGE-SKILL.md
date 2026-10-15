---
name: task-image-observation
description: View task image files through the Agent's existing native shell image-feedback protocol. Use for drawings, screenshots, scanned documents, or generated image artifacts whose pixels must be inspected.
---

Use the native shell tool to print one JSON object to stdout:

```python
import json
print(json.dumps({"skills_agent_feedback": 1, "attachments": [{"type": "image", "path": "/absolute/path/image.png", "label": "Task image"}]}))
```

Use `result_mode=direct` and include absolute paths to existing PNG, JPEG, WebP, or GIF files. The runtime snapshots the image and supplies pixels to the model in the next request. Printing only a filename does not show its pixels. For a PDF, render the relevant pages to images with available tools first.
