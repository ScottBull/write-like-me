=== SYSTEM ===
You merge a list of claims about one person into a shorter list without losing anything true. All the claims are of one kind and were extracted from different batches of their writing, so many say the same thing in different words, and some show a view changing over time.

Rules:
- Merge claims that make the same point into one claim. Keep the stronger, more specific wording. Each merged claim lists the input claim numbers it came from in "from".
- Keep claims that make different points separate, even when they share a topic. Do not merge a stance with its opposite: if the inputs show a change over time, write the later position as the claim and describe the change in "evolution" ("thought X in 2019-2021, Y by 2024"); the "from" list then holds both.
- Do not invent. Every output claim must be supported by its "from" inputs. Do not add claims, qualifiers or specifics the inputs do not carry. Do not drop a claim because it is unflattering.
- Do not describe the person's writing style; drop any input that does (and list it in "dropped" with the reason "style").
- No named third parties beyond "a coworker", "their partner", "a friend", "a family member", "a manager", "a stranger online". No client or customer names, product brands, project code names, budgets, headcounts. Rewrite an input that carries one at the generic level, or drop it and list it in "dropped" with the reason "confidential" or "third party".
- Write each claim as one declarative sentence in the third person, with the pronoun the inputs use.

Output: a JSON object only, no prose before or after, no code fence:
{"claims": [{"claim": "<sentence>", "from": [<input numbers>], "evolution": "<text or null>"}], "dropped": [{"from": <input number>, "reason": "style|confidential|third party"}]}

=== USER ===
Kind: {kind}. {kind_note}

{claim_count} input claims. Each line: number, confidence, years the supporting entries span, number of supporting entries, then the claim.

{claims}
