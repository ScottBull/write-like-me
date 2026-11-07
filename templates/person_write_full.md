=== SYSTEM ===
You extend a short description of a person (the "core", given below, already final) with further sections that go deeper: their opinions by topic, the things they return to, and more detail on how they work and reason. The extension is appended after the core, so it must not repeat the core's sentences or restate claims the core already uses; it adds what the core left out. Together they sit in front of an archive of the person's own writing when a model drafts text in their voice, so they carry the judgment layer only: never their style, which the archive shows by example.

Format rules, which a deterministic checker enforces after you:
- Every sentence ends with a citation marker listing the claim numbers it rests on: ... [c23] or ... [c23, c41].
- A sentence with no marker is deleted by the checker, not fixed. A marker naming a claim number not in the list is deleted with its sentence. Cite only numbers from the list, and cite every sentence.
- Write in the third person (the pronoun the claims use), present tense for what holds now. Group opinions under topic sub-headings (### Topic) you choose from what the claims cover. Short paragraphs or short bullets. No preamble, no closing remark, no commentary about the task.
- Claims marked "(single source)" are biographical facts stated once: you may use them, worded plainly.
- Where a claim carries an evolution note, say what changed and roughly when.
- Do not soften, hedge or balance a stance the claims state plainly, and do not add stances, facts or examples the claims do not carry. Do not quote their words.
- Do not name any third party beyond "a coworker", "their partner", "a friend", "a family member", "a manager". No client or customer names, product brands, project code names, budgets, headcounts.
- Nothing about their writing style, tone, punctuation, vocabulary or formatting.

Length: about {target_tokens} tokens of prose (roughly {target_chars} characters, not counting the markers). Prefer breadth of well-supported claims over elaboration; leave out weak ones rather than pad.

Sections, in this order, with these headings:
{sections}

=== USER ===
{frame}

The core, already written (do not repeat its content; the numbers in brackets are the claims it used):

{core}

Claims ({claim_count}; those the core already used are marked "(used in core)" and should be cited again only when a new point needs them). Each line: number, kind, number of supporting entries, years, then the claim; "evolution:" notes a change over time.

{claims}
