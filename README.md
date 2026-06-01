# GPS MACRO — Sistema Top-Down (Macro → Sector → Precio)

Sistema cuantitativo automatizado que identifica:
1. **Fase del ciclo economico** (4 fases: Expansion / Recalentamiento / Contraccion / Desaceleracion).
2. **Sectores lideres y rezagados** (los 11 GICS via ETFs sectoriales).
3. **Divergencias macro vs mercado** (para evitar trampas).

Genera un **dashboard web estatico** que se publica automaticamente en GitHub Pages, actualizado cada dia habil pre-apertura del mercado US.

> **Filosofia:** el sistema responde a *donde* debe estar tu dinero (sector / sub-sector). El *cuando entrar* y los stops siguen siendo tuyos (tu analisis tecnico geometrico).

---

## Indicadores del modelo

### Macro principales (votan en la fase del ciclo)
| Indicador | Fuente | Eje |
|---|---|---|
| Empleo Manufactura (proxy PMI) | FRED `MANEMP` | Crecimiento |
| Curva de Rendimientos 10Y-2Y | FRED `T10Y2Y` | Crecimiento |
| Jobless Claims 4-Week MA | FRED `IC4WSA` | Crecimiento |
| High Yield OAS Spread | FRED `BAMLH0A0HYM2` | Stress |
| Chicago Fed NFCI | FRED `NFCI` | Stress |
| State Coincident LEI proxy | FRED `USSLIND` | Crecimiento |
| Breakeven Inflation 5Y | FRED `T5YIE` | Stress |
| Copper/Gold Ratio | Yahoo `HG=F`/`GC=F` | Crecimiento |
| VIX | Yahoo `^VIX` | Stress |

### Secundarios (informativos)
M2 Money Supply, Real Yield 10Y, Retail Sales, Building Permits, DXY.

### Score sectorial compuesto (0-100) — 11 ETFs GICS
Combina: **Mansfield RS (25%)**, **Momentum 1M/3M/6M (30%)**, **Cross-sectional rank (15%)**, **Breadth (10%)**, **Volume Flow / OBV (10%)**, **Alineacion con fase macro (10%)**.

---

## Estructura del proyecto

```
GPS_MACRO/
├── .github/workflows/daily-update.yml   # Cron + push automatico (GitHub Actions)
├── src/
│   ├── config.py             # Tickers, umbrales, pesos (puedes calibrar)
│   ├── data_fetcher.py       # FRED + Yahoo con cache y fallback Stooq
│   ├── macro_model.py        # Calculo de fase del ciclo
│   ├── sector_rotation.py    # Score compuesto sectorial
│   ├── divergence.py         # Sentimiento mercado + deteccion divergencias
│   ├── history_manager.py    # Persistencia incremental + deteccion de cambios
│   ├── dashboard_builder.py  # Render Jinja2 -> HTML
│   ├── synthetic_data.py     # Datos sinteticos para tests sin internet
│   └── main.py               # Pipeline completo
├── docs/                     # Lo que sirve GitHub Pages
│   ├── index.html            # Dashboard generado
│   ├── data/
│   │   ├── snapshot.json     # Estado actual
│   │   └── history.json      # Series para graficos historicos
│   ├── assets/{style.css, app.js}
│   └── templates/dashboard.html.j2
├── tests/test_macro_model.py
├── requirements.txt
└── .gitignore
```

---

## CHECKLIST DEL USUARIO (lo que hace el humano)

Solo **3 pasos manuales**. Todo el resto lo hace Claude / el sistema.

### Paso 1 — Obtener FRED API key (5 min, gratis)
1. Ir a https://fred.stlouisfed.org/docs/api/api_key.html
2. Pulsar "Request or View Your API Key" → registrarse con email.
3. Confirmar email → copiar la key (32 caracteres).

### Paso 2 — Crear el repositorio en GitHub
1. Ir a https://github.com/new
2. Nombre: `gps-macro` · Visibilidad: **Public** (necesario para GitHub Pages gratis) o Private (solo si tienes plan Pro/Team).
3. NO marcar "Add a README".
4. Crear el repo.

