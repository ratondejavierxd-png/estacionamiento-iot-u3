"""
API del Estacionamiento Inteligente (Flask).
 - Usuarios y login: MySQL en AWS RDS (contraseñas con bcrypt, token con expiracion).
 - Telemetria y confirmaciones: AWS DynamoDB (tabla parqueo_mensajes, alimentada por una regla de IoT Core).
 - Comandos: se publican en AWS IoT Core (topic parqueo/comando); si falla, quedan en una cola local.
Variables de entorno: DB_HOST, DB_USER, DB_PASS, IOT_ENDPOINT. Credenciales AWS: ~/.aws/credentials
"""
import os, re, time, json, secrets, datetime
import bcrypt, pymysql, boto3
from boto3.dynamodb.conditions import Key
from flask import Flask, request, jsonify

app = Flask(__name__)

BASE = os.path.dirname(os.path.abspath(__file__))
CA_PATH = os.path.join(BASE, "global-bundle.pem")
COLA_LOCAL = os.path.join(BASE, "cola_comandos.jsonl")
REGION = "us-east-1"
TABLA = "parqueo_mensajes"
TOKEN_TTL = 3600            # el token dura 1 hora
UMBRAL_DESACTUALIZADO_S = 30
TOKENS = {}
CACHE = {"estado": None}    # ultimo estado correcto (modo degradado)
_tabla = None


# ---------------- MySQL (usuarios) ----------------
def conectar():
    return pymysql.connect(
        host=os.environ["DB_HOST"], user=os.environ["DB_USER"], password=os.environ["DB_PASS"],
        database=os.environ.get("DB_NAME", "estacionamiento"),
        ssl={"ca": CA_PATH}, autocommit=True, connect_timeout=10,
        cursorclass=pymysql.cursors.DictCursor)


def consulta(sql, params=(), uno=False):
    con = conectar()
    try:
        with con.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone() if uno else cur.fetchall()
    finally:
        con.close()


def error(msg, code):
    return jsonify({"error": msg}), code


def autenticado():
    token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    datos = TOKENS.get(token)
    if datos and datos["exp"] > time.time():
        return datos
    TOKENS.pop(token, None)
    return None


@app.errorhandler(pymysql.MySQLError)
def error_bd(e):
    print("Error de base de datos:", e)
    return error("Base de datos no disponible", 503)


# ---------------- DynamoDB ----------------
def tabla():
    global _tabla
    if _tabla is None:
        _tabla = boto3.resource("dynamodb", region_name=REGION).Table(TABLA)
    return _tabla


def ultimos(topic, n):
    r = tabla().query(KeyConditionExpression=Key("topic").eq(topic),
                      ScanIndexForward=False, Limit=n)
    return r.get("Items", [])


def num(x):
    return float(x) if x is not None else None


def hora(ts):
    return datetime.datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d %H:%M:%S")


def construir_estado():
    tel = ultimos("parqueo/telemetria", 60)
    plazas = []
    for pid in (1, 2, 3):
        it = next((i for i in tel if int(i.get("plaza_id", -1)) == pid), None)
        plazas.append({
            "id": pid, "nombre": "Plaza %d" % pid,
            "estado": it["estado"] if it else "DISPONIBLE",
            "distancia": num(it.get("distancia")) if it else None,
            "actualizado": hora(it["ts"]) if it else "-"})
    edad = max(0, int(time.time() - int(tel[0]["ts"]) / 1000)) if tel else None

    acks = ultimos("parqueo/ack", 1)
    ack, actuador = None, "DESCONOCIDO"
    if acks:
        a = acks[0]
        ack = {"id": a.get("id"), "estado": a.get("estado"), "trama": a.get("trama")}
        actuador = a.get("estado", "DESCONOCIDO")

    est = ultimos("parqueo/estado", 1)
    dispositivo = est[0].get("estado", "?") if est else "?"
    return {"plazas": plazas, "edad_s": edad,
            "desactualizado": edad is None or edad > UMBRAL_DESACTUALIZADO_S,
            "dispositivo": dispositivo, "actuador": actuador, "ack": ack}


