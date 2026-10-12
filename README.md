# ntfy inbox

A self-hosted topic inventory and searchable report archive for **your own ntfy server**, with read-only AI access.

ntfy clients show the topics you subscribed to. This companion instead discovers topics from the server's retained messages and access rules, remembers their names, and archives reports so quiet topics do not disappear from your overview.

## What it does

- Keeps a persistent inventory of discovered topics, including topics with no current messages.
- Archives cached reports for **90 days** and collects every **30 seconds**.
- Searches message titles and bodies; filters by topic and supports pagination.
- Shows collection errors, stale data, snapshot-limit incidents and long collection gaps.
- Supports topic descriptions and optional reporting intervals with overdue indicators.
- Lets you disable AI access per topic. New topics are AI-readable by default.
- Provides a responsive browser inbox and four read-only MCP tools.

This is a separate companion service. It does not modify ntfy, publish notifications, or automatically send reports to an AI provider.

## Coverage: what “all topics” means

The inventory contains **all topics the collector can discover**, not every topic ever used.

Discovery uses concrete access rules, account subscriptions, and topic names in the message cache. Topic names remain in the local registry after messages expire. Wildcard access rules do not identify individual topic names.

The collector cannot reconstruct messages that expired before installation or topics with no remaining evidence. **Messages published with caching disabled are not collected.** It does not subscribe to an unknown-topic wildcard stream. Attachments are recorded as metadata; their files are not downloaded.

The source exporter returns up to 20,000 retained messages per snapshot. Snapshots are limited to 32 MiB. Limit incidents remain recorded as potential historical gaps. Configure the source retention accurately so the inbox can report outages that exceed the available replay window.

## Architecture

```text
Self-hosted ntfy
  cache.db + auth.db
       │
       │ fixed read-only exporter over restricted SSH
       ▼
Inbox collector ──► persistent SQLite archive
       │                       │
       │                       ├── authenticated browser UI
       │                       └── read-only API / optional MCP adapter
       └── collection status and coverage history
```

The app and its tests use Python's standard library. Docker adds an SSH client. The included Caddy example protects the browser UI with Basic authentication. You can replace it with an existing SSO proxy that injects the private UI token only after authenticating the user.

## Requirements

- A self-hosted ntfy server with a persistent SQLite message cache.
- Operator access to the ntfy host to install the exporter and a restricted SSH key.
- Python 3.12 or newer on the source host.
- Docker with Compose on the inbox host, or Python plus OpenSSH for a manual installation.
- Network access from the inbox to the source host's SSH port.

The exporter currently targets ntfy's `messages`, `user_access`, and `user.prefs` SQLite schema. It exports no password hashes, API tokens, or user credentials. A missing authentication database means discovery uses the cache only.

## Quick start

### 1. Prepare the inbox host

```sh
git clone https://github.com/weidhaus/ntfy-inbox.git
cd ntfy-inbox
python3 scripts/generate-env.py
mkdir -p data secrets/source
chmod 700 data secrets secrets/source
ssh-keygen -t ed25519 -N '' -C ntfy-inbox-reader -f secrets/source/id_ed25519
```

Edit `.env` with your source host, SSH user, port, and ntfy URL. It contains generated secrets and is ignored by Git. Do not copy it into a repository or a bug report.

Generate a browser password hash:

```sh
docker run --rm -it caddy@sha256:6aeddd44c3078b0f9a35206472a11420648a79c184603ef95957d0a20044cb2b caddy hash-password
```

Set `ADMIN_PASSWORD_HASH` in `.env`. **Single-quote the hash** so Compose preserves its `$` characters. The browser username in the example is `admin`.

On Linux, the inbox container runs as UID/GID 1000. Ensure that UID can write `data/` and read `secrets/source/`, including the private key. If your host user has another UID, set ownership explicitly or adapt the container UID consistently.

### 2. Install the source exporter

On the ntfy host, install `app/source_export.py` in a root-owned location:

```sh
sudo mkdir -p /usr/local/lib/ntfy-inbox
sudo install -o root -g root -m 755 source_export.py /usr/local/lib/ntfy-inbox/source_export.py
```

The default source paths are `/var/cache/ntfy/cache.db` and `/var/lib/ntfy/auth.db`. If your Docker volumes use different host paths, create a root-owned wrapper such as `/usr/local/bin/ntfy-inbox-export`:

