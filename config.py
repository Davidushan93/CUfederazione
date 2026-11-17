CLUB_INFO = """
Società: U.S. Pergolettese 1932 s.r.l. (cerca varianti come "Pergolettese", "U.S. Pergolettese", "US Pergolettese 1932", "Pergo").
Squadre/Categorie di nostro interesse diretto:
- Primavera 3 (Lega Pro / Serie C)
- Under 17 Serie C
- Under 15 Serie C
- Under 14 PRO
- Under 13 PRO
- Under 13 Interregionali (Under 12 PRO)
- Esordienti Provinciali 11 anni (Cremona)
- Pulcini 10 anni (Cremona)
- Pulcini 9 anni (Cremona)
- Primi Calci 7/8 anni (Cremona)
"""

FIRST_RUN_LOOKBACK_DAYS = 3
NORMAL_MAX_AGE_DAYS = 14
IMAP_LOOKBACK_DAYS = 10

# Prova prima il modello Flash principale (20 req/giorno gratis),
# poi scala automaticamente su Flash-Lite (centinaia di req/giorno gratis) se serve.
GEMINI_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
]

FIGC_SOURCES = [
    {
        "name": "FIGC - Segreteria SGS",
        "url": "https://www.figc.it/it/giovani/norme/comunicati?category=Segreteria+SGS",
    },
    {
        "name": "FIGC - Campionati Giovanili",
        "url": "https://www.figc.it/it/giovani/norme/comunicati?category=Campionati+Giovanili",
    },
]

# Rimossi i parametri data-il / data-dal / data-al dagli URL originali
CRL_SOURCES = [
    {
        "name": "CR Lombardia - Comunicati CRL (Regionale)",
        "url": "https://www.crlombardia.it/comunicati?content_category_value_id=27&delegazioni%5B%5D=12",
    },
    {
        "name": "CR Lombardia - Comunicati Cremona (Provinciale)",
        "url": "https://www.crlombardia.it/comunicati?content_category_value_id=27&delegazioni%5B%5D=16",
    },
    {
        "name": "CR Lombardia - News Settore Giovanile (CRL)",
        "url": "https://www.crlombardia.it/news/settore-giovanile?content_category_value_id=37&delegazioni%5B%5D=12",
    },
    {
        "name": "CR Lombardia - Documenti (CRL)",
        "url": "https://www.crlombardia.it/documenti?content_category_value_id=26&delegazioni%5B%5D=12",
    },
]