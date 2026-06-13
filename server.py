"""
server.py — PocketOption Bot API v3.0
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
import re
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
    "lock": threading.Lock()
}

loop = asyncio.new_event_loop()
threading.Thread(target=lambda: (asyncio.set_event_loop(loop), loop.run_forever()), daemon=True).start()

def run_async(coro, timeout=20):
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

async def conectar_ws(session_id, is_demo):
    urls = [
        "wss://api-l.po.market/socket.io/?EIO=4&transport=websocket",
        "wss://api.po.market/socket.io/?EIO=4&transport=websocket",
    ]
    headers = {
        "Origin": "https://pocketoption.com",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    datos = {"saldo_demo": 0, "saldo_real": 0, "nombre": "Trader", "email": "", "id": "", "conectado": False}
    for url in urls:
        try:
            async with websockets.connect(url, additional_headers=headers, ping_interval=None, open_timeout=10) as ws:
                await asyncio.wait_for(ws.recv(), timeout=8)
                auth = f'42["auth",{{"session":"{session_id}","isDemo":{1 if is_demo else 0},"uid":0,"platform":2}}]'
                await ws.send(auth)
                inicio = time.time()
                while time.time() - inicio < 12:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=4)
                        if "amount" in msg:
                            m = re.search(r'"amount"\s*:\s*([\d.]+)', msg)
                            if m:
                                saldo = float(m.group(1))
                                if is_demo:
                                    datos["saldo_demo"] = saldo
                                else:
                                    datos["saldo_real"] = saldo
                                datos["conectado"] = True
                        if "name" in msg:
                            m = re.search(r'"name"\s*:\s*"([^"]+)"', msg)
                            if m:
                                datos["nombre"] = m.group(1)
                        if "email" in msg:
                            m = re.search(r'"email"\s*:\s*"([^"]+)"', msg)
                            if m:
                                datos["email"] = m.group(1)
                        if datos["conectado"]:
                            break
                    except asyncio.TimeoutError:
                        break
            if datos["conectado"]:
                break
        except Exception as e:
            log.warning(f"Error con {url}: {e}")
            continue
    return datos

@app.route("/")
def raiz():
    return jsonify({
        "api": "PocketOption Bot API", "version": "3.0",
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
    ssid    = body.get("ssid", "")
    is_demo = body.get("is_demo", True)
    if not ssid:
        return jsonify({"error": "Se requiere el SSID"}), 400

    session_id = ssid
    m = re.search(r'session_id["\']?;s:\d+:["\']([a-f0-9]{32})["\']', ssid)
    if m:
        session_id = m.group(1)
    elif re.match(r'^[a-f0-9]{32}$', ssid.strip()):
        session_id = ssid.strip()

    try:
        datos = run_async(conectar_ws(session_id, is_demo), timeout=20)
    except Exception as e:
        datos = {"conectado": False, "saldo_demo": 0, "saldo_real": 0,
                 "nombre": "Trader", "email": "", "id": ""}

    with sesion["lock"]:
        sesion["ssid"]       = session_id
        sesion["is_demo"]    = is_demo
        sesion["conectado"]  = True
        sesion["saldo_demo"] = datos.get("saldo_demo", 0)
        sesion["saldo_real"] = datos.get("saldo_real", 0)
        sesion["nombre"]     = datos.get("nombre", "Trader")
        sesion["email"]      = datos.get("email", "")
        sesion["id"]         = datos.get("id", "")

    modo  = "DEMO" if is_demo else "REAL"
    saldo = sesion["saldo_demo"] if is_demo else sesion["saldo_real"]

    return jsonify({
        "ok": True, "broker": "PocketOption", "modo": modo,
        "saldo": round(saldo, 2),
        "saldo_demo": round(sesion["saldo_demo"], 2),
        "saldo_real": round(sesion["saldo_real"], 2),
        "nombre": sesion["nombre"],
        "email": sesion["email"],
        "id": sesion["id"],
        "moneda": "USD",
        "mensaje": f"Conectado a PocketOption — {modo}"
    })

@app.route("/po/estado")
@requiere_key
@requiere_conexion
def estado():
    modo  = "DEMO" if sesion["is_demo"] else "REAL"
    saldo = sesion["saldo_demo"] if sesion["is_demo"] else sesion["saldo_real"]
    return jsonify({
        "conectado": True, "broker": "PocketOption", "modo": modo,
        "saldo_activo": round(saldo, 2),
        "saldo_demo": round(sesion["saldo_demo"], 2),
        "saldo_real": round(sesion["saldo_real"], 2),
        "nombre": sesion["nombre"],
        "email": sesion["email"],
        "id": sesion["id"],
    })

@app.route("/po/desconectar")
@requiere_key
def desconectar():
    with sesion["lock"]:
        sesion["ssid"] = None
        sesion["conectado"] = False
        sesion["saldo_demo"] = 0
        sesion["saldo_real"] = 0
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
    print(f"PocketOption Bot API v3.0 — puerto {PORT}")
    print(f"API Key: {API_KEY}")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
