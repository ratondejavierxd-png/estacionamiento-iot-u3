package com.example.estacionamiento

import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.example.estacionamiento.databinding.ActivityMonitorBinding
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

class MonitorActivity : AppCompatActivity() {
    private lateinit var b: ActivityMonitorBinding
    private var intervaloMs = 2000L
    private var sinCambios = 0
    private var ultimaHuella = ""
    private var fallos = 0

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityMonitorBinding.inflate(layoutInflater)
        setContentView(b.root)

        b.btnAbrir.setOnClickListener { enviarComando("ABRIR") }
        b.btnCerrar.setOnClickListener { enviarComando("CERRAR") }
        refrescarPendientes()

        // Consulta solo mientras la pantalla está visible (ahorro de batería)
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                while (true) {
                    try {
                        sincronizarPendientes()
                        val r = Api.request("/estado")
                        fallos = 0
                        Almacen.guardarEstado(this@MonitorActivity, r.toString())
                        b.tvConexion.text = "🟢 Conectado"
                        mostrar(r, desdeCache = false)

                        // Energía: si no hay cambios, se espacian las consultas (2 s -> 8 s)
                        val huella = r.optJSONArray("plazas").toString() + r.optString("actuador")
                        if (huella == ultimaHuella) {
                            sinCambios++
                            if (sinCambios >= 3) intervaloMs = minOf(intervaloMs + 1000L, 8000L)
                        } else {
                            sinCambios = 0
                            intervaloMs = 2000L
                            ultimaHuella = huella
                        }
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: SesionExpirada) {
                        Toast.makeText(this@MonitorActivity, "Sesión expirada", Toast.LENGTH_SHORT).show()
                        Api.volverAlLogin(this@MonitorActivity)
                        return@repeatOnLifecycle
                    } catch (e: Exception) {
                        // Reconexión automática con espera creciente: 4 s, 8 s, 16 s
                        fallos++
                        intervaloMs = minOf(2000L shl minOf(fallos, 3), 16000L)
                        b.tvConexion.text = "🔴 Sin conexión · reintentando (intento $fallos)"
                        Almacen.leerEstado(this@MonitorActivity)?.let {
                            mostrar(JSONObject(it), desdeCache = true)
                        }
                    }
                    refrescarPendientes()
                    delay(intervaloMs)
                }
            }
        }
    }

    /** Envía una trama $CMD (con checksum). Si no hay conexión, queda guardada y se envía al reconectar. */
    private fun enviarComando(accion: String) {
        val id = (System.currentTimeMillis() % 1_000_000L).toString()
        val trama = Trama.construir("CMD", id, "BARRERA", accion)
        lifecycleScope.launch {
            try {
                Api.request("/comando", JSONObject().put("trama", trama))
                Toast.makeText(this@MonitorActivity, "Comando enviado: $trama", Toast.LENGTH_SHORT).show()
            } catch (e: SesionExpirada) {
                Api.volverAlLogin(this@MonitorActivity)
            } catch (e: ApiError) {
                Toast.makeText(this@MonitorActivity, e.message ?: "Error", Toast.LENGTH_SHORT).show()
            } catch (e: Exception) {
                Almacen.encolar(this@MonitorActivity, trama)
                Toast.makeText(this@MonitorActivity,
                    "Sin conexión: comando guardado, se enviará al reconectar", Toast.LENGTH_LONG).show()
                refrescarPendientes()
            }
        }
    }

    /** Sincronización: envía en orden los comandos guardados mientras no había conexión. */
    private suspend fun sincronizarPendientes() {
        while (true) {
            val t = Almacen.primeraPendiente(this) ?: return
            try {
                Api.request("/comando", JSONObject().put("trama", t))
            } catch (e: ApiError) {
                // el servidor la rechazó: se descarta para no bloquear la cola
            }
            Almacen.quitarPrimera(this)
        }
    }

    private fun refrescarPendientes() {
        val n = Almacen.cantidadPendientes(this)
        b.tvPendientes.text = if (n > 0) "⏳ Comandos pendientes de sincronizar: $n" else ""
    }

    private fun mostrar(r: JSONObject, desdeCache: Boolean) {
        pintar(r.getJSONArray("plazas"))
        b.tvActuador.text = "Barrera: ${r.optString("actuador", "?")}"

        val ack = r.optJSONObject("ack")
        b.tvAck.text = if (ack == null) {
            "Último ACK: -"
        } else {
            val campos = Trama.validar(ack.optString("trama"))
            if (campos != null && campos.size == 4) {
                "Último ACK: ${campos[3]} ✔ (checksum válido, comando ${campos[1]})"
            } else {
                "⚠️ Trama ACK corrupta descartada"
            }
        }

        b.tvAviso.text = when {
            desdeCache -> "⚠️ Mostrando datos guardados (sin conexión con el servidor)"
            r.optBoolean("desactualizado", false) ->
                "⚠️ Datos desactualizados: el microcontrolador no responde (hace ${r.optInt("edad_s", -1)} s)"
            else -> ""
        }
    }

    private fun pintar(plazas: JSONArray) {
        b.contenedor.removeAllViews()
        val alto = (80 * resources.displayMetrics.density).toInt()
        val margen = (10 * resources.displayMetrics.density).toInt()
        for (i in 0 until plazas.length()) {
            val p = plazas.getJSONObject(i)
            val ocupado = p.getString("estado") == "OCUPADO"
            val d = p.optDouble("distancia", Double.NaN)
            val extra = if (d.isNaN()) "" else " · $d m"
            val tv = TextView(this).apply {
                text = "${p.getString("nombre")}: ${p.getString("estado")}$extra"
                textSize = 18f
                setTextColor(Color.WHITE)
                gravity = Gravity.CENTER
                setBackgroundColor(Color.parseColor(if (ocupado) "#D32F2F" else "#388E3C"))
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, alto
                ).apply { setMargins(0, 0, 0, margen) }
            }
            b.contenedor.addView(tv)
        }
    }
}
