from __future__ import annotations

import email
import imaplib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.utils import requote_uri

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
}

ITALIAN_MONTHS = {
    "gennaio": 1, "gen": 1,
    "febbraio": 2, "feb": 2,
    "marzo": 3, "mar": 3,
    "aprile": 4, "apr": 4,
    "maggio": 5, "mag": 5,
    "giugno": 6, "giu": 6,
    "luglio": 7, "lug": 7,
    "agosto": 8, "ago": 8,
    "settembre": 9, "set": 9,
    "ottobre": 10, "ott": 10,
    "novembre": 11, "nov": 11,
    "dicembre": 12, "dic": 12,
}

IMAP_MONTHS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


@dataclass
class SourceDocument:
    item_id: str
    source_name: str
    title: str
    link_url: str
    published_at: datetime | None = None
    context_text: str = ""
    pdf_url: str | None = None
    pdf_bytes: bytes | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def parse_italian_date(text: str) -> datetime | None:
    if not text:
        return None
    clean = " ".join(text.lower().split())

    # Formato "16 novembre 2026"
    m = re.search(r"\b(\d{1,2})\s+([a-zà]+)\s+(20\d{2})\b", clean)
    if m:
        day = int(m.group(1))
        month = ITALIAN_MONTHS.get(m.group(2))
        year = int(m.group(3))
        if month:
            try:
                return datetime(year, month, day, tzinfo=timezone.utc)
            except ValueError:
                pass

    # Formato "16/11/2026" o "16-11-2026" o "16.11.2026"
    m = re.search(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](20\d{2})\b", clean)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            pass

    return None