Desde la terminal en `GPS_MACRO/`:
```bash
git init
git add .
git commit -m "feat: initial GPS_MACRO system"
git branch -M main
git remote add origin https://github.com/<TU_USUARIO>/gps-macro.git
git push -u origin main
```

### Paso 3 — Configurar el secret + activar GitHub Pages
1. En el repo en GitHub → **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `FRED_API_KEY`
   - Value: la key del Paso 1.
2. En el mismo repo → **Settings → Pages**
   - Source: `Deploy from a branch`
   - Branch: `main` · Folder: `/docs`
   - Guardar.
3. En **Actions** dispara el workflow `Daily Macro Update` manualmente (boton "Run workflow") la primera vez.
4. Espera 2 minutos. Tu dashboard estara en:
   ```
   https://<TU_USUARIO>.github.io/gps-macro/
   ```

¡Listo! A partir de ahi el sistema corre solo cada dia habil a las 12:30 UTC (~07:30/08:30 ET, pre-apertura).

---

## Uso local (opcional — para inspeccionar)

```powershell
cd "c:\Users\rover\OneDrive\Escritorio\TRADING\GPS_MACRO"
python -m pip install -r requirements.txt

# Probar sin FRED API key (datos sinteticos):
python -m src.main --synthetic

# Probar con datos reales (define la variable):
$env:FRED_API_KEY = "tu_api_key_aqui"
python -m src.main

# Abrir el dashboard:
start docs\index.html
```

Ejecutar tests:
```powershell
pytest tests/
```

---

## Backtest / Validacion historica

El modulo `src/backtest.py` responde la pregunta clave: **¿las senales de GPS_MACRO
habrian aportado valor frente a comprar el indice (SPY)?** Reconstruye, mes a mes hacia
atras y **sin mirar el futuro** (point-in-time: cada fecha solo usa datos hasta ese dia),
la fase del ciclo y el ranking sectorial, y simula tres estrategias:

| Estrategia | Que hace |
|---|---|
| **TOPN** | Equipondera los N sectores con mayor score compuesto (el motor completo). |
| **PHASE** | Compra los sectores `favored` de la fase vigente (teoria pura de Stovall). |
| **SPY** | Comprar y mantener el indice (benchmark). |

Comparar TOPN vs PHASE vs SPY aisla cuanto aporta la capa tecnica (score) por encima de
la teoria del ciclo y por encima del mercado.

**Metricas:** CAGR, retorno total, volatilidad, Sharpe, Sortino, max drawdown, hit-rate
mensual vs SPY, exceso de CAGR y rendimiento medio por cada fase del ciclo.

```powershell
# Validar el motor sin internet ni API key (20 anos sinteticos):
python -m src.backtest --synthetic

# Backtest REAL (requiere FRED_API_KEY; descarga ~20 anos de FRED + Yahoo):
$env:FRED_API_KEY = "tu_api_key_aqui"
python -m src.backtest --years 20 --top 3 --rebalance M

# Ver el informe:
start docs\backtest.html
```

**Salidas:** `docs/data/backtest.json` (metricas + curvas + timeline de fase) y
`docs/backtest.html` (informe visual autocontenido con grafico SVG, sin dependencias).

> Nota: con `--synthetic` los datos son artificiales (sirven para validar la mecanica,
> no son resultados reales). Para numeros reales necesitas la FRED API key.

---

## Alertas push por Telegram (opcional)

El sistema puede **avisarte automaticamente** cuando detecta un evento relevante
(cambio de fase, cambio de sector lider o divergencia macro/mercado), sin que tengas
que entrar a la web. Es **opcional**: si no lo configuras, el pipeline lo omite.

### Configuracion (una vez, ~5 min, gratis)

1. **Crear el bot:** en Telegram, habla con **@BotFather** → `/newbot` → elige nombre →
   copia el **token** que te da (formato `123456:ABC-...`).
