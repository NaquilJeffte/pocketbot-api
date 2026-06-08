# PocketOption Bot API 🚀

API personal para conectar Lovable con PocketOption.

---

## ⚡ Diferencia importante con IQ Option

PocketOption **NO usa email/contraseña**. Usa un **SSID** (cookie de sesión del navegador).

### Cómo obtener tu SSID:
1. Abre **pocketoption.com** en tu navegador e inicia sesión
2. Presiona **F12** → pestaña **Network** → filtra por **WS**
3. Recarga la página
4. Busca un mensaje WebSocket que empiece con: `42["auth",`
5. Copia ese mensaje completo — se ve así:
```
42["auth",{"session":"tu_sesion_aqui","isDemo":1,"uid":12345,"platform":1}]
```
6. Ese es tu SSID — expira cada cierto tiempo, necesitas renovarlo

---

## 🌐 Endpoints

**URL base:** `https://tu-proyecto.up.railway.app`

**Header requerido:** `X-API-Key: LCn_cReJtXYhmiUxXDO_DNZZ6VYx4hqT2nyNlk_Rk6c`

---

### `POST /po/conectar`
```json
{
  "ssid":    "42[\"auth\",{\"session\":\"...\",\"isDemo\":1}]",
  "is_demo": true
}
```

### `GET /po/estado`
Ver saldo y estado de conexión.

### `GET /po/activos`
Lista activos con payout %.

### `GET /po/velas?activo=EURUSD_otc&intervalo=60&cantidad=100`
Velas históricas.

### `POST /po/senal`
```json
{
  "activo":     "EURUSD_otc",
  "intervalo":  60,
  "duracion":   1,
  "estrategia": "auto"
}
```
Responde con BUY/SELL/NEUTRAL + confianza + análisis.

### `GET /demo/senal?activo=EURUSD_otc`
Demo sin login.

---

## 🔑 Tu API Key
```
LCn_cReJtXYhmiUxXDO_DNZZ6VYx4hqT2nyNlk_Rk6c
```

---

## ⚠️ Solo para estudio personal.
