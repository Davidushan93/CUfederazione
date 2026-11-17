from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from analyzer import AnalysisResult, analyze_document
from config import (
    CRL_SOURCES,
    FIGC_SOURCES,
    FIRST_RUN_LOOKBACK_DAYS,
    IMAP_LOOKBACK_DAYS,
    NORMAL_MAX_AGE_DAYS,
)
from notifier import send_telegram_report
from scrapers import (
    SourceDocument,
    download_pdf,
    fetch_aruba_pr3_emails,
    fetch_crlombardia,
    fetch_figc,
)

SEEN_PATH = Path("seen.json")
ROME_TZ = ZoneInfo("Europe/Rome")


def load_seen() -> dict:
    if SEEN_PATH.exists():
        try:
            data = json.loads(SEEN_PATH.read_text(encoding="utf-8"))
            if "items" in data and isinstance(data["items"], dict):
                return data
        except Exception:
            pass
    return {"_last_schedule_date": "", "items": {}}


def save_seen(data: dict) -> None:
    SEEN_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def should_run_now(seen_data: dict) -> tuple[bool, str]:
    now_rome = datetime.now(ROME_TZ)
    today_str = now_rome.strftime("%Y-%m-%d")
    event_name = os.getenv("GITHUB_EVENT_NAME", "local")

    # Se lanciato a mano ("workflow_dispatch") o in locale, esegui sempre
    if event_name != "schedule":
        return True, today_str

    # Da cron automatico: esegui solo nella finestra 07:30 - 08:45 ora italiana
    # (gestisce automaticamente ora solare/legale ed eventuali ritardi dei server GitHub)
    minutes_from_midnight = now_rome.hour * 60 + now_rome.minute
    if not (7 * 60 + 30 <= minutes_from_midnight <= 8 * 60 + 45):
        print(
            f"Run schedulata alle {now_rome.strftime('%H:%M')} Europe/Rome "
            "(fuori finestra 07:30-08:45). Salto."
        )
        return False, today_str

    if seen_data.get("_last_schedule_date") == today_str:
        print(f"Controllo automatico già eseguito oggi ({today_str}). Salto.")
        return False, today_str

    return True, today_str


def main() -> None:
    load_dotenv()
    seen_data = load_seen()
    can_run, today_str = should_run_now(seen_data)
    if not can_run:
        return

    seen_items: dict[str, str] = seen_data["items"]
    is_first_run = len(seen_items) == 0
    max_age_days = FIRST_RUN_LOOKBACK_DAYS if is_first_run else NORMAL_MAX_AGE_DAYS
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    print(
        f"Avvio controllo ({'PRIMO AVVIO' if is_first_run else 'normale'}, "
        f"finestra ultimi {max_age_days} giorni)..."
    )

    all_docs: list[SourceDocument] = []

    for src in FIGC_SOURCES:
        try:
            fetched = fetch_figc(src)
            print(f"[{src['name']}] Estratti {len(fetched)} elementi.")
            all_docs.extend(fetched)
        except Exception as e:
            print(f"[ERRORE] {src['name']}: {e}")

    for src in CRL_SOURCES:
        try:
            fetched = fetch_crlombardia(src, max_entries=12)
            print(f"[{src['name']}] Estratti {len(fetched)} elementi.")
            all_docs.extend(fetched)
        except Exception as e:
            print(f"[ERRORE] {src['name']}: {e}")

    try:
        imap_host = os.getenv("IMAP_HOST", "imaps.aruba.it")
        imap_user = os.getenv("IMAP_USER", "")
        imap_pass = os.getenv("IMAP_PASS", "")
        fetched = fetch_aruba_pr3_emails(
            imap_host, imap_user, imap_pass, lookback_days=IMAP_LOOKBACK_DAYS
        )
        print(f"[Email PR3] Estratti {len(fetched)} allegati PR3.")
        all_docs.extend(fetched)
    except Exception as e:
        print(f"[ERRORE] Email PR3: {e}")

    findings: list[tuple[str, str, str, AnalysisResult]] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for doc in all_docs:
        if doc.item_id in seen_items:
            continue

        # Se ha una data ed è più vecchio della soglia (es. > 3 giorni al primo avvio),
        # lo marchiamo come già visto senza consumare chiamate Gemini.
        if doc.published_at is not None:
            pub_utc = (
                doc.published_at.astimezone(timezone.utc)
                if doc.published_at.tzinfo
                else doc.published_at.replace(tzinfo=timezone.utc)
            )
            if pub_utc < cutoff_dt:
                seen_items[doc.item_id] = now_iso
                continue
        elif is_first_run:
            # Nel raro caso manchi la data su CRLombardia al primissimo avvio,
            # evitiamo di processare roba potenzialmente vecchia: marchiamo visto.
            seen_items[doc.item_id] = now_iso
            continue

        print(f"Analizzo: [{doc.source_name}] {doc.title}")
        try:
            pdf_bytes = doc.pdf_bytes
            if pdf_bytes is None and doc.pdf_url:
                pdf_bytes = download_pdf(doc.pdf_url)

            result = analyze_document(
                source_name=doc.source_name,
                title=doc.title,
                context_text=doc.context_text,
                pdf_bytes=pdf_bytes,
            )
            seen_items[doc.item_id] = now_iso

            if result.rilevante:
                print(f"  -> RILEVANTE ({result.motivo})")
                findings.append((doc.source_name, doc.title, doc.link_url, result))
            else:
                print(f"  -> Non rilevante")

        except Exception as e:
            print(f"  -> Errore analisi {doc.item_id}: {e}")
            # Non lo salvo in seen_items così ci riprova al giro dopo

    if findings:
        date_label = datetime.now(ROME_TZ).strftime("%d/%m/%Y")
        send_telegram_report(findings, date_label)
        print(f"Inviato report Telegram con {len(findings)} segnalazioni.")
    else:
        print("Nessuna novità rilevante oggi: nessuna notifica inviata.")

    if os.getenv("GITHUB_EVENT_NAME") == "schedule":
        seen_data["_last_schedule_date"] = today_str

    save_seen(seen_data)
    print("Stato seen.json aggiornato.")


if __name__ == "__main__":
    main()