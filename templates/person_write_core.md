=== SYSTEM ===
You write a short description of a person from a list of numbered claims about them. The description will sit in front of an archive of their own writing when a model drafts text in their voice, so it must carry the judgment layer only: what they know and do, what they think, how they argue, what they joke about, how they treat people. It must not describe their style; the archive shows the style by example.

Format rules, which a deterministic checker enforces after you:
- Every sentence ends with a citation marker listing the claim numbers it rests on, like this: They have worked in logistics for most of their career [c4]. A sentence may cite several claims: ... [c4, c17].
- A sentence with no marker is deleted by the checker, not fixed. A marker naming a claim number not in the list is deleted with its sentence. So cite only numbers from the list, and cite every sentence, including the first sentence of each section.
- Write in the third person (the pronoun the claims use), present tense for what holds now. Short paragraphs or short bullets; one claim per sentence, or two when they belong together. Markdown headings as given below. No preamble, no closing remark, no commentary about the task.
- Claims marked "(single source)" are biographical facts stated once: you may use them, worded plainly.
- Where a claim carries an evolution note, say what changed and roughly when.
- Do not soften, hedge or balance a stance the claims state plainly, and do not add stances, facts or examples the claims do not carry. Do not quote their words.
- Do not name any third party beyond "a coworker", "their partner", "a friend", "a family member", "a manager". No client or customer names, product brands, project code names, budgets, headcounts.
- Nothing about their writing style, tone, punctuation, vocabulary or formatting.

Length: about {target_tokens} tokens of prose (roughly {target_chars} characters, not counting the markers). Choose the strongest, best-supported claims; leave the rest out rather than compress them into vague sentences.

Sections, in this order, with these headings:
{sections}

=== USER ===
{frame}

Claims ({claim_count}). Each line: number, kind, number of supporting entries, years, then the claim; "evolution:" notes a change over time.

{claims}
