// D1 schema init + query helpers.
// House rule: always prepare().run() / prepare().all(), never db.exec().

const SCHEMA = [
  `CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    google_doc_id TEXT,
    google_doc_url TEXT,
    event_date TEXT,
    location TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(chat_id, slug)
  )`,
  `CREATE TABLE IF NOT EXISTS active_session (
    chat_id TEXT PRIMARY KEY,
    active_event_id INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(active_event_id) REFERENCES events(id)
  )`,
  `CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    chat_id TEXT NOT NULL,
    telegram_message_id INTEGER NOT NULL,
    telegram_update_id INTEGER,
    raw_text TEXT NOT NULL,
    received_at TEXT NOT NULL,
    classification_status TEXT NOT NULL DEFAULT 'pending',
    section_key TEXT,
    placement_text TEXT,
    pipeline_fields TEXT,
    flag_reason TEXT,
    raw_model_response TEXT,
    doc_synced_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(chat_id, telegram_message_id)
  )`,
  `CREATE TABLE IF NOT EXISTS oauth_cache (
    provider TEXT PRIMARY KEY,
    access_token TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
  )`,
];

// Added after the initial schema, so applied as idempotent ALTERs rather than
// baked into the CREATE TABLE above (which only runs on first-ever creation).
const MIGRATIONS = [
  `ALTER TABLE notes ADD COLUMN image_drive_id TEXT`,
  `ALTER TABLE notes ADD COLUMN image_drive_url TEXT`,
];

export async function initSchema(db) {
  for (const stmt of SCHEMA) {
    await db.prepare(stmt).run();
  }
  for (const stmt of MIGRATIONS) {
    try {
      await db.prepare(stmt).run();
    } catch (err) {
      // SQLite/D1 has no "ADD COLUMN IF NOT EXISTS" — ignore "duplicate column" once applied.
      if (!/duplicate column/i.test(err.message || "")) throw err;
    }
  }
}

