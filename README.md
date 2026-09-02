# Cloud Player

Personal music PWA.

```
iPhone --HTTPS--> Cloudflare Worker --(Workers VPC Service, over the existing "VM" tunnel)--> Ubuntu VM :8000 (music API, /music)
```

Status: repository and Cloudflare infrastructure are set up (Worker config,
a Workers VPC Service binding the Worker to the VM over the existing
tunnel); no application code exists yet. See `docs/HANDOFF.md` for the
full spec, what's already configured, and what's left (just the
`AUTH_PASSWORD` secret).

- `worker/` — the Cloudflare Worker: serves the PWA and proxies
  `/api/songs`, `/api/stream/:id`, `/api/upload`, `/api/delete/:id` to the
  VM API. Upload and delete require auth; browsing and streaming are
  public.
- `vm-api/` — the music API that runs on the Ubuntu VM behind the existing
  Cloudflare Tunnel, handling `/music` filesystem access, metadata, and
  Range-request streaming.
