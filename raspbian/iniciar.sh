#!/bin/bash
# Arranca el puerto serie virtual, el gateway y la API. Uso:  bash iniciar.sh
cd ~/estacionamiento
source env.sh
pkill -f "python3 api.py" 2>/dev/null
pkill -f "python3 gateway.py" 2>/dev/null
pkill -f "python3 microcontrolador.py" 2>/dev/null
pkill -f "socat.*ttyMICRO" 2>/dev/null
sleep 1
rm -f /tmp/ttyMICRO /tmp/ttyGATEWAY /tmp/forzar_offline
nohup socat -d -d pty,raw,echo=0,link=/tmp/ttyMICRO pty,raw,echo=0,link=/tmp/ttyGATEWAY > socat.log 2>&1 &
sleep 1
nohup python3 api.py > api.log 2>&1 &
nohup python3 gateway.py > gateway.log 2>&1 &
sleep 3
echo "Listo. Ahora, en OTRA terminal:   cd ~/estacionamiento && source env.sh && python3 microcontrolador.py"
echo "Ver el gateway en vivo:           tail -f ~/estacionamiento/gateway.log"
