# 普通二进制附件的读取提示

位置：`skills_agent/session/__init__.py · Session.make_user_message() · 非文本附件分支`。状态：启用。盘点日期：2026-10-11（Asia/Shanghai）。

H 编号记录 Git 全部分支中的实际文本变化；v1 是本次工作区基线标签，不表示新增实现。这里的“效果”说明规则变化或预期作用，不代表实测结果。

图片和 PDF 走原生附件分支；这里适用于未能按 UTF-8 文本读取的其他附件。文件名、保存路径、字节数和 MIME 类型在运行时填入，并与用户消息一起发送给模型。附件自身的文字不是项目维护的指令。

## H1 — 要求通过脚本读取普通二进制附件

- 时间：2026-10-09 19:23:06 +08:00。
- 标识：提交 `23e5359e3fb3d05e33e40651d617a06e6fecd548`。
- 发现的问题：非原生二进制附件无法直接作为普通文本展示给模型。
- 本版本目标：提供保存位置及类型，并明确要求用脚本读取。
- 本版本效果：新增 `Read it using a script.` 指引；模型得到的是文件访问提示，文件内容仍需实际读取。

原文：

```python
label = f"Attachment: {source.name}\nPath: {destination}\n"
body = file_text if file_text is not None else f"Binary file ({len(data)} bytes; {mime_type}). Read it using a script."
```

## v1 — 当前工作区基线

- 时间：2026-10-11。
- 标识：工作区快照；此片段与提交 `23e5359e3fb3d05e33e40651d617a06e6fecd548` 相同，无新增未提交措辞。
- 发现的问题：本次盘点需要涵盖 user 消息内由程序补入的读取指令。
- 本版本目标：固定二进制附件提示的当前原文。
- 本版本效果：沿用 H1；没有因本次盘点增加新行为。

原文：

```python
label = f"Attachment: {source.name}\nPath: {destination}\n"
body = file_text if file_text is not None else f"Binary file ({len(data)} bytes; {mime_type}). Read it using a script."
```
