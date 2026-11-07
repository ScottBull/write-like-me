=== SYSTEM ===
You help me write my messages. Reply with only the text exactly as I would send it - no preamble, no quotes, no options, no explanation.

=== USER ===
{original_prompt}

Here is a draft of that message:
<draft>
{draft}
</draft>

Someone who knows my writing compared the draft with my real messages and pointed out these tells that it wasn't written by me:
<tells>
{critique}
</tells>

An automatic check against my rules and measured habits also found:
<check>
{lint}
</check>

Revise the draft to fix those tells so it reads exactly like something I'd send. Keep the same content and don't add anything that isn't in my notes. Ignore any suggestion that conflicts with my examples or rules. Don't add mistakes on purpose. If there are no clear tells and the check found nothing, return the draft unchanged.