def _find_key_recursive(obj: Any, target_key: str) -> Any:
    if isinstance(obj, dict):
        if target_key in obj:
            return obj[target_key]
        for v in obj.values():
            found = _find_key_recursive(v, target_key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_key_recursive(item, target_key)
            if found is not None:
                return found
    elif isinstance(obj, str) and target_key in obj and obj.lstrip().startswith("{"):
        try:
            parsed = json.loads(obj)
            return _find_key_recursive(parsed, target_key)
        except Exception:
            pass
    return None


def download_pdf(url: str, session: requests.Session | None = None) -> bytes:
    s = session or requests.Session()
    safe_url = requote_uri(url)
    resp = s.get(safe_url, headers=HEADERS, timeout=45)
    resp.raise_for_status()
    return resp.content


# ---------------- 1. FIGC (Next.js) ----------------

def fetch_figc(source_cfg: dict[str, str]) -> list[SourceDocument]:
    name = source_cfg["name"]
    url = source_cfg["url"]
    docs: list[SourceDocument] = []

    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    next_script = soup.find("script", id="__NEXT_DATA__")
    contents = []
    if next_script and next_script.string:
        try:
            next_data = json.loads(next_script.string)
            search_res = _find_key_recursive(next_data, "comunicatoMassimaSearchV2")
            if isinstance(search_res, dict):
                contents = search_res.get("contents") or []
        except Exception as e:
            print(f"[{name}] Avviso parsing __NEXT_DATA__: {e}")

    if contents:
        for item in contents:
            title = (item.get("title") or "").strip()
            page_url = item.get("url") or url
            body_html = item.get("body") or ""
            body_text = BeautifulSoup(body_html, "html.parser").get_text(" ", strip=True)

            pub_dt = None
            if item.get("publishedDate"):
                try:
                    pub_dt = datetime.fromisoformat(
                        item["publishedDate"].replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            resources = item.get("resources") or []
            pdf_resources = [
                r for r in resources
                if (r.get("url") or "").lower().split("?")[0].endswith(".pdf")
            ]

            if pdf_resources:
                for r in pdf_resources:
                    r_url = r["url"]
                    r_title = (r.get("title") or "").strip()
                    full_title = f"{title} - {r_title}" if r_title and r_title not in title else title
                    docs.append(
                        SourceDocument(
                            item_id=r_url,
                            source_name=name,
                            title=full_title or "Comunicato FIGC",
                            link_url=r_url,
                            published_at=pub_dt,
                            context_text=f"Titolo: {title}\nDescrizione: {body_text}",
                            pdf_url=r_url,
                        )
                    )
            elif body_text:
                docs.append(
                    SourceDocument(
                        item_id=page_url,
                        source_name=name,
                        title=title or "Comunicato FIGC",
                        link_url=page_url,
                        published_at=pub_dt,
                        context_text=f"Titolo: {title}\nTesto: {body_text}",
                    )
                )
        return docs

    # Fallback generico su link PDF nella pagina
    seen_urls = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"])
        if urlparse(href).path.lower().endswith(".pdf") and href not in seen_urls:
            seen_urls.add(href)
            docs.append(
                SourceDocument(
                    item_id=href,
                    source_name=name,
                    title=a.get_text(" ", strip=True) or "Comunicato FIGC",
                    link_url=href,
                    pdf_url=href,
                )
            )
    return docs[:20]


# ---------------- 2. CR LOMBARDIA (Laravel + AJAX form) ----------------

def _extract_crl_form_payload(form, source_url: str) -> list[tuple[str, str]]:
    payload: list[tuple[str, str]] = []
    present_keys: set[str] = set()

    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        itype = (inp.get("type") or "text").lower()
        val = inp.get("value", "")

        if itype in ("checkbox", "radio"):
            if inp.has_attr("checked"):
                payload.append((name, val))
                present_keys.add(name)
        else:
            payload.append((name, val))
            present_keys.add(name)

    for sel in form.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        selected = sel.find("option", selected=True)
        val = selected.get("value", "") if selected else ""
        payload.append((name, val))
        present_keys.add(name)

    # Assicura che i filtri della query string (es. content_category_value_id, delegazioni[])
    # siano sempre presenti nel POST anche se il form non li pre-compila lato server
    qs = parse_qs(urlparse(source_url).query)
    for key, values in qs.items():
        if key in ("data-il", "data-dal", "data-al", "q", "page"):
            continue
        existing_vals = {v for k, v in payload if k == key and v != ""}
        for v in values:
            if v not in existing_vals:
                # Rimuovi eventuale placeholder vuoto per quella chiave scalare
                if not key.endswith("[]"):
                    payload = [(k, old_v) for (k, old_v) in payload if k != key]
                payload.append((key, v))

    return payload


def _extract_detail_content(
    session: requests.Session, detail_url: str, fallback_title: str
) -> tuple[str, datetime | None, list[str]]:
    resp = session.get(requote_uri(detail_url), headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Rimuovi navigazione/footer per lasciare solo il contenuto utile
    main_soup = BeautifulSoup(str(soup), "html.parser")
    for tag in main_soup(["nav", "header", "footer", "script", "style"]):
        tag.decompose()
    text_body = " ".join(main_soup.get_text(" ", strip=True).split())
    pub_dt = parse_italian_date(text_body[:1000])

    pdf_urls: list[str] = []
    seen = set()
    for a in soup.find_all("a", href=True):
        full_href = urljoin(detail_url, a["href"].strip())
        if urlparse(full_href).path.lower().endswith(".pdf") and full_href not in seen:
            seen.add(full_href)
            pdf_urls.append(full_href)

    h1 = soup.find(["h1", "h2"])
    title = h1.get_text(" ", strip=True) if h1 else fallback_title
    excerpt = f"Titolo: {title}\nTesto pagina: {text_body[:3500]}"
    return excerpt, pub_dt, pdf_urls


def fetch_crlombardia(
    source_cfg: dict[str, str], max_entries: int = 12
) -> list[SourceDocument]:
    name = source_cfg["name"]
    url = source_cfg["url"]
    docs: list[SourceDocument] = []

    session = requests.Session()
    resp = session.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    form = soup.find("form", id="form-filter")
    list_html = ""
    grid_html = ""

    if form:
        action_url = urljoin(url, form.get("action") or url)
        payload = _extract_crl_form_payload(form, url)
        ajax_headers = {
            **HEADERS,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": url,
        }
        ajax_resp = session.post(action_url, data=payload, headers=ajax_headers, timeout=30)
        ajax_resp.raise_for_status()
        try:
            data = ajax_resp.json()
            list_html = data.get("list") or ""
            grid_html = data.get("grid") or ""
        except Exception as e:
            print(f"[{name}] Risposta AJAX non JSON ({e}): {ajax_resp.text[:250]}")
    else:
        print(f"[{name}] Form #form-filter non trovato, uso HTML diretto.")
        list_html = resp.text

    # Preferisci la vista tabellare (#lista) perché ogni riga <tr> contiene titolo + data + allegati
    entries: list[tuple[str, str, datetime | None, list[str]]] = []
    seen_detail_urls: set[str] = set()

    list_soup = BeautifulSoup(list_html, "html.parser")
    rows = list_soup.find_all("tr")

    if rows:
        for tr in rows:
            row_text = tr.get_text(" ", strip=True)
            row_dt = parse_italian_date(row_text)
            detail_url = None
            row_title = ""
            direct_pdfs: list[str] = []

            for a in tr.find_all("a", href=True):
                href = urljoin(url, a["href"].strip())
                path_low = urlparse(href).path.lower()
                if path_low.endswith(".pdf"):
                    if href not in direct_pdfs:
                        direct_pdfs.append(href)
                elif href.startswith("https://www.crlombardia.it/") and not href.endswith("#"):
                    if not detail_url:
                        detail_url = href
                        row_title = a.get_text(" ", strip=True)

            if detail_url and detail_url not in seen_detail_urls:
                seen_detail_urls.add(detail_url)
                entries.append((detail_url, row_title or row_text[:120], row_dt, direct_pdfs))
            elif direct_pdfs and not detail_url:
                entries.append((url, row_title or row_text[:120], row_dt, direct_pdfs))

    # Se la vista lista è vuota, analizza #griglia
    if not entries and grid_html:
        grid_soup = BeautifulSoup(grid_html, "html.parser")
        for a in grid_soup.find_all("a", href=True):
            href = urljoin(url, a["href"].strip())
            if not href.startswith("https://www.crlombardia.it/") or href.endswith("#"):
                continue
            if urlparse(href).path.lower().endswith(".pdf"):
                continue
            if href in seen_detail_urls:
                continue
            parent_text = a.parent.get_text(" ", strip=True) if a.parent else a.get_text(" ", strip=True)
            seen_detail_urls.add(href)
            entries.append((href, a.get_text(" ", strip=True) or parent_text[:120], parse_italian_date(parent_text), []))

    for detail_url, title, row_dt, direct_pdfs in entries[:max_entries]:
        pdf_urls = list(direct_pdfs)
        context_text = f"Titolo: {title}"
        pub_dt = row_dt

        if detail_url != url:
            try:
                excerpt, page_dt, page_pdfs = _extract_detail_content(session, detail_url, title)
                context_text = excerpt
                if pub_dt is None:
                    pub_dt = page_dt
                for p in page_pdfs:
                    if p not in pdf_urls:
                        pdf_urls.append(p)
            except Exception as e:
                print(f"[{name}] Errore lettura dettaglio {detail_url}: {e}")

        if pdf_urls:
            for p_url in pdf_urls:
                filename = urlparse(p_url).path.split("/")[-1]
                docs.append(
                    SourceDocument(
                        item_id=p_url,
                        source_name=name,
                        title=f"{title} ({filename})" if filename else title,
                        link_url=p_url,
                        published_at=pub_dt,
                        context_text=context_text,
                        pdf_url=p_url,
                        extra={"detail_url": detail_url},
                    )
                )
        else:
            # News o avviso solo testuale (es. emergenze / rinvii)
            docs.append(
                SourceDocument(
                    item_id=detail_url,
                    source_name=name,
                    title=title or "News CR Lombardia",
                    link_url=detail_url,
                    published_at=pub_dt,
                    context_text=context_text,
                )
            )

    return docs


# ---------------- 3. EMAIL ARUBA (Primavera 3 - PR3) ----------------

def _decode_mime_str(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    decoded: list[str] = []
    for chunk, charset in parts:
        if isinstance(chunk, bytes):
            decoded.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(chunk)
    return "".join(decoded).strip()


def fetch_aruba_pr3_emails(
    imap_host: str,
    imap_user: str,
    imap_pass: str,
    lookback_days: int = 10,
) -> list[SourceDocument]:
    docs: list[SourceDocument] = []
    if not imap_user or not imap_pass:
        print("[Email PR3] Credenziali IMAP mancanti, salto.")
        return docs

    since_dt = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    since_str = f"{since_dt.day:02d}-{IMAP_MONTHS[since_dt.month - 1]}-{since_dt.year}"

    mail = imaplib.IMAP4_SSL(imap_host, 993)
    try:
        mail.login(imap_user, imap_pass)
        mail.select("INBOX", readonly=True)

        status, data = mail.uid("search", None, f'(SINCE "{since_str}")')
        if status != "OK" or not data or not data[0]:
            return docs

        uids = data[0].split()
        for uid_bytes in uids:
            uid = uid_bytes.decode("utf-8", errors="ignore")
            res, msg_data = mail.uid("fetch", uid_bytes, "(RFC822)")
            if res != "OK" or not msg_data:
                continue

            raw_email = None
            for part in msg_data:
                if isinstance(part, tuple) and len(part) >= 2:
                    raw_email = part[1]
                    break
            if not raw_email:
                continue

            msg = email.message_from_bytes(raw_email)
            subject = _decode_mime_str(msg.get("Subject"))
            message_id = (msg.get("Message-ID") or uid).strip()

            pub_dt = None
            if msg.get("Date"):
                try:
                    pub_dt = parsedate_to_datetime(msg["Date"])
                    if pub_dt.tzinfo is None:
                        pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                except Exception:
                    pass

            body_snippets: list[str] = []
            attachments: list[tuple[str, bytes]] = []

            for part in msg.walk():
                if part.is_multipart():
                    continue
                raw_filename = part.get_filename()
                filename = _decode_mime_str(raw_filename)
                ctype = part.get_content_type()

                if filename and filename.lower().endswith(".pdf"):
                    fn_low = filename.lower()
                    subj_low = subject.lower()
                    # Regola PR3: prendi PDF che contengono "pr3" nel nome file,
                    # oppure PDF senza "pr4" quando l'oggetto riguarda solo PR3.
                    is_pr3_pdf = ("pr3" in fn_low) or (
                        "pr3" in subj_low and "pr4" not in fn_low and "pr4" not in subj_low
                    )
                    if is_pr3_pdf:
                        payload = part.get_payload(decode=True)
                        if payload:
                            attachments.append((filename, payload))
                elif ctype == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        body_snippets.append(payload.decode(charset, errors="replace"))

            body_text = "\n".join(body_snippets)[:1500]
            for filename, pdf_bytes in attachments:
                docs.append(
                    SourceDocument(
                        item_id=f"email:{message_id}:{filename}",
                        source_name="Lega Pro - Primavera 3 (Email)",
                        title=f"{subject} ({filename})" if subject else filename,
                        link_url="https://www.seriec.com/documentazione",
                        published_at=pub_dt,
                        context_text=f"Oggetto email: {subject}\nAllegato: {filename}\n{body_text}",
                        pdf_bytes=pdf_bytes,
                    )
                )
    finally:
        try:
            mail.logout()
        except Exception:
            pass

    return docs