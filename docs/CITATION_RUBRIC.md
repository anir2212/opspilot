# Semantic citation review: rubric and labelling instructions

This rubric is for **human labellers**. It decides whether one cited source supports one claim written by the OpsPilot model. A model or script must not fill in these labels. Citation-ID validity, meaning the ID exists in the agent's context, is not evidence of support.

## What you are judging

Each packet item shows two things:

- **CLAIM**: one hypothesis or recommendation sentence written by the model.
- **SOURCE**: the exact evidence object the model cited for that sentence, as JSON. It can be an alert, a fixture metric or log observation, a deploy or backup record, a runbook excerpt, a block of runbook search results, or the read-only policy note.

Ask one question: **does this source, read on its own, support this claim?** Do not judge whether the claim is true in the real world, whether it is good advice, or whether a different source would support it. Do not use outside knowledge to fill gaps.

## Labels

Use exactly one of these three values in the `label` column.

| Label | Use it when |
|---|---|
| `supports_claim` | The source states, or directly and unambiguously implies, **every** factual assertion in the claim. For a recommendation ("check X", "follow step Y"), the source explicitly contains that action for this kind of situation. |
| `unsupported` | Any key assertion in the claim is missing from the source, contradicted by it, or about a different service, component, cause, or number. This includes partial support. |
| `ambiguous` | After applying the edge-case rules below, a careful reader could still reasonably choose either other label. Also use it when the claim is too vague to check, or the source text is unreadable. Use it sparingly. A note is **required**. |

## Edge cases

1. **Partial support.** If the claim makes two assertions and the source supports only one, label `unsupported`. Write "partial" in the note.
2. **Causal claims.** "X caused Y" needs the source to state or directly show the causal link. If the source shows only that X and Y both occurred, or describes only Y, label `unsupported`.
3. **Hedged claims.** "May", "possible", or "could" lowers the claim's strength, but it is not a free pass. Label `supports_claim` only if the source presents the hedged possibility as a candidate for this signal, for example a runbook listing it as a likely cause. A hedge about something the source never mentions is `unsupported`.
4. **Runbooks as sources.** A runbook describes procedures and typical causes. It does not show that this incident is happening. "Check disk usage" cited to a runbook that lists that check is `supports_claim`. "The disk is full" cited only to a runbook is `unsupported`.
5. **Alerts as sources.** An alert supports claims about its own labels and annotations, such as service, alert name, status and description text. It does not support a specific root cause unless the description states it.
6. **Search-result blocks.** If the source is a list of several runbook results, the claim is supported if **any** shown result supports it fully. Name that result in the note.
7. **Truncated text.** Runbook excerpts are cut at 1,800 characters, which is the text the model saw. Judge only what is shown. A claim that relies on unseen text is `unsupported`.
8. **Vague claims.** "Investigate the issue" or "monitor the service" has no checkable content. Label `ambiguous` and write "vacuous" in the note, unless the source explicitly recommends that action.
9. **Policy note.** `read-only investigation; changes require approval` supports claims that no change was made or that changes need approval. It supports nothing else.
10. **Numbers and names.** Services, alert names, error codes, thresholds and counts must match. A mismatch makes the claim `unsupported`.
11. **Redaction marker.** `[scenario-id-redacted]` replaces scenario IDs and alert fingerprints so you cannot see the test case's intended answer. Ignore it. Do not try to guess what it hid.
12. **Self-reference.** A claim that only repeats the source's own wording, such as "the alert reports the scheduled backup job failed", is `supports_claim` when the wording matches.

## Labelling procedure

1. The packet folder is `eval/citation_review/packet/`. Each labeller uses their own file there: labeller A uses `labels_A.csv`, labeller B uses `labels_B.csv`. `packet.txt` shows the same items in a readable layout.
2. **Do not open** `eval/citation_review/sealed/`, `eval/citation_review/export_*.jsonl`, or `eval/citation_review/agent_results_*.json` until both labellers have finished. They reveal the model, scenario and generation path.
3. Label independently. Do not discuss items with the other labeller until both files are complete.
4. Fill only the `label` and `note` columns. Do not edit `item_id` or `claim_text`; the scorer refuses edited claim text. Every row needs a label.
5. A note is required for `ambiguous` and recommended for `unsupported`.
6. Record your name or initials, the date and the approximate time spent. Give these to whoever updates `RESULTS.md` so the labels have provenance.

## Scoring

```sh
uv run python eval/citation_packet.py score \
  --key eval/citation_review/sealed/key.json \
  --labels eval/citation_review/packet/labels_A.csv eval/citation_review/packet/labels_B.csv \
  --out eval/citation_review/scores.json
```

The scorer refuses to run if a file has missing, unknown or duplicate item IDs, blank or invalid labels, or edited claim text. It reports:

- **Per labeller**: precision = `supports_claim / (supports_claim + unsupported)`, the labelled denominator, a Wilson 95% interval, and the ambiguous and unsupported counts. Precision is also broken down by generation path.
- **Two labellers**: agreements, disagreements, percent agreement, Cohen's kappa over the three labels, the confusion counts, and the IDs of disagreed items.
- **Consensus precision**: the same formula over items where both labellers chose the same non-ambiguous label.

After independent scoring, the labellers may discuss disagreements and write an adjudicated file, `labels_adjudicated.csv`. Score that file alone to get adjudicated precision. Report it **alongside** the independent agreement figures, not instead of them.

## Scope of the resulting number

The precision is conditional on the selection rules in `src/opspilot/citation_review.py` (`eligible_pairs`). Only pairs written by the model with a resolvable cited source are eligible. Validator fallback sentences, unresolved citation IDs and exact duplicate pairs are excluded and counted in the key's `pool_counts`. The scenarios are authored synthetic fixtures. The result describes this local model on these fixtures, not production incident advice.
