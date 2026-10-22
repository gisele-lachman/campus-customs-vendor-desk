"""Code defense: reduce a vendor document to the text a person would actually see on screen.

HTML is rendered in a locked-down headless Chrome (no network, no vendor scripts) and every piece of text is checked
against what the page really draws. PDF text is kept only where the rendered page shows ink. Emails keep only what an
email app displays. Comments, metadata, hidden parts and invisible characters are dropped everywhere. Nothing here
ever follows or opens a link.
"""

import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from io import BytesIO
from pathlib import Path

import pdfplumber
from bs4 import BeautifulSoup, Comment

CHROME = os.environ.get("CHROME_PATH", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
MIN_PDF_FONT_PT = 4.0  # smaller than this can't be read on screen
PDF_INK_THRESHOLD = 30  # 0-255 brightness spread a glyph needs to count as drawn
MAX_PDF_PAGES = 30


@dataclass
class Visible:
    text: str
    removed: list[str] = field(default_factory=list)  # hidden text that was dropped, for the audit trail


def clean_chars(text: str) -> str:
    """Drop invisible format characters (zero-width spaces, bidi overrides, tag characters) and tidy whitespace."""
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf" or ch in "\n\t")
    text = re.sub(r"[ \t ]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


# --------------------------------------------------------------------------- HTML

# Strip anything that can run code or load something, then let the browser decide what is visible.
_DROP_TAGS = ["script", "noscript", "template", "iframe", "frame", "frameset", "object", "embed", "applet", "link",
              "meta", "base", "img", "picture", "source", "video", "audio", "track", "canvas", "input", "select",
              "textarea", "button", "dialog", "title"]
_URL_ATTRS = {"href", "src", "srcset", "action", "formaction", "poster", "background", "data", "xlink:href", "ping",
              "manifest", "longdesc", "cite", "usemap", "lowsrc", "dynsrc"}

_PROBE_JS = r"""
window.addEventListener('load', function () {
  const W = window.innerWidth;
  function rgba(s) { const m = s.match(/[\d.]+/g) || [0, 0, 0, 0]; return m.map(Number).concat([1]).slice(0, 4); }
  function lum(c) { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
                    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); }
  function background(el) {
    for (let e = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') return null;  // can't judge contrast on an image
      const c = rgba(cs.backgroundColor); if (c[3] > 0.5) return c;
    }
    return [255, 255, 255, 1];
  }
  function hidden(node) {
    const el = node.parentElement; if (!el) return 'no element';
    const r = document.createRange(); r.selectNodeContents(node);
    const rects = Array.from(r.getClientRects()).filter(b => b.width > 0 && b.height > 0);
    if (!rects.length) return 'not drawn';
    const cs = getComputedStyle(el);
    if (cs.visibility !== 'visible') return 'visibility';
    if (parseFloat(cs.fontSize) < 6) return 'tiny font';
    if (Math.max(...rects.map(b => b.height)) < 5) return 'squashed';
    let op = 1; for (let e = el; e; e = e.parentElement) op *= parseFloat(getComputedStyle(e).opacity);
    if (op < 0.15) return 'transparent';
    const fg = rgba(cs.color), fill = rgba(cs.webkitTextFillColor || cs.color);
    if (fg[3] < 0.15 || fill[3] < 0.15) return 'transparent text';
    const bg = background(el);
    if (bg) { const a = lum(fill), b = lum(bg); if ((Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05) < 1.5) return 'same color as background'; }
    if (rects.every(b => b.right <= 0 || b.bottom <= 0 || b.left >= W || b.top > 20000)) return 'off screen';
    const b = rects[0];
    for (let e = el; e && e !== document.documentElement; e = e.parentElement) {
      const s = getComputedStyle(e);
      const clips = s.overflowX !== 'visible' || s.overflowY !== 'visible' || s.clipPath !== 'none' || s.clip !== 'auto';
      if (!clips) continue;
      const p = e.getBoundingClientRect();
      const w = Math.min(b.right, p.right) - Math.max(b.left, p.left), h = Math.min(b.bottom, p.bottom) - Math.max(b.top, p.top);
      if (w <= 1 || h <= 1 || p.width < 5 || p.height < 5) return 'clipped';
    }
    const cx = b.left + b.width / 2, cy = b.top + b.height / 2;
    if (cx >= 0 && cy >= 0 && cx < W && cy < window.innerHeight) {
      const top = document.elementFromPoint(cx, cy);
      if (!top || !el.contains(top)) return 'covered or clipped';  // something else is drawn on top of this text
    }
    return null;
  }
  const removed = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const skip = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE']);
  const nodes = []; let n;
  while ((n = walker.nextNode())) if (n.nodeValue.trim() && !(n.parentElement && skip.has(n.parentElement.tagName))) nodes.push(n);
  const verdicts = nodes.map(hidden);
  nodes.forEach((n, i) => { if (verdicts[i]) { removed.push('[' + verdicts[i] + '] ' + n.nodeValue.trim()); n.nodeValue = ''; } });
  const out = document.createElement('script'); out.type = 'application/json'; out.id = '__ID__';
  out.textContent = JSON.stringify({text: document.body.innerText, removed: removed}).replace(/</g, '\\u003c');
  document.body.appendChild(out);
});
"""


def _defang_html(raw: str, nonce: str, probe_id: str) -> str:
    soup = BeautifulSoup(raw, "html.parser")
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for tag in soup(_DROP_TAGS):
        tag.decompose()
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            if attr.lower().startswith("on") or attr.lower() in _URL_ATTRS:
                del tag[attr]
    if not soup.html:
        soup = BeautifulSoup(f"<html><head></head><body>{soup}</body></html>", "html.parser")
    if not soup.head:
        soup.html.insert(0, soup.new_tag("head"))
    if not soup.body:
        body = soup.new_tag("body")
        for child in list(soup.html.contents):
            if child is not soup.head:
                body.append(child.extract())
        soup.html.append(body)
    # Block every load (links, images, fonts, CSS url()) and every script except our own probe.
    csp = soup.new_tag("meta", attrs={"http-equiv": "Content-Security-Policy",
                                      "content": f"default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-{nonce}'"})
    soup.head.insert(0, csp)
    soup.head.insert(0, soup.new_tag("meta", attrs={"charset": "utf-8"}))
    probe = soup.new_tag("script", attrs={"nonce": nonce})
    probe.string = _PROBE_JS.replace("__ID__", probe_id)
    soup.body.append(probe)
    return str(soup)


def html_visible(raw: bytes | str) -> Visible:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if not Path(CHROME).exists():
        raise RuntimeError(f"Chrome not found at {CHROME}; the code defense needs it to render HTML (set CHROME_PATH).")
    nonce, probe_id = secrets.token_hex(12), "probe_" + secrets.token_hex(8)
    workdir = tempfile.mkdtemp(prefix="sanitize_")
    try:
        page = Path(workdir) / "page.html"
        page.write_text(_defang_html(raw, nonce, probe_id), encoding="utf-8")
        cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
               "--disable-extensions", "--disable-background-networking", "--disable-sync", "--mute-audio",
               "--blink-settings=imagesEnabled=false", "--host-resolver-rules=MAP * ~NOTFOUND",
               f"--user-data-dir={workdir}/profile", "--window-size=1280,4000", "--virtual-time-budget=3000",
               "--dump-dom", page.as_uri()]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            dom, _ = proc.communicate(timeout=45)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)  # our own Chrome and its helpers only
                proc.wait()
        tag = BeautifulSoup(dom.decode("utf-8", errors="replace"), "html.parser").find("script", id=probe_id)
        if tag is None:
            raise RuntimeError("Chrome didn't return the visible text for this HTML.")
        data = json.loads(tag.string)
        return Visible(clean_chars(data["text"]), data["removed"])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------- PDF


