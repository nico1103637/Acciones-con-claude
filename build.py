#!/usr/bin/env python3
"""Screener de acciones: arma site/data.json a partir de los datos crudos.

Datos crudos (JSON Lines, la última línea de cada ticker manda):
  raw/stats.jsonl     página /statistics/ de stockanalysis.com (diario, todas)
  raw/fin.jsonl       página /financials/ (+ cash-flow si falta FCF)  (semanal)
  raw/forecast.jsonl  página /forecast/ (diario, top 12)
  raw/tech.jsonl      página principal: rango de 52 semanas (diario, top 12)
  raw/proj.jsonl      proyección (semanal y tras cada reporte, todas):
                        stockanalysis /forecast/ (estimado del año fiscal: ventas, utilidad, EPS, FCF),
                        stockanalysis /financials/cash-flow-statement/?p=quarterly (FCF de 8 trimestres),
                        marketbeat /earnings/ (EPS estimado por trimestre y fecha del próximo reporte)
Config: tickers.json (sector, origen y supuestos manuales opcionales).

Uso:
  python3 build.py            -> escribe site/data.json
  python3 build.py --need     -> imprime los 12 tickers de mayor puntaje (para traer forecast y rango 52s)
  python3 build.py --proj-due -> imprime los tickers cuya proyección falta, tiene más de 7 días
                                 o ya pasó su fecha de reporte (hay que volver a traerla)
"""
import json, sys, os, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
TOP_N = 10


def latest(path):
    out = {}
    p = os.path.join(HERE, path)
    if not os.path.exists(p):
        return out
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        cur = out.setdefault(r["ticker"].upper(), {})
        for k, v in r.items():
            if v is not None or k not in cur:  # un dato vacío no borra uno anterior
                cur[k] = v
    return out


def compact(path):
    """Reescribe un .jsonl dejando una sola línea (la combinada más reciente) por ticker."""
    p = os.path.join(HERE, path)
    if not os.path.exists(p):
        return
    data = latest(path)
    with open(p, "w", encoding="utf-8") as f:
        for t in sorted(data):
            f.write(json.dumps(data[t], ensure_ascii=False) + "\n")


def num(v):
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def sube(x, y):
    x, y = num(x), num(y)
    return x is not None and y is not None and x > 0 and x > y


def pick(lst, i):
    return lst[i] if isinstance(lst, list) and len(lst) > i else None


def evaluar(a):
    v0, v1 = num(a["ventas_fy0"]), num(a["ventas_fy1"])
    u0, u1 = num(a["utilidad_fy0"]), num(a["utilidad_fy1"])
    m0 = u0 / v0 * 100 if u0 is not None and v0 else None
    m1 = u1 / v1 * 100 if u1 is not None and v1 else None
    pe, peg, roic, cr, de = num(a["pe"]), num(a["peg"]), num(a["roic"]), num(a["current_ratio"]), num(a["deuda_patrimonio_pct"])
    chk = {
        "chk_cap": (num(a["cap_b"]) or 0) > 10,
        "chk_ventas": sube(v0, v1),
        "chk_utilidad": sube(u0, u1),
        "chk_margen": m0 is not None and m1 is not None and m0 > 0 and m0 > m1,
        "chk_eps": sube(a["eps_fy0"], a["eps_fy1"]),
        "chk_pe": pe is not None and 0 < pe <= 20,
        "chk_peg": peg is not None and 0 < peg <= 2,
        "chk_fcf": sube(a["fcf_fy0"], a["fcf_fy1"]),
        "chk_roi": roic is not None and roic > 15,
        "chk_cr": cr is not None and cr > 1.5,
        "chk_de": de is not None and de < 200,
    }
    cumple = sum(chk.values())
    precio, obj = num(a["precio"]), num(a["precio_objetivo"])
    potencial = round((obj / precio - 1) * 100, 1) if precio and obj else None
    pf, fy, g = num(a["pe_fwd"]), num(a["fcf_yield"]), num(a["crec_eps_est"])
    valor = 15 if pf and 0 < pf <= 12 else 10 if pf and 0 < pf <= 18 else 5 if pf and 0 < pf <= 25 else 0
    caja = 10 if fy is not None and fy >= 8 else 7 if fy is not None and fy >= 5 else 4 if fy is not None and fy >= 3 else 0
    crec = 15 if g is not None and g >= 20 else 10 if g is not None and g >= 12 else 5 if g is not None and g >= 7 else 0
    pot = 10 if potencial is not None and potencial >= 30 else 6 if potencial is not None and potencial >= 15 else 3 if potencial is not None and potencial >= 5 else 0
    puntaje = round(cumple / 11 * 50 + valor + caja + crec + pot)
    a.update(chk)
    a.update(crec_ventas=pct_cambio(v0, v1), crec_utilidad=pct_cambio(u0, u1), crec_eps=pct_cambio(a["eps_fy0"], a["eps_fy1"]),
             crec_fcf=pct_cambio(a["fcf_fy0"], a["fcf_fy1"]),
             delta_margen=None if m0 is None or m1 is None else round(m0 - m1, 1))
    a.update(chk_div=(num(a["dividendo_fy0"]) or 0) > 0, margen_fy0=None if m0 is None else round(m0, 1),
             margen_fy1=None if m1 is None else round(m1, 1), cumple=cumple, potencial=potencial, puntaje=puntaje,
             veredicto="Candidata fuerte" if puntaje >= 75 else "Vigilar" if puntaje >= 60 else "No cumple")
    return a


