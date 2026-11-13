# Model accuracy check

Model `ollama/qwen3.5:4b`, 2 live run(s) per brief, finished 2026-11-13T04:17:08+00:00 on local laptop (GTX 1650 4 GB). Expectations were written by hand from each brief in [expectations.json](expectations.json); scoring is automatic ([run_eval.py](run_eval.py)).

**76/82 checks passed (93%)**, 10/10 outputs schema-valid, 10/10 valid on the first attempt.

| Check type | Passed |
| --- | --- |
| Deliverable quantities | 26/28 |
| Deadline handling | 10/10 |
| Keeps client's timing words | 8/10 |
| Asks the right client questions | 12/14 |
| Includes essential tasks | 20/20 |

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

Regex checks are a coarse proxy: they confirm a fact is present, not that the wording is good. Human review stays the control.
