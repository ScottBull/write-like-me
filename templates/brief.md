=== SYSTEM ===
You turn messages into content-only briefs for a writing test. Output only valid JSON, nothing else.

=== USER ===
I'm building a blind test of whether an assistant can write like me. Below is a {kind} I sent, plus the message it followed (if any).

Write a CONTENT-ONLY brief: the note I could have jotted for an assistant ("here's what I want to say") so it could draft the message. The assistant will draft from the brief plus the same context, and the draft will be compared with my real message. So the brief must carry the substance and none of the style.

Write it as terse first-person notes: "I" = me, "they" = the recipient (use names only if the message names someone). Separate points with semicolons.

Include ONLY what the message itself conveys:
- every fact, number, name, product and time reference in it
- the point of each part: answering, asking, informing, suggesting, agreeing, declining, praising
- any question or request, stated as an intent ("ask whether X", "ask them to send Y")
- an opinion or recommendation where it is part of the substance

Do NOT include:
- any run of 3+ consecutive words from the message (names, numbers and addresses excepted). Paraphrase everything else.
- slang, idioms, interjections, intensifiers, hedges or jokes from the message, even paraphrased into an instruction. Never write things like "react with amusement", "express uncertainty", "tentatively", "casually"
- how confident or emotional I sound, how it opens or closes, whether it greets or names the person, tone, formality, length, punctuation, capitalization or emoji
- anything the message doesn't say: no background from the context message, no explanations, no expanded acronyms
- any instruction about how the reply should sound

Keep the brief no longer than the message itself. Before answering, check the brief against the message for any shared 3-word run and remove it.

Message it followed (context):
<<<
{context}
>>>

My message:
<<<
{text}
>>>

Output JSON exactly like: {"brief": "..."}
