# 图片引用提示词版本时间线

源位置：`skills_agent/images.py: image_reference()` 与 `context.image_records()`。

## v1 · 2026-10-16 · 图片按请求加载与卸载

来源：当前工作区（未提交）。

- 发现的问题：历史图片的 base64 在每轮重复发送，使请求体超过传输上限。
- 本版本目标：每次附图携带稳定快照 URL；历史图转为引用，本轮只保留最多五张，不能通过拆分工具调用绕过。
- 本版本效果：每次 assistant 响应后移除上一请求中的图片块，保留可重读引用和证据事件；超额本轮图片有明确说明。原始日志和图片文件不变。

### 完整提示词模板

```text
Image: {label}
Image URL: {snapshot_url}
Source URL: {source_url}
Image not attached: request limit is 5; newest images retained.
Evidence event: {event_id}
```

`Source URL` 仅在有效且不同于快照 URL 时出现；`Image not attached` 仅用于本轮超额图片；`Evidence event` 由工作上下文补充。该文本是证据引用，不代表图片像素仍随请求发送。
