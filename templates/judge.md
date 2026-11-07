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