def pct_cambio(a, b):
    a, b = num(a), num(b)
    return round((a / b - 1) * 100, 1) if a is not None and b is not None and b > 0 else None


def proyeccion(p):
    """Proyección trimestral y de flujo de caja libre (FCF) a partir de raw/proj.jsonl."""
    fq = [num(v) for v in (p.get("fcf_q") or [])]
    ends = list(p.get("q_end") or [])
    labels = list(p.get("q_labels") or [])
    n = min(len(fq), len(ends), len(labels))
    fq, ends, labels = fq[:n], ends[:n], labels[:n]
    # ¿reporta trimestral o semestral?
    gaps = []
    for i in range(min(n, 5) - 1):
        try:
            d0 = datetime.date.fromisoformat(ends[i]); d1 = datetime.date.fromisoformat(ends[i + 1])
            gaps.append((d0 - d1).days)
        except (TypeError, ValueError):
            pass
    per_anio = 2 if gaps and sorted(gaps)[len(gaps) // 2] > 130 else 4
    k = per_anio
    ok = lambda xs: len(xs) == k and all(v is not None for v in xs)
    ttm = sum(fq[:k]) if ok(fq[:k]) else None
    ttm_prev = sum(fq[k:2 * k]) if ok(fq[k:2 * k]) else None
    ult_aa = pct_cambio(fq[0], fq[k]) if n > k else None
    est, prev = num(p.get("fcf_est")), num(p.get("fcf_prev"))
    fpe = p.get("fy_prev_end")
    ytd_i = [i for i in range(n) if fpe and ends[i] and ends[i] > fpe]
    ytd = sum(fq[i] for i in ytd_i) if ytd_i and all(fq[i] is not None for i in ytd_i) else (0.0 if not ytd_i and n else None)
    restantes = per_anio - len(ytd_i)
    impl = round((est - ytd) / restantes, 1) if est is not None and ytd is not None and restantes > 0 else None
    prom_reciente = sum(fq[:k]) / k if ttm is not None else None
    exigencia = round(impl / prom_reciente, 2) if impl is not None and prom_reciente and prom_reciente > 0 else None
    est_crec = pct_cambio(est, prev)
    if est is not None and est <= 0:
        tend = "Negativo"
    else:
        base = est_crec if est_crec is not None else pct_cambio(ttm, ttm_prev)
        tend = None if base is None else "Creciendo" if base >= 10 else "Estable" if base >= -5 else "Cayendo"
    eps_q = [q for q in (p.get("eps_trim") or []) if isinstance(q, dict) and num(q.get("eps")) is not None][:4]
    q1 = eps_q[0] if eps_q else {}
    q2 = eps_q[1] if len(eps_q) > 1 else {}
    return {
        "q_moneda": p.get("q_moneda") or "USD", "periodos_anio": per_anio,
        "fcf_hist": [{"q": labels[i], "fin": ends[i], "fcf": fq[i]} for i in range(n)][::-1],  # antiguo -> reciente
        "fcf_ttm": None if ttm is None else round(ttm, 1), "fcf_ttm_crec": pct_cambio(ttm, ttm_prev), "fcf_ult_aa": ult_aa,
        "fy_est": p.get("fy_est"), "fy_prev": p.get("fy_prev"), "fcf_est": est, "fcf_prev": prev, "fcf_est_crec": est_crec,
        "fcf_ytd": None if ytd is None else round(ytd, 1), "periodos_ytd": len(ytd_i), "periodos_restantes": max(restantes, 0),
        "fcf_impl_trim": impl, "fcf_exigencia": exigencia, "fcf_tend": tend,
        "ventas_est": num(p.get("rev_est")), "ventas_est_crec": pct_cambio(p.get("rev_est"), p.get("rev_prev")),
        "util_est": num(p.get("ni_est")), "util_est_crec": pct_cambio(p.get("ni_est"), p.get("ni_prev")),
        "eps_est_fy": num(p.get("eps_est")), "eps_est_fy_crec": pct_cambio(p.get("eps_est"), p.get("eps_prev")),
        "analistas_fy": p.get("analysts"),
        "prox_reporte": p.get("prox_reporte"), "reporte_confirmado": p.get("reporte_confirmado"),
        "eps_trim": eps_q, "eps_q1": num(q1.get("eps")), "eps_q1_lbl": q1.get("q"), "eps_q1_n": q1.get("n"),
        "eps_q2": num(q2.get("eps")), "eps_q2_lbl": q2.get("q"), "eps_q2_n": q2.get("n"),
        "proj_fecha": p.get("proj_fecha"),
    }


def proj_due(cfg, pr):
    hoy = datetime.date.today()
    out = []
    for t in cfg:
        p = pr.get(t)
        if not p or not p.get("fcf_q") or not p.get("fy_est"):
            out.append(t); continue
        try:
            f = datetime.date.fromisoformat(p.get("proj_fecha"))
        except (TypeError, ValueError):
            out.append(t); continue
        rep = None
        try:
            rep = datetime.date.fromisoformat(p.get("prox_reporte"))
        except (TypeError, ValueError):
            pass
        if (hoy - f).days >= 7 or (rep and f <= rep < hoy):
            out.append(t)
    return out


def supuestos(a, cfg):
    """Múltiplo y margen: los manuales de tickers.json, o una regla transparente."""
    if cfg.get("multiplo") and cfg.get("margen_seg"):
        return cfg["multiplo"], cfg["margen_seg"], "manual"
    g = num(a["crec_eps_est"]) or 10
    m = max(10, min(22, round(g)))
    cambio, beta = num(a["cambio_52s"]) or 0, num(a["beta"]) or 1
    if a["sector"] == "Materiales":
        m = min(m, 12)
    if cambio > 150:  # posible pico de ciclo
        m = min(m, 8)
    margen = 15
    if beta > 1.5:
        margen = 20
    if beta > 2 or a["sector"] == "Materiales" or cambio > 150:
        margen = 25
    return m, margen, "regla"


def plan(a, cfg, fc, tc):
    precio = num(a["precio"])
    eps, ptm = num(fc.get("eps_prox")), num(fc.get("pt_mediana"))
    if not (precio and eps and ptm):
        return None
    mult, margen, origen = supuestos(a, cfg)
    fundamental = eps * mult
    vj = (fundamental + ptm) / 2
    emax = vj * (1 - margen / 100)
    e1 = min(precio, emax)
    sops = [v for v in (num(a.get("ma50")), num(a.get("ma200")), num(tc.get("min_52s"))) if v is not None and e1 * 0.80 <= v < e1 * 0.97]
    e2 = max(sops) * 1.01 if sops else e1 * 0.88
    prom = (e1 + e2) / 2
    stop = e2 * 0.92
    r = lambda v: round(v, 2)
    return {
        "ticker": a["ticker"], "empresa": a["empresa"], "precio": precio, "multiplo": mult, "margen_seg": margen,
        "supuesto": origen, "eps_prox": eps, "fy_prox": fc.get("fy_prox"), "pt_bajo": num(fc.get("pt_bajo")),
        "pt_mediana": ptm, "pt_alto": num(fc.get("pt_alto")), "ma50": num(a.get("ma50")), "ma200": num(a.get("ma200")),
        "min_52s": num(tc.get("min_52s")), "max_52s": num(tc.get("max_52s")),
        "valor_fundamental": r(fundamental), "valor_justo": r(vj), "entrada_max": r(emax), "entrada_1": r(e1),
        "entrada_2": r(e2), "entrada_promedio": r(prom), "stop": r(stop),
        "beneficio_pct": round((vj / prom - 1) * 100, 1), "beneficio_entrada1_pct": round((vj / e1 - 1) * 100, 1),
        "riesgo_beneficio": round((vj - prom) / (prom - stop), 1) if prom > stop else None,
        "accion": "Comprar escalonado" if precio <= emax else "Esperar a entrada 1",
        "tesis": cfg.get("tesis"), "riesgo": cfg.get("riesgo"), "puntaje": a["puntaje"], "cumple": a["cumple"],
    }


def main():
    cfg = {t["ticker"]: t for t in json.load(open(os.path.join(HERE, "tickers.json"), encoding="utf-8"))}
    if "--proj-due" in sys.argv:
        print(" ".join(proj_due(cfg, latest("raw/proj.jsonl"))))
        return
    if "--need" not in sys.argv:
        for f in ("raw/stats.jsonl", "raw/fin.jsonl", "raw/forecast.jsonl", "raw/tech.jsonl", "raw/proj.jsonl"):
            compact(f)
    st, fi, fcs, tcs = latest("raw/stats.jsonl"), latest("raw/fin.jsonl"), latest("raw/forecast.jsonl"), latest("raw/tech.jsonl")
    pr = latest("raw/proj.jsonl")
    rows = []
    for t, c in cfg.items():
        s, f = st.get(t), fi.get(t, {})
        if not s:
            continue
        de = num(s.get("debt_equity"))
        rev, ni, eps, fcf, div = (f.get(k) or [None] * 3 for k in ("revenue", "net_income", "eps_diluted", "fcf", "dividend_per_share"))
        rows.append(evaluar({
            "ticker": t, "empresa": s.get("company"), "sector": c["sector"], "origen": c["origen"],
            "precio": num(s.get("price")), "fecha_precio": s.get("price_date"), "cap_b": num(s.get("market_cap_b")),
            "pe": num(s.get("pe")), "pe_fwd": num(s.get("forward_pe")), "peg": num(s.get("peg")), "p_fcf": num(s.get("pfcf")),
            "ev_ebitda": num(s.get("ev_ebitda")), "fcf_yield": num(s.get("fcf_yield")), "roe": num(s.get("roe")),
            "roic": num(s.get("roic")), "margen_bruto": num(s.get("gross_margin")), "margen_op": num(s.get("operating_margin")),
            "margen_neto_ttm": num(s.get("profit_margin")), "deuda_patrimonio_pct": None if de is None else round(de * 100, 1),
            "current_ratio": num(s.get("current_ratio")), "crec_ventas_est": num(s.get("rev_growth_fcst")),
            "crec_eps_est": num(s.get("eps_growth_fcst")), "recomendacion": s.get("analyst_rating"),
            "analistas": s.get("analyst_count"), "precio_objetivo": num(s.get("price_target")),
            "cambio_52s": num(s.get("change_52w")), "rsi": num(s.get("rsi")), "beta": num(s.get("beta")),
            "ma50": num(s.get("ma50")) or num(tcs.get(t, {}).get("ma50")), "ma200": num(s.get("ma200")) or num(tcs.get(t, {}).get("ma200")),
            "moneda_estados": f.get("currency", "USD"), "fy0": f.get("fy0_label"), "fy1": f.get("fy1_label"),
            "ventas_fy0": pick(rev, 1), "ventas_fy1": pick(rev, 2), "utilidad_fy0": pick(ni, 1), "utilidad_fy1": pick(ni, 2),
            "eps_fy0": pick(eps, 1), "eps_fy1": pick(eps, 2), "fcf_fy0": pick(fcf, 1), "fcf_fy1": pick(fcf, 2),
            "dividendo_fy0": pick(div, 1), "fuente": f"https://stockanalysis.com/stocks/{t.lower()}/statistics/",
        }))
    for r in rows:
        r.update(proyeccion(pr.get(r["ticker"], {})))
    rows.sort(key=lambda r: -r["puntaje"])
    if "--need" in sys.argv:
        print(" ".join(r["ticker"] for r in rows[:12]))
        return
    planes, faltan = [], []
    for r in rows:
        if len(planes) >= TOP_N:
            break
        p = plan(r, cfg[r["ticker"]], fcs.get(r["ticker"], {}), tcs.get(r["ticker"], {}))
        if p:
            planes.append(p)
        else:
            faltan.append(r["ticker"])
    def fecha(s):
        for fmt in ("%b %d, %Y", "%Y-%m-%d"):
            try:
                return datetime.datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
            except (ValueError, AttributeError):
                pass
        return None
    for r in rows:
        r["fecha_precio"] = fecha(r["fecha_precio"]) or r["fecha_precio"]
    fechas = sorted({r["fecha_precio"] for r in rows if r["fecha_precio"] and r["fecha_precio"][:2] == "20"})
    out = {
        "actualizado": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fecha_precios": fechas[-1] if fechas else None,
        "resumen": {"analizadas": len(rows), "cumplen_todo": sum(r["cumple"] == 11 for r in rows),
                    "cumplen_9": sum(r["cumple"] >= 9 for r in rows), "fuertes": sum(r["puntaje"] >= 75 for r in rows)},
        "acciones": rows, "plan": planes, "sin_plan": faltan,
    }
    os.makedirs(os.path.join(HERE, "site"), exist_ok=True)
    json.dump(out, open(os.path.join(HERE, "site", "data.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    sin_proj = [r["ticker"] for r in rows if not r["fcf_hist"]]
    print(f"OK: {len(rows)} acciones, {len(planes)} planes. Sin datos de plan: {faltan or 'ninguna'}. Sin proyección: {sin_proj or 'ninguna'}")


if __name__ == "__main__":
    main()
