# Cloud Player

Personal music PWA.

```
iPhone --HTTPS--> Cloudflare Worker --(existing Cloudflare Tunnel "VM")--> Ubuntu VM (music API, /music)
```

Status: repository scaffolding only. No application code exists yet — see
`docs/HANDOFF.md` for the full spec, what's already set up, and what the
next session still needs (including infrastructure values only the user
can supply).

- `worker/` — the Cloudflare Worker: serves the PWA and proxies
  `/api/songs`, `/api/stream/:id`, `/api/upload`, `/api/delete/:id` to the
  VM API. Upload and delete require auth; browsing and streaming are
  public.
- `vm-api/` — the music API that runs on the Ubuntu VM behind the existing
  Cloudflare Tunnel, handling `/music` filesystem access, metadata, and
  Range-request streaming.
