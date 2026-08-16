// Telegram update parsing, outbound replies, and command parsing.

export async function sendMessage(env, chatId, text) {
  const resp = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text, disable_web_page_preview: false }),
  });
  if (!resp.ok) {
    // Never throw here — a failed reply shouldn't crash note processing that already succeeded.
    console.error(`Telegram sendMessage failed (${resp.status})`);
  }
}

// Returns { kind: "text", chatId, userId, text, messageId, updateId }
// or { kind: "photo", chatId, userId, fileId, caption, messageId, updateId }
// or null if the update isn't something this bot handles (edited_message,
// channel_post, stickers, voice, etc.).
export function parseUpdate(update) {
  const message = update.message;
  if (!message) return null;

  const chatId = String(message.chat.id);
  const userId = String(message.from?.id ?? "");
  const messageId = message.message_id;
  const updateId = update.update_id;

  if (typeof message.text === "string") {
    return { kind: "text", chatId, userId, text: message.text.trim(), messageId, updateId };
  }

  if (Array.isArray(message.photo) && message.photo.length > 0) {
    // Telegram sends the same photo at several resolutions; the last entry
    // is the largest (PhotoSize objects are ordered smallest to largest).
    const largest = message.photo[message.photo.length - 1];
    return {
      kind: "photo",
      chatId,
      userId,
      fileId: largest.file_id,
      caption: typeof message.caption === "string" ? message.caption.trim() : "",
      messageId,
      updateId,
    };
  }

  return null;
}

// Fetches a Telegram file's temporary download URL, then downloads its bytes.
// This URL is only guaranteed valid briefly, so callers must consume it
// immediately (e.g. by uploading straight to Drive), never store it.
export async function downloadTelegramFile(env, fileId) {
  const infoResp = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/getFile?file_id=${fileId}`);
  if (!infoResp.ok) throw new Error(`Telegram getFile failed (${infoResp.status})`);
  const info = await infoResp.json();
  const filePath = info?.result?.file_path;
  if (!filePath) throw new Error("Telegram getFile returned no file_path");

  const fileResp = await fetch(`https://api.telegram.org/file/bot${env.TELEGRAM_BOT_TOKEN}/${filePath}`);
  if (!fileResp.ok) throw new Error(`Telegram file download failed (${fileResp.status})`);

  const bytes = new Uint8Array(await fileResp.arrayBuffer());
  const contentType = fileResp.headers.get("content-type") || "image/jpeg";
  return { bytes, contentType, filePath };
}

// Returns { command, args } if text starts with a slash command, else null.
export function parseCommand(text) {
  if (!text.startsWith("/")) return null;
  const [rawCommand, ...rest] = text.split(/\s+/);
  const command = rawCommand.slice(1).toLowerCase();
  return { command, args: rest.join(" ").trim() };
}

export const HELP_TEXT = `Island Ventures field report bot

Send any plain text message to log it as a field note for the active event.
Send a photo (with an optional caption) to log it in the Media Log — it gets
uploaded to Drive and embedded in the Doc automatically, auto-numbered.

Commands:
/newevent <name>    Start a new report (creates a fresh Google Doc)
/status              Show the active event and note counts
/switchevent <name>  Switch which event new notes are logged against
/events              List all events for this chat
/freeze               Stop auto-rewriting the Doc (protect hand edits before submission)
/unfreeze             Resume auto-rewriting the Doc
/endevent             Archive the active event
/help                 Show this message`;
