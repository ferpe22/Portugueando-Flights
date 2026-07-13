#!/usr/bin/env python3
"""
Portugueando Flight Tracker
============================
Busca diariamente ofertas de vuelos desde Porto/Lisboa/Madrid/Barcelona
hacia Buenos Aires (EZE/AEP), usando la Travelpayouts Data API.

Uso:
    python flight_tracker.py            # corre en modo real (necesita TRAVELPAYOUTS_TOKEN)
    python flight_tracker.py --demo     # corre con datos de ejemplo, sin llamar a la API

Salidas:
    - results.json  -> usado por el dashboard (dashboard.html)
    - Si hay ofertas bajo el umbral y variables de email configuradas, envía un resumen.
"""

import os
import sys
import json
import smtplib
from datetime import date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ---------------------------------------------------------------------------
# CONFIGURACIÓN — ajustá esto a tu gusto
# ---------------------------------------------------------------------------

ORIGINS = ["OPO", "LIS", "MAD", "BCN"]   # Porto, Lisboa, Madrid, Barcelona
DESTINATION = "BUE"                       # Código de ciudad que agrupa EZE + AEP

# Meses a barrer con el endpoint de calendario (trae de a un mes por llamada)
MONTHS_TO_QUERY = ["2027-02", "2027-03"]

# Ventana real de fechas que te interesa (filtrado del lado del script,
# porque el parámetro depart_date de la API no acota con precisión)
from datetime import date as _date
PREFERRED_WINDOWS = [
    (_date(2027, 2, 16), _date(2027, 2, 28)),  # 2da quincena de febrero
    (_date(2027, 3, 1), _date(2027, 3, 15)),   # 1ra quincena de marzo
]

MAX_PRICE_EUR_PER_PERSON = 500
TOP_N_IF_NO_DEALS = 5   # cuántas opciones "más baratas encontradas" mostrar aunque no bajen del umbral
PASSENGERS = 2
CURRENCY = "eur"

TRAVELPAYOUTS_TOKEN = os.environ.get("TRAVELPAYOUTS_TOKEN", "")
BASE_URL = "https://api.travelpayouts.com"


def _in_preferred_window(iso_date_str):
    try:
        d = _date.fromisoformat(iso_date_str[:10])
    except (ValueError, TypeError):
        return False
    return any(start <= d <= end for start, end in PREFERRED_WINDOWS)

# Email (opcional). Se puede usar Gmail con "contraseña de aplicación" o cualquier SMTP.
EMAIL_FROM = os.environ.get("EMAIL_FROM", "")
EMAIL_TO = os.environ.get("EMAIL_TO", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))


# ---------------------------------------------------------------------------
# BÚSQUEDA
# ---------------------------------------------------------------------------

def fetch_calendar_prices(origin, destination, month):
    """
    Llama a /v1/prices/calendar, que trae el precio más barato encontrado
    PARA CADA DÍA que tenga caché disponible. Confirmado funcionando con:
      - autenticación por header X-Access-Token (¡el parámetro ?token= da 401!)
      - filtrado real de fechas se hace en Python, porque el parámetro
        depart_date de la API no acota con precisión al mes pedido.
    Devuelve una lista de ofertas (dicts) ya limitadas a PREFERRED_WINDOWS.
    """
    import requests

    url = f"{BASE_URL}/v1/prices/calendar"
    params = {
        "currency": CURRENCY,
        "origin": origin,
        "destination": destination,
        "depart_date": month,
        "calendar_type": "departure_date",
        "limit": 31,
    }
    headers = {"X-Access-Token": TRAVELPAYOUTS_TOKEN}

    resp = requests.get(url, params=params, headers=headers, timeout=20)
    resp.raise_for_status()
    payload = resp.json()

    raw = payload.get("data", {}) or {}
    offers = []
    for depart_date_str, info in raw.items():
        if not _in_preferred_window(depart_date_str):
            continue
        offer = dict(info)
        offer["depart_date"] = depart_date_str
        offers.append(offer)
    return offers


