"""
analysis.py — Motor de análisis técnico
Indicadores: EMA, RSI, MACD, Bollinger, Estocástico, SuperTrend,
             Fibonacci, patrones de velas japonesas
Selección automática de estrategia según volatilidad del mercado
"""

import math


# ─────────────────────────────────────────────────────────────────
#  Indicadores base
# ─────────────────────────────────────────────────────────────────

def ema(prices, period):
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    val = sum(prices[:period]) / period
    for p in prices[period:]:
        val = p * k + val * (1 - k)
    return round(val, 6)


def rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(prices)):
        d = prices[i] - prices[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return round(100 - 100 / (1 + ag / al), 2)


def macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow + signal:
        return None, None, None
    ef = ema(prices, fast)
    es = ema(prices, slow)
    if ef is None or es is None:
        return None, None, None
    macd_line = round(ef - es, 6)
    # línea de señal simplificada
    macd_vals = []
    for i in range(slow, len(prices) + 1):
        ef2 = ema(prices[:i], fast)
        es2 = ema(prices[:i], slow)
        if ef2 and es2:
            macd_vals.append(ef2 - es2)
    sig_line = ema(macd_vals, signal) if len(macd_vals) >= signal else macd_line
    hist = round(macd_line - (sig_line or macd_line), 6)
    return macd_line, round(sig_line, 6) if sig_line else None, hist


def bollinger(prices, period=20, dev=2):
    if len(prices) < period:
        return None, None, None
    sub = prices[-period:]
    sma = sum(sub) / period
    std = math.sqrt(sum((p - sma) ** 2 for p in sub) / period)
    return round(sma + dev * std, 6), round(sma, 6), round(sma - dev * std, 6)


def stochastic(candles, k=14, d=3):
    if len(candles) < k:
        return None, None
    sub = candles[-k:]
    lo = min(c["min"] for c in sub)
    hi = max(c["max"] for c in sub)
    cl = candles[-1]["close"]
    if hi == lo:
        return 50.0, 50.0
    kval = round(((cl - lo) / (hi - lo)) * 100, 2)
    kvals = []
    for i in range(len(candles) - d, len(candles)):
        if i < k:
            continue
        s2 = candles[i - k + 1:i + 1]
        lo2 = min(c["min"] for c in s2)
        hi2 = max(c["max"] for c in s2)
        cl2 = candles[i]["close"]
        if hi2 != lo2:
            kvals.append(((cl2 - lo2) / (hi2 - lo2)) * 100)
    dval = round(sum(kvals) / len(kvals), 2) if kvals else kval
    return kval, dval


def supertrend(candles, period=7, mult=3.0):
    if len(candles) < period + 1:
        return None, None
    atrs = []
    for i in range(1, len(candles)):
        h = candles[i]["max"]
        l = candles[i]["min"]
        pc = candles[i - 1]["close"]
        atrs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(atrs) < period:
        return None, None
    atr_val = sum(atrs[-period:]) / period
    cl = candles[-1]["close"]
    mid = (candles[-1]["max"] + candles[-1]["min"]) / 2
    up_band = mid + mult * atr_val
    lo_band = mid - mult * atr_val
    trend = "UP" if cl > (up_band + lo_band) / 2 else "DOWN"
    return trend, round(atr_val, 6)


def fibonacci(candles, lookback=20):
    sub = candles[-min(lookback, len(candles)):]
    hi = max(c["max"] for c in sub)
    lo = min(c["min"] for c in sub)
    d = hi - lo
    niveles = {
        "0.0":   round(hi, 5),
        "23.6":  round(hi - 0.236 * d, 5),
        "38.2":  round(hi - 0.382 * d, 5),
        "50.0":  round(hi - 0.500 * d, 5),
        "61.8":  round(hi - 0.618 * d, 5),
        "78.6":  round(hi - 0.786 * d, 5),
        "100.0": round(lo, 5),
    }
    return niveles, hi, lo


def zona_fibonacci(precio, niveles, tol=0.002):
    for nivel, precio_nivel in niveles.items():
        if precio_nivel and abs(precio - precio_nivel) / precio_nivel <= tol:
            return nivel, precio_nivel
    return None, None


def patrones_velas(candles, n=3):
    if len(candles) < n:
        return []
    out = []
    ult = candles[-n:]

    def alc(c): return c["close"] > c["open"]
    def baj(c): return c["close"] < c["open"]
    def cuerpo(c): return abs(c["close"] - c["open"])
    def s_sup(c): return c["max"] - max(c["close"], c["open"])
    def s_inf(c): return min(c["close"], c["open"]) - c["min"]

    c = ult[-1]
    rango = c["max"] - c["min"] or 0.00001
    if cuerpo(c) < rango * 0.1:
        out.append({"patron": "Doji", "tipo": "NEUTRAL", "fuerza": 55})
    if s_inf(c) > cuerpo(c) * 2 and s_sup(c) < cuerpo(c) * 0.5:
        out.append({"patron": "Martillo", "tipo": "BUY", "fuerza": 75})
    if s_sup(c) > cuerpo(c) * 2 and s_inf(c) < cuerpo(c) * 0.5:
        out.append({"patron": "Estrella Fugaz", "tipo": "SELL", "fuerza": 75})

    if n >= 2:
        c0, c1 = ult[-2], ult[-1]
        if baj(c0) and alc(c1) and c1["open"] < c0["close"] and c1["close"] > c0["open"]:
            out.append({"patron": "Engulfing Alcista", "tipo": "BUY", "fuerza": 85})
        if alc(c0) and baj(c1) and c1["open"] > c0["close"] and c1["close"] < c0["open"]:
            out.append({"patron": "Engulfing Bajista", "tipo": "SELL", "fuerza": 85})

    if n >= 3:
        c0, c1, c2 = ult[-3], ult[-2], ult[-1]
        if all(alc(x) for x in [c0, c1, c2]) and c1["close"] > c0["close"] and c2["close"] > c1["close"]:
            out.append({"patron": "3 Soldados Blancos", "tipo": "BUY", "fuerza": 90})
        if all(baj(x) for x in [c0, c1, c2]) and c1["close"] < c0["close"] and c2["close"] < c1["close"]:
            out.append({"patron": "3 Cuervos Negros", "tipo": "SELL", "fuerza": 90})

    return out


# ─────────────────────────────────────────────────────────────────
#  Detección de volatilidad → selección automática de estrategia
# ─────────────────────────────────────────────────────────────────

def detectar_volatilidad(candles, periodo=14):
    """
    Retorna: "alta", "media", "baja"
    Basado en ATR relativo al precio
    """
    if len(candles) < periodo + 1:
        return "media"
    atrs = []
    for i in range(1, len(candles)):
        h = candles[i]["max"]
        l = candles[i]["min"]
        pc = candles[i - 1]["close"]
        atrs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr_val = sum(atrs[-periodo:]) / periodo
    precio = candles[-1]["close"]
    atr_pct = (atr_val / precio) * 100 if precio else 0

    if atr_pct > 0.3:
        return "alta"
    elif atr_pct > 0.1:
        return "media"
    else:
        return "baja"


def seleccionar_estrategia_auto(candles):
    """
    Elige la mejor estrategia según el mercado:
    - Alta volatilidad  → bollinger (rebotes en bandas)
    - Media volatilidad → fibonacci (retrocesos)
    - Baja volatilidad  → macd + ema (seguimiento de tendencia)
    """
    vol = detectar_volatilidad(candles)
    if vol == "alta":
        return "bollinger", vol
    elif vol == "media":
        return "fibonacci", vol
    else:
        return "tendencia", vol


# ─────────────────────────────────────────────────────────────────
#  Motor principal de señales
# ─────────────────────────────────────────────────────────────────

def generar_senal(candles, estrategia="auto"):
    if len(candles) < 30:
        return {"error": "Se necesitan al menos 30 velas"}

    closes = [c["close"] for c in candles]

    # Selección automática
    volatilidad = detectar_volatilidad(candles)
    if estrategia == "auto":
        estrategia, _ = seleccionar_estrategia_auto(candles)

    # ── Calcular todos los indicadores ──────────────────────────
    rsi_val   = rsi(closes)
    macd_v, macd_sig, macd_hist = macd(closes)
    ema9_v    = ema(closes, 9)
    ema21_v   = ema(closes, 21)
    ema50_v   = ema(closes, 50)
    bb_up, bb_mid, bb_lo = bollinger(closes)
    sto_k, sto_d = stochastic(candles)
    st_trend, atr_val = supertrend(candles)
    fib_niv, fib_hi, fib_lo = fibonacci(candles)
    fib_zona, fib_precio = zona_fibonacci(closes[-1], fib_niv)
    patrones  = patrones_velas(candles)
    precio    = closes[-1]

    # ── Puntuación ───────────────────────────────────────────────
    sb, ss = 0, 0   # score buy / sell
    razones = []

    # RSI
    if rsi_val is not None:
        if rsi_val < 30:
            sb += 3; razones.append(f"RSI sobrevendido ({rsi_val}) → compra")
        elif rsi_val > 70:
            ss += 3; razones.append(f"RSI sobrecomprado ({rsi_val}) → venta")
        elif rsi_val < 45:
            sb += 1; razones.append(f"RSI débil ({rsi_val}) — tendencia bajista moderada")
        elif rsi_val > 55:
            ss += 1; razones.append(f"RSI fuerte ({rsi_val}) — tendencia alcista moderada")

    # MACD
    if macd_hist is not None:
        if macd_hist > 0:
            sb += 2; razones.append(f"MACD histograma positivo ({macd_hist:.6f})")
        else:
            ss += 2; razones.append(f"MACD histograma negativo ({macd_hist:.6f})")

    # EMAs
    if ema9_v and ema21_v:
        if ema9_v > ema21_v:
            sb += 2; razones.append("EMA9 > EMA21 → tendencia alcista")
        else:
            ss += 2; razones.append("EMA9 < EMA21 → tendencia bajista")
    if ema21_v and ema50_v:
        if ema21_v > ema50_v:
            sb += 1; razones.append("EMA21 > EMA50 → momentum alcista")
        else:
            ss += 1; razones.append("EMA21 < EMA50 → momentum bajista")

    # Bollinger
    if bb_up and bb_lo:
        if precio <= bb_lo:
            sb += 3; razones.append("Precio en banda INFERIOR Bollinger → rebote alcista esperado")
        elif precio >= bb_up:
            ss += 3; razones.append("Precio en banda SUPERIOR Bollinger → rebote bajista esperado")
        elif estrategia == "bollinger":
            mid_dist = (precio - bb_mid) / (bb_up - bb_mid) if bb_up != bb_mid else 0
            if mid_dist < -0.3:
                sb += 1; razones.append("Precio bajo la media Bollinger → presión alcista")
            elif mid_dist > 0.3:
                ss += 1; razones.append("Precio sobre la media Bollinger → presión bajista")

    # Estocástico
    if sto_k is not None:
        if sto_k < 20:
            sb += 2; razones.append(f"Estocástico sobrevendido K={sto_k} D={sto_d}")
        elif sto_k > 80:
            ss += 2; razones.append(f"Estocástico sobrecomprado K={sto_k} D={sto_d}")
        if sto_d and sto_k > sto_d and sto_k < 50:
            sb += 1; razones.append("Cruce alcista estocástico")
        elif sto_d and sto_k < sto_d and sto_k > 50:
            ss += 1; razones.append("Cruce bajista estocástico")

    # SuperTrend
    if st_trend:
        if st_trend == "UP":
            sb += 2; razones.append("SuperTrend alcista ↑")
        else:
            ss += 2; razones.append("SuperTrend bajista ↓")

    # Fibonacci
    if estrategia == "fibonacci" and fib_zona:
        nivel = float(fib_zona)
        if nivel >= 61.8:
            sb += 4; razones.append(f"Precio en soporte Fibonacci {fib_zona}% → zona de rebote alcista")
        elif nivel <= 38.2:
            ss += 4; razones.append(f"Precio en resistencia Fibonacci {fib_zona}% → zona de rechazo bajista")

    # Patrones de velas
    for p in patrones:
        if p["tipo"] == "BUY":
            sb += 3; razones.append(f"Patrón: {p['patron']} → BUY (fuerza {p['fuerza']}%)")
        elif p["tipo"] == "SELL":
            ss += 3; razones.append(f"Patrón: {p['patron']} → SELL (fuerza {p['fuerza']}%)")
        else:
            razones.append(f"Patrón: {p['patron']} → NEUTRAL")

    # ── Decisión ─────────────────────────────────────────────────
    total = sb + ss
    if total == 0:
        direccion, confianza = "NEUTRAL", 50
    elif sb > ss:
        direccion = "BUY"
        confianza = round((sb / total) * 100)
    elif ss > sb:
        direccion = "SELL"
        confianza = round((ss / total) * 100)
    else:
        direccion, confianza = "NEUTRAL", 50

    if confianza < 60:
        direccion = "NEUTRAL"

    return {
        "direccion":    direccion,
        "confianza":    confianza,
        "score_buy":    sb,
        "score_sell":   ss,
        "estrategia":   estrategia,
        "volatilidad":  volatilidad,
        "razones":      razones,
        "indicadores": {
            "precio":       precio,
            "rsi":          rsi_val,
            "macd":         macd_v,
            "macd_signal":  macd_sig,
            "macd_hist":    macd_hist,
            "ema9":         ema9_v,
            "ema21":        ema21_v,
            "ema50":        ema50_v,
            "bb_superior":  bb_up,
            "bb_media":     bb_mid,
            "bb_inferior":  bb_lo,
            "estocastico_k": sto_k,
            "estocastico_d": sto_d,
            "supertrend":   st_trend,
            "atr":          atr_val,
        },
        "fibonacci": {
            "niveles":    fib_niv,
            "maximo":     round(fib_hi, 5),
            "minimo":     round(fib_lo, 5),
            "zona_actual": fib_zona,
            "precio_zona": fib_precio,
        },
        "patrones_velas": patrones,
    }
