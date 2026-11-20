"""``GET /p/<id>/<path>``: the files of a ready preview, for signed-in people who can open its card.

The route is public to the gateway so that a browser without a session gets a small sign-in
page instead of a JSON error; it checks the session itself. A signed-in visit to
``/p/<id>/<path>`` is sent on to ``/p/<id>/~<token>/<path>`` (``links``), because the page's own
requests carry no session; every request under a token checks the person again. Agent tokens are
refused (previews are for people; a Coworker uses the preview tools). An unknown id, another
workspace's id and a card the caller cannot see all get the same 404 page.

Preview files are built by an agent and run on the app's own origin, so every file is sent
with a sandbox policy that gives the page an opaque origin: it cannot read the app's cookies
or storage, and its requests carry no session. Pages that are not ready get a plain page with
the reason (410 stopped, 503 failed or being published). Nothing here runs under the
control-database lock except the row reads in ``service``; files are read after it is
released.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import bundle, links, service, store
from .store import NotFound

PREFIX = "/p/"
_ID = re.compile(r"pv_[0-9a-f]{16}")

# Every preview file. ``sandbox`` without ``allow-same-origin`` is the isolation; the rest keeps
# the page to its own files and lets the Deck frame it. Inline scripts and styles are allowed: a
# single-file report is the usual shape of a Coworker's page, and an inline script can do nothing
# a script file in the same bundle could not.
FILE_POLICY = (
    "sandbox allow-scripts allow-forms allow-popups; default-src 'self' data: blob:; "
    "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
    "connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'self'"
)
# The state and sign-in pages are this module's own text: no script at all.
PAGE_POLICY = (
    "sandbox; default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'self'"
)

CONTENT_TYPES = {
    "html": "text/html; charset=utf-8",
    "htm": "text/html; charset=utf-8",
    "css": "text/css; charset=utf-8",
    "js": "text/javascript; charset=utf-8",
    "mjs": "text/javascript; charset=utf-8",
    "json": "application/json",
    "map": "application/json",
    "txt": "text/plain; charset=utf-8",
    "md": "text/plain; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
    "xml": "application/xml",
    "svg": "image/svg+xml",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "avif": "image/avif",
    "ico": "image/x-icon",
    "woff": "font/woff",
    "woff2": "font/woff2",
    "ttf": "font/ttf",
    "otf": "font/otf",
    "wasm": "application/wasm",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "pdf": "application/pdf",
}
UNKNOWN_TYPE = "application/octet-stream"

STATE_STATUS = {"stopped": 410, "failed": 503, "building": 503}


def content_type(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return (
        CONTENT_TYPES.get(name.rsplit(".", 1)[-1].lower(), UNKNOWN_TYPE)
        if "." in name
        else UNKNOWN_TYPE
    )


def _send(h, status, body: bytes, kind: str, policy: str, extra=()) -> None:
    h.send_response(status)
    h.send_header("Content-Type", kind)
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Content-Security-Policy", policy)
    h.send_header("Cache-Control", "private, no-store")
    h.send_header("Referrer-Policy", "no-referrer")
    # No resource or opener policy: the sandboxed page has an opaque origin, so a same-origin
    # resource policy would block its own stylesheets and scripts, and a browser refuses a
    # sandboxed page that sets an opener policy. Other sites get no session cookie (SameSite).
    for name, value in extra:
        h.send_header(name, value)
    h.end_headers()
    h.wfile.write(body)


def page(h, status, title, lines, extra=(), links=()) -> None:
    """A plain page in words: the title, a few sentences and the links to go on with. The links
    are this module's own fixed addresses; nothing from the request is quoted."""
    text = "".join(f"<p>{html.escape(line)}</p>" for line in lines if line)
    onward = "".join(
        f'<p><a href="{html.escape(href, quote=True)}">{html.escape(words)}</a></p>'
        for href, words in links
    )
    body = (
        "<!doctype html><html lang=en><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:36rem;margin:3rem auto;"
        "padding:0 1rem;color:#1d1d1f;background:#fff}h1{font-size:1.4rem}"
        "a{display:inline-block;min-height:44px;line-height:44px;color:#0b57d0}</style>"
        f"<main><h1>{html.escape(title)}</h1>{text}{onward}</main>"
    ).encode("utf-8")
    _send(h, status, body, "text/html; charset=utf-8", PAGE_POLICY, extra)


def card_link(number) -> tuple:
    return (f"/app/#/card/{int(number)}", f"Open card #{int(number)} in Commonplace")


def not_found(h) -> None:
    page(
        h,
        404,
        "Preview not found",
        [
            "There is no preview at this address that you can open.",
            "Check the link, or find the preview under Previews on its card.",
        ],
        links=[("/app/", "Open Commonplace")],
    )


def file_path(rest: str) -> str | None:
    """The stored path a request asks for, or ``None`` when it may not name one: decoded once,
    then held to the same rules as a published path. An empty path or one ending in ``/`` asks
    for that folder's ``index.html``."""
    try:
        decoded = "/".join(unquote(part, errors="strict") for part in rest.split("/"))
    except UnicodeDecodeError:
        return None
    if decoded == "" or decoded.endswith("/"):
        decoded += bundle.ENTRY
    try:
        return bundle.clean_path(decoded)
    except bundle.Refused:
        return None


