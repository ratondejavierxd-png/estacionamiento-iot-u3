#!/usr/bin/env python3
"""
Gateway del microcontrolador virtual: puente entre el puerto serie y AWS IoT Core (MQTT sobre TLS).
 - Lee tramas seriales ($LECT / $ACK), verifica el checksum y las publica por MQTT.
 - Recibe comandos ($CMD) por MQTT (o desde la cola local de la API) y los escribe en el puerto serie.
 - Reconexion automatica con espera creciente y buffer local cuando no hay conexion.
 - Simular corte de red:   touch /tmp/forzar_offline     (restablecer: rm /tmp/forzar_offline)
"""
import os, ssl, time, json, threading
import serial
import paho.mqtt.client as mqtt

ENDPOINT = os.environ["IOT_ENDPOINT"]
BASE = os.path.dirname(os.path.abspath(__file__))
CERTS = os.path.join(BASE, "certs")
PUERTO = "/tmp/ttyGATEWAY"
BUFFER = os.path.join(BASE, "buffer_mensajes.jsonl")
COLA_LOCAL = os.path.join(BASE, "cola_comandos.jsonl")
FLAG_OFFLINE = "/tmp/forzar_offline"
CLIENT_ID = "parqueo-micro-01"

estado = {"conectado": False}
lock_buf = threading.Lock()
lock_ser = threading.Lock()
ser = None


def checksum(payload):
    c = 0
    for b in payload.encode():
        c ^= b
    return "%02X" % c


def desarmar(linea):
    linea = linea.strip()
    if not linea.startswith("$") or "*" not in linea:
        return None
    cuerpo, _, cc = linea[1:].rpartition("*")
    if checksum(cuerpo) != cc.strip().upper():
        return None
    return cuerpo.split(",")


def ahora_ms():
    return int(time.time() * 1000)


# ---------------- puerto serie ----------------
def abrir_serie():
    while True:
        try:
            s = serial.Serial(PUERTO, 115200, timeout=0.5, write_timeout=1)
            print("[GW] Puerto serie abierto:", PUERTO)
            return s
        except Exception as e:
            print("[GW] Esperando puerto serie...", e)
            time.sleep(1)


def escribir_serie(trama):
    with lock_ser:
        ser.write((trama + "\n").encode())
    print("[GW->MCU]", trama)


# ---------------- buffer local (sin conexion) ----------------
def guardar_en_buffer(topic, payload):
    with lock_buf:
        with open(BUFFER, "a") as fh:
            fh.write(json.dumps({"topic": topic, "payload": payload}) + "\n")
    print("[GW] Sin conexion: mensaje guardado en el buffer local")


def vaciar_buffer():
    with lock_buf:
        if not os.path.exists(BUFFER):
            return
        with open(BUFFER) as fh:
            lineas = fh.readlines()
        os.remove(BUFFER)
    print("[GW] Sincronizando %d mensajes guardados..." % len(lineas))
    for i, l in enumerate(lineas):
        if not estado["conectado"]:                     # se cayo otra vez: guardar el resto
            for resto in lineas[i:]:
                m = json.loads(resto)
                guardar_en_buffer(m["topic"], m["payload"])
            return
        try:
            m = json.loads(l)
            m["payload"]["reenviado"] = True
            c.publish(m["topic"], json.dumps(m["payload"]), qos=1)
            time.sleep(0.05)
        except Exception as e:
            print("[GW] Error reenviando:", e)
    print("[GW] Sincronizacion completa")


def publicar(topic, payload):
    if estado["conectado"]:
        r = c.publish(topic, json.dumps(payload), qos=1)
        if r.rc != mqtt.MQTT_ERR_SUCCESS:
            guardar_en_buffer(topic, payload)
    else:
        guardar_en_buffer(topic, payload)


# ---------------- MQTT ----------------
def on_connect(cl, ud, flags, rc):
    if rc != 0:
        print("[GW] Conexion rechazada, rc =", rc)
        return
    estado["conectado"] = True
    print("[GW] Conectado a AWS IoT Core")
    cl.subscribe("parqueo/comando", qos=1)
    cl.publish("parqueo/estado", json.dumps({"dispositivo": CLIENT_ID, "estado": "online",
                                             "ts_origen": ahora_ms()}), qos=1)
    threading.Thread(target=vaciar_buffer, daemon=True).start()


