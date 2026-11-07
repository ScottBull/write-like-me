=== SYSTEM ===
You are an expert forensic linguist doing authorship verification on short personal and workplace writing.

=== USER ===
Below are reference {kinds} that one person (call them P) wrote. Study P's habits: punctuation, capitalization, length, openers, word choice, hedging, and what P leaves out.

<reference_messages>
{refs}
</reference_messages>

Then come {n} independent trials. In each trial you may see the message P was replying to and a short summary of what P's message needed to say. Exactly one of candidate A and candidate B is the text P actually wrote. The other was written by an AI imitating P. Decide which one P wrote. Judge each trial on its own; the real one is equally likely to be A or B.

{trials}

Reply with ONLY a JSON array, one object per trial, in the same order:
[{"id": "<trial id>", "real": "A" or "B", "confidence": <50-100>, "tell": "<at most 15 words: the main giveaway>"}]

=== LINEUP ===
Below are reference {kinds} that one person (call them P) wrote. Study P's habits: punctuation, capitalization, length, openers, word choice, hedging, and what P leaves out.

<reference_messages>
{refs}
</reference_messages>

Then come {n} independent lineups. Each lineup shows four candidates, A to D, each with the message it was replying to (if any). Three of the four are texts P actually wrote. Exactly one was written by an AI imitating P. Decide which one P did NOT write. Judge each lineup on its own; lineups are unrelated to each other and the AI-written one is equally likely to be in any position.

{trials}

Reply with ONLY a JSON array, one object per lineup, in the same order:
[{"id": "<lineup id>", "not_p": "A", "B", "C" or "D", "confidence": <25-100>, "reason": "<at most 15 words: the main giveaway>"}]
