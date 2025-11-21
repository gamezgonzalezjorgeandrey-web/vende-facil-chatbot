from fastapi import FastAPI
from pydantic import BaseModel
import json
import os

app = FastAPI()

DB_FILE = "database.json"

# Cargar base de datos
def load_db():
    if not os.path.exists(DB_FILE):
        return {}
    with open(DB_FILE, "r") as f:
        return json.load(f)

# Guardar base de datos
def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=4)

class Message(BaseModel):
    phone: str
    text: str

@app.post("/webhook")
def webhook(msg: Message):
    db = load_db()
    user = msg.phone

    if user not in db:
        db[user] = {"estado": "nuevo", "historial": []}

    db[user]["historial"].append(msg.text)

    # Lógica simple de demo
    text = msg.text.lower()

    if "hola" in text:
        response = "Hola, soy tu asistente automático de Vende Fácil. ¿Te puedo hacer 2 preguntas rápidas?"
    elif "si" in text:
        response = "Perfecto. ¿Conoces la propuesta?"
    elif "no" in text:
        response = "Entiendo. ¿Te consideras a favor, indeciso o en contra?"
    elif "favor" in text:
        db[user]["estado"] = "a_favor"
        response = "Registrado como A FAVOR ✔️"
    elif "indeciso" in text:
        db[user]["estado"] = "indeciso"
        response = "Registrado como INDECISO 🟡"
    elif "contra" in text:
        db[user]["estado"] = "en_contra"
        response = "Registrado como EN CONTRA ❌"
    else:
        response = "No entendí bien, ¿puedes repetirlo por favor?"

    save_db(db)
    return {"respuesta": response}
