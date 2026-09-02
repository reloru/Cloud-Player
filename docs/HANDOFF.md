# Handoff: Cloud Player

This repo has been set up (config, directory layout, and one piece of live
Cloudflare infrastructure) but has no application code yet. The next
session writes `worker/src/index.ts`, `worker/public/`, and `vm-api/`.

## Repo state after setup

```
worker/
  wrangler.jsonc     real config: assets binding for the PWA, and a
                      vpc_services binding to the VM music API (see below).
                      main points at src/index.ts, which does not exist yet.
  package.json        wrangler 4.128.0, typescript 7.0.2,
                      @cloudflare/workers-types 5.20260902.1 (devDependencies,
                      not installed — run npm install before first use)
  tsconfig.json
  src/                empty — write index.ts here first
  public/             empty — PWA static assets go here
vm-api/
  README.md            placeholder only — write the Python API here
docs/
  HANDOFF.md           this file
```

`worker/` and `vm-api/` are two separate deployables from one repo:
`worker/` is pushed with `wrangler deploy`; `vm-api/` is pulled and run on
the Ubuntu VM over the user's Termius session, which no Claude Code session
in this environment can reach directly.

## Task

Build a minimal personal music PWA:

```
iPhone --HTTPS--> Cloudflare Worker --(Workers VPC Service, over the existing "VM" tunnel)--> Ubuntu VM :8000 (music API, /music)
```

Worker (`worker/`):
- Serves the PWA via the `assets` binding already configured.
- Proxies music API requests to the VM via the `MUSIC_API` VPC Service
  binding already configured (`env.MUSIC_API.fetch(...)`) — not a public
  hostname, not a `fetch()` to an external URL.
- Routes:
  - `GET /api/songs` — public
  - `GET /api/stream/:id` — public, must support Range requests end to end
    (client Range header -> VM API -> Worker response, all three hops)
  - `POST /api/upload` — requires auth
  - `DELETE /api/delete/:id` — requires auth
- Auth (decided this session): the client caches the password after first
  entry (e.g. `localStorage`) and sends it as `Authorization: Bearer
  <password>` on upload/delete requests only. The Worker compares it
  against the `AUTH_PASSWORD` secret (`wrangler secret put AUTH_PASSWORD`
  — no value exists yet, the user provides it). No sessions, no cookies, no
  KV/DB.
- Stream the upload body through to the VPC Service (`request.body` piped,
  not buffered) — Workers have a 128 MB memory limit, and Cloudflare caps
  inbound request bodies at 100 MB on Free/Pro, 200 MB on Business (source:
  developers.cloudflare.com/workers/platform/limits/). That cap governs
  the largest file this app can accept; fine for individual tracks.

VM music API (`vm-api/`):
- **Language (decided this session): Python, standard library only**
  (`http.server` or similar). The user had no preference; Python is chosen
  because a Python stack is already on the VM per the environment notes in
  CLAUDE.md, so it needs no new installs. Confirm what's actually on the
  VM before assuming a specific Python version or module availability —
  this session could not check the VM directly.
- Stores files under `/music`, handles filesystem ops and metadata.
- Supports HTTP Range requests for streaming (`Accept-Ranges`,
  `Content-Range`, `Content-Length`, correct `Content-Type`).
- Supports streaming uploads — do not buffer whole files in memory.
- **Must listen on `localhost:8000`.** That exact host/port is already
  registered as the VPC Service target (see below) — a different port
  means recreating the VPC Service.

PWA (`worker/public/`):
- Installable on iPhone (manifest + service worker).
- Browse library, play via `<audio>`, pick files via the iOS Files picker,
  upload, delete (upload/delete require login — see auth above).
- Minimal, mobile-friendly, no framework unless it earns its keep.

Constraints carried over from the original task, still in force: no
database/queue/object storage, smallest working version first, don't
invent infrastructure values that aren't already pinned down below. The
original spec said "do not use Workers VPC" — that's been reversed (see
below); everything else in the original spec stands.

## Infrastructure already configured — do not recreate

