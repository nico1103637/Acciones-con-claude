# Screener de acciones

Sitio estático publicado en Netlify (carpeta `site/`). Se actualiza cada día hábil:
1. Se traen datos de stockanalysis.com a `raw/*.jsonl` (la última línea de cada ticker manda).
   Proyección (FCF trimestral, estimados del año y EPS por trimestre) → `raw/proj.jsonl`; `python3 build.py --proj-due` dice qué tickers refrescar (semanal y después de cada reporte).
2. `python3 build.py` recalcula filtros, puntaje y plan de entrada → `site/data.json`.
3. Un push a `main` dispara el despliegue en Netlify.

Agregar o quitar acciones: editar `tickers.json` (ticker, sector, origen; múltiplo y margen opcionales).

## Mi cartera

`cartera.json` tiene las posiciones reales (acciones y costo promedio) y el efectivo. Si compras o vendes, edítalo.
Cada día `build.py` guarda el cierre de esas acciones en `raw/hist.jsonl` y calcula valor, ganancia, señales y una
recomendación por reglas. La lectura del analista va en `raw/notas.jsonl` (una línea por día y por ticker, más `CARTERA`).
