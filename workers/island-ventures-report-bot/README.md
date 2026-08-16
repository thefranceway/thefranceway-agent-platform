# Island Ventures Field Report Bot

Text field observations to Telegram while at an event; they get classified into
a fixed report structure and compiled live into a single Google Doc via the
real Docs API (in-place `batchUpdate`, not a new Doc each time). Runs on a
Cloudflare Worker so it works even when your laptop is closed.

Full design: `~/.claude/plans/lucky-swimming-lamport.md`

## Setup (one time)

1. **D1 database** — already created: `island-ventures-reports`
   (`43f317d9-0441-4562-b39f-d93290bb09ab`), wired in `wrangler.json`.

2. **Deploy the Worker:**
   ```bash
   cd ~/projects/island-ventures-report-bot
   wrangler deploy
   ```
   Note the resulting `*.workers.dev` URL.

3. **Google OAuth consent** (interactive, must be done by you in a browser):
   ```bash
   cd ~/projects/hermes-agent/skills/productivity/google-workspace/scripts
   python3 setup.py --client-secret ~/.config/google/client_secret_749086244795-ag93q1jb1n05nhm6k8248lslj717pgdr.apps.googleusercontent.com.json
   python3 setup.py --auth-url
   # open the printed URL, click Allow, copy the resulting (broken) redirect URL from the address bar
   python3 setup.py --auth-code "<pasted URL>"
   python3 setup.py --check   # should say AUTHENTICATED
   ```
   Then pull `refresh_token`, `client_id`, `client_secret` out of
   `~/.hermes/google_token.json`.

4. **Set secrets** (from `~/projects/island-ventures-report-bot`):
   ```bash
   wrangler secret put GOOGLE_CLIENT_ID
   wrangler secret put GOOGLE_CLIENT_SECRET
   wrangler secret put GOOGLE_REFRESH_TOKEN
   security find-generic-password -a francesca -s telegram-bot-token -w | wrangler secret put TELEGRAM_BOT_TOKEN
   wrangler secret put TELEGRAM_WEBHOOK_SECRET      # e.g. output of: openssl rand -hex 32
   wrangler secret put ANTHROPIC_API_KEY            # from console.anthropic.com
   wrangler secret put TELEGRAM_ALLOWED_CHAT_ID     # your Telegram numeric chat/user ID
   ```

5. **Register the Telegram webhook** (this is the actual "go live" step):
   ```bash
   curl "https://api.telegram.org/bot<token>/setWebhook" \
     -d url="https://<your-worker>.workers.dev/telegram-webhook" \
     -d secret_token="<the TELEGRAM_WEBHOOK_SECRET value>"
   curl "https://api.telegram.org/bot<token>/getWebhookInfo"
   ```

## Using it

- `/newevent <name>` — start a new report, creates a fresh Google Doc
- plain text — logs a field note against the active event
- `/status` — active event, note counts, last note time
- `/switchevent <name>` — resume an older event
- `/events` — list all events for this chat
- `/freeze` — stop auto-rewriting the Doc (safe to hand-edit)
- `/unfreeze` — resume auto-rewrite (overwrites any hand edits made while frozen)
- `/endevent` — archive the active event

**Known risk:** the Doc is a full-body rewrite sourced from every logged note,
every time. Any manual edit made directly in the Doc while an event is active
gets overwritten by the next note. Use `/freeze` before hand-polishing.

## Verification

```bash
wrangler tail island-ventures-report-bot   # watch live logs
wrangler d1 execute island-ventures-reports --command "SELECT * FROM events"
wrangler d1 execute island-ventures-reports --command "SELECT id, section_key, classification_status FROM notes"
```

See the full 11-point verification checklist in the plan file.
