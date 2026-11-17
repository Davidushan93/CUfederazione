from __future__ import annotations

import html
import os
import requests

from analyzer import AnalysisResult


def _send_telegram_chunk(token: str, chat_id: str, html_text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": html_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    if not resp.ok:
        raise RuntimeError(f"Errore Telegram {resp.status_code}: {resp.text}")


def send_telegram_report(
    findings: list[tuple[str, str, str, AnalysisResult]],
    date_label: str,
) -> None:
    """findings: lista di (source_name, doc_title, link_url, AnalysisResult)"""
    if not findings:
        return

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    header = f"⚽ <b>Comunicati U.S. Pergolettese — {html.escape(date_label)}</b>\n"
    blocks: list[str] = []

    for source_name, doc_title, link_url, res in findings:
        cats = ", ".join(res.categorie_coinvolte) if res.categorie_coinvolte else res.motivo
        lines = [
            f"📌 <b>{html.escape(res.titolo_sintetico or doc_title)}</b>",
            f"<i>Fonte:</i> {html.escape(source_name)} | <i>Tag:</i> {html.escape(cats)}",
        ]
        for pt in res.punti_chiave:
            lines.append(f"• {html.escape(pt)}")
        lines.append(f'🔗 <a href="{html.escape(link_url, quote=True)}">Apri comunicato</a>')
        blocks.append("\n".join(lines))

    current_msg = header
    for block in blocks:
        candidate = f"{current_msg}\n\n{block}"
        if len(candidate) > 3600:
            _send_telegram_chunk(token, chat_id, current_msg)
            current_msg = block
        else:
            current_msg = candidate

    if current_msg:
        _send_telegram_chunk(token, chat_id, current_msg)