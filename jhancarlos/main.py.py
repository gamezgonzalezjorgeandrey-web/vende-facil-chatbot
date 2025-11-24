from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
import os
import asyncio
import asyncpg
import httpx
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("whatsapp-bot")

app = FastAPI()

DATABASE_URL = os.getenv("DATABASE_URL")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "jhan-alvernia-2025")

db_pool: asyncpg.pool.Pool = None

# ===============================
# DATABASE INIT
# ===============================

async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=5, max_size=20)
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS votantes (
                id SERIAL PRIMARY KEY,
                telefono TEXT UNIQUE NOT NULL,
                nombre TEXT,
                estado TEXT DEFAULT 'inicio',
                resultado TEXT,
                opt_in BOOLEAN DEFAULT FALSE,
                no_contactar BOOLEAN DEFAULT FALSE,
                barrio TEXT,
                primera_interaccion TIMESTAMP DEFAULT NOW()
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS interacciones (
                id SERIAL PRIMARY KEY,
                telefono TEXT NOT NULL,
                mensaje_enviado TEXT,
                respuesta_usuario TEXT,
                fecha TIMESTAMP DEFAULT NOW()
            );
        """)

@app.on_event("startup")
async def startup_event():
    await init_db()

# ===============================
# DB HELPERS
# ===============================

async def get_votante(telefono):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM votantes WHERE telefono=$1", telefono)

async def crear_votante(telefono):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO votantes (telefono, opt_in) VALUES ($1, FALSE) ON CONFLICT DO NOTHING",
            telefono
        )

async def actualizar_estado(telefono, estado, resultado=None, barrio=None):
    async with db_pool.acquire() as conn:
        if resultado and barrio:
            await conn.execute(
                "UPDATE votantes SET estado=$1, resultado=$2, barrio=$3 WHERE telefono=$4",
                estado, resultado, barrio, telefono
            )
        elif resultado:
            await conn.execute(
                "UPDATE votantes SET estado=$1, resultado=$2 WHERE telefono=$3",
                estado, resultado, telefono
            )
        else:
            await conn.execute(
                "UPDATE votantes SET estado=$1 WHERE telefono=$2",
                estado, telefono
            )

async def registrar_interaccion(telefono, enviado, recibido):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO interacciones (telefono, mensaje_enviado, respuesta_usuario) VALUES ($1, $2, $3)",
            telefono, enviado, recibido
        )

async def marcar_no_contactar(telefono):
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE votantes SET no_contactar=TRUE WHERE telefono=$1", telefono)

# ===============================
# WHATSAPP SEND
# ===============================

async def enviar_whatsapp(telefono, texto):
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": telefono, "type": "text", "text": {"body": texto}}
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(url, json=data, headers=headers)
            resp.raise_for_status()
            return texto
        except Exception as e:
            logger.error(f"ERROR enviando a {telefono}: {e}")
            return None

# ===============================
# HUMAN HANDOFF (placeholder)
# ===============================

async def notificar_agente_humano(telefono, razon):
    logger.info(f"DERIVADO A HUMANO: {telefono} — motivo: {razon}")

# ===============================
# MESSAGE HANDLER
# ===============================

async def manejar_mensaje(telefono, texto_usuario):
    texto_usuario = texto_usuario.strip().lower()

    if any(x in texto_usuario for x in ["stop", "no contactar", "cancelar", "parar", "no volver"]):
        await marcar_no_contactar(telefono)
        await enviar_whatsapp(telefono, "Entendido, no volveremos a contactarte.")
        return

    votante = await get_votante(telefono)
    if not votante:
        await crear_votante(telefono)
        votante = await get_votante(telefono)

    if votante["resultado"] is not None or votante["no_contactar"]:
        logger.info(f"Ignorando mensaje de {telefono}: ya finalizado o no_contactar.")
        return

    estado = votante["estado"]

    if estado == "inicio":
        msg = (
            "Hola, te habla el equipo de Jhan Carlos Alvernia.\n"
            "¿Conoces su propuesta para la Alcaldía?\n\n"
            "1. Sí y estoy de acuerdo\n"
            "2. La conozco pero tengo dudas\n"
            "3. No estoy de acuerdo\n"
            "4. No la conozco"
        )
        enviado = await enviar_whatsapp(telefono, msg)
        await actualizar_estado(telefono, "pregunta_simpatia")
        await registrar_interaccion(telefono, enviado, texto_usuario)

    elif estado == "pregunta_simpatia":
        if "1" in texto_usuario:
            await enviar_whatsapp(telefono, "¡Gracias por tu apoyo! ¿Puedes decirme tu barrio?")
            await actualizar_estado(telefono, "pedir_barrio", "a_favor")
        elif "2" in texto_usuario:
            await enviar_whatsapp(telefono, "Te contamos: seguridad, empleo y transporte. ¿Quieres hablar con un asesor?")
            await actualizar_estado(telefono, "derivacion", "indeciso")
        elif "3" in texto_usuario:
            await enviar_whatsapp(telefono, "Gracias por tu sinceridad. ¡Feliz día!")
            await actualizar_estado(telefono, "finalizado", "en_contra")
        elif "4" in texto_usuario:
            await enviar_whatsapp(telefono, "Trabajamos por seguridad, empleo y movilidad. ¿Puedes decirme tu barrio?")
            await actualizar_estado(telefono, "pedir_barrio", "indeciso")
        else:
            await enviar_whatsapp(telefono, "Responde 1, 2, 3 o 4 por favor.")

    elif estado == "pedir_barrio":
        await actualizar_estado(telefono, "finalizado", votante["resultado"], texto_usuario)
        await enviar_whatsapp(telefono, "¡Gracias! Te mantendremos informado para tu zona.")

    elif estado == "derivacion":
        if "si" in texto_usuario or "sí" in texto_usuario or "ok" in texto_usuario:
            await notificar_agente_humano(telefono, votante["resultado"])
            await enviar_whatsapp(telefono, "Un asesor te contactará pronto.")
        else:
            await enviar_whatsapp(telefono, "¡Gracias! Si necesitas algo, escribe.")
        await actualizar_estado(telefono, "finalizado")

# ===============================
# WEBHOOKS
# ===============================

@app.get("/webhook/whatsapp")
async def verificar_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(content=challenge, media_type="text/plain")
    return JSONResponse(status_code=403, content={"status": "forbidden"})

@app.post("/webhook/whatsapp")
async def recibir_mensaje(request: Request):
    data = await request.json()
    if data.get("object") == "whatsapp_business_account":
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                if change.get("field") == "messages":
                    value = change.get("value", {})
                    for msg in value.get("messages", []):
                        if msg.get("type") == "text":
                            telefono = msg["from"]
                            # Evitar loop: no procesar mensajes enviados por el bot
                            if telefono == PHONE_NUMBER_ID:
                                continue
                            if telefono.isdigit() and len(telefono) >= 10:
                                asyncio.create_task(manejar_mensaje(telefono, msg["text"]["body"]))
    return {"status": "ok"}

# ===============================
# METRICS
# ===============================

@app.get("/metrics")
async def get_metrics():
    async with db_pool.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM votantes")
        a_favor = await conn.fetchval("SELECT COUNT(*) FROM votantes WHERE resultado='a_favor'")
        indecisos = await conn.fetchval("SELECT COUNT(*) FROM votantes WHERE resultado='indeciso'")
        en_contra = await conn.fetchval("SELECT COUNT(*) FROM votantes WHERE resultado='en_contra'")
        respuestas = await conn.fetchval("SELECT COUNT(*) FROM interacciones")
        no_contactar = await conn.fetchval("SELECT COUNT(*) FROM votantes WHERE no_contactar=TRUE")
    return {
        "total": total,
        "respuestas": respuestas,
        "a_favor": a_favor,
        "indecisos": indecisos,
        "en_contra": en_contra,
        "no_contactar": no_contactar,
        "tasa_respuesta": round((respuestas / total) * 100, 2) if total > 0 else 0
    }