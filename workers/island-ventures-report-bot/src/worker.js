import {
  initSchema,
  getActiveEvent,
  createEvent,
  findEventsByName,
  setActiveEvent,
  clearActiveSession,
  setEventStatus,
  listEvents,
  insertNote,
  updateNoteClassification,
  setNoteImage,
  getNotesForEvent,
  markNotesSynced,
  getNoteCounts,
  getLastNoteTime,
} from "./db.js";
import { sendMessage, parseUpdate, parseCommand, downloadTelegramFile, HELP_TEXT } from "./telegram.js";
import { classifyNote } from "./classify.js";
import { createDoc, replaceDocBody, uploadImageToDrive } from "./docs.js";
import { compileReportSegments } from "./template.js";

async function recompileDoc(env, db, event) {
  const notes = await getNotesForEvent(db, event.id);
  const segments = compileReportSegments(event, notes);
  await replaceDocBody(env, db, event.google_doc_id, segments);
  const syncedIds = notes
    .filter((n) => n.classification_status === "classified" || n.classification_status === "flagged")
    .map((n) => n.id);
  await markNotesSynced(db, syncedIds);
}

async function handleCommand(env, db, chatId, command, args) {
  switch (command) {
    case "help":
      return HELP_TEXT;

    case "newevent": {
      if (!args) return "Usage: /newevent <name>";
      const doc = await createDoc(env, db, `Venture Intelligence Report: ${args}`);
      const result = await createEvent(db, { chatId, name: args, docId: doc.id, docUrl: doc.url });
      if (result.error === "duplicate") {
        return `An event named "${args}" already exists. Use /switchevent ${args} to resume it, or pick a different name.`;
      }
      // Seed the Doc with the empty template skeleton immediately.
      const event = await getActiveEvent(db, chatId);
      await recompileDoc(env, db, event);
      return `Started "${args}". Doc: ${doc.url}\nSend plain text messages to log notes.`;
    }

    case "status": {
      const event = await getActiveEvent(db, chatId);
      if (!event) return "No active event. Use /newevent <name> to start one.";
      const counts = await getNoteCounts(db, event.id);
      const lastAt = await getLastNoteTime(db, event.id);
      const countsText = counts.length
        ? counts.map((c) => `${c.classification_status}: ${c.count}`).join(", ")
        : "no notes yet";
      return (
        `Active event: ${event.name} (${event.status})\n` +
        `Doc: ${event.google_doc_url}\n` +
        `Notes: ${countsText}\n` +
        `Last note: ${lastAt || "none"}`
      );
    }

    case "switchevent": {
      if (!args) return "Usage: /switchevent <name>";
      const matches = await findEventsByName(db, chatId, args);
      if (matches.length === 0) return `No event found matching "${args}". Use /events to list all.`;
      if (matches.length > 1) {
        const names = matches.map((e) => e.name).join(", ");
        return `Multiple matches: ${names}. Be more specific.`;
      }
      await setActiveEvent(db, chatId, matches[0].id);
      return `Switched to "${matches[0].name}". Doc: ${matches[0].google_doc_url}`;
    }

    case "events": {
      const events = await listEvents(db, chatId);
      if (events.length === 0) return "No events yet. Use /newevent <name> to start one.";
      return events.map((e) => `${e.name} — ${e.status}`).join("\n");
    }

    case "freeze": {
      const event = await getActiveEvent(db, chatId);
      if (!event) return "No active event.";
      await setEventStatus(db, event.id, "frozen");
      return `Frozen "${event.name}". New notes will still be logged and classified, but the Doc won't be rewritten until /unfreeze. Safe to hand-edit now.`;
    }

    case "unfreeze": {
      const event = await getActiveEvent(db, chatId);
      if (!event) return "No active event.";
      await setEventStatus(db, event.id, "active");
      const refreshed = await getActiveEvent(db, chatId);
      await recompileDoc(env, db, refreshed);
      return `Unfrozen "${event.name}" and recompiled the Doc from all logged notes. Any hand edits made while frozen are now overwritten.`;
    }

    case "endevent": {
      const event = await getActiveEvent(db, chatId);
      if (!event) return "No active event.";
      await setEventStatus(db, event.id, "archived");
      await clearActiveSession(db, chatId);
      return `Archived "${event.name}". Use /newevent <name> to start a new one.`;
    }

    default:
      return `Unknown command /${command}. Send /help for the list.`;
  }
}