def pdf_visible(raw: bytes) -> Visible:
    """Keep a character only if it's on the page, big enough to read, and the rendered page shows ink where it sits."""
    removed: list[str] = []
    pages: list[str] = []
    with pdfplumber.open(BytesIO(raw)) as pdf:
        for page in pdf.pages[:MAX_PDF_PAGES]:
            x_off, y_off = page.bbox[0], page.bbox[1]
            scale = 144 / 72
            img = page.to_image(resolution=144).original.convert("L")
            iw, ih = img.size
            dropped: dict[int, list[str]] = {}

            def keep(obj: dict) -> bool:
                if obj.get("object_type") != "char":
                    return True
                reason = None
                if (obj["x1"] <= page.bbox[0] or obj["x0"] >= page.bbox[2]
                        or obj["bottom"] <= page.bbox[1] or obj["top"] >= page.bbox[3]):
                    reason = "off page"
                elif obj.get("size", 0) < MIN_PDF_FONT_PT:
                    reason = "tiny font"
                elif obj["text"].strip():
                    box = (max(0, int((obj["x0"] - x_off) * scale)), max(0, int((obj["top"] - y_off) * scale)),
                           min(iw, int((obj["x1"] - x_off) * scale) + 1), min(ih, int((obj["bottom"] - y_off) * scale) + 1))
                    if box[2] <= box[0] or box[3] <= box[1]:
                        reason = "off page"
                    else:
                        lo, hi = img.crop(box).getextrema()
                        if hi - lo < PDF_INK_THRESHOLD:
                            reason = "not drawn (invisible, same color as background, or covered)"
                if reason:
                    dropped.setdefault(round(obj["top"]), [reason]).append(obj["text"])
                return reason is None

            pages.append(page.filter(keep).extract_text() or "")
            removed += [f"[{v[0]}] {''.join(v[1:]).strip()}" for v in dropped.values() if "".join(v[1:]).strip()]
    # Metadata, annotations, attachments and form data are never read.
    return Visible(clean_chars("\n\n".join(pages)), removed)


