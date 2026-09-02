# vm-api

Not yet implemented. This directory holds the music API that runs on the
Ubuntu VM behind the existing "VM" Cloudflare Tunnel — filesystem access
under `/music`, metadata, Range-request streaming, and streaming (not
buffered) uploads.

This session cannot reach the Ubuntu VM. Deployment there happens from the
Oracle Cloud box over Termius, by pulling this repo and running whatever
this directory ends up containing.

See ../docs/HANDOFF.md for the full spec and the infrastructure values the
implementation session still needs.
