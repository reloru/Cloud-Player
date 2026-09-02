# Handoff: Cloud Player

This session did repository setup only — no application code. The next
session implements the app described below.

## Repo state after this session

```
worker/
  wrangler.jsonc     real config, but main points at src/index.ts which
                      does not exist yet — create it first
  package.json        wrangler 4.128.0, typescript 7.0.2,
                      @cloudflare/workers-types 5.20260902.1 (devDependencies,
                      versions read from the npm registry this session, not
                      installed — run npm install before first use)
  tsconfig.json
  src/                empty
  public/             empty — PWA static assets go here
vm-api/
  README.md            placeholder only
docs/
  HANDOFF.md           this file
```

`worker/` and `vm-api/` are two separate deployables from one repo: `worker/`
is pushed with `wrangler deploy`; `vm-api/` is pulled and run on the Ubuntu
VM over the user's Termius session, which this session cannot reach.

## Task

Build a minimal personal music PWA:

```
iPhone --HTTPS--> Cloudflare Worker --(existing Cloudflare Tunnel "VM")--> Ubuntu VM (music API, /music)
```

Worker (`worker/`):
- Serves the PWA (static assets via the `assets` binding already configured
  in `worker/wrangler.jsonc`).
- Proxies music API requests to the VM through the existing tunnel.
- Routes:
  - `GET /api/songs` — public
  - `GET /api/stream/:id` — public, must support Range requests end to end
  - `POST /api/upload` — requires auth
  - `DELETE /api/delete/:id` — requires auth
- Auth: simple password check enforced in the Worker, only for upload and
  delete. Store the password as a Worker secret (`wrangler secret put
  AUTH_PASSWORD`), never in `vars` or committed config.

VM music API (`vm-api/`):
- Stores files under `/music`, handles filesystem ops and metadata.
- Supports HTTP Range requests for streaming (`Accept-Ranges`,
  `Content-Range`, `Content-Length`, correct `Content-Type`).
- Supports streaming uploads — do not buffer whole files in memory.
- Language/framework choice was left to the implementation session; nothing
  is installed on the VM by this session and this session cannot verify
  what's already there. The VM has cloudflared, Node, a Python stack, and
  gh/wrangler per the environment notes in CLAUDE.md — confirm what's
  actually available before picking a stack, don't assume.

PWA (`worker/public/`):
- Installable on iPhone (manifest + service worker).
- Browse library, play via `<audio>`, pick files via the iOS Files picker,
  upload, delete (upload/delete require login).
- Minimal, mobile-friendly, no framework unless it earns its keep.

Constraints carried over from the task: no Workers VPC, no database/queue/
object storage, smallest working version first, don't invent infrastructure
values.

## Infrastructure values the user must still provide

None of these were invented and none exist in this repo yet:

1. **Public hostname for the VM music API.** Workers VPC is explicitly
   excluded, so the Worker must reach the VM API over a normal HTTPS
   `fetch()`. The mechanism, confirmed against Cloudflare's Tunnel routing
   docs (developers.cloudflare.com/cloudflare-one/networks/routes/add-routes/
   and developers.cloudflare.com/tunnel/routing/): add a **Published
   application** route on the existing "VM" tunnel (Cloudflare dashboard →
   Networking → Tunnels → VM → Routes → Add route → Published application),
   giving it a subdomain on a domain already on the user's Cloudflare
   account, with **Service URL** pointing at wherever the VM API ends up
   listening locally (e.g. `http://localhost:8787`). That hostname becomes
   the Worker's upstream origin (`MUSIC_API_ORIGIN` in `worker/wrangler.jsonc`
   `vars`). Needed: a domain on the account, and the VM-local port the API
   will bind to. One documented caveat: public hostname routes proxy through
   Cloudflare, and on Free/Pro/Business plans the service-specific terms
   require a specific paid service for serving video/large files — worth
   the user's attention since this proxies audio files (source: the routing
   doc above).
2. **AUTH_PASSWORD** — the login password, set as a Worker secret. No value
   exists; the user supplies it when the implementation session runs
   `wrangler secret put AUTH_PASSWORD`.
3. **Worker deployment target** — `worker/wrangler.jsonc` has no `routes` or
   custom domain, so it will deploy to the default `*.workers.dev`
   subdomain unless the user wants a custom domain, which would need to be
   named explicitly.

## Confirmed this session (so the next one doesn't re-derive it)

- Cloudflare account (via `workers_list`, this session's Cloudflare
  Developer Platform MCP connection) currently has no Worker named
  `cloud-player` — the name in `worker/wrangler.jsonc` is free. Existing
  Workers: get-it, voice-agent, patchbay, music-editor, gitframe, dev-proto,
  screenshot-cropper, jarvis-assistant, invar-sub, crosbynews.
- `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_ZONE_ID` are present as
  environment variables in this session's environment. Per Cloudflare's
  Wrangler docs (system-environment-variables, and the Profiles page's
  account-selection order), Wrangler reads `CLOUDFLARE_ACCOUNT_ID` from the
  environment automatically, so `account_id` was deliberately left out of
  `wrangler.jsonc`. Whether these same env vars are present in the next
  session's environment was not checked and should not be assumed.
- The `Cloudflare_MCP` connector (Tunnel/Zero Trust management) failed to
  connect in this session (404, CLIENT_HTTP_NOT_IMPLEMENTED) — tunnel route
  configuration could not be attempted or verified here, only researched
  against docs. Retry it in the implementation session before assuming it's
  unavailable.
- `npx wrangler`, `npm`, and registry access all work in this environment;
  package versions above were read from the npm registry directly (not
  guessed).

## Suggested order for the implementation session

1. Retry the `Cloudflare_MCP` connector; if it connects, use it (or ask the
   user) to confirm the tunnel's current routes and get the required domain
   value instead of guessing.
2. Get the two required values above from the user (domain for the public
   hostname, and confirm the VM API's local port) — ask, don't assume.
3. Write `worker/src/index.ts` (routing + auth) and the `vm-api/` service.
4. Write the PWA under `worker/public/`.
5. Wire `MUSIC_API_ORIGIN` into `worker/wrangler.jsonc` `vars` and set
   `AUTH_PASSWORD` via `wrangler secret put`.
6. `npm install` in `worker/`, then `wrangler dev` / deploy.