def on_disconnect(cl, ud, rc):
    estado["conectado"] = False
    print("[GW] Desconectado de AWS IoT (rc=%s). Reconectando automaticamente..." % rc)


def on_message(cl, ud, msg):
    try:
        datos = json.loads(msg.payload.decode())
        trama = datos["trama"]
    except Exception:
        print("[GW] Mensaje de comando ilegible")
        return
    if desarmar(trama) is None:
        print("[GW] Comando con checksum invalido, descartado:", trama)
        return
    escribir_serie(trama)


c = mqtt.Client(client_id=CLIENT_ID)
c.tls_set(ca_certs=os.path.join(CERTS, "AmazonRootCA1.pem"),
          certfile=os.path.join(CERTS, "certificado.pem.crt"),
          keyfile=os.path.join(CERTS, "clave-privada.pem.key"),
          tls_version=ssl.PROTOCOL_TLSv1_2)
c.will_set("parqueo/estado", json.dumps({"dispositivo": CLIENT_ID, "estado": "offline"}), qos=1)
c.reconnect_delay_set(min_delay=1, max_delay=30)
c.on_connect = on_connect
c.on_disconnect = on_disconnect
c.on_message = on_message


def conectar_con_backoff():
    espera, intento = 1, 0
    while not os.path.exists(FLAG_OFFLINE):
        intento += 1
        try:
            print("[GW] Conectando a AWS IoT (intento %d)..." % intento)
            c.connect(ENDPOINT, 8883, keepalive=30)
            c.loop_start()
            return
        except Exception as e:
            print("[GW] Fallo la conexion (%s). Reintento en %ds" % (e, espera))
            time.sleep(espera)
            espera = min(espera * 2, 30)


# ---------------- tramas del microcontrolador ----------------
def procesar_linea(linea):
    campos = desarmar(linea)
    if campos is None:
        print("[GW] Trama corrupta descartada:", linea)
        return
    tipo = campos[0]
    if tipo == "LECT" and len(campos) == 5:
        payload = {"seq": int(campos[1]), "plaza_id": int(campos[2]), "distancia": float(campos[3]),
                   "estado": campos[4], "origen": CLIENT_ID, "ts_origen": ahora_ms(), "trama": linea}
        publicar("parqueo/telemetria", payload)
        print("[GW->AWS] telemetria plaza %s: %s m (%s)" % (campos[2], campos[3], campos[4]))
    elif tipo == "ACK" and len(campos) == 4:
        payload = {"id": campos[1], "actuador": campos[2], "estado": campos[3],
                   "origen": CLIENT_ID, "ts_origen": ahora_ms(), "trama": linea}
        publicar("parqueo/ack", payload)
        print("[GW->AWS] ack comando %s: %s" % (campos[1], campos[3]))
    elif tipo == "NAK":
        print("[GW] El microcontrolador rechazo un comando:", linea)


def revisar_cola_local():
    """Comandos que la API dejo en un archivo cuando no pudo publicar en IoT Core."""
    if not os.path.exists(COLA_LOCAL):
        return
    tmp = COLA_LOCAL + ".proc"
    try:
        os.rename(COLA_LOCAL, tmp)
    except OSError:
        return
    with open(tmp) as fh:
        for l in fh:
            try:
                trama = json.loads(l)["trama"]
                if desarmar(trama):
                    escribir_serie(trama)
            except Exception:
                pass
    os.remove(tmp)


def main():
    global ser
    ser = abrir_serie()
    conectar_con_backoff()
    forzado = False
    while True:
        off = os.path.exists(FLAG_OFFLINE)
        if off and not forzado:
            forzado = True
            print("[GW] *** MODO SIN CONEXION (simulado) ***")
            if estado["conectado"]:
                c.publish("parqueo/estado", json.dumps({"dispositivo": CLIENT_ID, "estado": "offline",
                                                        "ts_origen": ahora_ms()}), qos=1)
                time.sleep(0.5)
            c.disconnect()
            c.loop_stop()
            estado["conectado"] = False
        elif not off and forzado:
            forzado = False
            print("[GW] Red restablecida: reconectando...")
            conectar_con_backoff()

        revisar_cola_local()
        try:
            linea = ser.readline().decode(errors="ignore").strip()
        except Exception as e:
            print("[GW] Error de puerto serie:", e)
            time.sleep(1)
            continue
        if linea:
            procesar_linea(linea)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[GW] Apagado")
