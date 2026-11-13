package com.example.estacionamiento

/** Tramas tipo NMEA:  $TIPO,campo1,campo2*CC   (CC = checksum XOR en hexadecimal). */
object Trama {
    fun checksum(payload: String): String {
        var c = 0
        for (ch in payload) c = c xor ch.code
        return "%02X".format(c)
    }

    fun construir(vararg campos: Any): String {
        val p = campos.joinToString(",")
        return "$" + p + "*" + checksum(p)
    }

    /** Devuelve los campos si el checksum es válido; null si la trama está corrupta. */
    fun validar(trama: String): List<String>? {
        val t = trama.trim()
        if (!t.startsWith("$") || !t.contains("*")) return null
        val i = t.lastIndexOf('*')
        val cuerpo = t.substring(1, i)
        val cc = t.substring(i + 1).uppercase()
        return if (checksum(cuerpo) == cc) cuerpo.split(",") else null
    }
}
