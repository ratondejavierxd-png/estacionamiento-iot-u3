# Estacionamiento Inteligente IoT – Unidad 3 (TI3042)

Estacionamiento de 3 plazas con sensor de distancia (menos de 5 m = OCUPADO), barrera actuadora y app Android. Solo AWS (IoT Core + DynamoDB + RDS).

## Topología

```
[Microcontrolador virtual]  --serie (tramas NMEA)-->  [Gateway]  --MQTT/TLS 8883-->  AWS IoT Core
   (Raspbian, Python)       <--------- ACK/CMD ------   (Raspbian)                        |
                                                                                    Regla IoT
                                                                                         |
App Android  --HTTP-->  API Flask (Raspbian)  <-- lee/consulta --  DynamoDB (parqueo_mensajes)
                              |
                         RDS MySQL (usuarios, login con bcrypt)
```

- Microcontrolador virtual: `raspbian/microcontrolador.py`, puerto serie virtual (socat).
- Gateway: `raspbian/gateway.py`, serie a MQTT con TLS y certificado X.509.
- API: `raspbian/api.py`. App: `android-cambios/` (Kotlin).

## Tramas seriales

Formato `$TIPO,campos*CC`, donde CC es el XOR de los caracteres entre `$` y `*`, en hexadecimal.

- `$LECT,seq,plaza,dist,estado*CC`: micro → gateway (lectura).
- `$CMD,id,BARRERA,ABRIR|CERRAR*CC`: app → API → IoT Core → gateway → micro.
- `$ACK,id,BARRERA,ABIERTA|CERRADA*CC` / `$NAK`: micro → gateway → nube → app.

Toda trama con checksum inválido se descarta (integridad).

## Seguridad (ISO 27000 / 27001 / 27002 / 27400)

| Control | Implementación |
|---|---|
| Cifrado en tránsito (27002 A.8.24) | MQTT sobre TLS 1.2, puerto 8883; RDS con TLS (global-bundle.pem) |
| Autenticación de dispositivo (27400) | Certificado X.509 por dispositivo + política IoT de mínimo privilegio |
| Autenticación de usuarios | Login con contraseña hasheada (bcrypt) y token con expiración de 1 h |
| Integridad | Checksum XOR en cada trama; validación en micro, gateway, API y app |
| Gestión de secretos | Certificados, claves y `env.sh` fuera del repositorio (`.gitignore`) |
| Mínimo privilegio | Política IoT limitada a topics `parqueo/*`; firewall RDS solo con IP autorizada |
| Inyección | Consultas SQL parametrizadas; validación de entradas |
| Disponibilidad | Reconexión automática, buffers y modo degradado (ver más abajo) |

## Protección de datos personales

Solo se almacena nombre de usuario y hash bcrypt de la contraseña (nunca en texto plano). No se recopilan datos de ubicación ni de las personas. Las lecturas son de ocupación de plazas, sin patentes. Los datos se usan únicamente para el funcionamiento del sistema.

## Almacenamiento temporal, reconexión y energía

- Microcontrolador: buffer de tramas si el puerto serie falla.
- Gateway: si no hay conexión a la nube guarda los mensajes en `buffer_mensajes.jsonl` y los reenvía al reconectar (`reenviado: true`). Reconexión con retroceso exponencial. Último Will en `parqueo/estado` (online/offline).
- App: caché local de la última lectura y cola de comandos pendientes que se sincronizan al volver la conexión; aviso de datos desactualizados (más de 30 s).
- Energía: el microcontrolador envía solo ante cambios de distancia (delta 0,5 m) o un latido cada 10 s, en vez de transmitir continuamente; la app ajusta su frecuencia de consulta (2 a 8 s).

## Plan de contingencia

| Falla | Respuesta |
|---|---|
| Caída de internet | El gateway acumula en buffer y resincroniza; la app muestra datos en caché con aviso |
| Caída de IoT Core | Los comandos de la API pasan a una cola local (`cola_comandos.jsonl`) |
| Caída de DynamoDB o RDS | La API responde con la última caché (modo degradado) |
| Falla del microcontrolador | El estado `parqueo/estado` pasa a offline (Último Will) y la app lo informa |
| Credenciales comprometidas | Desactivar el certificado en IoT Core y emitir otro |

## Ejecución (Raspbian)

```
cp env.example env.sh   # completar valores; no subir a GitHub
source env.sh
bash iniciar.sh
python3 microcontrolador.py   # en otra terminal; teclas 1, 2, 3 simulan vehículos
```

Prueba de corte: `touch /tmp/forzar_offline` y luego `rm /tmp/forzar_offline`.
