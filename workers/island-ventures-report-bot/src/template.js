// Fixed report backbone. Section order here is the order rendered in the Doc.
// House style is enforced upstream by the classifier prompt (British spelling,
// no connector dashes, no invented content) — this module only lays out structure.

export const SECTIONS = [
  { key: "exec_summary", heading: "Executive Summary and Macro Insights", level: "HEADING_2" },
  { key: "venue_logistics", heading: "1. General Overview and Venue Atmosphere", level: "HEADING_2" },
  { key: "tech_meta", heading: "2. Tech Meta and Developer Stack Observations", level: "HEADING_2" },
  { key: "sponsor_ecosystem", heading: "3. Sponsor and Ecosystem Dynamics", level: "HEADING_2" },
  { key: "builder_demographics", heading: "4. Builder Dynamics and On-the-Ground Feedback", level: "HEADING_2" },
  { key: "pipeline_radar", heading: "5. Standout Projects to Watch (Pipeline Radar)", level: "HEADING_2" },
  { key: "media_log", heading: "6. Media Log", level: "HEADING_2" },
  { key: "unclassified", heading: "Unclassified / Flagged for Review", level: "HEADING_2" },
];

function formatDate(isoString) {
  if (!isoString) return "[not set]";
  const d = new Date(isoString);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
}

// Builds an ordered list of { text, style } segments. style is one of:
// HEADING_1, HEADING_2, NORMAL_TEXT (segments flagged bullet: true render as a
// bulleted list item; segments flagged tableRow are handled separately by docs.js).
export function compileReportSegments(event, notes) {
  const segments = [];

  segments.push({ text: `Venture Intelligence Report: ${event.name}\n`, style: "HEADING_1" });
  segments.push({
    text:
      `Document ID: IV-VIR-${event.id}\n` +
      `Location: ${event.location || "[not set]"}\n` +
      `Event Date: ${event.event_date || "[not set]"}\n` +
      `Reporting Date: ${formatDate(new Date().toISOString())}\n` +
      `Author: AI Venture Scout, Island Ventures\n` +
      `Governance and Review: Proposes for approval (Chief of Staff and Human Pilots)\n` +
      `Note: Auto-generated from Telegram notes. Freeze with /freeze before hand-editing, ` +
      `edits made while active will be overwritten by the next note.\n`,
    style: "NORMAL_TEXT",
  });

  const notesBySection = {};
  for (const note of notes) {
    if (note.classification_status !== "classified" && note.classification_status !== "flagged") continue;
    const key = note.section_key || "unclassified";
    if (!notesBySection[key]) notesBySection[key] = [];
    notesBySection[key].push(note);
  }

  for (const section of SECTIONS) {
    segments.push({ text: `${section.heading}\n`, style: "HEADING_2" });

    const sectionNotes = notesBySection[section.key] || [];

    if (section.key === "pipeline_radar") {
      if (sectionNotes.length === 0) {
        segments.push({ text: "No standout projects logged yet.\n", style: "NORMAL_TEXT" });
      } else {
        for (const note of sectionNotes) {
          let fields = {};
          try {
            fields = note.pipeline_fields ? JSON.parse(note.pipeline_fields) : {};
          } catch {
            fields = {};
          }
          // Labelled sub-lines instead of a dash-joined line, mirrors the table
          // columns from the reference template (Project / Layer / Stack / Fit)
          // without using a dash as a connector.
          segments.push({
            text: `${fields.project_name || "[unnamed project]"}\n`,
            style: "NORMAL_TEXT",
            bullet: true,
          });
          segments.push({
            text: `Layer/Focus: ${fields.layer_focus || "[focus not captured]"}\n`,
            style: "NORMAL_TEXT",
            bullet: true,
            indent: true,
          });
          segments.push({
            text: `Tech Stack: ${fields.tech_stack || "[stack not captured]"}\n`,
            style: "NORMAL_TEXT",
            bullet: true,
            indent: true,
          });
          segments.push({
            text: `Venture Fit: ${fields.fit_signal || "[signal not captured]"}\n`,
            style: "NORMAL_TEXT",
            bullet: true,
            indent: true,
          });
        }
      }
      continue;
    }

    if (section.key === "media_log") {
      if (sectionNotes.length === 0) {
        segments.push({ text: "No media captured yet.\n", style: "NORMAL_TEXT" });
        continue;
      }
      let photoNumber = 0;
      for (const note of sectionNotes) {
        if (note.image_drive_url) {
          photoNumber += 1;
          segments.push({ image: true, driveUrl: note.image_drive_url });
          segments.push({ text: "\n", style: "NORMAL_TEXT" });
          segments.push({
            text: `Photo ${photoNumber}: ${note.placement_text || note.raw_text || "[no caption provided]"}\n`,
            style: "NORMAL_TEXT",
            bullet: true,
            indent: true,
          });
        } else {
          // Text-only media log entry (typed description, no attached photo).
          segments.push({
            text: `☐ ${note.placement_text || note.raw_text}\n`,
            style: "NORMAL_TEXT",
            bullet: true,
          });
        }
      }
      continue;
    }

    if (sectionNotes.length === 0) {
      segments.push({ text: "No notes captured yet.\n", style: "NORMAL_TEXT" });
      continue;
    }

    for (const note of sectionNotes) {
      const line =
        section.key === "unclassified" && note.flag_reason
          ? `${note.placement_text || note.raw_text} (flagged: ${note.flag_reason})`
          : note.placement_text || note.raw_text;
      segments.push({ text: `${line}\n`, style: "NORMAL_TEXT", bullet: true });
    }
  }

  segments.push({ text: "References Checked\n", style: "HEADING_2" });
  segments.push({
    text: "Field notes captured live via Telegram during the event, classified and compiled automatically.\n",
    style: "NORMAL_TEXT",
    bullet: true,
  });

  return segments;
}
