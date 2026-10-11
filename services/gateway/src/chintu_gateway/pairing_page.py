"""Local pairing page: a QR code the Chintu Android app scans to add this computer.

The page and the other ``/local`` endpoints answer only requests that prove they come
from the operator's own session on this machine: a loopback peer, no proxy headers,
a loopback ``Host``, no browser cross-origin markers, and the per-start local secret
(``X-Chintu-Local`` header, or ``?key=`` for the page a browser opens). A local
process or a DNS-rebinding web page that lacks the secret gets a generic 403.
"""

import hmac
import html
import json
import socket

PROXY_HEADERS = ("x-forwarded-for", "tailscale-user-login", "x-forwarded-host")
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
LOCAL_HEADER = "x-chintu-local"


def is_direct_loopback(client_host, headers):
    """True only for a non-proxied request from this machine."""
    if client_host not in {"127.0.0.1", "::1"}:
        return False
    lowered = {str(k).lower() for k in headers}
    return not any(name in lowered for name in PROXY_HEADERS)


def host_allowed(host, port):
    """``Host`` must name loopback with the bound port; a bare name only when the port is the scheme default."""
    if not isinstance(host, str):
        return False
    allowed = {f"{name}:{port}" for name in LOOPBACK_HOSTS}
    if port == 80:
        allowed.update(LOOPBACK_HOSTS)
    return host.strip().lower() in allowed


def proof_matches(presented, secret):
    if not isinstance(presented, str) or not isinstance(secret, str) or not secret:
        return False
    return hmac.compare_digest(presented.encode("utf-8", "replace"), secret.encode("utf-8"))


def is_local_request(client_host, headers, port, secret, presented):
    """All checks must pass; callers answer a single generic refusal for any failure."""
    lowered = {str(k).lower(): v for k, v in headers}
    if not is_direct_loopback(client_host, lowered):
        return False
    if not host_allowed(lowered.get("host"), port):
        return False
    if "origin" in lowered or lowered.get("sec-fetch-site", "").strip().lower() == "cross-site":
        return False
    return proof_matches(presented, secret)


def pairing_payload(url, name, secret, aliases=""):
    """Exact JSON the app's QR scanner expects; keep keys stable."""
    return json.dumps({"chintu": 1, "url": url, "name": name, "aliases": aliases, "secret": secret},
                      separators=(",", ":"), ensure_ascii=True)


def default_name(gateway_url=None):
    """Prefer the Tailscale machine name (first DNS label); fall back to the OS hostname."""
    label = ""
    if gateway_url:
        label = gateway_url.split("//", 1)[-1].split("/", 1)[0].split(".", 1)[0]
    if not label:
        label = socket.gethostname().split(".", 1)[0]
    words = " ".join(label.replace("-", " ").replace("_", " ").split())
    return " ".join(w if w.isupper() else w.capitalize() for w in words.split()) or "This computer"


def qr_svg(payload):
    import segno  # Pure-Python, no network, no image stack.
    code = segno.make(payload, error="m")
    # Dark modules on a white quiet zone: phone scanners do not decode inverted codes.
    return code.svg_inline(scale=6, dark="#060316", light="#FFFFFF", border=3)


def render(url, name, secret, ttl_seconds, aliases=""):
    payload = pairing_payload(url, name, secret, aliases)
    svg = qr_svg(payload)
    minutes = max(1, ttl_seconds // 60)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Pair Chintu</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: #060316; color: #F4F0FF;
         font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  main {{ text-align: center; padding: 32px 24px; max-width: 520px; }}
  h1 {{ font-weight: 600; font-size: 22px; margin: 0 0 4px; letter-spacing: .02em; }}
  .wordmark {{ letter-spacing: .35em; color: #C7A9FF; font-size: 14px; margin-bottom: 20px; }}
  .qr {{ background: #FFFFFF; border: 1px solid #48306D; border-radius: 28px; padding: 16px; display: inline-block; }}
  .qr svg {{ display: block; width: min(68vw, 360px); height: auto; }}
  p {{ color: #9691AE; margin: 16px 0 0; }}
  code {{ color: #F4F0FF; background: #18112C; padding: 2px 8px; border-radius: 8px; }}
  .expiry {{ margin-top: 20px; font-size: 14px; }}
  a {{ color: #C7A9FF; }}
</style></head>
<body><main>
  <div class="wordmark">C H I N T U</div>
  <h1>Pair {html.escape(name)}</h1>
  <p>In Chintu on your phone, open the computer selector, tap <strong>Add a computer</strong>, then <strong>Scan the QR code</strong>.</p>
  <div class="qr" aria-label="Pairing QR code">{svg}</div>
  <p class="expiry">This code works once and expires in {minutes} minutes. Reload this page for a new one.</p>
  <p>Gateway address: <code>{html.escape(url)}</code></p>
</main></body></html>
"""


def refused_page():
    return """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Pair Chintu</title>
<style>body{background:#060316;color:#F4F0FF;font:16px system-ui;display:grid;place-items:center;min-height:100vh;margin:0}
main{max-width:480px;padding:24px;text-align:center}p{color:#9691AE}</style></head>
<body><main><h1>Open this page on the computer itself</h1>
<p>The pairing code is only shown to a browser on the same machine as the Chintu gateway, opened from the link
that <code>chintu-gateway pair-url</code> prints, so the secret never leaves that screen.</p></main></body></html>
"""


def unavailable_page(reason):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Pair Chintu</title>
<style>body{{background:#060316;color:#F4F0FF;font:16px system-ui;display:grid;place-items:center;min-height:100vh;margin:0}}
main{{max-width:480px;padding:24px;text-align:center}}p{{color:#9691AE}}</style></head>
<body><main><h1>Chintu can't pair yet</h1><p>{html.escape(str(reason))}</p>
<p>Connect this computer to Tailscale, then reload this page.</p></main></body></html>
"""
