# Creative campaign discovery: business goal → production handoff → measurement

**Document type:** Proposed AgencyFlow improvement + application positioning. **Not an implemented feature or a real Beyond Creator client project.**

## Why this is a better fit for restaurant marketing

Beyond Creator describes a **consultative discovery** process, research into market trends and audience behaviour, tailored creative deliverables, and **monthly reporting** against agreed metrics. See its public [About](https://www.beyondcreator.ca/about), [Homepage](https://www.beyondcreator.ca/) and [Social Media Management](https://www.beyondcreator.ca/social-media-management) pages. The agency-specific opportunity is not “use AI to write posts”; it is to keep the purpose, constraints and measurement of a campaign attached to the production tasks.

AgencyFlow currently demonstrates the second half well: raw brief → local AI-generated deliverables and tasks → human edit/approval → verified n8n task storage. The first half (structured strategic discovery) and final outcomes/measurement handoff remain **design proposals**, not implemented work. No business, workflow or productivity claim here comes from a live Beyond Creator engagement.

## Fictional restaurant launch example

Start with the existing [Winter menu reels](../examples/01-winter-menu-relative-deadline.txt) demo brief, keeping facts and unknowns separate:

| Discovery field | Status | What is actually known from the brief |
| --- | --- | --- |
| Campaign | Confirmed from brief | Winter menu launch |
| Requested production | Confirmed from brief | 3 Instagram reels, 2 story graphics, 3 reel captions |
| Asset readiness | Partial | Existing food photos; video not yet shot |
| Creative direction | Confirmed from brief | Warm, modern, festive without being too Christmas-specific |
| Deadline | Unresolved | “Next Friday”; ask client for an unambiguous calendar date |
| Client review | Confirmed from brief | Drafts reviewed before publication |
| **Business objective** | **UNKNOWN** | Is the goal awareness, reservations, catering inquiries, or something else? |
| **Target audience** | **UNKNOWN** | Which guests, location, age groups, interests or dining occasions are most relevant? |
| **Success metric + baseline** | **UNKNOWN** | Which outcome will indicate success? How will it be measured? |
| Budget / distribution | UNKNOWN | Are these organic assets only? Is there paid media? |

Do **not** silently fill unknowns from marketing stereotypes. The model may suggest **questions**, but campaign facts require confirmation. It should not invent competitor findings, a target demographic, a market trend, existing analytics or ROI.

### Discovery questions that the coordinator could send later (not sent by the app)

1. “What is the main business outcome for the winter menu launch: reach, reservations, website visits, or another goal?”
2. “Who are you hoping to reach, and which locations or customer segments matter most?”
3. “What date does ‘next Friday’ refer to, and when can new video footage be filmed?”
4. “What platform analytics or booking data do you currently track, and what was the baseline?”
5. “Who gives final approval on content and when do they need to review drafts?”

If the client later confirms **“increase weekday reservations”**, the account manager could *propose* reservation-link clicks and attributable booking counts as KPIs; these would be planning suggestions until the tracking method, baseline, targets, and source-of-truth are confirmed.

## Smallest implementable upgrade (follow-up after sandbox Notion work)

**Add a compact “Campaign context” section** in the existing intake/review flow; don't build a CRM, live analytics collector or market-research agent.

Recommended data fields:
- `business_goal`: text + `status` = unknown / proposed / client_confirmed
- `target_audience`: text + status
- `primary_kpi`: text + status
- `measurement_source`: text + status (e.g. first-party platform report, booking system; never assume accessible)
- `baseline`, `target`: optional numbers/units, **unknown by default**, with provenance and date if confirmed
- `campaign_platforms`, `budget`: optional with status
- `discovery_questions`: human-editable list (can be model-generated)
- `reviewer_approval`: existing approval gate; don't add unnecessary approvals

Display a **concise “Needs client confirmation” checklist** for unknown high-impact fields. Allow human approval of *internal preparation* while marking missing strategic context as unresolved; never present those guesses as confirmed. Approval semantics must remain clear and existing n8n tasks work without these fields.

### Intended handoff

```text
Client message
  ↓
Known requirements + missing-context questions
  ↓
AI production draft (existing)
  ↓
Coordinator corrects and confirms campaign context
  ↓
Human approval → existing n8n task read-back (and optional sandbox Notion sync when separately implemented)
  ↓
Later: review actual reporting inputs against the *agreed* KPIs (future work)
```

The task metadata should retain the campaign objective and KPI as **context only**, not evidence of realized performance. Notion/Slack writes must remain opt-in, fictional/demo-only until authorized.

### Acceptance tests for a future PR

- [ ] Original winter brief produces **UNKNOWN** business goal, target audience, KPI and baseline, never fabricated values.
- [ ] Known deliverable quantities, missing videos, review constraint, and ambiguous date are preserved.
- [ ] Model-generated or rule-generated questions are **proposals** and remain editable.
- [ ] Human can confirm objective and KPI with a provenance note; an unconfirmed field never gains a confirmed status through regeneration.
- [ ] Existing approval, duplicate handling, n8n storage and failure/retry behaviour remain intact.
- [ ] No invented market research, audience data, campaign results, ROI or productivity uplift in outputs, tests or README.
- [ ] No external integrations, new paid services, secrets or real customer data necessary for the demonstration.

## Application/demo message

A more business-specific opening:

> “I built AgencyFlow AI around a common creative-agency handoff: a restaurant sends an informal promotion request, but an account manager still needs to clarify the objective, audience, creative assets, deadline and how results will be judged. The working prototype turns the production side into an editable AI draft, gates the handoff on human approval and verifies tasks landed in n8n. I'm exploring a discovery checklist so that those tasks stay tied to the campaign's business goal, rather than treating content volume itself as success.”

State the discovery checklist as a **planned improvement** unless implemented and validated. The existing [demo video](demo/agencyflow-demo.mp4) shows the creator-brand-deal flow, not this new discovery feature.

## Later evidence: measure useful outcomes, not invented ROI

In a small **fictional or permissioned** trial, log manual versus AI-assisted time to an **approved, actionable brief** (including correction time); missing business goal/target audience/metric counts; task quantity corrections; verified n8n handoffs. An actual campaign's ROI would require agreed attribution, spend/cost data, and independently verified results, none of which this portfolio prototype currently has.

**Priority order:** Submit the application using the current repo/demo; finish the Notion sandbox PR if useful; only then implement the narrow discovery checklist if it doesn't delay job applications or paying client work.
