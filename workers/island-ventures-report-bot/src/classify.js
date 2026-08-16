// Classifies one raw field note into a report section via the Anthropic Messages API.
// Hard constraint: never invent content. When unsure, flag rather than force.

const SYSTEM_PROMPT = `You are a strict field-note classifier for Island Ventures' event scouting reports.
You receive ONE raw text note captured live at an event via Telegram. Your job:

1. Decide which ONE section of the fixed report backbone it belongs to.
2. Lightly clean the note into a placement-ready bullet: fix obvious typos, expand
   shorthand, use British English spelling throughout, remove filler ("um", "like").
   NEVER add facts, names, numbers, opinions, or context absent from the original note.
   Never infer motive, sentiment, or outcome beyond what was stated. A fragment
   should stay a fragment rather than be completed with invented detail.
3. If the note does not clearly fit one section, or you are not confident, classify
   it as "unclassified" and set confident=false. When in doubt, flag rather than force.

Sections (mapped to Island Ventures' four scouting pillars):
- exec_summary: macro thesis alignment, geopolitical/location context, genuine
  summary-level takeaways only (rare, most notes are NOT exec_summary)
- venue_logistics [Pillar: Venue & Logistical Execution]: phase/timeline, room
  energy/vibe, spatial layout, participant dynamics, wifi/desks/lounge infra
- tech_meta [Pillar: Emerging Product & Tech Meta]: dev tools visible,
  frameworks/protocol stacks, dominant project themes
- sponsor_ecosystem [Pillar: Sponsor & Ecosystem Dynamics]: sponsor booth
  traffic, integration hurdles/mentor feedback, bounty strategy
- builder_demographics [Pillar: Builder & Participant Demographics]: team
  structure, dev/designer/business ratio, mentor or hacker quotes
- pipeline_radar: a specific standout project worth tracking (name + focus +
  stack + why it is a venture-fit signal)
- media_log: a note that a photo/video/evidence was captured (log entry only)
- unclassified: anything else, or anything ambiguous

Style rules (always applied to placement_text):
- British English spelling (organise, colour, programme, ...)
- Never use em dashes or en dashes as a sentence connector. Hyphens in
  compound words are fine. An em dash is allowed ONLY inside a direct
  quote's attribution, e.g. "quote" — Speaker Name.
- Never end a heading or label with a period.

Respond with ONLY valid JSON, no prose, exactly matching:
{
  "confident": boolean,
  "section_key": "exec_summary"|"venue_logistics"|"tech_meta"|"sponsor_ecosystem"|
                  "builder_demographics"|"pipeline_radar"|"media_log"|"unclassified",
  "placement_text": string,
  "pipeline_fields": {"project_name": string|null, "layer_focus": string|null,
                       "tech_stack": string|null, "fit_signal": string|null} | null,
  "flag_reason": string | null
}
Only set pipeline_fields when section_key is "pipeline_radar". Only set a
non-null flag_reason when section_key is "unclassified" or confident is
false — a short, honest reason (max 15 words), not an invented one.`;

const VALID_SECTIONS = new Set([
  "exec_summary",
  "venue_logistics",
  "tech_meta",
  "sponsor_ecosystem",
  "builder_demographics",
  "pipeline_radar",
  "media_log",
  "unclassified",
]);

export async function classifyNote(env, eventName, rawText) {
  let responseText = null;
  try {
    const resp = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "x-api-key": env.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: "claude-haiku-4-5-20251001",
        max_tokens: 300,
        temperature: 0,
        system: SYSTEM_PROMPT,
        messages: [{ role: "user", content: `Event: ${eventName}\nNote: ${rawText}` }],
      }),
    });

    if (!resp.ok) {
      return fallbackUnclassified(rawText, `classifier API error (${resp.status})`);
    }

    const data = await resp.json();
    responseText = data?.content?.[0]?.text ?? null;
    if (!responseText) {
      return fallbackUnclassified(rawText, "empty classifier response");
    }

    const parsed = JSON.parse(responseText);

    if (!VALID_SECTIONS.has(parsed.section_key) || typeof parsed.placement_text !== "string") {
      return fallbackUnclassified(rawText, "model output failed schema validation", responseText);
    }

    return {
      status: parsed.section_key === "unclassified" ? "flagged" : "classified",
      sectionKey: parsed.section_key,
      placementText: parsed.placement_text,
      pipelineFields: parsed.section_key === "pipeline_radar" ? parsed.pipeline_fields : null,
      flagReason: parsed.flag_reason ?? null,
      rawModelResponse: responseText,
    };
  } catch (err) {
    return fallbackUnclassified(rawText, `classifier exception: ${err.message}`, responseText);
  }
}

function fallbackUnclassified(rawText, reason, rawModelResponse = null) {
  return {
    status: "flagged",
    sectionKey: "unclassified",
    placementText: rawText,
    pipelineFields: null,
    flagReason: `${reason}, flagged automatically`,
    rawModelResponse,
  };
}
