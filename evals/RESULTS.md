# Model accuracy check

Live runs scored automatically against facts written by hand from each brief ([expectations.json](expectations.json), [run_eval.py](run_eval.py)). Briefs 01–05 were used while improving the prompt and rules; **briefs 06–07 are held out**: their expectations were committed before any tuning and they were never used to tune. Nothing is mocked.

| Run | Model | Tuned briefs 01–05 | Held-out briefs 06–07 | Valid on 1st attempt | Avg time |
| --- | --- | --- | --- | --- | --- |
| `qwen3.5-4b-baseline` | ollama/qwen3.5:4b | 76/82 (93%) | not run | 10/10 | 79s |

Baseline = the prompt before improvements. Improved = tighter prompt rules plus deterministic fixes in [brief_checks.py](../brief_checks.py) (copies the client's timing words when the model drops them). Count warnings shown to reviewers are not part of the score.

## `qwen3.5-4b-baseline`

ollama/qwen3.5:4b, 2 run(s) per brief, finished 2026-11-13T04:17:08+00:00.

| Check type | Passed |
| --- | --- |
| Deliverable quantities | 26/28 (93%) |
| Deadline handling | 10/10 (100%) |
| Keeps client's timing words | 8/10 (80%) |
| Asks the right client questions | 12/14 (86%) |
| Includes essential tasks | 20/20 (100%) |

| Brief | Run | Passed | Time | Failed checks |
| --- | --- | --- | --- | --- |
| 01-winter-menu-relative-deadline.txt | 1 | 7/7 | 78s | none |
| 01-winter-menu-relative-deadline.txt | 2 | 7/7 | 65s | none |
| 02-patio-opening-fixed-date.txt | 1 | 8/8 | 85s | none |
| 02-patio-opening-fixed-date.txt | 2 | 8/8 | 86s | none |
| 03-catering-launch-messy-email.txt | 1 | 8/9 | 74s | asks about pricing |
| 03-catering-launch-messy-email.txt | 2 | 8/9 | 66s | asks about pricing |
| 04-creator-brand-sponsorship.txt | 1 | 7/8 | 92s | 2 TikToks (got 1) |
| 04-creator-brand-sponsorship.txt | 2 | 7/8 | 90s | 2 TikToks (got 1) |
| 05-creator-youtube-launch.txt | 1 | 8/9 | 80s | keeps the client's timing words ('january') (got '') |
| 05-creator-youtube-launch.txt | 2 | 8/9 | 76s | keeps the client's timing words ('january') (got '') |

Regex checks are a coarse proxy: they confirm a fact is present, not that the plan is good. Human review stays the control.
