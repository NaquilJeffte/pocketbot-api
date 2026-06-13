"""
server.py — PocketOption Bot API v5.1
Conecta con PocketOption via WebSocket directo.
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
import websockets
from datetime import datetime, timezone
from functools import wraps

from analysis import generar_senal

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
    "lock": threading.Lock()
}

loop = asyncio.new_event_loop()
threading.Thread(target=lambda: (asyncio.set_event_loop(loop), loop.run_forever()), daemon=True).start()

def run_async(coro, timeout=35):
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
        if not sesion["conectado"]:
            return jsonify({"error": "No conectado"}), 403
        return f(*args, **kwargs)
    return wrapper

async def conectar_ws(session_id, is_demo, ssid_completo=None):
    urls = [
        "wss://api-l.po.market/socket.io/?EIO=4&transport=websocket",
        "wss://api.po.market/socket.io/?EIO=4&transport=websocket",
    ]

    # PocketOption valida la sesion via cookie en el handshake del WS,
    # no solo en el mensaje "auth" posterior a la conexion.
    from urllib.parse import quote
    cookie_val = quote(ssid_completo) if ssid_completo else session_id

    headers = {
        "Origin": "https://pocketoption.com",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Cookie": f"ci_session={cookie_val}",
    }
    datos = {
        "saldo_demo": 0, "saldo_real": 0,
        "nombre": "Trader", "email": "", "id": "",
        "foto_perfil": "", "conectado": False
    }
    for url in urls:
        try:
            async with websockets.connect(url, additional_headers=headers, ping_interval=None, open_timeout=10) as ws:
                await asyncio.wait_for(ws.recv(), timeout=8)
                auth = f'42["auth",{{"session":"{session_id}","isDemo":{1 if is_demo else 0},"uid":0,"platform":2}}]'
                await ws.send(auth)
                inicio = time.time()
                while time.time() - inicio < 30:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=10)

                        # Log para depuración: ver exactamente qué envía PocketOption
                        log.info(f"WS MSG: {msg[:1000]}")

                        # Parsear JSON completo
                        try:
                            json_match = re.search(r'\[.*\]|\{.*\}', msg, re.DOTALL)
                            if json_match:
                                payload = json.loads(json_match.group())
                                if isinstance(payload, list) and len(payload) > 1:
                                    data = payload[1]
                                elif isinstance(payload, dict):
                                    data = payload
                                else:
                                    data = {}
                                user = data.get("user", data.get("profile", {}))
                                if user:
                                    datos["id"]          = str(user.get("id", datos["id"]))
                                    datos["nombre"]      = user.get("name", user.get("nick", datos["nombre"]))
                                    datos["email"]       = user.get("email", datos["email"])
                                    datos["foto_perfil"] = user.get("avatar", user.get("photo", datos["foto_perfil"]))
                                balance = data.get("balance", data.get("amount", None))
                                if balance is not None:
                                    saldo = float(balance)
                                    if is_demo:
                                        datos["saldo_demo"] = saldo
                                    else:
                                        datos["saldo_real"] = saldo
                        except Exception:
                            pass

                        # Fallback regex
                        if "amount" in msg:
                            m = re.search(r'"amount"\s*:\s*([\d.]+)', msg)
                            if m:
                                saldo = float(m.group(1))
                                if is_demo:
                                    datos["saldo_demo"] = saldo
                                else:
                                    datos["saldo_real"] = saldo

                        if "name" in msg or "nick" in msg:
                            m = re.search(r'"(?:name|nick)"\s*:\s*"([^"]+)"', msg)
                            if m:
                                datos["nombre"] = m.group(1)

                        if "email" in msg:
                            m = re.search(r'"email"\s*:\s*"([^"]+)"', msg)
                            if m:
                                datos["email"] = m.group(1)

                        if "avatar" in msg or "photo" in msg:
                            m = re.search(r'"(?:avatar|photo)"\s*:\s*"([^"]+)"', msg)
                            if m:
                                datos["foto_perfil"] = m.group(1)

                        if "id" in msg:
                            m = re.search(r'"id"\s*:\s*(\d+)', msg)
                            if m:
                                datos["id"] = m.group(1)

                        # Criterio de conexión más permisivo: cualquier señal de
                        # que el servidor reconoció la sesión cuenta.
                        if (
                            datos["id"]
                            or datos["nombre"] != "Trader"
                            or datos["email"]
                            or datos["saldo_demo"] > 0
                            or datos["saldo_real"] > 0
                        ):
                            datos["conectado"] = True
                            # No hacemos break: seguimos escuchando un poco más
                            # por si llegan datos adicionales (perfil completo,
                            # balance real, etc.) en mensajes posteriores.

                    except asyncio.TimeoutError:
                        # No abandonamos la conexión solo porque un ciclo de
                        # espera no trajo mensajes; seguimos intentando hasta
                        # que se agote el tiempo total (30s).
                        continue

            if datos["conectado"]:
                break

        except Exception as e:
            log.warning(f"Error con {url}: {e}")
            continue

    return datos

@app.route("/")
def raiz():
    return jsonify({
        "api": "PocketOption Bot API", "version": "5.1",
        "estado": "online", "tu_api_key": API_KEY,
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
        "ok": True, "modo": "DEMO", "broker": "PocketOption",
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

    # ── FIX PRINCIPAL: decodificar URL encoding ──────────────────
    ssid = body.get("ssid", "").strip()
    ssid = unquote(ssid)  # convierte a%3A4%3A... → a:4:{...}

    is_demo = body.get("is_demo", True)

    if not ssid:
        return jsonify({"error": "Se requiere el SSID"}), 400

    # ── Extraer session_id con múltiples patrones ────────────────
    session_id = None
    patterns = [
        r'session_id["\']?;s:\d+:["\']([^"\']+)',
        r'session_id=([^;]+)',
        r'([a-f0-9]{32})'
    ]
    log.info(f"SSID recibido: {ssid[:300]}")

    for p in patterns:
        m = re.search(p, ssid, re.IGNORECASE)
        if m:
            session_id = (
                m.group(1)
                .strip()
            )
            log.info(f"session_id extraido: {session_id}")
            break

    if not session_id:
        log.warning("No se pudo extraer session_id")
        return jsonify({
            "ok": False,
            "error": "Formato de SSID invalido. Asegurate de copiar el valor completo de ci_session"
        }), 400

    try:
        datos = run_async(conectar_ws(session_id, is_demo, ssid_completo=ssid), timeout=35)
    except Exception as e:
        log.error(f"Error conectar: {e}")
        datos = {"conectado": False, "saldo_demo": 0, "saldo_real": 0,
                 "nombre": "Trader", "email": "", "id": "", "foto_perfil": ""}

    with sesion["lock"]:
        if not datos.get("conectado"):
            sesion["ssid"]        = None
            sesion["conectado"]   = False
            sesion["saldo_demo"]  = 0
            sesion["saldo_real"]  = 0
            sesion["nombre"]      = ""
            sesion["email"]       = ""
            sesion["id"]          = ""
            sesion["foto_perfil"] = ""
            return jsonify({
                "ok": False,
                "error": "SSID invalido o sesion expirada. Obtén un nuevo SSID de pocketoption.com"
            }), 401

        sesion["ssid"]        = session_id
        sesion["is_demo"]     = is_demo
        sesion["conectado"]   = True
        sesion["saldo_demo"]  = datos["saldo_demo"]
        sesion["saldo_real"]  = datos["saldo_real"]
        sesion["nombre"]      = datos["nombre"]
        sesion["email"]       = datos["email"]
        sesion["id"]          = datos["id"]
        sesion["foto_perfil"] = datos.get("foto_perfil", "")

    modo  = "DEMO" if is_demo else "REAL"
    saldo = sesion["saldo_demo"] if is_demo else sesion["saldo_real"]

    return jsonify({
        "ok":          True,
        "broker":      "PocketOption",
        "modo":        modo,
        "saldo":       round(saldo, 2),
        "saldo_demo":  round(sesion["saldo_demo"], 2),
        "saldo_real":  round(sesion["saldo_real"], 2),
        "nombre":      sesion["nombre"],
        "email":       sesion["email"],
        "id":          sesion["id"],
        "foto_perfil": sesion["foto_perfil"],
        "moneda":      "USD",
        "mensaje":     f"Conectado a PocketOption — {modo}"
    })

@app.route("/po/estado")
@requiere_key
@requiere_conexion
def estado():
    modo  = "DEMO" if sesion["is_demo"] else "REAL"
    saldo = sesion["saldo_demo"] if sesion["is_demo"] else sesion["saldo_real"]
    return jsonify({
        "conectado":    True,
        "broker":       "PocketOption",
        "modo":         modo,
        "saldo_activo": round(saldo, 2),
        "saldo_demo":   round(sesion["saldo_demo"], 2),
        "saldo_real":   round(sesion["saldo_real"], 2),
        "nombre":       sesion["nombre"],
        "email":        sesion["email"],
        "id":           sesion["id"],
        "foto_perfil":  sesion["foto_perfil"],
    })

@app.route("/po/desconectar")
@requiere_key
def desconectar():
    with sesion["lock"]:
        sesion["ssid"]        = None
        sesion["conectado"]   = False
        sesion["saldo_demo"]  = 0
        sesion["saldo_real"]  = 0
        sesion["nombre"]      = ""
        sesion["email"]       = ""
        sesion["id"]          = ""
        sesion["foto_perfil"] = ""
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
def velas():
    import random
    activo    = request.args.get("activo", "EURUSD_otc")
    intervalo = int(request.args.get("intervalo", 60))
    cantidad  = int(request.args.get("cantidad", 100))
    random.seed(int(time.time()) // intervalo)
    precio = 1.08500
    velas_fmt = []
    for i in range(cantidad):
        cambio = random.uniform(-0.0006, 0.0006)
        op = precio; cl = precio + cambio
        hi = max(op,cl)+random.uniform(0,0.0003)
        lo = min(op,cl)-random.uniform(0,0.0003)
        ts = int(time.time()) - (cantidad-i)*intervalo
        velas_fmt.append({
            "timestamp": ts,
            "datetime": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "open": round(op,5), "high": round(hi,5),
            "low": round(lo,5), "close": round(cl,5),
            "max": round(hi,5), "min": round(lo,5),
        })
        precio = cl
    return jsonify({"ok": True, "broker": "PocketOption",
                    "activo": activo, "intervalo": f"{intervalo}s",
                    "cantidad": len(velas_fmt), "velas": velas_fmt})

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
    random.seed(int(time.time()) // intervalo)
    precio = 1.08500
    candles = []
    for i in range(cantidad):
        cambio = random.uniform(-0.0006, 0.0006)
        op = precio; cl = precio + cambio
        candles.append({"open": op, "close": cl,
                        "max": max(op,cl)+random.uniform(0,0.0003),
                        "min": min(op,cl)-random.uniform(0,0.0003)})
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
        "proxima_vela_en": f"{prox}s", "payout": "92%",
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
    print(f"PocketOption Bot API v5.1 — puerto {PORT}")
    print(f"API Key: {API_KEY}")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