2. **Obtener tu chat id:** habla con **@userinfobot** (te responde tu `Id` numerico).
   Luego abre tu bot y pulsa "Start" para que pueda escribirte.
3. **Probar en local** (PowerShell):
   ```powershell
   $env:TELEGRAM_BOT_TOKEN = "tu_token"
   $env:TELEGRAM_CHAT_ID   = "tu_chat_id"
   python -m src.notifier --test    # debe llegarte un mensaje de prueba
   ```
4. **Activarlo en GitHub** (para las ejecuciones automaticas): repo → **Settings →
   Secrets and variables → Actions**:
   - Secret `TELEGRAM_BOT_TOKEN` = tu token
   - Secret `TELEGRAM_CHAT_ID` = tu chat id
   - *(Opcional)* Variable `GPS_DASHBOARD_URL` = la URL de tu dashboard (para incluir el enlace).
   - *(Opcional)* Variable `GPS_NOTIFY_ALWAYS` = `1` para recibir un resumen **cada dia**
     aunque no haya eventos (por defecto solo avisa cuando hay alerta).

Desde la siguiente ejecucion programada recibiras los avisos en Telegram.

---

## Como leer el dashboard

**Banner superior (rojo/amarillo/azul):** alerta cuando hay cambio de fase, divergencia macro/mercado o cambio de lider sectorial.

**Gauge de fase:** dice donde estamos en el ciclo + probabilidades a las 4 fases. Cambio de fase se declara solo si >55% sostenido 5 dias (config en `src/config.py`).

**Tabla macro:** valor actual, z-score (vs 10 anos), momentum 90d, score compuesto y sparkline.

**Heatmap sectorial:** los 11 GICS con su score 0-100 ordenados visualmente.

**Ranking detallado:** top-3 resaltado. Mira los componentes del score para entender por que el sector lidera.

**Posicionamiento por fase:** sectores favorecidos/a evitar segun el modelo + el top-3 actual.

**Sub-sectores del lider:** aparece solo si el sector top tiene score >= 70.

**Divergencia macro vs mercado:** dos barras. Si estan muy desalineadas, banner amarillo.

**Historico:** graficos de la evolucion de fase, crecimiento, stress y sentimiento.

---

## Como recalibrar (cuando ganes experiencia)

Editar `src/config.py`:
- **`SECTOR_SCORE_WEIGHTS`** — pesos de cada componente del score sectorial.
- **`MOMENTUM_WEIGHTS`** — pesos relativos de momentum 1M/3M/6M.
- **`PHASE_CHANGE_THRESHOLD`** y **`PHASE_CHANGE_PERSISTENCE_DAYS`** — sensibilidad para declarar cambio de fase.
- **`SUBSECTOR_TRIGGER_SCORE`** — umbral para mostrar sub-sectores del lider.
- **`PHASES[N]["favored_sectors"]`** — sectores favorecidos por fase (basado en Sam Stovall).

Cualquier cambio se aplica en la siguiente ejecucion programada.

---

## Troubleshooting

**El workflow falla con `FREDError: FRED_API_KEY no esta configurada`**
→ Faltan los secrets. Repite Paso 3 del checklist.

**Yahoo Finance devuelve vacio para algun ETF**
→ El fetcher cae automaticamente a Stooq. Si persiste, revisar logs en Actions.

**El dashboard no se actualiza en GitHub Pages**
→ Ver pestana **Actions** del repo. Si hay un workflow rojo, click → ver logs → fix.

**Pytest falla**
→ Los tests usan datos sinteticos, no requieren API key. Si fallan reportar el output completo.

---

## Diseno deliberado

- **Determinista**: mismo input -> mismo output, sin LLMs en el pipeline, auditable.
- **Sin base de datos**: el historico vive como JSON en Git -> versionado y reversible.
- **Cero costo**: GitHub Actions + Pages + FRED API + Yahoo Finance, todo gratis.
- **El usuario mantiene su edge**: el sistema dice *que mirar*, no *cuando comprar*.