def generate_demo_data():
    """Datos de ejemplo con la MISMA forma que devuelve /v1/prices/calendar
    (verificada contra la API real), para poder construir y probar el resto
    del pipeline sin gastar cuota."""
    return [
        {"origin": "MAD", "destination": "BUE", "airline": "LH", "departure_at": "2027-02-12T07:25:00+01:00", "return_at": "2027-02-26T01:40:00-03:00", "price": 916, "transfers": 2, "depart_date": "2027-02-12"},
        {"origin": "MAD", "destination": "BUE", "airline": "AC", "departure_at": "2027-02-17T13:25:00+01:00", "return_at": "2027-02-28T16:45:00-03:00", "price": 799, "transfers": 2, "depart_date": "2027-02-17"},
        {"origin": "MAD", "destination": "BUE", "airline": "AA", "departure_at": "2027-02-26T16:20:00+01:00", "return_at": "2027-03-06T23:35:00-03:00", "price": 831, "transfers": 1, "depart_date": "2027-02-26"},
        {"origin": "MAD", "destination": "BUE", "airline": "PU", "departure_at": "2027-03-01T23:10:00+01:00", "return_at": "2027-03-07T14:25:00-03:00", "price": 844, "transfers": 0, "depart_date": "2027-03-01"},
        {"origin": "LIS", "destination": "BUE", "airline": "ET", "departure_at": "2027-02-21T14:05:00Z", "return_at": "2027-03-30T22:10:00-03:00", "price": 479, "transfers": 1, "depart_date": "2027-02-21"},
    ]