def resolve_file(folder: Path, path: str) -> Path | None:
    """The real file ``path`` names inside ``folder``, or ``None``: never a directory, never a
    link (the file or any folder on the way) and never anything that resolves outside."""
    base = folder.resolve()
    target = folder / path
    try:
        if target.is_symlink() or not target.is_file():
            return None
        inner = Path(path).parents
        if any((folder / part).is_symlink() for part in inner if str(part) != "."):
            return None
        real = target.resolve(strict=True)
        if not real.is_relative_to(base) or real == base:
            return None
        return real
    except (OSError, ValueError):
        return None


def read_file(folder: Path, path: str) -> bytes | None:
    real = resolve_file(folder, path)
    try:
        return real.read_bytes() if real is not None else None
    except OSError:
        return None


_SAFE_REST = re.compile(r"[A-Za-z0-9._~!$&'()*+,;=:@%/-]*")


def _redirect(h, status, location) -> None:
    h.send_response(status)
    h.send_header("Location", location)
    h.send_header("Content-Length", "0")
    h.send_header("Cache-Control", "private, no-store")
    h.send_header("Referrer-Policy", "no-referrer")
    h.end_headers()


def _sign_in(h) -> None:
    page(
        h,
        401,
        "Sign in to open this preview",
        [
            "Previews are open only to people in the workspace who can open the card.",
            "Opening a preview gives its address a key for 30 minutes: anyone who has that "
            "exact address can open the preview until then, so share the preview, not the "
            "address.",
            "Sign in to Commonplace, then open this preview again from its card.",
        ],
        (("Link", '</app/login>; rel="login"'),),
        links=[("/app/login", "Sign in to Commonplace")],
    )


def install(gateway) -> None:
    def serve(h):
        path = urlsplit(h.path).path
        identity, slash, inner = path[len(PREFIX) :].partition("/")
        if not _ID.fullmatch(identity) or not _SAFE_REST.fullmatch(inner):
            not_found(h)
            return
        token = None
        if inner.startswith(links.MARK):
            token, token_slash, inner = inner[len(links.MARK) :].partition("/")
        signed_in = gateway.principal_for(h)
        if token is not None:
            principal = links.principal(gateway, identity, token)
            other = signed_in is not None and (
                not signed_in.person or principal is None or signed_in.user_id != principal.user_id
            )
            if principal is None or other:
                # An unknown, expired or someone else's link: a signed-in browser starts again
                # from the plain address (which checks them); anyone else is asked to sign in.
                if signed_in is not None and signed_in.person:
                    _redirect(h, 303, f"{PREFIX}{identity}/{inner}")
                else:
                    _sign_in(h)
                return
        else:
            principal = signed_in
            if principal is None:
                _sign_in(h)
                return
            if not principal.person:
                page(
                    h,
                    403,
                    "Previews are for people",
                    ["Open this link in a browser where you are signed in to the app."],
                )
                return
        try:
            shown, folder = service.open_preview(gateway, principal, identity)
        except NotFound:
            not_found(h)
            return
        if not slash or (token is not None and not token_slash):
            # Relative links in the preview resolve against the folder, so send them there.
            base = f"{PREFIX}{identity}/" + (f"{links.MARK}{token}/" if token is not None else "")
            _redirect(h, 308, base)
            return
        state = shown["state"]
        card = card_link(shown["card"])
        if state != "ready":
            extra = (("Retry-After", "30"),) if state == "building" else ()
            title = {
                "stopped": "This preview is stopped",
                "failed": "This preview could not be published",
                "building": "This preview is being published",
            }[state]
            lines = [shown["reason_text"], shown["next"]]
            if state == "building":
                # This page does not refresh itself, unlike the card.
                lines[1] = "Reload this page, or open the card, to see when it is ready."
            page(h, STATE_STATUS[state], title, lines, extra, links=[card])
            return
        wanted = file_path(inner)
        unreadable = folder is None or not folder.is_dir()
        if wanted is not None and not unreadable and resolve_file(folder, wanted) is None:
            wanted = None  # an address that names no file: no key is made for it
        if wanted is None or unreadable:
            if unreadable:
                text, nxt = store.REASONS["files_unreadable"]
                page(h, 503, "This preview cannot be opened right now", [text, nxt], links=[card])
                return
            page(
                h,
                404,
                "Not in this preview",
                ["This preview has no file at this address."],
                links=[(f"{PREFIX}{identity}/", "Go to the preview's start page"), card],
            )
            return
        if token is None:
            # The page's own requests carry no session (it has no origin of its own), so it is
            # opened under a short-lived link that stands in for the session for this preview.
            _redirect(
                h,
                303,
                f"{PREFIX}{identity}/{links.MARK}{links.mint(gateway, principal, identity)}/{inner}",
            )
            return
        data = read_file(folder, wanted)
        if data is None:
            text, nxt = store.REASONS["files_unreadable"]
            page(h, 503, "This preview cannot be opened right now", [text, nxt], links=[card])
            return
        kind = content_type(wanted)
        extra = (("Content-Disposition", "attachment"),) if kind == UNKNOWN_TYPE else ()
        _send(h, 200, data, kind, FILE_POLICY, extra)

    gateway.add_prefix_route("GET", PREFIX, serve)
