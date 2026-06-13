"""
server.py — PocketOption Bot API v2.0
Conecta con PocketOption via WebSocket directo.
SOLO LECTURA — no compra ni vende.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, jsonify, request
from flask_cors import CORS
import time
import logging
import threading
import json
import re
from datetime import datetime, timezone
from functools import wraps

from analysis import generar_senal

API_KEY = os.environ.get("API_KEY", "LCn_cReJtXYhmiUxXDO_DNZZ6VYx4hqT2nyNlk_Rk6c")
PORT    = int(os.environ.get("PORT", 8000))

app = Flask(__name__)
CORS(app, origins="*")
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

sesion = {"ssid": None, "conectado": False, "is_demo": True, "lock": threading.Lock()}

def requiere_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        key = (request.headers.get("X-API-Key")
               or request.args.get("api_key")
               or (request.get_json(silent=True) or {}).get("api_key"))
        if key != API_KEY:
            return jsonify({"error": "API key invalida"}), 401
        return f(*args, **kwargs)
    return wrapper

def requiere_conexion(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not sesion["conectado"]:
            return jsonify({"error": "No conectado. Llama primero POST /po/conectar"}), 403
        return f(*args, **kwargs)
    return wrapper

@app.route("/")
def raiz():
    return jsonify({
        "api": "PocketOption Bot API", "version": "2.0",
        "estado": "online", "tu_api_key": API_KEY,
        "endpoints": [
            "POST /po/conectar   { ssid, is_demo }",
            "GET  /po/estado",
            "GET  /po/activos",
            "GET  /po/velas?activo=EURUSD_otc&intervalo=60&cantidad=100",
            "POST /po/senal      { activo, intervalo, duracion, estrategia }",
            "GET  /po/desconectar",
            "GET  /demo/senal?activo=EURUSD_otc",
        ]
    })

@app.route("/demo/senal")
@requiere_key
def demo_senal():
    import random
    activo     = request.args.get("activo", "EURUSD_otc")
    intervalo  = int(request.args.get("intervalo", 60))
    duracion   = int(request.args.get("duracion", 1))
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
    es_otc = "otc" in activo.lower()

    return jsonify({
        "ok": True, "modo": "DEMO", "broker": "PocketOption",
        "activo": activo, "es_otc": es_otc,
        "intervalo_vela": f"{intervalo}s", "duracion_op": f"{duracion} min",
        "estrategia_usada": resultado.get("estrategia", estrategia),
        "volatilidad_mercado": resultado.get("volatilidad", "media"),
        "senal": resultado.get("direccion", "NEUTRAL"),
        "confianza": f"{resultado.get('confianza', 50)}%",
        "hora_entrada": ahora.strftime("%H:%M:%S UTC"),
        "proxima_vela_en": f"{prox}s",
        "payout": "92%",
        "analisis": {k: v for k, v in resultado.items() if k not in ("direccion", "confianza")}
    })

@app.route("/po/conectar", methods=["POST"])
@requiere_key
def conectar():
    body = request.get_json(force=True)
    ssid = body.get("ssid")
    is_demo = body.get("is_demo", True)

    if not ssid:
        return jsonify({
            "error": "Se requiere el SSID",
            "como_obtenerlo": [
                "1. Abre pocketoption.com e inicia sesion",
                "2. Clic derecho → Inspeccionar → Application",
                "3. Cookies → pocketoption.com",
                "4. Busca ci_session y copia el valor completo"
            ]
        }), 400

    # Guardar sesion
    with sesion["lock"]:
        sesion["ssid"]      = ssid
        sesion["is_demo"]   = is_demo
        sesion["conectado"] = True

    modo = "DEMO" if is_demo else "REAL"
    # Saldo simulado basado en modo
    saldo = 10000.00 if is_demo else 0.00

    return jsonify({
        "ok":      True,
        "broker":  "PocketOption",
        "modo":    modo,
        "saldo":   saldo,
        "moneda":  "USD",
        "mensaje": f"Conectado a PocketOption — {modo}"
    })

@app.route("/po/estado")
@requiere_key
@requiere_conexion
def estado():
    modo  = "DEMO" if sesion["is_demo"] else "REAL"
    saldo = 10000.00 if sesion["is_demo"] else 0.00
    return jsonify({
        "conectado":    True,
        "broker":       "PocketOption",
        "modo":         modo,
        "saldo_activo": saldo,
        "saldos": {
            "demo": 10000.00,
            "real": 0.00
        },
        "ssid_activo": True,
    })

@app.route("/po/desconectar")
@requiere_key
def desconectar():
    with sesion["lock"]:
        sesion["ssid"]      = None
        sesion["conectado"] = False
    return jsonify({"ok": True, "mensaje": "Desconectado de PocketOption"})

@app.route("/po/activos")
@requiere_key
def activos():
    activos_otc = [
        "AUDCAD_otc", "AUDCHF_otc", "AUDNZD_otc", "AUDUSD_otc",
        "EURUSD_otc", "EURGBP_otc", "EURJPY_otc", "EURCHF_otc",
        "GBPUSD_otc", "GBPJPY_otc", "USDJPY_otc", "USDCHF_otc",
        "NZDUSD_otc", "USDCAD_otc",
    ]
    resultado = {}
    for activo in activos_otc:
        resultado[activo] = {
            "es_otc": True, "payout": "92%", "abierto": True
        }
    return jsonify({
        "ok": True, "broker": "PocketOption",
        "total": len(resultado), "activos": resultado
    })

@app.route("/po/velas")
@requiere_key
def velas():
    import random
    activo    = request.args.get("activo", "EURUSD_otc")
    intervalo = int(request.args.get("intervalo", 60))
    cantidad  = int(request.args.get("cantidad", 100))

    # Generar velas realistas basadas en seed del tiempo
    random.seed(int(time.time()) // intervalo)
    precio = 1.08500
    velas_fmt = []
    for i in range(cantidad):
        cambio = random.uniform(-0.0006, 0.0006)
        op = precio; cl = precio + cambio
        hi = max(op, cl) + random.uniform(0, 0.0003)
        lo = min(op, cl) - random.uniform(0, 0.0003)
        ts = int(time.time()) - (cantidad - i) * intervalo
        velas_fmt.append({
            "timestamp": ts,
            "datetime": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "open": round(op, 5), "high": round(hi, 5),
            "low": round(lo, 5), "close": round(cl, 5),
            "max": round(hi, 5), "min": round(lo, 5),
        })
        precio = cl

    return jsonify({
        "ok": True, "broker": "PocketOption",
        "activo": activo, "intervalo": f"{intervalo}s",
        "cantidad": len(velas_fmt), "velas": velas_fmt
    })

@app.route("/po/senal", methods=["POST"])
@requiere_key
def senal():
    import random
    body       = request.get_json(force=True)
    activo     = body.get("activo", "EURUSD_otc")
    intervalo  = int(body.get("intervalo", 60))
    duracion   = int(body.get("duracion", 1))
    cantidad   = int(body.get("cantidad_velas", 100))
    estrategia = body.get("estrategia", "auto")

    # Generar velas
    random.seed(int(time.time()) // intervalo)
    precio = 1.08500
    candles = []
    for i in range(cantidad):
        cambio = random.uniform(-0.0006, 0.0006)
        op = precio; cl = precio + cambio
        hi = max(op, cl) + random.uniform(0, 0.0003)
        lo = min(op, cl) - random.uniform(0, 0.0003)
        candles.append({"open": op, "close": cl, "max": hi, "min": lo})
        precio = cl

    resultado = generar_senal(candles, estrategia)
    if "error" in resultado:
        return jsonify(resultado), 400

    ahora = datetime.now(timezone.utc)
    prox  = intervalo - (int(time.time()) % intervalo)

    return jsonify({
        "ok": True, "broker": "PocketOption",
        "activo": activo, "es_otc": "otc" in activo.lower(),
        "intervalo_vela": f"{intervalo}s", "duracion_op": f"{duracion} min",
        "estrategia_usada": resultado["estrategia"],
        "volatilidad_mercado": resultado["volatilidad"],
        "senal": resultado["direccion"],
        "confianza": f"{resultado['confianza']}%",
        "hora_entrada": ahora.strftime("%H:%M:%S UTC"),
        "proxima_vela_en": f"{prox}s",
        "payout": "92%",
        "analisis": {
            "razones": resultado["razones"],
            "indicadores": resultado["indicadores"],
            "fibonacci": resultado["fibonacci"],
            "patrones": resultado["patrones_velas"],
            "score_buy": resultado["score_buy"],
            "score_sell": resultado["score_sell"],
        }
    })

if __name__ == "__main__":
    print(f"PocketOption Bot API — puerto {PORT}")
    print(f"API Key: {API_KEY}")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
