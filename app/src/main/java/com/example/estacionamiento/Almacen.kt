package com.example.estacionamiento

import android.content.Context
import org.json.JSONArray

/** Almacenamiento temporal local: último estado conocido y comandos pendientes de enviar. */
object Almacen {
    private const val PREFS = "estacionamiento_cache"

    private fun prefs(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun guardarEstado(ctx: Context, json: String) {
        prefs(ctx).edit().putString("estado", json).apply()
    }

    fun leerEstado(ctx: Context): String? = prefs(ctx).getString("estado", null)

    private fun lista(ctx: Context) = JSONArray(prefs(ctx).getString("pendientes", "[]"))

    fun encolar(ctx: Context, trama: String) {
        val arr = lista(ctx)
        arr.put(trama)
        prefs(ctx).edit().putString("pendientes", arr.toString()).apply()
    }

    fun cantidadPendientes(ctx: Context): Int = lista(ctx).length()

    fun primeraPendiente(ctx: Context): String? {
        val arr = lista(ctx)
        return if (arr.length() > 0) arr.getString(0) else null
    }

    fun quitarPrimera(ctx: Context) {
        val arr = lista(ctx)
        val nuevo = JSONArray()
        for (i in 1 until arr.length()) nuevo.put(arr.get(i))
        prefs(ctx).edit().putString("pendientes", nuevo.toString()).apply()
    }
}
