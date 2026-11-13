#!/usr/bin/env python3
"""
Microcontrolador virtual del estacionamiento (firmware simulado).
Habla SOLO por puerto serie con tramas tipo NMEA:   $TIPO,campo1,campo2*CC   (CC = checksum XOR en hexadecimal)

  Envia :  $LECT,<seq>,<plaza>,<distancia>,<estado>*CC     (lecturas del sensor)
           $ACK,<id>,BARRERA,<ABIERTA|CERRADA>*CC          (confirmacion de un comando)
           $NAK,<id>,<motivo>*CC                           (trama corrupta o comando invalido)
  Recibe:  $CMD,<id>,BARRERA,<ABRIR|CERRAR>*CC

Ahorro de energia: solo transmite cuando cambia el estado/distancia o cada HEARTBEAT_S segundos.
Si el enlace serie no esta disponible, guarda las tramas en un buffer y las envia al volver.
"""
import sys, time, random, threading, collections
import serial

PUERTO = "/tmp/ttyMICRO"
UMBRAL_M = 5.0        # menos de 5 m = OCUPADO
MUESTREO_S = 2.0
HEARTBEAT_S = 10.0
DELTA_M = 0.5


def checksum(payload):
    c = 0
    for b in payload.encode():
        c ^= b
    return "%02X" % c


def armar(*campos):
    p = ",".join(str(x) for x in campos)
    return "$%s*%s" % (p, checksum(p))


def desarmar(linea):
    """Devuelve la lista de campos si el checksum es valido; None si la trama esta corrupta."""
    linea = linea.strip()
    if not linea.startswith("$") or "*" not in linea:
        return None
    cuerpo, _, cc = linea[1:].rpartition("*")
    if checksum(cuerpo) != cc.strip().upper():
        return None
    return cuerpo.split(",")


class Micro:
    def __init__(self):
        self.ser = None
        self.lock = threading.Lock()
        self.cola_tx = collections.deque(maxlen=500)   # buffer temporal del microcontrolador
        self.seq = 0
        self.actuador = "CERRADA"
        self.enviadas = 0
        self.omitidas = 0
        self.plazas = {i: {"ocupada": False, "ult_estado": None, "ult_dist": None, "ult_envio": 0.0}
                       for i in (1, 2, 3)}

    def abrir_puerto(self):
        while True:
            try:
                self.ser = serial.Serial(PUERTO, 115200, timeout=0.2, write_timeout=1)
                print("[MCU] Puerto serie abierto:", PUERTO)
                return
            except Exception as e:
                print("[MCU] Esperando puerto serie...", e)
                time.sleep(1)

    # ---------- transmision con buffer ----------
    def tx(self, trama):
        with self.lock:
            self.cola_tx.append(trama)
            self._vaciar()

    def _vaciar(self):
        while self.cola_tx:
            t = self.cola_tx[0]
            try:
                self.ser.write((t + "\n").encode())
                print("[TX]", t)
                self.cola_tx.popleft()
            except (serial.SerialException, OSError):
                print("[MCU] Enlace serie no disponible: %d tramas en buffer" % len(self.cola_tx))
                return

    # ---------- recepcion de comandos ----------
    def leer(self):
        while True:
            try:
                linea = self.ser.readline().decode(errors="ignore").strip()
            except Exception:
                time.sleep(0.5)
                continue
            if not linea:
                continue
            print("[RX]", linea)
            campos = desarmar(linea)
            if campos is None:
                self.tx(armar("NAK", 0, "CHECKSUM"))
                continue
            if campos[0] == "CMD" and len(campos) == 4 and campos[2] == "BARRERA":
                if campos[3] == "ABRIR":
                    self.actuador = "ABIERTA"
                elif campos[3] == "CERRAR":
                    self.actuador = "CERRADA"
                else:
                    self.tx(armar("NAK", campos[1], "ACCION"))
                    continue
                print("[MCU] Actuador BARRERA ->", self.actuador)
                self.tx(armar("ACK", campos[1], "BARRERA", self.actuador))

    # ---------- simulacion del sensor ----------
    def medir(self, pid):
        base = 2.0 if self.plazas[pid]["ocupada"] else 8.0
        return round(base + random.uniform(-0.15, 0.15), 2)

    def ciclo(self):
        ahora = time.time()
        for pid, p in self.plazas.items():
            if random.random() < 0.01:                  # llegada/salida aleatoria de vehiculos
                p["ocupada"] = not p["ocupada"]
            d = self.medir(pid)
            estado = "OCUPADO" if d < UMBRAL_M else "DISPONIBLE"
            cambio = (p["ult_dist"] is None or estado != p["ult_estado"]
                      or abs(d - p["ult_dist"]) >= DELTA_M)
            if cambio or ahora - p["ult_envio"] >= HEARTBEAT_S:
                self.seq += 1
                self.tx(armar("LECT", self.seq, pid, d, estado))
                p["ult_estado"], p["ult_dist"], p["ult_envio"] = estado, d, ahora
                self.enviadas += 1
                time.sleep(0.15)    # separa las tramas para no coincidir en el mismo milisegundo
            else:
                self.omitidas += 1

    def estadisticas(self):
        total = self.enviadas + self.omitidas
        ahorro = 100.0 * self.omitidas / total if total else 0.0
        print("[ENERGIA] transmisiones: %d | omitidas: %d | ahorro: %.1f%%"
              % (self.enviadas, self.omitidas, ahorro))

    def teclado(self):
        print("Teclado: 1/2/3 + Enter = llega o se va un vehiculo en esa plaza | s = estadisticas")
        for linea in sys.stdin:
            l = linea.strip()
            if l in ("1", "2", "3"):
                p = self.plazas[int(l)]
                p["ocupada"] = not p["ocupada"]
                print("[SIM] Plaza", l, "->", "OCUPADA" if p["ocupada"] else "LIBRE")
            elif l == "s":
                self.estadisticas()


def main():
    m = Micro()
    m.abrir_puerto()
    threading.Thread(target=m.leer, daemon=True).start()
    threading.Thread(target=m.teclado, daemon=True).start()
    ultimo_resumen = time.time()
    try:
        while True:
            m.ciclo()
            with m.lock:
                m._vaciar()          # reintenta enviar lo que haya quedado en el buffer
            if time.time() - ultimo_resumen > 60:
                m.estadisticas()
                ultimo_resumen = time.time()
            time.sleep(MUESTREO_S)
    except KeyboardInterrupt:
        print("\n[MCU] Apagado")


if __name__ == "__main__":
    main()
