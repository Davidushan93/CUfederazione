from __future__ import annotations

import os
import tempfile
import time
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

from config import CLUB_INFO, GEMINI_MODELS


class AnalysisResult(BaseModel):
    rilevante: bool = Field(
        description=(
            "True se il documento menziona Pergolettese / le sue squadre, "
            "oppure contiene un avviso/scadenza/regola generale importante per tutte le società."
        )
    )
    motivo: str = Field(
        description="Breve classificazione: es. 'Citata Pergolettese', 'Avviso generale importante', 'Non rilevante'."
    )
    categorie_coinvolte: list[str] = Field(
        default_factory=list,
        description="Elenco categorie interessate o 'Avviso Generale'.",
    )
    titolo_sintetico: str = Field(
        description="Titolo breve e chiaro della notizia/provvedimento."
    )
    punti_chiave: list[str] = Field(
        default_factory=list,
        description=(
            "Elenco puntato sintetico e preciso di cosa c'è da sapere "
            "(squalifiche, ammende, variazioni gara con data/ora/campo, scadenze, regole)."
        ),
    )


def _build_prompt(source_name: str, title: str, context_text: str) -> str:
    return f"""Sei l'assistente della segreteria sportiva della società calcistica italiana descritta sotto.
Analizza il documento ufficiale / comunicato allegato (e il relativo testo di contesto) e stabilisci se contiene informazioni utili per la nostra società.

{CLUB_INFO}

Fonte: {source_name}
Titolo comunicato: {title}
Contesto testuale dal sito/email:
{context_text[:2500]}

CRITERI DI RILEVANZA (rilevante = true):
1. Qualsiasi menzione esplicita di "Pergolettese" (squalifiche, diffide, inibizioni, ammende/multe, omologazioni/decisioni del Giudice Sportivo, variazioni orario/data/campo gare, calendari/gironi con Pergolettese, convocazioni rappresentative, ecc.).
2. Avvisi, circolari o informazioni GENERALI molto importanti valide per tutte le società delle nostre categorie (Serie C/Primavera 3, SGS nazionale, CRLombardia o Delegazione Cremona), ad esempio:
   - scadenze per presentazione documenti, iscrizioni, tesseramenti, svincoli
   - nuovi portali, aggiornamenti operativi o cambi di regolamento importanti
   - riunioni, assemblee o incontri informativi per le società
   - rinvii o sospensioni generali dell'attività (es. maltempo, disposizioni federali)

CRITERI DI ESCLUSIONE (rilevante = false):
- Risultati, classifiche, squalifiche, multe o spostamenti gare che riguardano SOLO altre società (dove non compare Pergolettese).
- Calendari/comunicati di categorie a cui non partecipiamo (es. Eccellenza, Promozione, Prima/Seconda/Terza Categoria, Calcio a 5, Femminile), salvo avvisi generali amministrativi validi per tutte le società.
- Semplice modulistica vuota (es. moduli referto standard) senza nuovi obblighi o scadenze.

ISTRUZIONI:
- Sii preciso e non inventare mai dati. Riporta date, orari, nomi e importi esatti.
- Se rilevante = false, lascia punti_chiave vuoto.
"""


def _is_rate_limit_error(err: Exception) -> bool:
    msg = str(err).lower()
    return "429" in msg or "resource_exhausted" in msg or "quota" in msg


def analyze_document(
    source_name: str,
    title: str,
    context_text: str,
    pdf_bytes: bytes | None = None,
) -> AnalysisResult:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    prompt = _build_prompt(source_name, title, context_text)

    uploaded_file = None
    tmp_path = None

    try:
        contents: list = []
        if pdf_bytes:
            # Sotto i 15 MB invio diretto in memoria; sopra uso Files API
            if len(pdf_bytes) < 15 * 1024 * 1024:
                contents.append(
                    types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
                )
            else:
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(pdf_bytes)
                    tmp_path = tmp.name
                uploaded_file = client.files.upload(
                    file=tmp_path,
                    config={"mime_type": "application/pdf"},
                )
                contents.append(uploaded_file)

        contents.append(prompt)

        last_err: Exception | None = None
        for model_name in GEMINI_MODELS:
            for attempt in range(2):
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=AnalysisResult,
                            temperature=0.1,
                        ),
                    )
                    # Piccola pausa cortese per non saturare RPM del piano free
                    time.sleep(3)
                    if getattr(response, "parsed", None) is not None:
                        return response.parsed
                    return AnalysisResult.model_validate_json(response.text)
                except Exception as e:
                    last_err = e
                    if _is_rate_limit_error(e):
                        print(
                            f"[Gemini] Limite/quota su {model_name} (tentativo {attempt+1}): {e}"
                        )
                        if attempt == 0:
                            time.sleep(10)
                            continue
                        break  # passa al modello successivo (es. flash-lite)
                    raise

        raise RuntimeError(f"Tutti i modelli Gemini hanno esaurito la quota: {last_err}")

    finally:
        if uploaded_file is not None:
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:
                pass
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass