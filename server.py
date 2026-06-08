"""
server.py — PocketOption Bot API
Servidor personal protegido con API Key para conectar con Lovable.
SOLO LECTURA — no compra ni vende automaticamente.

Endpoints:
  POST /po/conectar     → conectar con SSID de PocketOption
  GET  /po/estado       → estado conexion y saldo
  GET  /po/activos      → lista activos disponibles con payout
  GET  /po/velas        → velas historicas
  POST /po/senal        → senal BUY/SELL con analisis tecnico
  GET  /po/desconectar  → cerrar sesion
  GET  /demo/senal      → demo sin login
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, jsonify, request
from flask_cors import CORS
import asyncio
import time
import logging
import threading
from datetime import datetime, timezone
from functools import wraps

from analysis import generar_senal

# ── Configuracion ────────────────────────────────────────────────
API_KEY = os.environ.get("API_KEY", "LCn_cReJtXYhmiUxXDO_DNZZ6VYx4hqT2nyNlk_Rk6c")
PORT    = int(os.environ.get("PORT", 8000))

app = Flask(__name__)
CORS(app, origins="*")
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ── Sesion global ────────────────────────────────────────────────
sesion = {"client": None, "ssid": None, "conectado": False, "lock": threading.Lock()}

# ── Loop asyncio dedicado ────────────────────────────────────────
loop = asyncio.new_event_loop()

def iniciar_loop():
    asyncio.set_event_loop(loop)
    loop.run_forever()

hilo_loop = threading.Thread(target=iniciar_loop, daemon=True)
hilo_loop.start()

def run_async(coro):
    """Ejecutar corutina desde hilo sincrono"""
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=30)


# ── Auth ─────────────────────────────────────────────────────────
def requiere_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        key = (request.headers.get("X-API-Key")
               or request.args.get("api_key")
               or (request.get_json(silent=True) or {}).get("api_key"))
        if key != API_KEY:
            return jsonify({"error": "API key invalida", "hint": "Incluye X-API-Key en el header"}), 401
        return f(*args, **kwargs)
    return wrapper

def requiere_conexion(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not sesion["conectado"] or sesion["client"] is None:
            return jsonify({"error": "No conectado. Llama primero POST /po/conectar"}), 403
        return f(*args, **kwargs)
    return wrapper


# ── Helpers ──────────────────────────────────────────────────────
def vela_a_dict(c):
    ts = int(c.timestamp.timestamp()) if hasattr(c.timestamp, 'timestamp') else int(c.timestamp)
    return {
        "timestamp": ts,
        "datetime":  datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "open":  float(c.open),
        "high":  float(c.high),
        "low":   float(c.low),
        "close": float(c.close),
        "max":   float(c.high),
        "min":   float(c.low),
        "volumen": float(c.volume) if hasattr(c, 'volume') else 0,
    }


# ════════════════════════════════════════════════════════════════
#  Endpoints publicos
# ════════════════════════════════════════════════════════════════

@app.route("/")
def raiz():
    return jsonify({
        "api":     "PocketOption Bot API",
        "version": "1.0",
        "estado":  "online",
        "tu_api_key": API_KEY,
        "nota": "Usa el SSID de tu navegador para conectarte",
        "endpoints": [
            "POST /po/conectar   { ssid, is_demo }",
            "GET  /po/estado",
            "GET  /po/activos",
            "GET  /po/velas?activo=EURUSD_otc&intervalo=60&cantidad=100",
            "POST /po/senal      { activo, intervalo, duracion, estrategia }",
            "GET  /po/desconectar",
            "GET  /demo/senal?activo=EURUSD_otc&intervalo=60",
        ]
    })


@app.route("/demo/senal")
@requiere_key
def demo_senal():
    import random
    activo     = request.args.get("activo",    "EURUSD_otc")
    intervalo  = int(request.args.get("intervalo",  60))
    duracion   = int(request.args.get("duracion",    1))
    estrategia = request.args.get("estrategia", "auto")

    random.seed(int(time.time()) // intervalo)
    precio = 1.08500
    candles = []
    for i in range(120):
        cambio = random.uniform(-0.0006, 0.0006)
        op = precio; cl = precio + cambio
        hi = max(op, cl) + random.uniform(0, 0.0004)
        lo = min(op, cl) - random.uniform(0, 0.0004)
        candles.append({"open": op, "close": cl, "max": hi, "min": lo})
        precio = cl

    resultado = generar_senal(candles, estrategia)
    ahora = datetime.now(timezone.utc)
    prox  = intervalo - (int(time.time()) % intervalo)
    es_otc = "otc" in activo.lower() or "OTC" in activo

    return jsonify({
        "ok":             True,
        "modo":           "DEMO",
        "broker":         "PocketOption",
        "activo":         activo,
        "es_otc":         es_otc,
        "intervalo_vela": f"{intervalo}s",
        "duracion_op":    f"{duracion} min",
        "estrategia_usada": resultado.get("estrategia", estrategia),
        "volatilidad_mercado": resultado.get("volatilidad", "media"),
        "senal":          resultado.get("direccion", "NEUTRAL"),
        "confianza":      f"{resultado.get('confianza', 50)}%",
        "hora_entrada":   ahora.strftime("%H:%M:%S UTC"),
        "proxima_vela_en": f"{prox}s",
        "analisis": {k: v for k, v in resultado.items() if k not in ("direccion", "confianza")}
    })


# ════════════════════════════════════════════════════════════════
#  Endpoints PocketOption
# ════════════════════════════════════════════════════════════════

@app.route("/po/conectar", methods=["POST"])
@requiere_key
def conectar():
    """
    POST /po/conectar
    Body: { "ssid": "42[\"auth\",{...}]", "is_demo": true }

    Como obtener el SSID:
    1. Abre pocketoption.com en tu navegador
    2. Presiona F12 → pestaña Network → filtrar por WS
    3. Busca un mensaje que empiece con: 42["auth",
    4. Copia ese mensaje completo
    """
    body    = request.get_json(force=True)
    ssid    = body.get("ssid")
    is_demo = body.get("is_demo", True)

    if not ssid:
        return jsonify({
            "error": "Se requiere el SSID",
            "como_obtenerlo": [
                "1. Abre pocketoption.com en tu navegador",
                "2. Presiona F12 → pestaña Network → filtrar WS",
                "3. Busca mensaje que empiece con: 42[\"auth\",",
                "4. Copia ese mensaje completo y pegalo aqui"
            ]
        }), 400

    try:
        from pocketoptionapi_async import AsyncPocketOptionClient

        async def _conectar():
            with sesion["lock"]:
                if sesion["client"]:
                    try: await sesion["client"].disconnect()
                    except: pass

                client = AsyncPocketOptionClient(
                    ssid=ssid,
                    is_demo=is_demo,
                    enable_logging=False
                )
                ok = await client.connect()
                if not ok:
                    return None, "No se pudo conectar — verifica que el SSID sea valido y reciente"

                balance = await client.get_balance()
                sesion["client"]    = client
                sesion["ssid"]      = ssid[:30] + "..."
                sesion["conectado"] = True
                return balance, None

        balance, error = run_async(_conectar())
        if error:
            return jsonify({"ok": False, "error": error}), 401

        return jsonify({
            "ok":      True,
            "broker":  "PocketOption",
            "modo":    "DEMO" if is_demo else "REAL",
            "saldo":   round(float(balance.balance), 2),
            "moneda":  balance.currency,
            "mensaje": f"Conectado a PocketOption — {'Demo' if is_demo else 'Real'}"
        })

    except Exception as e:
        log.exception("Error conectar PocketOption")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/po/estado")
@requiere_key
@requiere_conexion
def estado():
    client = sesion["client"]
    try:
        async def _estado():
            balance = await client.get_balance()
            return balance

        balance = run_async(_estado())
        info = client.connection_info() if hasattr(client, 'connection_info') else {}

        return jsonify({
            "conectado":  True,
            "broker":     "PocketOption",
            "saldo":      round(float(balance.balance), 2),
            "moneda":     balance.currency,
            "conectado_ws": client.is_connected if hasattr(client, 'is_connected') else True,
            "ssid_usado": sesion["ssid"],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/po/desconectar")
@requiere_key
def desconectar():
    with sesion["lock"]:
        if sesion["client"]:
            try:
                run_async(sesion["client"].disconnect())
            except: pass
        sesion["client"]    = None
        sesion["conectado"] = False
        sesion["ssid"]      = None
    return jsonify({"ok": True, "mensaje": "Desconectado de PocketOption"})


@app.route("/po/activos")
@requiere_key
@requiere_conexion
def activos():
    """Lista activos disponibles con su payout %"""
    client = sesion["client"]
    try:
        activos_lista = {}

        # Activos comunes de PocketOption
        comunes = [
            "EURUSD_otc", "GBPUSD_otc", "USDJPY_otc", "USDCHF_otc",
            "AUDUSD_otc", "NZDUSD_otc", "USDCAD_otc", "EURGBP_otc",
            "EURUSD", "GBPUSD", "USDJPY", "AUDUSD",
            "#AAPL_otc", "#GOOGL_otc", "#AMZN_otc", "#TSLA_otc",
        ]

        for activo in comunes:
            payout = None
            try:
                payout = client.get_payout(activo)
            except: pass
            activos_lista[activo] = {
                "es_otc":  "otc" in activo.lower(),
                "payout":  f"{round(payout * 100)}%" if payout else "N/D",
                "abierto": payout is not None and payout > 0,
            }

        return jsonify({
            "ok":     True,
            "broker": "PocketOption",
            "total":  len(activos_lista),
            "activos": activos_lista,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/po/velas")
@requiere_key
@requiere_conexion
def velas():
    """
    GET /po/velas?activo=EURUSD_otc&intervalo=60&cantidad=100
    intervalo en segundos: 5, 10, 15, 30, 60, 300, 900, 3600
    """
    client    = sesion["client"]
    activo    = request.args.get("activo",    "EURUSD_otc")
    intervalo = int(request.args.get("intervalo", 60))
    cantidad  = int(request.args.get("cantidad",  100))

    try:
        async def _velas():
            candles = await client.get_candles(
                asset=activo,
                timeframe=intervalo,
                count=cantidad
            )
            return candles

        raw = run_async(_velas())
        if not raw:
            return jsonify({"error": f"Sin datos para {activo}"}), 404

        velas_fmt = [vela_a_dict(c) for c in raw]
        return jsonify({
            "ok":        True,
            "broker":    "PocketOption",
            "activo":    activo,
            "intervalo": f"{intervalo}s",
            "cantidad":  len(velas_fmt),
            "velas":     velas_fmt,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/po/senal", methods=["POST"])
@requiere_key
@requiere_conexion
def senal():
    """
    POST /po/senal
    Body: { "activo": "EURUSD_otc", "intervalo": 60, "duracion": 1, "estrategia": "auto" }
    """
    client = sesion["client"]
    body   = request.get_json(force=True)

    activo     = body.get("activo",    "EURUSD_otc")
    intervalo  = int(body.get("intervalo",  60))
    duracion   = int(body.get("duracion",    1))
    cantidad   = int(body.get("cantidad_velas", 100))
    estrategia = body.get("estrategia", "auto")

    try:
        # 1. Obtener velas
        async def _velas():
            return await client.get_candles(asset=activo, timeframe=intervalo, count=cantidad)

        raw = run_async(_velas())
        if not raw:
            return jsonify({"error": f"Sin velas para {activo}"}), 404

        candles = [vela_a_dict(c) for c in raw]

        # 2. Analisis tecnico
        resultado = generar_senal(candles, estrategia)
        if "error" in resultado:
            return jsonify(resultado), 400

        # 3. Info de mercado
        es_otc = "otc" in activo.lower()
        ahora  = datetime.now(timezone.utc)
        prox   = intervalo - (int(time.time()) % intervalo)

        # 4. Payout
        payout = None
        try:
            p = client.get_payout(activo)
            payout = f"{round(p * 100)}%" if p else "N/D"
        except: pass

        return jsonify({
            "ok":              True,
            "broker":          "PocketOption",
            "activo":          activo,
            "es_otc":          es_otc,
            "intervalo_vela":  f"{intervalo}s",
            "duracion_op":     f"{duracion} min",
            "estrategia_usada": resultado["estrategia"],
            "volatilidad_mercado": resultado["volatilidad"],
            "senal":           resultado["direccion"],
            "confianza":       f"{resultado['confianza']}%",
            "hora_entrada":    ahora.strftime("%H:%M:%S UTC"),
            "proxima_vela_en": f"{prox}s",
            "payout":          payout or "N/D",
            "analisis": {
                "razones":     resultado["razones"],
                "indicadores": resultado["indicadores"],
                "fibonacci":   resultado["fibonacci"],
                "patrones":    resultado["patrones_velas"],
                "score_buy":   resultado["score_buy"],
                "score_sell":  resultado["score_sell"],
            }
        })

    except Exception as e:
        log.exception("Error senal PocketOption")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    print("=" * 60)
    print("  PocketOption Bot API  v1.0")
    print(f"  http://localhost:{PORT}")
    print("=" * 60)
    print(f"  API Key: {API_KEY}")
    print("=" * 60)
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
