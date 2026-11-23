# Model assist rubric (ADR 0023)

The prompt is `src/sebastian/assist_prompt.md`. A model answer is scored per clause on TWO separate axes, never mixed:

1. **Kind** (done, defer, forgot, delete, keep, remind, plan, add, read, info, other): right when the model's acting kinds equal the owner's meaning.
2. **Reference**: right when the candidate ids chosen for each kind are exactly the items the owner meant (no extra, none missing). A wrong item is
   worse than a wrong kind: it changes the wrong record. Scored as the wrong-item rate (ids acted on that the gold did not name).

Dates and clock times are NOT scored on the model: code reads them. A model answer is rejected (and the rules' answer stands) when:
an id is outside the supplied list, a span is not part of the message, a kind or a field is outside the schema, a defer has no day in the words, or
a kind that needs an item has none.

Rules a reviewer applies when judging a disagreement in the shadow log (`audit_log` rows `nl_assist`):
- finished beats deleted ('bitti' is done, never delete);
- 'yetisemedi', 'olmadi', 'gittim ama kapaliydi' are NOT done;
- a noun that looks like a verb is not a verb; chatter around a statement is `info`;
- two equally good candidates: the model must answer `other`, not pick one.
Promotion and kill criteria: ADR 0023.