# --------------------------------------------------------------------------- email


def _part_visible(part: EmailMessage) -> Visible | None:
    ctype = part.get_content_type()
    name = part.get_filename() or ""
    if ctype == "text/html" or name.lower().endswith((".html", ".htm")):
        return html_visible(part.get_payload(decode=True) or b"")
    if ctype == "application/pdf" or name.lower().endswith(".pdf"):
        return pdf_visible(part.get_payload(decode=True) or b"")
    if ctype == "text/plain":
        return Visible(clean_chars(part.get_content()))
    return None


def _body_parts(part: EmailMessage, removed: list[str]) -> list[Visible]:
    """Walk the MIME tree the way an email app shows it."""
    if part.get_content_maintype() != "multipart":
        if part.is_attachment() or part.get_filename():
            # An app shows an attachment only as a file icon; its contents are never on screen, so drop it.
            removed.append(f"[attachment dropped: {clean_chars(part.get_filename() or part.get_content_type())}]")
            return []
        v = _part_visible(part)
        return [v] if v else []
    sub = list(part.iter_parts())
    if part.get_content_subtype() == "alternative":
        # Apps show one version, the richest they can: HTML over plain text. The others are never seen.
        best = next((p for p in reversed(sub) if p.get_content_type() == "text/html"), None) or \
            next((p for p in reversed(sub) if p.get_content_type() == "text/plain"), None)
        for p in sub:
            if p is not best and p.get_content_maintype() == "text":
                removed.append(f"[alternative part not shown: {p.get_content_type()}] " + clean_chars(p.get_content())[:300])
        return _body_parts(best, removed) if best is not None else []
    out = []
    for p in sub:
        out += _body_parts(p, removed)
    return out


def eml_visible(raw: bytes) -> Visible:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    removed: list[str] = []
    # Only the headers an email app shows; X- headers and the rest are dropped.
    lines = [f"{h}: {clean_chars(str(msg[h]))}" for h in ("From", "To", "Cc", "Date", "Subject") if msg[h]]
    blocks = []
    for v in _body_parts(msg, removed):
        blocks.append(v.text)
        removed += v.removed
    return Visible("\n".join(lines) + "\n\n" + "\n\n".join(b for b in blocks if b), removed)


# --------------------------------------------------------------------------- entry point


def visible_document(path: Path) -> Visible:
    raw = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix in (".html", ".htm"):
        return html_visible(raw)
    if suffix == ".pdf":
        return pdf_visible(raw)
    if suffix == ".eml":
        return eml_visible(raw)
    return Visible(clean_chars(raw.decode("utf-8", errors="replace")))
