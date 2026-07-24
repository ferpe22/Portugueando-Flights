#!/usr/bin/env python3
"""
Portugueando Flight Tracker — chequeo en vivo (Google Flights vía SerpApi)
============================================================================
Complementa a flight_tracker.py (que corre todos los días con Travelpayouts,
datos en caché). Este script consulta Google Flights EN VIVO para una
muestra de fechas dentro de tus ventanas preferidas, así te confirma
precios reales del momento — no cacheados.

Pensado para correr 2 veces por semana (no todos los días), para quedarse
cómodo dentro del plan gratis de SerpApi (250 búsquedas/mes).

Uso:
    python serpapi_live_check.py            # modo real (necesita SERPAPI_KEY)
    python serpapi_live_check.py --demo      # con datos de ejemplo
"""

import os
import sys
import json
import smtplib
from datetime import date, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ---------------------------------------------------------------------------
# CONFIGURACIÓN
# ---------------------------------------------------------------------------

ORIGINS = ["OPO", "LIS", "MAD", "BCN"]
DESTINATION = "EZE"   # SerpApi/Google Flights necesita un aeropuerto puntual, no "BUE".
                       # Consultamos solo EZE (no AEP también) para mantenernos
                       # dentro del plan gratis de SerpApi — no te importa cuál
                       # aeropuerto te toque, así que no vale la pena duplicar consultas.

MAX_PRICE_EUR_PER_PERSON = 500
PASSENGERS = 2
CURRENCY = "EUR"

SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")
SERPAPI_URL = "https://serpapi.com/search"