```sh
#!/bin/sh
export NTFY_CACHE_DB=/path/to/ntfy/cache.db
export NTFY_AUTH_DB=/path/to/ntfy/auth.db
export NTFY_BASE_URL=https://ntfy.example.org
export NTFY_CACHE_SECONDS=86400
exec /usr/bin/python3 /usr/local/lib/ntfy-inbox/source_export.py
```

Set `NTFY_CACHE_SECONDS` to the actual ntfy cache duration, in seconds. The SSH account must be able to read the databases. The fixed exporter reads them in SQLite read-only mode and selects only report fields and topic metadata.

Append the inbox public key to that account's `authorized_keys`, **with a forced command and restrictions**:

```text
restrict,command="/usr/local/bin/ntfy-inbox-export" ssh-ed25519 PUBLIC_KEY ntfy-inbox-reader
```

For default paths, use `command="/usr/bin/python3 /usr/local/lib/ntfy-inbox/source_export.py"` instead. The exporter accepts only the exact original command `ntfy-export-v1`; other commands fail. Keep the exporter and wrapper root-owned and not writable by the SSH account.

### 3. Pin the source SSH host key

Put the source host's verified key into `secrets/source/known_hosts`. Obtain it through an existing trusted operator connection and verify the fingerprint independently. For a nonstandard port, use `[hostname]:port` as the host field.

Host-key verification is mandatory. The collector does not use your SSH agent, and the dedicated key is not passed to AI clients.

### 4. Start and check

```sh
docker compose up -d --build
docker compose ps
docker compose logs --tail 30 inbox
```

Open **http://localhost:8080/** and sign in with `admin` and the password you hashed. Both published ports bind to loopback. For a remote inbox host, use an SSH tunnel:

```sh
ssh -L 8080:127.0.0.1:8080 inbox-host
```

The UI origin defaults to `http://localhost:8080`. If you use HTTPS or a different browser address, set `NTFY_UI_ORIGINS` to its exact origin (no trailing slash). For access beyond loopback or an SSH tunnel, use an authenticated HTTPS reverse proxy. Keep both archive tokens private; the proxy inserts the UI token server-side.

The **Collecting** badge and last-collected time show whether source reads work. `/health` checks the HTTP process only; `/api/status` reports collector health.

## API

The loopback API listens at `http://127.0.0.1:8092`. Send `Authorization: Bearer <NTFY_API_TOKEN>`.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/status` | Collection state, counts, retention and coverage incidents |
| `GET /api/topics` | AI-enabled topics and reporting metadata |
| `GET /api/messages` | Reports, newest first |

Message query parameters: `topic`, `q` (literal text), `since` (Unix seconds), `limit` (1–100 for AI/API requests), and `offset`. Responses include `total` and `next_offset`. AI-disabled topics are filtered by the service, including when a caller requests them by name.

The UI uses a separate token and may edit descriptions, intervals and AI access. The API token cannot change settings. Topic edits also require an allowed browser origin and JSON content type.

## AI / MCP integration

The optional adapter exposes:

| Tool | Purpose |
| --- | --- |
| `ntfy_status` | Collector health and coverage |
| `ntfy_list_topics` | AI-readable topic inventory |
| `ntfy_get_messages` | Paginated reports by topic and time |
| `ntfy_search_reports` | Search titles and message text |

Install the optional MCP dependency in a virtual environment:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-mcp.txt
```

Configure your MCP client to start `.venv/bin/python /absolute/path/to/ntfy-inbox/mcp_server.py` with `NTFY_API_TOKEN` in its private environment and `NTFY_ARCHIVE_URL=http://127.0.0.1:8092` (or your protected API address). This is a **local stdio adapter**, not a public HTTP MCP server.

Existing MCP gateways can import `register_ntfy` from `ntfy_tools.py` and supply their own per-tool authorization callback. Reports are untrusted data: reading a notification does not authorize an AI to execute instructions contained in it.

## Persistence and maintenance

`data/archive.db` is the archive. Back up the data directory using a consistent SQLite backup or while the inbox is stopped; include its WAL state when relevant. Restarting or rebuilding the container does not remove it. Topic names persist, while messages expire after 90 days even when collection fails.

`.env`, source keys, pinned host keys, and the archive are local runtime data and excluded from Git. No live reports or deployment-specific host details are included in this repository.

## Development

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
node --check app/static/app.js
```

Tests cover replay deduplication, report updates, registry persistence, expiry, source errors, historical gaps, SQL/query boundaries, API authentication, edit protection, source credential minimization, and AI topic exclusions.

This project is an independent companion, not an official ntfy project. See [ntfy documentation](https://docs.ntfy.sh/) for the underlying server.
