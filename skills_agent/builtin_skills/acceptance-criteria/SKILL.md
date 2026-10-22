---
name: acceptance-criteria
description: >-
  Must use before executing a complex task with interdependent steps, material
  uncertainty or interacting requirements. Save the original user requirements
  and model-defined acceptance criteria in a document inside the task project.
  Skip simple Q&A, direct lookups and small isolated changes with an obvious check;
  delegated tasks reuse the existing document instead of creating another.
---

Read this skill at the start of a complex task. Inspect only the inputs needed to understand the requirements, then save a concise Markdown acceptance document inside the actual task project before substantive implementation or production. For a task without a separate project, use the working directory. Follow existing document conventions; otherwise choose a task-specific path such as `docs/acceptance/<task>.md`. Reuse the document when continuing the same task; do not overwrite another task's requirements. Simple tasks need a proportionate direct check, not this document workflow.

Complexity comes from dependent stages, interacting constraints or uncertainty requiring several kinds of evidence, not the number of tool calls. If initially simple work develops those properties, establish the document before continuing the expanded work. A history retrieval agent performs its assigned role without creating a new acceptance document. Delegated tasks use the parent's supplied document for their assigned scope.

The document must contain:

- **Original requirements:** preserve the user's original task wording and accepted later corrections, with source references and readable paths to relevant attachments. Keep assumptions and unresolved questions distinct from user requirements. If earlier requirements are missing, the main agent uses the existing `subagent(history=true)` retrieval route before relying on them; never reconstruct user wording from a summary and present it as a quote.
- **Acceptance criteria:** list the model's task-specific, observable completion conditions and how each will be checked. Derive them from the requirements rather than an intended implementation. Cover the overall outcome and relevant component relationships, constraints, boundary conditions and delivery requirements. Do not add features or restrictions the user did not request.

Choose checks that could reveal a plausible failure even if the implementation appears successful. Use actual execution for behavior, original sources for factual conclusions, and rendered output for visible properties. Combine overall inspection with relevant details; a component's presence does not establish its correct connection to the rest. Avoid turning examples into a universal checklist or substituting file existence and passing syntax checks for the requested outcome.

Keep the document available during execution. Update it for accepted requirement changes or newly discovered omissions, recording the reason while preserving the original requirements. Do not relax a criterion merely because the current result fails it. Use feedback as needed to obtain evidence; a written criterion is not a completed check.

Respect explicit user restrictions on file creation; if they prevent saving the document, report that conflict rather than silently writing outside the permitted scope or claiming the document exists.