def sample_dates():
    """
    Elige ~6 fechas repartidas dentro de tus dos ventanas preferidas
    (16-28 feb 2027 y 1-15 mar 2027), para no gastar de más la cuota
    gratis consultando cada día individualmente.
    """
    windows = [
        (date(2027, 2, 16), date(2027, 2, 28)),
        (date(2027, 3, 1), date(2027, 3, 15)),
    ]
    dates = []
    for start, end in windows:
        span = (end - start).days
        step = max(span // 2, 1)  # ~3 fechas por ventana
        d = start
        while d <= end:
            dates.append(d.isoformat())
            d += timedelta(days=step)
    return dates


# ---------------------------------------------------------------------------
# BÚSQUEDA
# ---------------------------------------------------------------------------

def fetch_live_price(origin, destination, outbound_date):
    """Una consulta = un crédito de SerpApi. Solo ida (type=2)."""
    import requests

    params = {
        "engine": "google_flights",
        "departure_id": origin,
        "arrival_id": destination,
        "outbound_date": outbound_date,
        "type": "2",          # solo ida
        "currency": CURRENCY,
        "adults": PASSENGERS,
        "hl": "es",
        "api_key": SERPAPI_KEY,
    }
    resp = requests.get(SERPAPI_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if "error" in data:
        raise RuntimeError(data["error"])

    google_flights_url = data.get("search_metadata", {}).get("google_flights_url", "")
    all_flights = data.get("best_flights", []) + data.get("other_flights", [])

    offers = []
    for f in all_flights:
        total_price = f.get("price")
        if total_price is None:
            continue
        legs = f.get("flights", [])
        if not legs:
            continue
        first_leg = legs[0]
        offers.append({
            "origin": origin,
            "destination": destination,
            "depart_date": outbound_date,
            "price_per_person": round(total_price / PASSENGERS, 2),
            "airline": first_leg.get("airline", "?"),
            "departure_at": first_leg.get("departure_airport", {}).get("time", ""),
            "stops": len(legs) - 1,
            "total_duration_min": f.get("total_duration"),
            "verify_link": google_flights_url,
            "source": "Google Flights (en vivo)",
        })
    return offers


def generate_demo_data():
    return [
        {"origin": "MAD", "destination": "EZE", "depart_date": "2027-02-17", "price_per_person": 465.0,
         "airline": "Air Europa", "departure_at": "2027-02-17 13:25", "stops": 1, "total_duration_min": 900,
         "verify_link": "https://www.google.com/travel/flights", "source": "Google Flights (en vivo)"},
        {"origin": "LIS", "destination": "EZE", "depart_date": "2027-03-01", "price_per_person": 512.0,
         "airline": "TAP", "departure_at": "2027-03-01 09:10", "stops": 1, "total_duration_min": 850,
         "verify_link": "https://www.google.com/travel/flights", "source": "Google Flights (en vivo)"},
    ]


def collect_all(demo=False):
    if demo:
        return generate_demo_data()

    if not SERPAPI_KEY:
        print("ERROR: falta SERPAPI_KEY. Usá --demo para probar sin key.")
        sys.exit(1)

    all_offers = []
    dates = sample_dates()
    calls_made = 0
    for origin in ORIGINS:
        for d in dates:
            try:
                offers = fetch_live_price(origin, DESTINATION, d)
                all_offers.extend(offers)
                calls_made += 1
            except Exception as e:
                print(f"[aviso] Falló {origin}->{DESTINATION} {d}: {e}")
    print(f"[info] {calls_made} consultas hechas a SerpApi en esta corrida.")
    return all_offers


def filter_deals(offers, max_price=MAX_PRICE_EUR_PER_PERSON):
    deals = sorted(
        [o for o in offers if o.get("price_per_person", 10**9) <= max_price],
        key=lambda o: o["price_per_person"],
    )
    return deals


# ---------------------------------------------------------------------------
# SALIDA
# ---------------------------------------------------------------------------

def save_results_json(deals, path="live_results.json"):
    payload = {
        "generated_at": date.today().isoformat(),
        "max_price_eur": MAX_PRICE_EUR_PER_PERSON,
        "source": "Google Flights (SerpApi, en vivo)",
        "deals": deals,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"Guardado {path} con {len(deals)} oferta(s) en vivo bajo €{MAX_PRICE_EUR_PER_PERSON}.")


def send_email_summary(deals):
    EMAIL_FROM = os.environ.get("EMAIL_FROM", "")
    EMAIL_TO = os.environ.get("EMAIL_TO", "")
    EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
    SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))

    if not (EMAIL_FROM and EMAIL_TO and EMAIL_PASSWORD):
        print("[email] Variables de email no configuradas, se omite el envío.")
        return
    if not deals:
        print("[email] Sin ofertas en vivo bajo el umbral, no se manda mail esta vez.")
        return

    lines = [
        f"- €{d['price_per_person']} p/persona | {d['origin']} → {d['destination']} | "
        f"{d['airline']} | sale {d['depart_date']} | {d['stops']} escala(s) | "
        f"Ver en Google Flights: {d['verify_link']}"
        for d in deals
    ]
    body = "Precios EN VIVO (Google Flights) confirmados bajo tu objetivo:\n\n" + "\n".join(lines)

    msg = MIMEMultipart()
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg["Subject"] = f"🔴 EN VIVO: {len(deals)} oferta(s) confirmadas bajo €{MAX_PRICE_EUR_PER_PERSON}"
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(EMAIL_FROM, EMAIL_PASSWORD)
        server.send_message(msg)
    print("[email] Resumen en vivo enviado.")


def main():
    demo = "--demo" in sys.argv
    print(f"Chequeo en vivo (SerpApi)... (modo demo: {demo})")

    all_offers = collect_all(demo=demo)
    deals = filter_deals(all_offers)
    save_results_json(deals)
    send_email_summary(deals)

    if deals:
        print(f"\n{len(deals)} oferta(s) EN VIVO bajo €{MAX_PRICE_EUR_PER_PERSON}:")
        for d in deals:
            print(f"  €{d['price_per_person']} — {d['origin']} → {d['destination']} ({d['airline']})")
    else:
        print("Nada en vivo bajo el umbral en esta corrida.")


if __name__ == "__main__":
    main()
