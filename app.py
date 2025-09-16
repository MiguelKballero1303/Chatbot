from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import uvicorn
import json, os, random, requests
import openai
from datetime import datetime

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("❌ Falta OPENAI_API_KEY en .env")

client = openai.OpenAI(api_key=OPENAI_API_KEY)

ROBOT_EMAIL = os.getenv("ROBOT_EMAIL")
ROBOT_PASSWORD = os.getenv("ROBOT_PASSWORD")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:3000")

with open("corpus_quechua_espanol.json", encoding="utf-8") as f:
    corpus = json.load(f)
if os.path.exists("corpus_runasimi_ampliado.json"):
    with open("corpus_runasimi_ampliado.json", encoding="utf-8") as f:
        corpus.extend(json.load(f))

preguntas_recoleccion = [
    "¿Qué te motivó a buscar apoyo psicológico en este momento?",
    "¿Desde cuándo vienes experimentando esta situación o malestar?",
    "¿Cómo describirías tu estado de ánimo en las últimas semanas?",
    "¿Has tenido dificultades para dormir, comer o concentrarte últimamente?",
    "¿Existen eventos recientes en tu vida que consideres importantes para tu bienestar emocional?",
    "¿Tienes antecedentes de haber recibido terapia psicológica o psiquiátrica antes?",
    "¿Hay situaciones o actividades que te ayuden a sentirte mejor cuando estás mal?",
    "¿Con quién cuentas como red de apoyo (familia, amigos, pareja, etc.)?",
    "¿Hay algún problema de salud física que quieras mencionar?",
    "¿Qué esperas lograr o cambiar a través de este proceso terapéutico?"
]

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class Mensaje(BaseModel):
    user_id: str
    mensaje: str

conversaciones = {}
estado_usuario = {}
datos_testimonio = {}
datos_parciales = {}
JWT_ROBOT = None

def autenticar_robot():
    global JWT_ROBOT
    login_data = {"email": ROBOT_EMAIL, "password": ROBOT_PASSWORD}
    try:
        resp = requests.post(f"{BACKEND_URL}/auth/login", json=login_data, timeout=15)
        if resp.status_code == 201:
            JWT_ROBOT = resp.json().get("access_token")
            print("✅ Token JWT obtenido correctamente")
        else:
            print(f"❌ Error autenticando robot: {resp.status_code} {resp.text}")
            JWT_ROBOT = None
    except Exception as e:
        print("❌ Error autenticando robot:", e)
        JWT_ROBOT = None

def contiene_quechua(texto):
    palabras = ["llaki", "kawsay", "ñawi", "munay", "wasi", "rimay", "sunqu", "llapa"]
    return any(p in texto.lower() for p in palabras)

def extraer_datos(texto):
    prompt = f"""
Extrae los siguientes campos del texto: nombre, apellido, dni, celular y correo. 
Devuelve solo un JSON como este:

{{
  "nombre": "Ana",
  "apellido": "Pérez",
  "dni": "12345678",
  "celular": "987654321",
  "correo": "ana.perez@gmail.com"
}}

Texto: "{texto}"
"""
    respuesta = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2
    )
    try:
        return json.loads(respuesta.choices[0].message.content.strip())
    except Exception as e:
        print("❌ Error procesando JSON:", e)
        return {}

def analizar_testimonio(texto):
    prompt = f"""
Analiza el siguiente testimonio de un paciente y responde con un JSON que incluya:

- "diagnostico": diagnóstico tentativo si es posible
- "notasProfesional": resumen del testimonio
- "especialidadRecomendada": perfil profesional más adecuado (por ejemplo: Psicólogo clínico, Terapeuta familiar, etc.)
- "planSeguimiento": sugerencias de seguimiento
- "ameritaGratuito": true o false según si parece necesitar ayuda económica

Responde solo con el JSON.

Testimonio:
"{texto}"
"""
    respuesta = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4
    )
    try:
        return json.loads(respuesta.choices[0].message.content.strip())
    except Exception as e:
        print("❌ Error procesando análisis:", e)
        return {}

def obtener_profesional_por_especialidad(especialidad):
    try:
        r = requests.get(f"{BACKEND_URL}/profesionales-salud", headers={"Authorization": f"Bearer {JWT_ROBOT}"}, timeout=15)
        if r.status_code != 200:
            print(f"❌ Error obteniendo profesionales: {r.status_code} {r.text}")
            return None

        profesionales = r.json()
        filtrados = [p for p in profesionales if especialidad.lower() in p.get("especialidad", "").lower()]
        return filtrados[0]["id"] if filtrados else (profesionales[0]["id"] if profesionales else None)
    except Exception as e:
        print("❌ Error obteniendo profesional:", e)
        return None

