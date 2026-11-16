# Evaluation: finding the cause of a weekly metric change and writing a checked review

On 36 known-answer cases of synthetic local-services data, the agent named every planted cause, with nothing extra and nothing too coarse, in 34 cases (94%); the fixed-rule script did so in 31 (86%) and the one-prompt approach in 28 (78%). Case by case, the agent was right where the one prompt was wrong 6 times and never the other way round (exact p = 0.031). Against the script it was 5 to 2 (p = 0.45), too close to call on 36 cases. The one prompt got none of the six two-dimension cases: five answers were one level too coarse and one named a chain instead.

After at most one revision, 33 of 36 agent reviews and 35 of 36 one-prompt reviews passed the number check. Every paragraph still held back failed on a Chinese number word such as 这两项 ("these two") or 前一天 ("the day before"), not on a wrong number. First drafts passed much less often: 12 of 36 for the agent, 19 of 36 for the one prompt. The agent's first drafts had 19 paragraphs quoting a number that was not in the cited row, against 3 for the one prompt; the revision fixed all of them.

At batch prices the agent cost $0.0057 per case, 1.8 times the one prompt's $0.0032. The whole test run cost $0.32.

| | Script | One prompt | Agent |
|---|---|---|---|
| All causes right | 31/36 (86%) | 28/36 (78%) | **34/36 (94%)** |
| Released after at most one revision | 36/36 | 35/36 | 33/36 |
| Released on the first draft | — | 19/36 | 12/36 |
| Cost per case (batch) | $0 | $0.0032 | $0.0057 |

## 1. Question

On synthetic local-services group-buying data, after the weekly monitor raises alerts:

1. Does each approach find the planted cause (which segment, which stage of the funnel)?
2. Do the numbers in a model-written review all trace back to query results? How many reviews pass on the first draft, and how many after one revision?
3. What does letting the model decide what to query (agent) get right, and cost, compared with handing it a fixed set of query results (one prompt)?

The plan was written and the files it depends on were fingerprinted before any model approach ran on the test cases: [`PREREGISTRATION.md`](PREREGISTRATION.md) (in Chinese), fingerprints in [`frozen.json`](frozen.json).

## 2. Cases

36 test cases, [`cases_test.yaml`](cases_test.yaml) (`python -m bizagent.cli make-cases test`; world seeds from 5000, draw seed 23). Each case is a world generated from its own seed, an analysis week and the event planted in that week; the answer is written into the case file when the case is made.

| Group | Cases | Content |
|---|---|---|
| One cause, one dimension | 20 | traffic (channel 2, city 1, category 1, category traffic surge 3), conversion (city 2, category 1), supply (chain merchant goes offline 3), redemption (city 1, category 1, merchant type 1), refund (category 1, city 1, chain merchant 2) |
| One cause, two dimensions | 6 | traffic (city × channel, category × channel), conversion (city × category 2), redemption (city × category), refund (city × category) |
| Two causes | 4 | channel traffic down + category refunds up; city conversion down + category redemption down; category traffic surge + city conversion down; chain offline + channel traffic down |
| Controls | 6 | 4 weeks with no event; a week containing the National Day holiday (the cause is the holiday); the week after it (no event) |

