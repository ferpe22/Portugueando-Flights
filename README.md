# Portugueando — Radar de vuelos ✈️

Busca diariamente ofertas Porto/Lisboa/Madrid/Barcelona → Buenos Aires
para feb–mar 2027, filtra las que están bajo €500/persona, y te las
muestra en un dashboard tipo "boarding pass" + resumen por email.

## Archivos

- `flight_tracker.py` — script principal (consulta la API, filtra, genera `results.json`, manda email)
- `dashboard.html` — dashboard visual (por ahora con datos de ejemplo embebidos)

## 1. Probar en local

```bash
pip install requests
python flight_tracker.py --demo     # sin token, con datos de ejemplo
```

Cuando tengas el token de Travelpayouts funcionando:

```bash
export TRAVELPAYOUTS_TOKEN="tu_token_aca"
python flight_tracker.py
```

Esto genera `results.json`. Reemplazá el objeto `DATA` dentro de
`dashboard.html` por el contenido de ese archivo para verlo actualizado
(o, en la versión automatizada de abajo, el dashboard lo lee solo).

## 2. Automatizar gratis con GitHub Actions

1. Creá un repo nuevo en GitHub (puede ser privado) y subí estos archivos.
2. En **Settings → Secrets and variables → Actions**, agregá como *secrets*:
   - `TRAVELPAYOUTS_TOKEN`
   - `EMAIL_FROM`, `EMAIL_TO`, `EMAIL_PASSWORD` (si usás Gmail, `EMAIL_PASSWORD`
     tiene que ser una "contraseña de aplicación", no tu contraseña normal:
     Google Cuenta → Seguridad → Contraseñas de aplicaciones)
3. Creá el archivo `.github/workflows/daily.yml` con:

```yaml
name: Búsqueda diaria de vuelos

on:
  schedule:
    - cron: "0 8 * * *"   # todos los días 8:00 UTC (~9:00 hora Porto)
  workflow_dispatch:        # también podés correrlo a mano desde GitHub

jobs:
  buscar:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install requests
      - run: python flight_tracker.py
        env:
          TRAVELPAYOUTS_TOKEN: ${{ secrets.TRAVELPAYOUTS_TOKEN }}
          EMAIL_FROM: ${{ secrets.EMAIL_FROM }}
          EMAIL_TO: ${{ secrets.EMAIL_TO }}
          EMAIL_PASSWORD: ${{ secrets.EMAIL_PASSWORD }}
      - name: Guardar resultados en el repo
        run: |
          git config user.name "flight-bot"
          git config user.email "bot@portugueando.local"
          git add results.json
          git commit -m "Actualiza resultados $(date +'%Y-%m-%d')" || echo "sin cambios"
          git push
```

4. Activá GitHub Pages (Settings → Pages → branch `main`) para que
   `dashboard.html` quede visible en una URL pública, y hacé que el
   dashboard haga `fetch('results.json')` en vez de usar datos embebidos
   (te lo dejo armado apenas confirmes que este flujo te sirve).

Con esto: todos los días a las 8am, GitHub busca, filtra, actualiza el
dashboard y te manda el email — gratis, sin servidor propio.

## 3. Cuando tengas acceso a Kiwi

Sumamos `fetch_kiwi_offers()` como fuente adicional en `flight_tracker.py`
(misma estructura, se combina con `collect_all_offers()`) y pasa a ser la
fuente principal de precio + equipaje confirmado, dejando Travelpayouts
como radar de tendencia.
