"""
server.py — PocketOption Bot API v6.0
Conecta con PocketOption usando BinaryOptionsToolsV2 (maneja el
WebSocket/Cloudflare por nosotros). Datos REALES: balance, velas/precios.
SOLO LECTURA — no compra ni vende.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, jsonify, request
from flask_cors import CORS
from urllib.parse import unquote
import time
import logging
import threading
import re
import json
import asyncio
from datetime import datetime, timezone
from functools import wraps

from analysis import generar_senal

try:
    from BinaryOptionsToolsV2.pocketoption import PocketOptionAsync
except Exception as e:  # la libreria puede no estar instalada todavia
    PocketOptionAsync = None
    _IMPORT_ERROR = e
else:
    _IMPORT_ERROR = None

API_KEY = os.environ.get("API_KEY", "LCn_cReJtXYhmiUxXDO_DNZZ6VYx4hqT2nyNlk_Rk6c")
PORT    = int(os.environ.get("PORT", 8000))

app = Flask(__name__)
CORS(app, origins="*")
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

sesion = {
    "ssid": None, "conectado": False, "is_demo": True,
    "saldo_demo": 0, "saldo_real": 0,
    "nombre": "Trader", "email": "", "id": "",
    "foto_perfil": "",
    "cliente": None,
    "lock": threading.Lock()
}

loop = asyncio.new_event_loop()
threading.Thread(target=lambda: (asyncio.set_event_loop(loop), loop.run_forever()), daemon=True).start()

def run_async(coro, timeout=60):
    return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=timeout)

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
        if not sesion["conectado"] or sesion["cliente"] is None:
            return jsonify({"error": "No conectado"}), 403
        return f(*args, **kwargs)
    return wrapper


def construir_ssid_mensaje(ssid_completo, is_demo, uid=0):
    """
    PocketOption (y las librerias que lo envuelven) esperan el mensaje
    completo de autenticacion de Socket.IO:

        42["auth",{"session":"<ci_session completo>","isDemo":0/1,"uid":0,"platform":2}]

    `ssid_completo` es la cadena PHP serializada completa
    (a:4:{s:10:"session_id";...}HASH). json.dumps la escapa
    correctamente para incrustarla dentro del JSON.
    """
    session_json = json.dumps(ssid_completo)
    return (
        '42["auth",{'
        f'"session":{session_json},'
        f'"isDemo":{1 if is_demo else 0},'
        f'"uid":{uid},'
        '"platform":2,'
        '"isFastHistory":true'
        '}]'
    )


async def _conectar_cliente(ssid_completo, is_demo):
    """Crea el cliente, espera a que conecte y devuelve (cliente, balance)."""
    if PocketOptionAsync is None:
        raise RuntimeError(f"BinaryOptionsToolsV2 no esta instalado: {_IMPORT_ERROR}")

    ssid_msg = construir_ssid_mensaje(ssid_completo, is_demo)
    log.info(f"Mensaje auth construido: {ssid_msg[:200]}")

    cliente = PocketOptionAsync(ssid=ssid_msg)

    # Dar tiempo a que la conexion WS interna se establezca
    balance = None
    ultimo_error = None
    for intento in range(10):
        await asyncio.sleep(1.5)
        try:
            balance = await cliente.balance()
            if balance is not None:
                break
        except Exception as e:
            ultimo_error = e
            log.info(f"Esperando conexion... intento {intento+1}: {e}")

    if balance is None:
        raise RuntimeError(f"No se pudo obtener balance tras conectar: {ultimo_error}")

    return cliente, balance


def _extraer_valor_balance(balance):
    """El objeto balance puede venir como float, dict u objeto con atributos."""
    if balance is None:
        return 0.0
    if isinstance(balance, (int, float)):
        return float(balance)
    if isinstance(balance, dict):
        for k in ("balance", "amount", "value"):
            if k in balance:
                try:
                    return float(balance[k])
                except (TypeError, ValueError):
                    pass
        return 0.0
    for k in ("balance", "amount", "value"):
        if hasattr(balance, k):
            try:
                return float(getattr(balance, k))
            except (TypeError, ValueError):
                pass
    return 0.0


def _normalizar_vela(c, fallback_ts=None):
    """Normaliza una vela (dict u objeto) al formato de salida de la API."""
    def get(obj, *names, default=None):
        if isinstance(obj, dict):
            for n in names:
                if n in obj:
                    return obj[n]
            return default
        for n in names:
            if hasattr(obj, n):
                return getattr(obj, n)
        return default

    op = get(c, "open", "o", default=0.0)
    cl = get(c, "close", "c", default=0.0)
    hi = get(c, "high", "max", "h", default=max(op, cl))
    lo = get(c, "low", "min", "l", default=min(op, cl))
    ts = get(c, "timestamp", "time", "ts", default=fallback_ts)

    try:
        ts = int(ts)
    except (TypeError, ValueError):
        ts = int(time.time())

    return {
        "timestamp": ts,
        "datetime": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "open": round(float(op), 5),
        "high": round(float(hi), 5),
        "low": round(float(lo), 5),
        "close": round(float(cl), 5),
        "max": round(float(hi), 5),
        "min": round(float(lo), 5),
    }


async def _obtener_velas(cliente, activo, intervalo, cantidad):
    """Obtiene velas reales del cliente y las normaliza."""
    crudos = await cliente.get_candles(activo, intervalo, intervalo * cantidad)

    if hasattr(crudos, "to_dict"):
        # Si vino como DataFrame de pandas
        crudos = crudos.to_dict("records")

    velas_fmt = [_normalizar_vela(c) for c in crudos]
    velas_fmt.sort(key=lambda v: v["timestamp"])
    return velas_fmt[-cantidad:] if cantidad else velas_fmt


@app.route("/")
def raiz():
    return jsonify({
        "api": "PocketOption Bot API", "version": "6.0",
        "estado": "online", "tu_api_key": API_KEY,
        "libreria_disponible": PocketOptionAsync is not None,
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
        candles.append({"open": op, "close": cl,
                        "max": max(op,cl)+random.uniform(0,0.0004),
                        "min": min(op,cl)-random.uniform(0,0.0004)})
        precio = cl
    resultado = generar_senal(candles, estrategia)
    ahora = datetime.now(timezone.utc)
    prox  = intervalo - (int(time.time()) % intervalo)
    return jsonify({
        "ok": True, "modo": "DEMO (simulado)", "broker": "PocketOption",
        "activo": activo, "es_otc": "otc" in activo.lower(),
        "intervalo_vela": f"{intervalo}s", "duracion_op": f"{duracion} min",
        "estrategia_usada": resultado.get("estrategia", estrategia),
        "volatilidad_mercado": resultado.get("volatilidad", "media"),
        "senal": resultado.get("direccion", "NEUTRAL"),
        "confianza": f"{resultado.get('confianza', 50)}%",
        "hora_entrada": ahora.strftime("%H:%M:%S UTC"),
        "proxima_vela_en": f"{prox}s", "payout": "92%",
        "analisis": {k: v for k, v in resultado.items() if k not in ("direccion","confianza")}
    })

@app.route("/po/conectar", methods=["POST"])
@requiere_key
def conectar():
    body    = request.get_json(force=True)

    ssid = body.get("ssid", "").strip()
    ssid = unquote(ssid)  # convierte a%3A4%3A... → a:4:{...}

    is_demo = body.get("is_demo", True)

    if not ssid:
        return jsonify({"error": "Se requiere el SSID"}), 400

    log.info(f"SSID recibido: {ssid[:300]}")

    try:
        cliente, balance = run_async(_conectar_cliente(ssid, is_demo), timeout=60)
    except Exception as e:
        log.error(f"Error conectar: {e}")
        with sesion["lock"]:
            sesion["conectado"] = False
            sesion["cliente"]   = None
        return jsonify({
            "ok": False,
            "error": "SSID invalido, sesion expirada o no se pudo conectar a PocketOption.",
            "detalle": str(e),
        }), 401

    saldo = _extraer_valor_balance(balance)

    with sesion["lock"]:
        sesion["ssid"]       = ssid
        sesion["is_demo"]    = is_demo
        sesion["conectado"]  = True
        sesion["cliente"]    = cliente
        if is_demo:
            sesion["saldo_demo"] = saldo
        else:
            sesion["saldo_real"] = saldo

    modo = "DEMO" if is_demo else "REAL"

    return jsonify({
        "ok":          True,
        "broker":      "PocketOption",
        "modo":        modo,
        "saldo":       round(saldo, 2),
        "saldo_demo":  round(sesion["saldo_demo"], 2),
        "saldo_real":  round(sesion["saldo_real"], 2),
        "moneda":      "USD",
        "mensaje":     f"Conectado a PocketOption — {modo} (datos reales)"
    })

@app.route("/po/estado")
@requiere_key
@requiere_conexion
def estado():
    try:
        balance = run_async(sesion["cliente"].balance(), timeout=20)
        saldo = _extraer_valor_balance(balance)
        with sesion["lock"]:
            if sesion["is_demo"]:
                sesion["saldo_demo"] = saldo
            else:
                sesion["saldo_real"] = saldo
    except Exception as e:
        log.warning(f"Error actualizando estado: {e}")
        saldo = sesion["saldo_demo"] if sesion["is_demo"] else sesion["saldo_real"]

    modo = "DEMO" if sesion["is_demo"] else "REAL"
    return jsonify({
        "conectado":    True,
        "broker":       "PocketOption",
        "modo":         modo,
        "saldo_activo": round(saldo, 2),
        "saldo_demo":   round(sesion["saldo_demo"], 2),
        "saldo_real":   round(sesion["saldo_real"], 2),
    })

@app.route("/po/desconectar")
@requiere_key
def desconectar():
    with sesion["lock"]:
        cliente = sesion["cliente"]
        sesion["ssid"]       = None
        sesion["conectado"]  = False
        sesion["saldo_demo"] = 0
        sesion["saldo_real"] = 0
        sesion["cliente"]    = None

    if cliente is not None:
        try:
            run_async(cliente.disconnect(), timeout=10)
        except Exception as e:
            log.warning(f"Error al desconectar: {e}")

    return jsonify({"ok": True})

@app.route("/po/activos")
@requiere_key
def activos():
    lista = ["AUDCAD_otc","AUDCHF_otc","AUDNZD_otc","AUDUSD_otc",
             "EURUSD_otc","EURGBP_otc","EURJPY_otc","EURCHF_otc",
             "GBPUSD_otc","GBPJPY_otc","USDJPY_otc","USDCHF_otc",
             "NZDUSD_otc","USDCAD_otc"]
    resultado = {a: {"es_otc": True, "payout": "92%", "abierto": True} for a in lista}
    return jsonify({"ok": True, "broker": "PocketOption",
                    "total": len(resultado), "activos": resultado})

@app.route("/po/velas")
@requiere_key
@requiere_conexion
def velas():
    activo    = request.args.get("activo", "EURUSD_otc")
    intervalo = int(request.args.get("intervalo", 60))
    cantidad  = int(request.args.get("cantidad", 100))

    try:
        velas_fmt = run_async(
            _obtener_velas(sesion["cliente"], activo, intervalo, cantidad),
            timeout=30
        )
    except Exception as e:
        log.error(f"Error obteniendo velas: {e}")
        return jsonify({"ok": False, "error": f"No se pudieron obtener velas reales: {e}"}), 502

    return jsonify({"ok": True, "broker": "PocketOption", "modo": "REAL",
                    "activo": activo, "intervalo": f"{intervalo}s",
                    "cantidad": len(velas_fmt), "velas": velas_fmt})

@app.route("/po/senal", methods=["POST"])
@requiere_key
@requiere_conexion
def senal():
    body       = request.get_json(force=True)
    activo     = body.get("activo", "EURUSD_otc")
    intervalo  = int(body.get("intervalo", 60))
    duracion   = int(body.get("duracion", 1))
    cantidad   = int(body.get("cantidad_velas", 100))
    estrategia = body.get("estrategia", "auto")

    try:
        velas_fmt = run_async(
            _obtener_velas(sesion["cliente"], activo, intervalo, cantidad),
            timeout=30
        )
    except Exception as e:
        log.error(f"Error obteniendo velas: {e}")
        return jsonify({"ok": False, "error": f"No se pudieron obtener velas reales: {e}"}), 502

    # generar_senal espera las claves "open","close","max","min"
    candles = [{"open": v["open"], "close": v["close"], "max": v["max"], "min": v["min"]} for v in velas_fmt]

    resultado = generar_senal(candles, estrategia)
    if "error" in resultado:
        return jsonify(resultado), 400

    ahora = datetime.now(timezone.utc)
    prox  = intervalo - (int(time.time()) % intervalo)
    return jsonify({
        "ok": True, "broker": "PocketOption", "modo": "REAL",
        "activo": activo, "es_otc": "otc" in activo.lower(),
        "intervalo_vela": f"{intervalo}s", "duracion_op": f"{duracion} min",
        "estrategia_usada": resultado["estrategia"],
        "volatilidad_mercado": resultado["volatilidad"],
        "senal": resultado["direccion"],
        "confianza": f"{resultado['confianza']}%",
        "hora_entrada": ahora.strftime("%H:%M:%S UTC"),
        "proxima_vela_en": f"{prox}s", "payout": "92%",
        "ultima_vela": velas_fmt[-1] if velas_fmt else None,
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
    print(f"PocketOption Bot API v6.0 — puerto {PORT}")
    print(f"API Key: {API_KEY}")
    print(f"BinaryOptionsToolsV2 disponible: {PocketOptionAsync is not None}")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