- **Tunnel**: named "VM", id `a2b9bc89-8a31-4406-8ad5-47d4923efd7b`,
  status healthy at time of setup. Already connected to the Ubuntu VM;
  nothing to do here.
- **VPC Service**: id `01a06084-afb3-7651-935c-59e35fa26e66`, name
  `music-api`, type `http`, target `localhost:8000` over the tunnel above.
  Created this session with `wrangler vpc service create music-api --type
  http --tunnel-id a2b9bc89-8a31-4406-8ad5-47d4923efd7b --hostname
  localhost --http-port 8000`. Already wired into `worker/wrangler.jsonc`
  as the `MUSIC_API` binding.
- Why VPC Services and not a public hostname route: the original spec
  banned Workers VPC and (implicitly) pointed toward a published
  application route on the tunnel — a public DNS hostname anyone on the
  internet could hit directly. That conflicts with the same spec's "the
  API should remain behind Cloudflare, not exposed to the internet," and
  since all auth is enforced in the Worker, a public hostname would let
  anyone skip the Worker and hit upload/delete on the VM API directly.
  Workers VPC Services was confirmed (Cloudflare docs, Workers VPC pages)
  to prevent exactly that: the binding can only reach the one registered
  host:port, nothing else is exposed, and no domain/DNS record is needed
  at all. Cost: Workers VPC is in beta per Cloudflare's own docs (APIs may
  still change), and it needed the account's Connectivity Directory Admin
  role to create — that role was implicitly available (the create command
  succeeded with the account's existing `CLOUDFLARE_API_TOKEN`).

## What the user still needs to provide

Only one thing remains, and it's deliberate (a secret, not something to
generate or guess):

1. **`AUTH_PASSWORD`** — the login password. Set it by running `npx
   wrangler secret put AUTH_PASSWORD` in `worker/` and entering a value
   when prompted.

Everything else that was previously listed as "needed from the user" (a
domain for a public hostname, the tunnel ID, the VM's local port, the auth
mechanism, the VM API's language) has been resolved above.

## Confirmed this session

- `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` are present as
  environment variables in this session's environment and were used
  directly — via the Cloudflare REST API (`GET
  /accounts/{account_id}/cfd_tunnel`) to find the tunnel, and via `wrangler
  vpc service create` (which reads `CLOUDFLARE_API_TOKEN` automatically) to
  create the VPC Service. Whether these same env vars are present in the
  next session's environment was not checked and should not be assumed —
  if they're missing, the tunnel ID and VPC Service ID recorded above are
  still valid and don't need to be re-derived.
- No Worker named `cloud-player` existed in the account before this setup
  (checked via `workers_list`).
- The `Cloudflare_MCP` connector (a separate MCP tool for Zero
  Trust/Tunnel management) failed to connect this session (404,
  CLIENT_HTTP_NOT_IMPLEMENTED) — the tunnel lookup and VPC Service creation
  above were done via direct Cloudflare REST API calls and the `wrangler`
  CLI instead, both using the env credentials. Retry the connector next
  session if it would help; it wasn't required.
- `npx wrangler`, `npm`, and registry access all work in this environment;
  `worker/package.json` versions were read from the npm registry directly.
- Cloudflare's published Workers limits (developers.cloudflare.com/workers/
  platform/limits/): request body size 100 MB (Free/Pro) / 200 MB
  (Business); response body size has no enforced limit — relevant to
  upload size and to streaming large audio files, respectively.

## Suggested order for the implementation session

1. Write `vm-api/` (Python stdlib, listens on `localhost:8000`, `/music`
   filesystem access, Range support, streaming upload handling). Confirm
   what's actually on the VM (Python version, etc.) before assuming.
2. Write `worker/src/index.ts`: routing, the four API routes proxying
   through `env.MUSIC_API`, and the bearer-password auth check on
   upload/delete.
3. Write the PWA under `worker/public/`.
4. Get `AUTH_PASSWORD` from the user and set it via `wrangler secret put`.
5. `npm install` in `worker/`, then `wrangler dev` (uses `remote: true` on
   the VPC Service binding to reach the real tunnel even in local dev) and
   `wrangler deploy`.
