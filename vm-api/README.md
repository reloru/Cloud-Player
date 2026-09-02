# vm-api

Not yet implemented. This directory holds the music API that runs on the
Ubuntu VM behind the existing "VM" Cloudflare Tunnel — filesystem access
under `/music`, metadata, Range-request streaming, and streaming (not
buffered) uploads.

Decided: Python, standard library only. Must listen on `localhost:8000` —
that host/port is already registered as a Workers VPC Service target on
the "VM" tunnel, so the Worker can reach it. A different port means
recreating the VPC Service.

This session cannot reach the Ubuntu VM. Deployment there happens from the
Oracle Cloud box over Termius, by pulling this repo and running whatever
this directory ends up containing.

See ../docs/HANDOFF.md for the full spec and what's already configured.