# ---------------- rutas ----------------
@app.route("/salud", methods=["GET"])
def salud():
    return jsonify({"ok": True})


@app.route("/registro", methods=["POST"])
def registro():
    j = request.get_json(silent=True) or {}
    usuario = str(j.get("username", "")).strip()
    clave = str(j.get("password", ""))
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,50}", usuario):
        return error("Usuario inválido (3-50 caracteres: letras, números, . _ -)", 400)
    if len(clave) < 8:
        return error("La contraseña debe tener al menos 8 caracteres", 400)
    hash_ = bcrypt.hashpw(clave.encode(), bcrypt.gensalt()).decode()
    con = conectar()
    try:
        with con.cursor() as cur:
            cur.execute("INSERT INTO usuarios (username, password_hash) VALUES (%s, %s)", (usuario, hash_))
    except pymysql.err.IntegrityError:
        return error("El usuario ya existe", 409)
    finally:
        con.close()
    return jsonify({"ok": True}), 201


@app.route("/login", methods=["POST"])
def login():
    j = request.get_json(silent=True) or {}
    usuario = str(j.get("username", ""))
    clave = str(j.get("password", ""))
    fila = consulta("SELECT id, password_hash FROM usuarios WHERE username = %s", (usuario,), uno=True)
    if not fila or not bcrypt.checkpw(clave.encode(), fila["password_hash"].encode()):
        return error("Credenciales inválidas", 401)
    token = secrets.token_hex(32)
    TOKENS[token] = {"uid": fila["id"], "usuario": usuario, "exp": time.time() + TOKEN_TTL}
    return jsonify({"token": token})


@app.route("/estado", methods=["GET"])
def estado():
    if not autenticado():
        return error("No autorizado", 401)
    try:
        e = construir_estado()
        e["cache"] = False
        CACHE["estado"] = e
    except Exception as ex:                      # modo degradado: ultimo estado conocido
        print("DynamoDB no disponible:", ex)
        if CACHE["estado"] is None:
            return error("Datos no disponibles", 503)
        e = dict(CACHE["estado"])
        e["cache"] = True
        e["desactualizado"] = True
    return jsonify(e)


@app.route("/historial", methods=["GET"])
def historial():
    if not autenticado():
        return error("No autorizado", 401)
    try:
        items = ultimos("parqueo/telemetria", 50)
    except Exception as ex:
        print("DynamoDB no disponible:", ex)
        return error("Datos no disponibles", 503)
    return jsonify({"lecturas": [{
        "plaza_id": int(i.get("plaza_id", 0)), "distancia_m": num(i.get("distancia")),
        "estado": i.get("estado"), "fecha": hora(i["ts"])} for i in items]})


def checksum(payload):
    c = 0
    for b in payload.encode():
        c ^= b
    return "%02X" % c


TRAMA_CMD = re.compile(r"^\$CMD,\d{1,9},BARRERA,(ABRIR|CERRAR)\*([0-9A-F]{2})$")


def trama_valida(t):
    m = TRAMA_CMD.match(t)
    return bool(m) and checksum(t[1:t.rindex("*")]) == m.group(2)


@app.route("/comando", methods=["POST"])
def comando():
    datos = autenticado()
    if not datos:
        return error("No autorizado", 401)
    j = request.get_json(silent=True) or {}
    trama = str(j.get("trama", "")).strip()
    if not trama_valida(trama):
        return error("Trama inválida o corrupta", 400)
    msg = {"trama": trama, "usuario": datos["usuario"], "ts_origen": int(time.time() * 1000)}
    try:
        iot = boto3.client("iot-data", region_name=REGION,
                           endpoint_url="https://" + os.environ["IOT_ENDPOINT"])
        iot.publish(topic="parqueo/comando", qos=1, payload=json.dumps(msg))
        via = "iot-core"
    except Exception as ex:                      # plan de contingencia: cola local
        print("Publicacion en IoT Core fallo, se usa la cola local:", ex)
        with open(COLA_LOCAL, "a") as fh:
            fh.write(json.dumps(msg) + "\n")
        via = "local"
    return jsonify({"ok": True, "via": via})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