async function handleNote(env, db, chatId, parsed) {
  const event = await getActiveEvent(db, chatId);
  if (!event) {
    return "No active event. Use /newevent <name> to start one before sending notes.";
  }

  const { inserted, noteId } = await insertNote(db, {
    eventId: event.id,
    chatId,
    telegramMessageId: parsed.messageId,
    telegramUpdateId: parsed.updateId,
    rawText: parsed.text,
  });

  if (!inserted) {
    return "Already logged that one.";
  }

  const classification = await classifyNote(env, event.name, parsed.text);
  await updateNoteClassification(db, noteId, classification);

  if (event.status !== "frozen") {
    const refreshed = await getActiveEvent(db, chatId);
    await recompileDoc(env, db, refreshed);
  }

  if (classification.status === "flagged") {
    return `Flagged for review: ${classification.flagReason}`;
  }
  return `Logged under: ${classification.sectionKey}`;
}

// Photos always land in media_log directly — no classifier call, since a
// photo isn't text to classify, only its caption is, and the section is
// already unambiguous.
async function handlePhoto(env, db, chatId, parsed) {
  const event = await getActiveEvent(db, chatId);
  if (!event) {
    return "No active event. Use /newevent <name> to start one before sending photos.";
  }

  const { inserted, noteId } = await insertNote(db, {
    eventId: event.id,
    chatId,
    telegramMessageId: parsed.messageId,
    telegramUpdateId: parsed.updateId,
    rawText: parsed.caption || "[no caption provided]",
  });

  if (!inserted) {
    return "Already logged that one.";
  }

  await updateNoteClassification(db, noteId, {
    status: "classified",
    sectionKey: "media_log",
    placementText: parsed.caption || "[no caption provided]",
  });

  try {
    const { bytes, contentType } = await downloadTelegramFile(env, parsed.fileId);
    const uploaded = await uploadImageToDrive(env, db, bytes, contentType, `field-photo-${noteId}.jpg`);
    await setNoteImage(db, noteId, { driveId: uploaded.id, driveUrl: uploaded.url });
  } catch (err) {
    // The note itself (caption + media_log placement) is already saved even if
    // the image upload fails — better a captioned placeholder than losing the note.
    console.error(`Photo upload failed for note ${noteId}: ${err.message}`);
    await updateNoteClassification(db, noteId, {
      status: "classified",
      sectionKey: "media_log",
      placementText: `${parsed.caption || "[no caption provided]"} (photo upload failed, resend if needed)`,
    });
  }

  if (event.status !== "frozen") {
    const refreshed = await getActiveEvent(db, chatId);
    await recompileDoc(env, db, refreshed);
  }

  return "Photo logged in the Media Log.";
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (url.pathname === "/" || url.pathname === "/health") {
      return new Response("ok", { status: 200 });
    }

    if (url.pathname !== "/telegram-webhook" || request.method !== "POST") {
      return new Response("not found", { status: 404 });
    }

    const secretHeader = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
    if (secretHeader !== env.TELEGRAM_WEBHOOK_SECRET) {
      return new Response("unauthorized", { status: 401 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("bad request", { status: 400 });
    }

    const parsed = parseUpdate(update);
    if (!parsed) {
      // Not something this bot handles (sticker, voice, edited message, etc.) — acknowledge and ignore.
      return new Response("ok", { status: 200 });
    }

    if (env.TELEGRAM_ALLOWED_CHAT_ID && parsed.chatId !== env.TELEGRAM_ALLOWED_CHAT_ID) {
      return new Response("ok", { status: 200 }); // silently ignore, don't leak bot behaviour to strangers
    }

    try {
      await initSchema(env.DB);

      let reply;
      if (parsed.kind === "photo") {
        reply = await handlePhoto(env, env.DB, parsed.chatId, parsed);
      } else {
        const cmd = parseCommand(parsed.text);
        reply = cmd
          ? await handleCommand(env, env.DB, parsed.chatId, cmd.command, cmd.args)
          : await handleNote(env, env.DB, parsed.chatId, parsed);
      }

      await sendMessage(env, parsed.chatId, reply);
      return new Response("ok", { status: 200 });
    } catch (err) {
      console.error(`Webhook handler error: ${err.message}`);
      // Best-effort error notification; if this also fails, the try/catch below just swallows it.
      try {
        await sendMessage(env, parsed.chatId, "Something went wrong processing that. It may not have been logged, please resend.");
      } catch {}
      return new Response("ok", { status: 200 }); // 200 so Telegram doesn't retry-storm us
    }
  },
};
