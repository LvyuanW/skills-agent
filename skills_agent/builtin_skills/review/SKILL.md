---
name: review
description: Independently review substantive task deliverables against original requirements and evidence before the main agent's final report.
---

When substantive work is ready for the final report, the main agent delegates an independent review through the existing `subagent` tool. Simple questions and conversational replies do not require this workflow. An assigned reviewer performs the review and returns its findings; it does not delegate another review. If `subagent` is unavailable, inspect the assigned scope directly and report missing inputs without attempting further delegation.

Give the subagent a short `name`. In `task`, explicitly assign the independent reviewer role, provide this SKILL.md's actual absolute path with instructions to read and follow its reviewer guidance, and state that deliverables must not be edited. Include:

- The original requirements, accepted corrections, constraints and acceptance conditions. Include relevant user wording or readable requirement sources; distinguish accepted decisions from proposals.
- The actual artifact paths, or the full conclusions or proposed solution being delivered when there is no file.
- Readable absolute paths to relevant requirement attachments, reference images and current artifact screenshots, clearly identifying references versus produced results.
- Existing checks and their evidence locations, known gaps, and the boundaries of what the reviewer may read or execute.

Ordinary subagents do not inherit the parent conversation or images and cannot delegate. If requirements or their adoption are unclear, the main agent first uses `subagent` with `history: true` and a concrete question to recover original requirements and later corrections. Include the returned relevant excerpts and references in the review task. Do not ask an ordinary reviewer to reconstruct the goal from session logs, and do not substitute the latest message or the producer's summary for missing requirements.

As the reviewer, inspect the actual supplied materials and use relevant checks or the feedback skill to obtain evidence. Explicitly supplied attachment files, including snapshots under `session/attachments`, may be read directly; do not search session logs or other private subagent sessions. Do not edit the deliverables. Judge whether the result covers the requested goal, is correct and supported, respects constraints, omits necessary work, and meets required formats. Select checks from the requirements, not from what the implementation happens to produce. For visible properties use the existing feedback skill to acquire and inspect image pixels; paths alone do not transfer images. For behavior exercise the relevant behavior. A successful write, passing schema or producer's completion claim alone is insufficient.

Return supported problems with their locations, observed versus required behavior, and evidence. Also identify unverified conditions and the specific material, check or clarification needed to resolve them. If no problems are found, state the reviewed scope and supporting checks without claiming that unobserved conditions passed. Missing requirements are an input gap, not proof that the result is acceptable.

The main agent evaluates the findings, makes justified corrections within the task, and obtains fresh evidence for affected conditions. Delegate another focused review only when changes or unresolved findings require it; do not repeat the whole review without new information. If the reviewer lacks input, the main agent supplies it in a new task. Finish when the evidence supports the required outcome, or report the concrete unresolved limitation without claiming completion.
