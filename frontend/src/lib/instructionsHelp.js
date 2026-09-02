/**
 * Shared copy for every "Instructions (optional)" box — Pipelines and Batch
 * both send this text to the same backend field (user_instructions) with the
 * same 2000-char cap (backend/app/schemas/schemas.py), so the UI text and
 * the limit live in one place rather than two copies that can drift.
 */

// Matches AgentRunRequest.user_instructions / the Form(max_length=2000) on
// POST /batch in backend/app/api/routes/batch.py.
export const INSTRUCTIONS_MAX_LEN = 2000;

export const INSTRUCTIONS_HELP_TEXT =
  "Read by the AI alongside your document — it genuinely shapes what the summary " +
  "focuses on and what gets flagged. Leave it blank for the standard extraction.";

// Concrete rather than vague — "any specific focus or context" told the user
// nothing about what a good instruction looks like or that the field does
// anything at all. One real example per domain beats a generic placeholder.
export const INSTRUCTIONS_EXAMPLE = {
  finance:    "e.g. Flag any line item over $1,000 and note if the tax rate looks unusual",
  healthcare: "e.g. Pay particular attention to dosage instructions and drug interactions",
  legal:      "e.g. Focus on termination clauses and any auto-renewal terms",
  logistics:  "e.g. Highlight if the declared value looks inconsistent with the item list",
  hr:         "e.g. Weigh recent experience more heavily than older roles",
  education:  "e.g. Flag any grade below a B and double-check the GPA calculation",
  government: "e.g. Note any deadline within the next 30 days",
  general:    "e.g. Summarize in plain language for someone unfamiliar with this topic",
  default:    "e.g. Focus on the payment terms and flag anything unusual",
};

export function instructionsPlaceholder(domain) {
  return INSTRUCTIONS_EXAMPLE[domain] || INSTRUCTIONS_EXAMPLE.default;
}
