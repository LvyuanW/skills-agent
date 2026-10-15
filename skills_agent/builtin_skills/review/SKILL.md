---
name: review
description: >-
  Must use before delivering a complex task to independently review actual results
  against original requirements and the project's acceptance-criteria document.
  Also use for an explicitly requested review. Skip routine self-checks of simple
  answers or small isolated changes; assigned reviewers inspect directly without
  further delegation.
---

Before final delivery of a complex task, the main agent must use this skill and delegate an independent review through the existing `subagent` tool. Complex work involves dependent stages, interacting requirements or material uncertainty, as described in the acceptance-criteria skill. Simple questions, direct lookups and small isolated changes need only proportionate self-checks unless a review is explicitly requested. An assigned reviewer performs the review and returns its findings; it does not create another acceptance document or delegate another review. If the main agent cannot delegate because `subagent` is unavailable, it checks the scope itself and reports that the review was not independent, along with any missing inputs.

Give the subagent a short `name`. In `task`, explicitly assign the independent reviewer role, provide this SKILL.md's actual absolute path with instructions to read and follow its reviewer guidance, and state that deliverables must not be edited. Include:

- Original requirement sources and accepted corrections; distinguish accepted decisions from proposals. For complex tasks, include the actual absolute path to the project's acceptance-criteria document so the reviewer can check its faithfulness and completeness.
- The actual artifact paths, or the full conclusions or proposed solution being delivered when there is no file.
- Readable absolute paths to relevant requirement attachments, reference images and current artifact screenshots, clearly identifying references versus produced results.
- Existing checks and their evidence locations, known gaps, and the boundaries of what the reviewer may read or execute.

Ordinary subagents do not inherit the parent conversation or images and cannot delegate. If requirements or their adoption are unclear, the main agent first uses `subagent` with `history: true` and a concrete question to recover original requirements and later corrections. Include the returned relevant excerpts and references in the review task. If a complex task lacks its acceptance document, the main agent establishes it from original requirements using acceptance-criteria before requesting review, without treating existing results as the standard. An explicitly requested review of simple work can use the supplied requirements directly without requiring a new document. Do not ask an ordinary reviewer to reconstruct the goal from session logs, and do not substitute the latest message or the producer's summary for missing requirements.

As the reviewer, read the original requirements and, for complex tasks, the acceptance document. Check whether the document omits or misinterprets requirements, adds unsupported constraints, or uses checks that could pass despite a defective result. Compare actual deliverables against its criteria and the original goal, including important gaps outside the checklist. Do not inherit the producer's pass/fail judgments. A missing required document or source is an input gap to report to the main agent, not a reason to invent criteria or mark the result passed.

Explicitly supplied attachment files, including snapshots under `session/attachments`, may be read directly; do not search session logs or other private subagent sessions. Do not edit the deliverables or acceptance document. Judge whether the result covers the requested goal, is correct and supported, respects constraints, omits necessary work, and meets required formats. Select checks from the requirements, not from what the implementation happens to produce. For visible properties use the existing feedback skill to acquire and inspect image pixels, examining both the whole result and relevant details or connections; paths alone do not transfer images. For behavior exercise the relevant behavior and boundaries. A successful write, passing schema or producer's completion claim alone is insufficient.

Return supported problems with their locations, the relevant acceptance criterion or original requirement, observed versus required behavior, and evidence. Include defects in the acceptance document itself. Also identify unverified conditions and the specific material, check or clarification needed to resolve them. If no problems are found, state the reviewed scope and supporting checks without claiming that unobserved conditions passed.

The main agent evaluates the findings, makes justified corrections within the task, and obtains fresh evidence for affected conditions. Delegate another focused review only when changes or unresolved findings require it; do not repeat the whole review without new information. If the reviewer lacks input, the main agent supplies it in a new task. Finish when the evidence supports the required outcome, or report the concrete unresolved limitation without claiming completion.