export function slugify(name) {
  return name
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export async function getActiveEvent(db, chatId) {
  const session = await db
    .prepare("SELECT active_event_id FROM active_session WHERE chat_id = ?")
    .bind(chatId)
    .first();
  if (!session) return null;
  return db
    .prepare("SELECT * FROM events WHERE id = ?")
    .bind(session.active_event_id)
    .first();
}

export async function createEvent(db, { chatId, name, docId, docUrl }) {
  const slug = slugify(name);
  const now = new Date().toISOString();
  const existing = await db
    .prepare("SELECT id FROM events WHERE chat_id = ? AND slug = ?")
    .bind(chatId, slug)
    .first();
  if (existing) return { error: "duplicate", slug };

  const result = await db
    .prepare(
      `INSERT INTO events (chat_id, name, slug, google_doc_id, google_doc_url, status, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, 'active', ?, ?)`
    )
    .bind(chatId, name, slug, docId, docUrl, now, now)
    .run();

  const eventId = result.meta.last_row_id;
  await db
    .prepare(
      `INSERT INTO active_session (chat_id, active_event_id, updated_at)
       VALUES (?, ?, ?)
       ON CONFLICT(chat_id) DO UPDATE SET active_event_id = excluded.active_event_id, updated_at = excluded.updated_at`
    )
    .bind(chatId, eventId, now)
    .run();

  return { eventId, slug };
}

export async function findEventsByName(db, chatId, name) {
  const like = `%${name.toLowerCase()}%`;
  const result = await db
    .prepare(
      `SELECT * FROM events WHERE chat_id = ? AND (LOWER(name) LIKE ? OR LOWER(slug) LIKE ?) ORDER BY updated_at DESC`
    )
    .bind(chatId, like, like)
    .all();
  return result.results;
}

export async function setActiveEvent(db, chatId, eventId) {
  const now = new Date().toISOString();
  await db
    .prepare(
      `INSERT INTO active_session (chat_id, active_event_id, updated_at)
       VALUES (?, ?, ?)
       ON CONFLICT(chat_id) DO UPDATE SET active_event_id = excluded.active_event_id, updated_at = excluded.updated_at`
    )
    .bind(chatId, eventId, now)
    .run();
}

export async function clearActiveSession(db, chatId) {
  await db.prepare("DELETE FROM active_session WHERE chat_id = ?").bind(chatId).run();
}

export async function setEventStatus(db, eventId, status) {
  await db
    .prepare("UPDATE events SET status = ?, updated_at = ? WHERE id = ?")
    .bind(status, new Date().toISOString(), eventId)
    .run();
}

export async function listEvents(db, chatId) {
  const result = await db
    .prepare("SELECT * FROM events WHERE chat_id = ? ORDER BY updated_at DESC")
    .bind(chatId)
    .all();
  return result.results;
}

// Returns { inserted: boolean, noteId } — inserted=false means this Telegram
// message was already logged (dedup guard), caller should short-circuit.
export async function insertNote(db, { eventId, chatId, telegramMessageId, telegramUpdateId, rawText }) {
  const now = new Date().toISOString();
  const result = await db
    .prepare(
      `INSERT OR IGNORE INTO notes
       (event_id, chat_id, telegram_message_id, telegram_update_id, raw_text, received_at, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?)`
    )
    .bind(eventId, chatId, telegramMessageId, telegramUpdateId ?? null, rawText, now, now)
    .run();

  if (result.meta.changes === 0) {
    return { inserted: false, noteId: null };
  }
  return { inserted: true, noteId: result.meta.last_row_id };
}

export async function updateNoteClassification(db, noteId, classification) {
  await db
    .prepare(
      `UPDATE notes SET
        classification_status = ?,
        section_key = ?,
        placement_text = ?,
        pipeline_fields = ?,
        flag_reason = ?,
        raw_model_response = ?
       WHERE id = ?`
    )
    .bind(
      classification.status,
      classification.sectionKey ?? null,
      classification.placementText ?? null,
      classification.pipelineFields ? JSON.stringify(classification.pipelineFields) : null,
      classification.flagReason ?? null,
      classification.rawModelResponse ?? null,
      noteId
    )
    .run();
}

export async function setNoteImage(db, noteId, { driveId, driveUrl }) {
  await db
    .prepare("UPDATE notes SET image_drive_id = ?, image_drive_url = ? WHERE id = ?")
    .bind(driveId, driveUrl, noteId)
    .run();
}

export async function getNotesForEvent(db, eventId) {
  const result = await db
    .prepare("SELECT * FROM notes WHERE event_id = ? ORDER BY received_at ASC")
    .bind(eventId)
    .all();
  return result.results;
}

export async function markNotesSynced(db, noteIds) {
  if (!noteIds.length) return;
  const now = new Date().toISOString();
  const placeholders = noteIds.map(() => "?").join(",");
  await db
    .prepare(`UPDATE notes SET doc_synced_at = ? WHERE id IN (${placeholders})`)
    .bind(now, ...noteIds)
    .run();
}

export async function getNoteCounts(db, eventId) {
  const result = await db
    .prepare(
      `SELECT classification_status, COUNT(*) as count FROM notes WHERE event_id = ? GROUP BY classification_status`
    )
    .bind(eventId)
    .all();
  return result.results;
}

export async function getLastNoteTime(db, eventId) {
  const row = await db
    .prepare("SELECT MAX(received_at) as last_at FROM notes WHERE event_id = ?")
    .bind(eventId)
    .first();
  return row?.last_at ?? null;
}

export async function getCachedAccessToken(db) {
  const row = await db
    .prepare("SELECT access_token, expires_at FROM oauth_cache WHERE provider = 'google'")
    .first();
  if (!row) return null;
  const expiresAt = new Date(row.expires_at).getTime();
  const bufferMs = 60 * 1000;
  if (Date.now() + bufferMs >= expiresAt) return null; // treat as expired
  return row.access_token;
}

export async function setCachedAccessToken(db, accessToken, expiresInSeconds) {
  const now = new Date().toISOString();
  const expiresAt = new Date(Date.now() + expiresInSeconds * 1000).toISOString();
  await db
    .prepare(
      `INSERT INTO oauth_cache (provider, access_token, expires_at, updated_at)
       VALUES ('google', ?, ?, ?)
       ON CONFLICT(provider) DO UPDATE SET access_token = excluded.access_token, expires_at = excluded.expires_at, updated_at = excluded.updated_at`
    )
    .bind(accessToken, expiresAt, now)
    .run();
}