def obtener_tratamientos(token: str):
    try:
        r = requests.get(f"{BACKEND_URL}/tratamientos", headers={"Authorization": f"Bearer {token}"}, timeout=15)
        if r.status_code == 200:
            return r.json()
        else:
            print(f"❌ Error al obtener tratamientos: {r.status_code} {r.text}")
            return []
    except Exception as e:
        print("❌ Error al conectar con backend de tratamientos:", e)
        return []

def seleccionar_tratamiento_mas_adecuado(testimonio: str, tratamientos: list):
    prompt = f"""
Paciente: "{testimonio}"

Lista de tratamientos:
{chr(10).join([f"{t.get('id')} - {t.get('nombreTratamiento')}: {t.get('descripcion', '')}" for t in tratamientos])}

De todos los tratamientos listados, ¿cuál parece más adecuado para ayudar al paciente según su testimonio? Devuelve solo el ID del tratamiento más apropiado.
"""
    try:
        respuesta = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        return respuesta.choices[0].message.content.strip()
    except Exception as e:
        print("❌ Error seleccionando tratamiento:", e)
        return None

@app.post("/chat")
async def chat(m: Mensaje):
    user_id = m.user_id.strip()
    texto = m.mensaje.strip()
    habla_quechua = contiene_quechua(texto)

    if user_id not in conversaciones:
        conversaciones[user_id] = []
        estado_usuario[user_id] = "inicio"
        datos_testimonio[user_id] = ""
        datos_parciales[user_id] = {"respuestas": {}, "pregunta_actual": 0}

    estado = estado_usuario[user_id]
    mensajes = conversaciones[user_id]

    if estado == "inicio":
        ejemplos = random.sample(corpus, min(6, len(corpus))) if habla_quechua else []
        for par in ejemplos:
            mensajes.append({"role": "user", "content": par["espanol"]})
            mensajes.append({"role": "assistant", "content": par["quechua"]})

        mensajes.insert(0, {
            "role": "system",
            "content": "Eres un terapeuta compasivo y multilingüe que conversa de forma empática con personas que buscan ayuda. Tu prioridad es escuchar con atención, validar sus emociones y ofrecer consuelo inicial."
        })

        mensajes.append({"role": "user", "content": texto})
        datos_testimonio[user_id] += " " + texto

        respuesta = client.chat.completions.create(
            model="gpt-4",
            messages=mensajes,
            temperature=0.7
        ).choices[0].message.content.strip()

        conversaciones[user_id] = mensajes + [{"role": "assistant", "content": respuesta}]
        estado_usuario[user_id] = "anamnesis"
        return {"respuesta": respuesta + "\n\n" + preguntas_recoleccion[0]}

    elif estado == "anamnesis":
        progreso = datos_parciales[user_id]
        idx = progreso["pregunta_actual"]
    
        if idx < len(preguntas_recoleccion):
            progreso["respuestas"][preguntas_recoleccion[idx]] = texto
            progreso["pregunta_actual"] += 1

        resumen_respuestas = " ".join([f"{k}: {v}" for k, v in progreso["respuestas"].items()])
        prompt_revision = f"""
        Eres un asistente clínico. Revisa estas respuestas del paciente:
        {resumen_respuestas}

        Solo responde "SI" si ya tienes información suficiente para una primera anamnesis psicológica,
        incluyendo al menos estas áreas:
        - Motivo de consulta
        - Tiempo o inicio del problema
        - Estado de ánimo actual
        - Red de apoyo
        - Expectativas del proceso terapéutico

        Si falta alguna de estas áreas, responde "NO".
        """

        decision = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt_revision}],
            temperature=0
        ).choices[0].message.content.strip().upper()

        if decision == "SI" or progreso["pregunta_actual"] >= len(preguntas_recoleccion):
            datos_testimonio[user_id] += " " + resumen_respuestas
            estado_usuario[user_id] = "espera_respuesta"
            return {"respuesta": "Gracias por responder. Con esta información podremos orientarte mejor. ¿Te gustaría registrarte para recibir una sesión gratuita?"}
        else:
            siguiente_pregunta = preguntas_recoleccion[progreso["pregunta_actual"]]
            return {"respuesta": siguiente_pregunta}

    elif estado == "espera_respuesta":
        if any(p in texto.lower() for p in ["sí", "si", "claro", "ari", "de acuerdo", "por favor"]):
            if "@" in texto:
                datos_parciales[user_id]["correo"] = texto.strip()
                estado_usuario[user_id] = "esperando_datos"
                return {"respuesta": "Gracias por proporcionar tu correo. Ahora por favor dime tu nombre, apellido, DNI y celular."}
            else:
                estado_usuario[user_id] = "esperando_datos"
                return {"respuesta": "Perfecto. Por favor dime tu nombre, apellido, DNI, celular y correo electrónico."}
        else:
            return {"respuesta": "Entiendo. Estoy aquí si necesitas hablar más."}

    elif estado == "esperando_datos":
        datos_extraidos = extraer_datos(texto)
        datos = {**datos_parciales.get(user_id, {}), **datos_extraidos}

        faltantes = [k for k in ["nombre", "apellido", "dni", "celular", "correo"] if not datos.get(k)]
        if faltantes:
            datos_parciales[user_id] = datos
            falta_str = ", ".join(faltantes)
            return {"respuesta": f"Aún necesito los siguientes datos para registrarte: {falta_str}."}

        try:
            response = requests.post(
                f"{BACKEND_URL}/pacientes",
                json=datos,
                headers={"Authorization": f"Bearer {JWT_ROBOT}"},
                timeout=20
            )
            if response.status_code == 401:
                autenticar_robot()
                response = requests.post(
                    f"{BACKEND_URL}/pacientes",
                    json=datos,
                    headers={"Authorization": f"Bearer {JWT_ROBOT}"},
                    timeout=20
                )

            if response.status_code not in [200, 201]:
                return {"respuesta": "Ocurrió un problema al registrar tus datos. Intenta nuevamente."}

            paciente = response.json()
            if "id" not in paciente:
                return {"respuesta": "El servidor no devolvió un ID de paciente. Por favor intenta más tarde."}

            analisis = analizar_testimonio(datos_testimonio[user_id])
            especialidad = analisis.get("especialidadRecomendada", "")
            profesional_id = obtener_profesional_por_especialidad(especialidad) or obtener_profesional_por_especialidad("")

            if not profesional_id:
                return {"respuesta": "No encontramos un profesional disponible. Te contactaremos pronto."}
            
            cita = {
                "paciente": paciente["id"],
                "profesionalSalud": profesional_id,
                "motivo": "Primera sesión gratuita ofrecida por el chatbot",
                "fechaHora": "2025-07-01T10:00:00Z",
                "estado": "PENDIENTE"
            }
            requests.post(
                f"{BACKEND_URL}/citas",
                json=cita,
                headers={"Authorization": f"Bearer {JWT_ROBOT}"},
                timeout=20
            )

            tratamientos = obtener_tratamientos(JWT_ROBOT)
            tratamiento_id = seleccionar_tratamiento_mas_adecuado(datos_testimonio[user_id], tratamientos)

            historia = {
                "paciente": paciente["id"],
                "profesionalSalud": profesional_id,
                "tratamiento": tratamiento_id,
                "fechaCreacion": datetime.utcnow().isoformat(),
                "notasProfesional": analisis.get("notasProfesional", ""),
                "diagnostico": analisis.get("diagnostico", ""),
                "planSeguimiento": analisis.get("planSeguimiento", ""),
                "observaciones": ""
            }
            requests.post(
                f"{BACKEND_URL}/historias-clinicas",
                json=historia,
                headers={"Authorization": f"Bearer {JWT_ROBOT}"},
                timeout=20
            )

            estado_usuario[user_id] = "finalizado"
            return {"respuesta": f"Tu sesión ha sido agendada exitosamente y tu historia clínica registrada. ¡Gracias por confiar en nosotros, {datos['nombre']}!"}
        except Exception as e:
            print("❌ Error en el registro:", e)
            return {"respuesta": "Ocurrió un error inesperado al registrar tus datos. Por favor, intenta nuevamente."}
    elif estado == "finalizado":
        return {"respuesta": "Tu cita ya está registrada. ¿Hay algo más en lo que pueda ayudarte?"}

if __name__ == "__main__":
    autenticar_robot()
    uvicorn.run(app, host="0.0.0.0", port=8000)
