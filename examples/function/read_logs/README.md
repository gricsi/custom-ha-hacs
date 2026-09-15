# read_logs

Lets the agent read your Home Assistant log — the page at *Settings → System → Logs*
(`/config/logs`) — so it can tell you what is actually broken and what to change to fix it.

That page shows two different things, and this one function serves both:

| `source` | What you get | Equivalent in the UI |
| --- | --- | --- |
| `errors` *(default)* | Deduplicated warnings and errors, newest first, each with its logger, source file and line, occurrence count and exception traceback | the list the page opens with |
| `raw` | The tail of `home-assistant.log`, verbatim | the **Load full logs** button |

Read-only: the agent cannot edit anything, so it hands you the fix and you apply it. Pair it with
[read_config](../read_config/) and it can go one step further — read the error, read the YAML that
caused it, and give you the corrected block to paste.

## Function

```yaml
- spec:
    name: read_logs
    description: >-
      Read the Home Assistant log, the same thing the Settings > System > Logs page shows. Use
      this when the user asks what is broken, why an integration stopped working or failed to
      load, what an error or warning means, or asks you to diagnose or fix a problem. Returns
      deduplicated entries newest first, each with the logger name, level, message, source file
      and line, how many times it has happened, and the exception traceback when there is one.
      This is read-only - you cannot change anything, so identify the cause and give the user
      the exact configuration change or steps that fix it.
    parameters:
      type: object
      properties:
        source:
          type: string
          enum:
            - errors
            - raw
          description: >-
            errors (the default) returns the deduplicated warning and error list. raw returns
            the end of the log file verbatim, which is the only way to see debug-level lines or
            what happened during startup.
        level:
          type: string
          enum:
            - WARNING
            - ERROR
            - CRITICAL
          description: >-
            Lowest severity to include. Omit to get everything the page shows. Ignored when
            source is raw.
        logger:
          type: string
          description: >-
            Case-insensitive substring matched against the logger name, to narrow to one
            integration - for example mqtt, zwave_js, or custom_components. Ignored when
            source is raw.
        limit:
          type: integer
          description: >-
            How many entries to return, newest first - default 25, maximum 100. When source is
            raw this is a number of lines instead - default 100, maximum 500.
  function:
    type: native
    name: read_logs
```

## Try it

> What's in my error log?

> Something is wrong with my MQTT setup — check the log and tell me what.

> Read the log and my automations.yaml, then give me a corrected version of whatever is failing.

> Show me the last 200 lines of the full log, I just restarted.

## What it returns

`source: errors` — the deduplicated list:

```json
{
  "source": "errors",
  "entries": [
    {
      "level": "ERROR",
      "logger": "homeassistant.components.automation.morning_lights",
      "message": "Error while executing automation: Entity light.kitchn not found",
      "source": "homeassistant/components/automation/__init__.py:673",
      "count": 12,
      "first_occurred": "2026-09-15T06:30:01+02:00",
      "last_occurred": "2026-09-15T09:30:00+02:00",
      "exception": ""
    }
  ],
  "returned": 1, "matched": 1, "available": 14, "truncated": false,
  "level_counts": {"ERROR": 3, "WARNING": 11}
}
```

`level_counts` covers everything held, not just what was returned, so the agent can say "3 errors
and 11 warnings" even when it only looked at a few. `count` is the one to read first: an error at
`count: 4000` is a loop, not an incident.

`source: raw` — the tail of the file:

```json
{"source": "raw", "path": "/config/home-assistant.log", "exists": true,
 "file_size": 8421553, "lines": 100, "truncated": true, "content": "2026-09-15 09:30:00 ERROR ..."}
```

## Notes

- **The error list dies at restart.** It is `system_log`'s in-memory store, the same one the
  frontend reads over the `system_log/list` websocket command — it holds the most recent 50 records
  (its `max_entries`, raise it in `configuration.yaml`) and keeps nothing across a restart. Asking
  "why did it crash?" after rebooting to fix the crash gets you an empty list; that is what
  `source: raw` is for, since the file survives.
- **Deduplicated, not chronological.** Records sharing a logger and source line collapse into one
  entry with a `count` and a first/last timestamp, so the same error logged 300 times costs one
  entry instead of flooding the context window. A single entry can carry several distinct messages,
  joined by newlines.
- **`raw` reads only the tail** — the last 512 KB of the file, then the last `limit` lines of that,
  capped at 20k characters. Enable debug logging for one chatty integration and this file passes
  100 MB in a day, so it is never read whole. `truncated: true` means there is more above what you
  got; the log file path comes from Home Assistant itself, so the model cannot point this at
  another file.
- **Each entry is bounded too**: 1500 characters of message, 2500 of traceback, 20k for the whole
  result, whichever comes first. Anything cut says so inline. Without that a dict result would slip
  past the `MAX_FUNCTION_RESULT_CHARS` guard in `conversation.py`, which only truncates strings.
- **Reading the log writes to the log.** `conversation.py` logs every function result at INFO,
  so what you read comes back round into `home-assistant.log`. Harmless — INFO never enters the
  `errors` store, so `source: errors` cannot feed on itself — but it does mean a few `raw` calls
  add their own 20k to the file you are inspecting.
- **No per-user permission check, by design** — matching [read_config](../read_config/). Home
  Assistant's own Logs page is admin-only, and this function is not: anyone who can reach the
  assistant, voice satellites included, can read the log. Gate it on `user_input.context.user_id`
  and `.is_admin` if that matters to you (`get_user_from_user_id` shows the pattern).
- **Logs leak more than you would guess.** Tracebacks carry URLs, local IPs, device and person
  names, and occasionally a token an integration logged on a failed auth. All of it goes to Proton
  with the request. This is the most privacy-relevant function in `examples/` — worth a look at
  your own log before enabling it.
- **If `system_log` is not loaded**, `source: errors` returns an error telling the model to retry
  with `source: raw` rather than failing silently. In practice it is always loaded — Home
  Assistant's bootstrap sets it up in stage 0 (`LOGGING_AND_HTTP_DEPS_INTEGRATIONS`) and `frontend`
  depends on it — so the branch is for oddities like a test harness or a deliberately minimal core.
