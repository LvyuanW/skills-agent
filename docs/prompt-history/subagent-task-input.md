# 子代理 user 任务输入模板

位置：`skills_agent/agent.py · Agent._subagent()` 中传给 `child_session.make_user_message()` 的文本、只读快照和原生附件。状态：启用。盘点日期：2026-10-11（Asia/Shanghai）。

H 编号记录 Git 全部分支中的实际模板变化；v1 是本次工作区基线标签，不表示新增实现。这里的“效果”说明输入结构变化或预期作用，不代表实测结果。

本页保存仓库维护的 user 输入模板，不把调用者实际提供的 `task`、快照内容或附件内容作为固定提示词。子代理的 system 追加要求另见 [公共报告指令](subagent-report.md) 与 [历史检索角色](history-retrieval-role.md)。代码块只收录直接生成输入的语句，不复制子代理创建、状态回调和异常处理。

## H1 — 以 Goal 标签包装委派任务

- 时间：2026-10-09 19:23:06 +08:00。
- 标识：提交 `23e5359e3fb3d05e33e40651d617a06e6fecd548`。
- 发现的问题：独立子代理需要通过新会话得到调用者提供的目标标签和任务正文。
- 本版本目标：将 `goal` 与 `task` 组成清晰的 user 消息。
- 本版本效果：输入模板使用 `Goal:`，随后空一行附上任务正文；此时没有 history 模式或父会话快照附件。

原文（保留任务输入模板及直接相关组装代码）：

```python
return child.run(child_session.make_user_message(f"Goal: {goal}\n\n{task}", []))
```

## H2 — 命名任务并增加历史检索的只读材料

- 时间：2026-10-10 04:34:41 +08:00。
- 标识：提交 `08586891a747eea02ff1b647afe944b736534f65`。
- 发现的问题：命名任务与历史检索角色需要统一输入，且图片/PDF 仅提供路径不能呈现其内容。
- 本版本目标：普通任务使用 `Task name:`；历史检索任务额外携带完整只读快照，并为原生附件标明来源记录。
- 本版本效果：将 `Goal:` 改为 `Task name:`；仅在 `history=True` 时追加 `Read-only working context:` 和 `Read-only attachment from …:`。相同路径的附件去重，附件块自身保留为原生输入。

原文（保留任务输入模板及直接相关组装代码）：

```python
prompt = f"Task name: {name}\n\n{task}"

media = []

if history:
    snapshot = self.context.snapshot(self._system(), self.tools.schemas)
    prompt += "\n\nRead-only working context:\n" + json.dumps(snapshot, ensure_ascii=False)
    seen = set()
    for record in snapshot["records"]:
        content = record["message"].get("content", [])
        for block in content if isinstance(content, list) else []:
            if block.get("type") in {"image", "file"} and block.get("path") not in seen:
                seen.add(block.get("path"))
                media.extend([{"type": "text", "text": f"Read-only attachment from {record['event_id']}:"},
                              deepcopy(block)])

message = child_session.make_user_message(prompt, [])
message["content"].extend(media)
return child.run(message)
```

## v1 — 当前工作区基线

- 时间：2026-10-11。
- 标识：工作区快照；模板与提交 `08586891a747eea02ff1b647afe944b736534f65` 相同，无新增未提交措辞。
- 发现的问题：本次盘点需要覆盖结构标签与只读材料标签，避免只保存 system 指令而遗漏 user 模板。
- 本版本目标：固定当前的普通任务与历史检索任务输入结构。
- 本版本效果：沿用 H2；没有新增输入模板规则，也没有 v2。

原文（保留任务输入模板及直接相关组装代码）：

```python
prompt = f"Task name: {name}\n\n{task}"

media = []

if history:
    snapshot = self.context.snapshot(self._system(), self.tools.schemas)
    prompt += "\n\nRead-only working context:\n" + json.dumps(snapshot, ensure_ascii=False)
    seen = set()
    for record in snapshot["records"]:
        content = record["message"].get("content", [])
        for block in content if isinstance(content, list) else []:
            if block.get("type") in {"image", "file"} and block.get("path") not in seen:
                seen.add(block.get("path"))
                media.extend([{"type": "text", "text": f"Read-only attachment from {record['event_id']}:"},
                              deepcopy(block)])

message = child_session.make_user_message(prompt, [])
message["content"].extend(media)
return child.run(message)
```
