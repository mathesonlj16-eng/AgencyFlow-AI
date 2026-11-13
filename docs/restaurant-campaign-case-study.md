# Restaurant campaign intake — a two-minute AgencyFlow story

**Status:** Application-focused case study and **recording plan**, not a claim that this particular new video has been recorded or that a real client has used the tool.

**Use case:** A Toronto-based creative agency handles many client requests across account management, design, video editing and client approvals. Requests arrive as informal messages; the coordination risk is overlooking deliverables, unconfirmed dates, missing assets or approval requirements. AgencyFlow AI tests one narrow solution: generate a *draft*, keep a coordinator in control, and create a traceable task handoff.

> AgencyFlow is an **independent portfolio demonstration**. The client, campaign and staff roles below are fictional. It is not a product commissioned by Beyond Creator, and the agency has not tested it.

## The fictional incoming request

Use the existing **Winter menu reels** sample in the app ([source brief](../examples/01-winter-menu-relative-deadline.txt)):

> We need three Instagram reels for the winter menu launch next Friday. We have photos of the new dishes, but we haven't shot video. The vibe should feel warm, modern and festive without looking too Christmas-specific. We also need two story graphics and a simple caption for each reel. Please get a first draft ready for review; nothing should be published without approval.

An account manager should be able to see:

- **Deliverables to check:** 3 reels, 2 story graphics, and 3 captions (one for each reel).
- **Dependencies:** video footage must be produced; photos already exist.
- **Unresolved date:** “next Friday” must be confirmed as an absolute date by a person.
- **Review gate:** the client must approve drafts before publication.

These are **expected facts from the fictional brief**, not a guarantee that every model generation extracts them perfectly. The UI includes count-warning and edit controls because an actual local 4B-model run can miss a quantity or detail.

## Human-in-the-loop workflow

| Step | Demonstration | Why an agency would care |
| --- | --- | --- |
| Intake | Paste the original client request | No elaborate template is required from the client |
| AI draft | Local model proposes deliverables, tasks, questions and risks | Helps a coordinator organize an incomplete request |
| Review | Compare proposed quantities with the source; correct mistakes and set the confirmed date | Prevents model guesses becoming operational facts |
| Approval | Lock the reviewed plan | Clear human decision boundary before handoff |
| n8n handoff | Send approved task rows; read the stored rows back | Makes the handoff verifiable rather than relying on a “200 OK” response |
| Failure/duplicates | Show the duplicate guard, and the safe failure/retry on an n8n outage | Helps avoid double-work and silent loss |

**Working scope from the README:** real local Ollama inference, FastAPI/SQLite workflow, editable review, approval state, duplicate protection, n8n data-table storage and read-back, failure/retry handling, and audit history. **No external client system is written to**: the downstream HTTP task-tool node remains disabled. Any future Notion integration should be described as *planned or in progress* until verified.

## Suggested new video recording (about 2 minutes)

This is a **new restaurant-specific cut**. The repository already contains [an existing two-minute video](demo/agencyflow-demo.mp4) showing a *creator brand-deal* example; do not imply that existing video depicts this restaurant story.

| Time | Shot / action | Suggested narration |
| --- | --- | --- |
| 0:00–0:15 | Show the raw restaurant message and highlight the missing footage, ambiguous Friday and approval sentence | “A restaurant sends an informal launch request. The coordination risk isn't writing a caption; it's missing a deliverable, asset, deadline or approval.” |
| 0:15–0:35 | Paste **Winter menu reels**, click generate | “AgencyFlow uses a local AI model to propose a production plan. The model doesn't publish or create downstream tasks on its own.” |
| 0:35–0:55 | Speed through actual inference with **“real local inference, shortened in video”** label; reveal generated draft | “This run uses a real model. I am shortening the wait in the recording, not substituting a mocked response.” |
| 0:55–1:20 | Check reel/story/caption quantities, fix anything missed; confirm the real date and edit a production task | “The coordinator catches mistakes, confirms the date and makes the task plan usable for the team.” |
| 1:20–1:40 | Save, approve and click send; show n8n task-table read-back | “Only an approved plan crosses the handoff boundary. I verify the task rows actually exist in n8n.” |
| 1:40–1:55 | Show recent-plan activity and a duplicate attempt, or use a brief cut of the documented outage/retry test | “Duplicates, failures and revisions are visible instead of being hidden.” |
| 1:55–2:05 | End on the reviewed plan and task table, with external sync disabled | “It's a focused demonstration of a safer creative-operations workflow, not a replacement for the creative team.” |

**Recording rules:** use fictional data; show the actual outputs even if imperfect; only speed up inference with an explicit caption; don't imply tasks were written to Notion, Slack or a live client workspace. Show actual n8n data-table rows, not just its acknowledgment. The video should be comprehensible without watching 100 seconds of inference.

## Business value: what is demonstrated versus hypothetical

**Demonstrated:** the application turns a messy brief into an editable task plan, requires human approval, persists approved tasks in n8n, and handles replay/failure scenarios. [Technical and live-run evidence](../README.md) and [evaluation details](../evals/RESULTS.md) document what was tested.

**Not yet demonstrated:** measurable time saved in a real creative agency, team adoption, reliable integration with a real project management tool, improvement in real client outcomes, or use by Beyond Creator.

A small, honest pilot could test those claims:

1. Take 5–10 fictional or permissioned briefs, record the **manual time** needed to reach a useful task plan, then record the **AI-assisted time including review**.
2. Have a coordinator and a designer/editor rate whether each task assignment, dependency and client question is actionable.
3. Measure edits, missing deliverables, unresolved dates, duplicate events, and the fraction of n8n handoffs verified by read-back.
4. Decide whether this is useful enough to integrate with the team's actual tools. Never infer a productivity percentage without measuring it.

## Recommended application positioning

**Opening sentence:** “I built AgencyFlow AI to explore a specific creative-agency bottleneck: turning informal restaurant campaign requests into reliable, reviewable work for account managers and production staff.”

**Personal contribution, with AI assistance stated accurately:** The project owner defined the objective, requirements, safety boundaries and acceptance criteria and coordinated an AI-assisted implementation. ChatGPT (GPT-6) produced the initial starter; Claude Code expanded, tested and published it under that direction. Claim specific manual coding, independent validation or deployment work only if personally performed.

**Best application evidence:**
- [Source repository and technical overview](../README.md)
- [Existing demo video — creator brand deal](demo/agencyflow-demo.mp4)
- [Observed model limitations and hold-out evaluation](../evals/RESULTS.md)

**Application recommendation:** The current public repository and existing video already support applying. This narrative is a focused presentation upgrade; do not delay an application waiting for a new feature or another video.