A drafted case is kept only if the monitor raises an alert on a metric the event directly affects (in total or in the event's segment); otherwise it is redrawn with a new seed, up to 12 times. The development set (16 cases, seeds from 1000) was used to write the rules and prompts and shares no seeds with the test set; the monitor thresholds were set on six other seeds (101–106).

## 3. Approaches

| Approach | What it is | Model calls |
|---|---|---|
| Script | [`baseline.py`](../bizagent/baseline.py): fixed rules (start from the strongest alert, follow the funnel to the first stage that changed, drill one level down, explain away segments the found cause already accounts for) | 0 |
| One prompt | [`arms.py`](../bizagent/arms.py) `run_oneshot`: a fixed pack of query results (overview, segment alerts, breakdowns of the top three alerted metrics by every dimension, check-ups of up to six alerted segments) goes to the model, which writes the review | 1, plus 1 revision if the check fails |
| Agent | [`arms.py`](../bizagent/arms.py) `run_agent`: the model queries with four tools (overview, breakdown, segment check-up, read-only SQL), then writes the review | up to 16, including 1 revision if the check fails |

Both model approaches use `claude-haiku-5-5` (effort medium, structured output, the same rule text in the system prompt; the only difference is the tools), run once per case through the Message Batches API. A run that errors, hits the call limit or returns no valid answer counts as not found and not released and stays in the denominator.

## 4. Scoring

All scoring is rule-based ([`score.py`](../bizagent/score.py), [`evidence.py`](../bizagent/evidence.py)); no model grades anything.

- **All causes right (primary):** every planted event found, nothing extra named, nothing answered at a coarser level. A named segment counts as the planted one when both cover the same orders: in the baseline weeks, coverage and purity are both at least 80%. In the holiday week the answer is "holiday"; in the two weeks after a holiday, naming the holiday is neither required nor counted as extra.
- **Stage also right:** all causes right, and each cause's stage (traffic, conversion, supply, redemption, refund, holiday) is right.
- **Released:** the review passes the number check. Every paragraph that contains a number or cites evidence is checked with evidence-check 0.4.0: each cited row must appear verbatim in the cited query result, and each number in the paragraph must appear in the cited rows. Three project rules on top: a finding must contain a number, a cause must cite at least one row, and a review that reports an anomaly must contain a finding.
- Rates come with Wilson 95% intervals. Two approaches are compared case by case; the report gives the cases only one of them got right and an exact binomial (sign) test.

## 5. Results

Model `claude-haiku-5-5`, effort medium, one run per case, Message Batches API. The file fingerprints were checked after the runs: 23 of 23 unchanged. Neither model run was interrupted (no answer was reused from an earlier process). Summary file: [`results/summary-test.json`](results/summary-test.json).

### 5.1 Main table

Percentages with Wilson 95% intervals.

| | Script | One prompt | Agent |
|---|---|---|---|
| All causes right (primary) | 31/36 (86.1%, 71.3–93.9) | 28/36 (77.8%, 61.9–88.3) | 34/36 (94.4%, 81.9–98.5) |
| Stage also right | 31/36 | 28/36 | 34/36 |
| Planted events found / too coarse / missed (of 35) | 33 / 0 / 2 | 28 / 6 / 1 | 34 / 0 / 1 |
| Extra causes named | 7 in 5 cases | 3 in 3 cases | 2 in 2 cases |
| Released after at most one revision | 36/36 (100%, 90.4–100) | 35/36 (97.2%, 85.8–99.5) | 33/36 (91.7%, 78.2–97.1) |
| Released on the first draft | — | 19/36 (52.8%, 37.0–68.0) | 12/36 (33.3%, 20.2–49.7) |
| Model calls per case | 0 | 1.47 | 6.28 |
| Tool calls per case | — | — | 9.56 |
| Cost per case (batch prices) | $0 | $0.00316 | $0.00574 |
| Total cost | $0 | $0.1139 | $0.2068 |

Whenever a cause was found, its stage was also right, so the two accuracy rows are the same.

### 5.2 By group of cases (all causes right)

| Group | Cases | Script | One prompt | Agent |
|---|---|---|---|---|
| One cause, one dimension | 20 | 18 | **20** | **20** |
| One cause, two dimensions | 6 | **6** | 0 | 4 |
| Two causes | 4 | 2 | 3 | **4** |
| Controls | 6 | 5 | 5 | **6** |

### 5.3 Case-by-case comparison (all causes right)

| Pair | Both right | Only the first | Only the second | Neither | Exact p |
|---|---|---|---|---|---|
| Script vs one prompt | 24 | 7 | 4 | 1 | 0.55 |
| Script vs agent | 29 | 2 | 5 | 0 | 0.45 |
| One prompt vs agent | 28 | 0 | 6 | 2 | **0.031** |

### 5.4 The number check

Each failing paragraph is sorted by why it failed. "Number words only" means every number the check could not find comes from a Chinese word containing a numeral (两 "two", 一 "one", 零 "zero"…), such as 两个渠道 ("two channels") or 前一天 ("the day before").

| | One prompt, first draft | One prompt, final | Agent, first draft | Agent, final |
|---|---|---|---|---|
| Paragraphs checked | 297 | 291 | 333 | 327 |
| Paragraphs failed | 28 | 2 | 58 | 4 |
| — number words only | 21 | 2 | 33 | 4 |
| — a number not in the cited row | 3 | 0 | 19 | 0 |
| — other: no row cited, quote not found | 0 | 0 | 4 | 0 |
| — a finding without a number | 4 | 0 | 2 | 0 |
| Reviews held back | 17 | 1 | 24 | 3 |

Every failing paragraph with its category and missing numbers: [`results/check-failures-test.json`](results/check-failures-test.json) (`python -m bizagent.check_failures test-oneshot test-agent`).

One of the agent's four final failures said a chain's orders had 归零 ("dropped to zero"); 零 is read as 0, and it is counted as a number word.

The four reviews held back in the end (one one-prompt, three agent) had no wrong numbers: every figure in them traces back to a query result, and the failing paragraphs failed because of 这两项, 两处, 前一天, 两个, 这两个 or 归零. The check cannot tell a count from a quantity, so it holds back both.

### 5.5 Time

Batch requests are not real-time: the one-prompt run took 15 minutes for 36 cases in 2 batches, the agent run 72 minutes in 11 batches (one batch per round of tool calls). A single real-time agent review of the demo week cost $0.010–0.013 (`usage` in [`run 1`](../reports/sample-agent-2026-W37-run1/result.json) and [`run 2`](../reports/sample-agent-2026-W37-run2/result.json)).

## 6. Where each approach went wrong

| Case | Planted | Script | One prompt | Agent |
|---|---|---|---|---|
| T02 | traffic, channel 搜索 | found it, plus an extra conversion drop in 武汉 | right | right |
| T13 | redemption, merchant type 单店 | found it, plus three sub-segments of 单店 as extra causes | right | right |
| T18 | traffic, 广州 × 搜索 | right | 广州 only (too coarse) | right |
| T19 | traffic, 快餐小吃 × 搜索 | right | 快餐小吃 only (too coarse) | right |
| T20 | conversion, 深圳 × 快餐小吃 | right | 深圳 only (too coarse) | right |
| T21 | conversion, 广州 × 快餐小吃 | right | 广州 only (too coarse) | right |
| T22 | redemption, 上海 × 茶饮咖啡 | right | 茶饮咖啡 only (too coarse), plus refunds in 自助餐 | found it, plus refunds in 自助餐 |
| T23 | refund, 北京 × 快餐小吃 | right | one chain in 快餐小吃 instead | 北京 × one chain in 快餐小吃 instead |
| T27 | traffic 直播 + refund 自助餐 | traffic right; the refund cause named as 自助餐 × 直播 traffic | right | right |
| T29 | traffic surge 茶饮咖啡 + conversion 杭州 | traffic right; 杭州 named as a traffic cause inside 茶饮咖啡 | right | right |
| T30 | chain 烧烤连锁A offline + traffic 搜索 | right | traffic right; the outage named for the whole 烧烤 category (too coarse) | right |
| T36 | none (week after a holiday) | an empty cause with no segment and no stage | a redemption change across the whole market | right |

- **Script**: its fixed drill-down handles single causes and two-dimension cells well (6/6). With two causes it can attach the second cause to the first one's segment, and in a few single-cause cases it keeps drilling and reports segments that only moved because of the first cause.
- **One prompt**: the pack breaks the alerted metrics down one dimension at a time and checks up on alerted segments, so it never sees the two-dimension cell and stops one level up. A cross-dimension breakdown in the pack would probably close most of this gap; that was not tested.
- **Agent**: it can query the cell it suspects, so it found 4 of the 6 two-dimension causes. In T22 the monitor itself flagged a refund rise in 自助餐 (robust Z 7.4) with nothing planted there; both model approaches explained that alert as a second cause, the script did not.

## 7. Predictions and results

| Pre-registered prediction | Result |
|---|---|
| All causes right: agent highest, 80–95%; one prompt and script 65–85% | Agent 94.4%, highest. One prompt 77.8%. Script 86.1%, just above the predicted range. |
| Agent vs script may or may not reach p < 0.05; agent vs one prompt will not | Agent vs script p = 0.45 (not reached). **Agent vs one prompt p = 0.031: reached, against the prediction.** |
| Two-dimension cases: one prompt most often too coarse; two-cause cases: script most often misses or over-reports; controls mostly right | All three held: one prompt 0/6 on two-dimension cases (5 too coarse); script 2/4 on two-cause cases; controls 5–6 of 6. |
| Released: script 100%; model approaches at least 90% after one revision | Held: 100%, 97.2%, 91.7%. |
| First draft: one prompt about 70–85%; agent clearly lower | **One prompt 52.8%, below the predicted range.** Agent 33.3%, clearly lower. |
| Cost: one prompt about $0.003 per case, agent about $0.005, about twice | $0.0032 and $0.0057, 1.8 times. Total $0.32 against the estimated $0.29. |

## 8. Deviations from the pre-registration

None in procedure: the cases, rules, prompts, model, settings and scoring were the fingerprinted ones, each approach ran once per case, and every case is in every denominator. The two model runs ran at the same time, each with its own batches.

## 9. How the rules and prompts were developed

All of this was done on the development set (16 cases, [`cases_dev.yaml`](cases_dev.yaml)) before the freeze.

- The monitor thresholds were set on 30 event-free weeks from six other seeds (101–106): 0 false alerts in totals, 2 in segments ([`results/monitor-false-alerts.json`](results/monitor-false-alerts.json), `python -m bizagent.monitor_check`).
- The script's drill-down was rewritten once: stage first, then segment, and a segment the found cause already explains is dropped. The rewritten version scores 12/16 on the development set ([`results/script-dev/scores.json`](results/script-dev/scores.json)).
- Prompt versions v1–v4. v3 added the structural rules (a finding must have a number, a cause must cite a row) after drafts passed the check by dropping their numbers. v4 says that number words and Chinese numerals are checked too, which took the one prompt's first-draft pass rate on the development set from 7/16 ([`results/dev-oneshot-v3/scores.json`](results/dev-oneshot-v3/scores.json)) to 13/16 ([`results/dev-oneshot-v4/scores.json`](results/dev-oneshot-v4/scores.json)).
- Development results with v4: script 12/16, one prompt 13/16, agent 16/16 ([`results/summary-dev.json`](results/summary-dev.json)).

## 10. Limitations

- The data, the events, the monitor and all three approaches come from the same project. The planted events are clean and sit inside the existing dimensions; real causes often do not.
- 36 cases show only large differences. The agent's lead over the script is not established.
- The recommended actions are not scored.
- The check's strictness on number words lowers the release rate without catching any wrong number. Where to draw that line is a product decision this evaluation does not settle.
- The agent's first drafts quote numbers from rows they do not cite far more often than the one prompt's. One revision fixed every such case here; without the check they would have gone out.

## Reproduce

```bash
python -m bizagent.cli make-cases test
python -m bizagent.freeze --check                       # the fingerprinted files are unchanged
python -m bizagent.cli baseline test                    # script, free
python -m bizagent.cli run oneshot test --out test-oneshot --batch
python -m bizagent.cli run agent test --out test-agent --batch
python -m bizagent.summary script=script-test oneshot=test-oneshot agent=test-agent --out eval/results/summary-test.json
```

Per-case scores are in `results/<run>/scores.json`; the run settings and batch ids in `results/<run>/run.json`.