def collect_all_offers(demo=False):
    if demo:
        return generate_demo_data()

    if not TRAVELPAYOUTS_TOKEN:
        print("ERROR: falta la variable de entorno TRAVELPAYOUTS_TOKEN. Usá --demo para probar sin token.")
        sys.exit(1)

    all_offers = []
    for origin in ORIGINS:
        origin_had_data = False
        for month in MONTHS_TO_QUERY:
            try:
                offers = fetch_calendar_prices(origin, DESTINATION, month)
                all_offers.extend(offers)
                if offers:
                    origin_had_data = True
            except Exception as e:
                print(f"[aviso] Falló la búsqueda {origin}->{DESTINATION} ({month}): {e}")
        if not origin_had_data:
            print(f"[info] Sin datos en caché para {origin}->{DESTINATION} en tu ventana de fechas.")

    # Deduplicar: la consulta de febrero y la de marzo se pisan en algunas
    # fechas límite, así que nos quedamos con una sola entrada por
    # (origin, depart_date, price, airline).
    seen = set()
    deduped = []
    for o in all_offers:
        key = (o.get("origin"), o.get("depart_date"), o.get("price"), o.get("airline"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(o)
    return deduped


# ---------------------------------------------------------------------------
# FILTRADO
# ---------------------------------------------------------------------------

def filter_deals(offers, max_price=MAX_PRICE_EUR_PER_PERSON):
    """
    Se queda con los vuelos por debajo del umbral. IMPORTANTE:
    el precio que da la API es indicativo (encontrado en una búsqueda pasada,
    hasta 7 días de antigüedad) y NO confirma equipaje incluido.
    Cualquier oferta acá es un candidato a VERIFICAR, no una compra confirmada.

    Si NINGUNA oferta baja del umbral, en vez de devolver una lista vacía
    (silencio total), devuelve las TOP_N_IF_NO_DEALS más baratas encontradas
    igual, marcadas como "por arriba del objetivo" — así ves la tendencia
    de precio acercándose (o no) a tu meta, en vez de no saber nada.
    """
    sorted_offers = sorted(offers, key=lambda o: o["price"])
    deals = [o for o in sorted_offers if o.get("price", 10**9) <= max_price]

    if not deals and sorted_offers:
        deals = sorted_offers[:TOP_N_IF_NO_DEALS]
        for d in deals:
            d["_above_target"] = True

    return deals


def build_aviasales_link(offer):
    origin = offer.get("origin", "")
    destination = offer.get("destination", "")
    dep = offer.get("departure_at", "")[:10].replace("-", "")[2:]  # ddmmyy aprox
    return f"https://www.aviasales.com/search/{origin}{dep}{destination}1"


# ---------------------------------------------------------------------------
# SALIDA: dashboard.json + email
# ---------------------------------------------------------------------------

def save_results_json(all_offers, deals, path="results.json"):
    payload = {
        "generated_at": date.today().isoformat(),
        "max_price_eur": MAX_PRICE_EUR_PER_PERSON,
        "passengers": PASSENGERS,
        "total_offers_checked": len(all_offers),
        "any_under_target": any(not d.get("_above_target") for d in deals),
        "deals": [
            {
                "origin": d.get("origin"),
                "destination": d.get("destination"),
                "price": d.get("price"),
                "airline": d.get("airline"),
                "departure_at": d.get("departure_at"),
                "return_at": d.get("return_at"),
                "stops": d.get("transfers"),
                "above_target": d.get("_above_target", False),
                "verify_link": build_aviasales_link(d),
            }
            for d in deals
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"Guardado {path} con {len(deals)} oferta(s).")
    return payload


def send_email_summary(deals):
    if not (EMAIL_FROM and EMAIL_TO and EMAIL_PASSWORD):
        print("[email] Variables EMAIL_FROM/EMAIL_TO/EMAIL_PASSWORD no configuradas, se omite el envío.")
        return

    real_deals = [d for d in deals if not d.get("_above_target")]

    if real_deals:
        subject = f"✈️ {len(real_deals)} oferta(s) bajo €{MAX_PRICE_EUR_PER_PERSON} — Porto/Lisboa/Madrid/Barcelona → Buenos Aires"
        target_list = real_deals
        intro = "Encontramos estas ofertas candidatas bajo tu objetivo (verificá precio final y equipaje antes de comprar):"
    elif deals:
        subject = f"Resumen diario — nada bajo €{MAX_PRICE_EUR_PER_PERSON} todavía, esto es lo más barato hoy"
        target_list = deals
        intro = "Ninguna llegó a tu objetivo hoy, pero así viene la tendencia (las más baratas encontradas):"
    else:
        subject = "Resumen diario — sin datos en caché hoy"
        body = "No había ningún precio cacheado para tus rutas hoy. Seguimos monitoreando mañana."
        target_list, intro = [], ""

    if target_list:
        lines = [
            f"- €{d['price']} | {d.get('origin')} → {d.get('destination')} | "
            f"{d.get('airline')} | sale {d.get('departure_at', '')[:10]} | "
            f"Verificar: {build_aviasales_link(d)}"
            for d in target_list
        ]
        body = intro + "\n\n" + "\n".join(lines)

    msg = MIMEMultipart()
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(EMAIL_FROM, EMAIL_PASSWORD)
        server.send_message(msg)
    print("[email] Resumen enviado.")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    demo = "--demo" in sys.argv
    print(f"Buscando vuelos... (modo demo: {demo})")

    all_offers = collect_all_offers(demo=demo)
    deals = filter_deals(all_offers)

    save_results_json(all_offers, deals)
    send_email_summary(deals)

    real_deals = [d for d in deals if not d.get("_above_target")]
    if real_deals:
        print(f"\n{len(real_deals)} oferta(s) encontradas bajo €{MAX_PRICE_EUR_PER_PERSON}:")
        for d in real_deals:
            print(f"  €{d['price']} — {d.get('origin')} → {d.get('destination')} ({d.get('airline')})")
    elif deals:
        print(f"\nNada bajo €{MAX_PRICE_EUR_PER_PERSON} todavía. Lo más barato encontrado:")
        for d in deals:
            print(f"  €{d['price']} — {d.get('origin')} → {d.get('destination')} ({d.get('airline')})")
    else:
        print("Sin datos en caché hoy para estas rutas.")


if __name__ == "__main__":
    main()
