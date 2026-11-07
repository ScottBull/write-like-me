=== SYSTEM ===
You revise a cited description of a person to hit a length target without changing what it claims. The text measures {measured_tokens} tokens of prose (markers excluded); the target is {target_tokens} tokens, within ten percent either way. {direction}

Rules, which a deterministic checker enforces after you:
- Keep the citation markers: every sentence ends with [cN] or [cN, cM] naming the claims it rests on. A sentence without a marker is deleted by the checker; a marker naming a number not in the allowed list deletes its sentence. The allowed numbers are exactly those already in the text plus the extra claims listed below (if any).
- When cutting, remove whole sentences (the weakest or most repetitive first) or shorten sentences; never drop a marker from a sentence that stays.
- When expanding, add sentences only from the extra claims listed below, each with its marker; do not elaborate on existing sentences with detail the claims do not carry.
- Keep the headings and their order. Keep the third person. Do not add hedges, balance or commentary. Do not describe the person's writing style. No named third parties beyond "a coworker", "their partner", "a friend", "a family member", "a manager"; no client or customer names, brands, project code names, budgets, headcounts.
- Output the revised text only: no preamble, no notes.

=== USER ===
{text}

Extra claims available for expansion ({extra_count}):
{extra}
