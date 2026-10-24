---
title: Public access
summary: A separate Proxy address for other people and devices: the model API only, its own keys with limits and a chosen list of models.
---

# Public access

The regular address of MemuAI Proxy (`127.0.0.1:8317`) is meant for programs on this same computer: the panel and account management live on it. It must not be opened to a network.

**Public access** is a second, separate address. From outside it serves only requests to the models: `/v1/models`, `/v1/chat/completions`, `/v1/responses` and `/v1/messages`. There is no panel, no management, none of your accounts or provider keys — any other path answers "not found". People get in only with the keys you created on the **Public access** tab.

This is how you let a friend, your phone, a server or a bot use the models — and limit or revoke that access at any moment.

## Turning it on

The **Public access** tab is four steps from top to bottom:

1. **Public server** — choose **where to listen** and the port (8320 by default; any number from 1 to 65535 — an empty field or 0 goes back to 8320), then flip the switch. The status line shows "Running" and the address; if the port is busy or this computer has no such address — the reason.
2. **The address to connect to** — links for this computer and the local network; for the internet the easiest is the free [Cloudflare Tunnel](#a-free-address-with-cloudflare-tunnel). Own domain, port forwarding and HTTPS are tucked away under "advanced".
3. **Models and keys** — tick the **models** that will be visible outside (the server does not start without them) and create at least one **key** — without a key the server lets nobody in.
4. **Connect** — ready-made examples with your address: curl, OpenAI Python, Node and the Anthropic SDK, with a copy button.

Connecting is the same as to any OpenAI-compatible API: the address from step 2 and an `mpk-…` key.

## Where to listen

| Option | When to choose it |
| --- | --- |
| Every network of this computer (`0.0.0.0`) | access from the local network and, after forwarding the port on the router, from the internet |
| This computer only (`127.0.0.1`) | a tunnel or a reverse proxy stands in front of the endpoint and faces outside |

**Domain or outside IP** is only for the link you show people: it does not change what the Proxy does.

### A free address with Cloudflare Tunnel

The simplest way to open access from the internet — no router setup, public IP or certificates. Press **Start the tunnel**: MemuAI runs the `cloudflared` program and after a few seconds shows an address like `https://…trycloudflare.com` with HTTPS. If `cloudflared` is not on the computer, MemuAI offers **Download cloudflared** — Cloudflare's official program from GitHub, after you confirm.

- Such an address is **temporary**: it changes when the tunnel restarts. Tick **Start the tunnel together with the server** so you do not have to start it by hand.
- For a permanent address on your own domain choose a **Named** tunnel: paste the **tunnel token** from the Cloudflare dashboard (**Networking → Tunnels → Create a tunnel**: the token is inside the install command, which you can paste whole) and its hostname — set it on the **Routes → Add route → Published application** tab with the Proxy address as the Service URL. The token is kept only on this computer and never shown again.
- For a tunnel, "This computer only (`127.0.0.1`)" is enough.
- The tunnel stops together with MemuAI Proxy — once the Proxy is closed, the address stops opening.

### For the address to open from the internet

- On the first start Windows asks about the firewall — allow access.
- Forward the chosen port on your router to this computer. If your internet provider gives you a shared address (CGNAT), forwarding will not help.
- Simpler and sturdier is a **tunnel**: the built-in Cloudflare Tunnel (section above), ngrok, Tailscale Funnel. Listen on `127.0.0.1` and point a third-party tunnel at `http://127.0.0.1:8320`. A tunnel also gives you HTTPS.
- **Without HTTPS** the keys and the conversations travel in the clear. For the internet use a tunnel, or set a certificate file and its key under "HTTPS and browsers" — the Proxy then answers over HTTPS itself.

**Check reachability** shows whether the server answers on this computer, and your outside address. Checking the port "from outside" from inside the network does not always work: many routers do not let a computer reach its own outside address. The reliable check is to open `…/health` from a phone on mobile data.

## Models

Only the ticked models are visible outside. A request for any other one gets "model not found" and never reaches your accounts. The list can be narrowed for an individual key as well.

## Limits of the endpoint

They apply to everyone together, on top of the keys' own limits. 0 means no limit.

| Limit | What it limits |
| --- | --- |
| Requests per minute | how many requests the endpoint accepts in a minute from all keys |
| Requests at once | how many answers are prepared at a time; the rest get "the server is busy" |
| Answer cap, tokens | a request with a larger `max_tokens` is refused |

## Keys

Give every person or app a key of their own. A key has:

| Field | Meaning |
| --- | --- |
| Issued to, note | to tell keys apart in the list |
| Requests per minute, per day | its own rate limits |
| Concurrent requests | how many answers for this key are prepared at a time |
| Tokens per day, per month | how many tokens (input + output) the key may spend; daily limits reset at midnight, monthly ones on the 1st |
| Tokens in total | once they are spent the key stops working |
| Max tokens per answer | a request with a larger `max_tokens` is refused |
| Budget, $ | a money cap, estimated from model prices (input + output) |
| Allowed IP addresses | comma-separated, subnets (CIDR) allowed; empty — any address. A list with a mistake (say, `1.2.3.4/40`) is not saved: the form stays open and shows which entries are wrong |
| Valid until | after this date the key is refused |
| Models | every model of the public list or only the chosen ones |

**A key is shown once** — right after it is created. Copy it: MemuAI keeps only a fingerprint of the key and cannot show it again. Lost a key? Press **Rotate**: the old one stops working at once and the new one is shown once.

The list shows how many requests and tokens a key spent today, this month and in total, the estimated cost, the last activity and how many times it was refused. A key can be paused with its switch, edited, reissued (**Rotate**), have its usage reset, or be revoked with the ✕ — the key is deleted and access with it stops at once.

Tokens are counted from the providers' answers. When a provider reports no token counts, the Proxy estimates them from the text length — token limits are approximate then.

When a limit is reached the client gets a 429 error with a clear reason and a `Retry-After` header. A wrong key gets 401; a disabled or expired key, a foreign IP or a model this key may not use get 403; an unpublished model gets 404.

### How the client address is found

The IP list is checked against the real address of whoever sent the request. When the request comes from this same computer (through a tunnel or a reverse proxy), the address is taken from the headers:

- `CF-Connecting-IP` counts **only while MemuAI's built-in tunnel is running** — Cloudflare itself sets it;
- of `X-Forwarded-For` only the **last** address counts — the nearest proxy (nginx, your own `cloudflared`) appends it; the addresses before it can be written by the client, so they are not trusted.

That way a key limited by IP can't be bypassed by putting someone else's address in a header.

## Worth remembering

- Requests with public keys spend your accounts and their limits exactly as your own do. Give keys only to people you trust, and set limits.
- The terms of some providers forbid sharing access. That is your responsibility.
- Requests through the public address go down the same pipeline and show on the Logs tab.
- The settings and the keys are stored in `public.json` in the Proxy data folder.

See also: [MemuAI Proxy](doc:proxy), [Proxy API](doc:proxy-api).
