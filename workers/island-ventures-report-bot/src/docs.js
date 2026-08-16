// Google Docs API access via fetch (Workers can't use googleapiclient).
// Ported from the request shapes confirmed in
// ~/projects/hermes-agent/skills/productivity/google-workspace/scripts/google_api.py
// (_docs_insert_text, docs_append's end-index logic) — reference only, not run directly.

import { getCachedAccessToken, setCachedAccessToken } from "./db.js";

async function getAccessToken(env, db) {
  const cached = await getCachedAccessToken(db);
  if (cached) return cached;

  const resp = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "refresh_token",
      client_id: env.GOOGLE_CLIENT_ID,
      client_secret: env.GOOGLE_CLIENT_SECRET,
      refresh_token: env.GOOGLE_REFRESH_TOKEN,
    }),
  });

  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`Google OAuth refresh failed (${resp.status}): ${body}`);
  }

  const data = await resp.json();
  await setCachedAccessToken(db, data.access_token, data.expires_in);
  return data.access_token;
}

async function docsFetch(env, db, path, options = {}) {
  const accessToken = await getAccessToken(env, db);
  const resp = await fetch(`https://docs.googleapis.com/v1/documents${path}`, {
    ...options,
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "content-type": "application/json",
      ...(options.headers || {}),
    },
  });
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`Google Docs API error (${resp.status}) on ${path}: ${body}`);
  }
  return resp.json();
}

export async function createDoc(env, db, title) {
  const doc = await docsFetch(env, db, "", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
  return {
    id: doc.documentId,
    url: `https://docs.google.com/document/d/${doc.documentId}/edit`,
  };
}

// Uploads image bytes to Drive and makes them link-viewable, so the resulting
// URL is durably fetchable by the Docs API on every future regeneration —
// unlike Telegram's own file URLs, which expire and would silently break
// the embed the next time an unrelated note triggers a full rebuild.
export async function uploadImageToDrive(env, db, bytes, contentType, filename) {
  const accessToken = await getAccessToken(env, db);
  const boundary = "island_ventures_boundary_" + crypto.randomUUID();
  const metadata = JSON.stringify({ name: filename });

  const encoder = new TextEncoder();
  const preamble = encoder.encode(
    `--${boundary}\r\n` +
      `Content-Type: application/json; charset=UTF-8\r\n\r\n` +
      `${metadata}\r\n` +
      `--${boundary}\r\n` +
      `Content-Type: ${contentType}\r\n\r\n`
  );
  const closing = encoder.encode(`\r\n--${boundary}--`);

  const body = new Uint8Array(preamble.length + bytes.length + closing.length);
  body.set(preamble, 0);
  body.set(bytes, preamble.length);
  body.set(closing, preamble.length + bytes.length);

  const uploadResp = await fetch(
    "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id",
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "content-type": `multipart/related; boundary=${boundary}`,
      },
      body,
    }
  );
  if (!uploadResp.ok) {
    const errBody = await uploadResp.text();
    throw new Error(`Drive upload failed (${uploadResp.status}): ${errBody}`);
  }
  const { id: fileId } = await uploadResp.json();

  const permResp = await fetch(`https://www.googleapis.com/drive/v3/files/${fileId}/permissions`, {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}`, "content-type": "application/json" },
    body: JSON.stringify({ role: "reader", type: "anyone" }),
  });
  if (!permResp.ok) {
    const errBody = await permResp.text();
    throw new Error(`Drive permission grant failed (${permResp.status}): ${errBody}`);
  }

  return { id: fileId, url: `https://drive.google.com/uc?export=view&id=${fileId}` };
}

function findBodyEndIndex(doc) {
  const content = doc.body?.content || [];
  if (content.length === 0) return 1;
  return content[content.length - 1].endIndex ?? 1;
}

// Full-body replace: delete everything, then insert the freshly compiled
// content segment by segment, in order. The Doc is always a rendering of
// D1's note history — never patched incrementally.
//
// Segments come in two shapes:
//   text segment:  { text, style?, bullet?, indent? }
//   image segment: { image: true, driveUrl, widthPt?, heightPt? }
// Requests are built sequentially (not as one bulk insertText) specifically
// so insertInlineImage requests can be interleaved at exact positions —
// each request's indices are relative to the document state after every
// prior request in this same batchUpdate has already applied, so the
// running `cursor` stays valid as long as every segment advances it by
// exactly what it inserted (text.length, or 1 for an inline image).
export async function replaceDocBody(env, db, documentId, segments) {
  const doc = await docsFetch(env, db, `/${documentId}`);
  const endIndex = findBodyEndIndex(doc);

  const requests = [];

  // Docs rejects deleting the mandatory trailing newline, so only delete
  // down to endIndex - 1 when there's real content to remove.
  if (endIndex > 2) {
    requests.push({
      deleteContentRange: { range: { startIndex: 1, endIndex: endIndex - 1 } },
    });
  }

  let cursor = 1;
  for (const segment of segments) {
    if (segment.image) {
      requests.push({
        insertInlineImage: {
          location: { index: cursor },
          uri: segment.driveUrl,
          objectSize: {
            height: { magnitude: segment.heightPt || 200, unit: "PT" },
            width: { magnitude: segment.widthPt || 280, unit: "PT" },
          },
        },
      });
      cursor += 1; // an inline image occupies exactly one index in the body
      continue;
    }

    const start = cursor;
    const end = cursor + segment.text.length;
    requests.push({ insertText: { location: { index: start }, text: segment.text } });

    if (segment.style === "HEADING_1" || segment.style === "HEADING_2") {
      requests.push({
        updateParagraphStyle: {
          range: { startIndex: start, endIndex: end },
          paragraphStyle: { namedStyleType: segment.style },
          fields: "namedStyleType",
        },
      });
    }

    if (segment.bullet) {
      requests.push({
        createParagraphBullets: {
          range: { startIndex: start, endIndex: end },
          bulletPreset: "BULLET_DISC_CIRCLE_SQUARE",
        },
      });
    }

    if (segment.indent) {
      requests.push({
        updateParagraphStyle: {
          range: { startIndex: start, endIndex: end },
          paragraphStyle: {
            indentStart: { magnitude: 54, unit: "PT" },
            indentFirstLine: { magnitude: 54, unit: "PT" },
          },
          fields: "indentStart,indentFirstLine",
        },
      });
    }

    cursor = end;
  }

  await docsFetch(env, db, `/${documentId}:batchUpdate`, {
    method: "POST",
    body: JSON.stringify({ requests }),
  });
}
