# ОТЛАДОЧНАЯ ВЕРСИЯ ПРОКСИ-ПРИЛОЖЕНИЯ
import os
import requests
from flask import Flask, request, Response

app = Flask(__name__)

OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
PROXY_SECRET_KEY = os.environ.get('PROXY_SECRET_KEY') 
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

@app.route('/proxy/openai', methods=['POST'])
def proxy_openai():
    print("\n--- PROXY REQUEST RECEIVED ---") # <-- Новое лог-сообщение

    # Логируем заголовки, которые пришли от российского сервера
    print(f"Headers: {request.headers}")

    # Логируем "сырое" тело запроса
    raw_body = request.get_data()
    print(f"Raw body (bytes): {raw_body}")

    # Пытаемся получить JSON и логируем результат
    try:
        json_data = request.get_json()
        print(f"Parsed JSON: {json_data}")
    except Exception as e:
        json_data = None
        print(f"!!! Failed to parse JSON: {e}")

    # --- Старая логика ---
    auth_header = request.headers.get('Authorization')
    if not auth_header or auth_header != f"Bearer {PROXY_SECRET_KEY}":
        print("!!! ERROR: Authorization failed.")
        return Response('{"error": "Unauthorized"}', status=403, mimetype='application/json')

    if not raw_body:
        print("!!! ERROR: Request body is empty. Returning 400.")
        return Response('{"error": "Missing request body"}', status=400, mimetype='application/json')

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        print("--- FORWARDING REQUEST TO OPENAI ---")
        response = requests.post(OPENAI_API_URL, data=raw_body, headers=headers)
        print(f"--- OPENAI RESPONSE STATUS: {response.status_code} ---")
        return Response(response.content, status=response.status_code, content_type=response.headers.get('Content-Type'))

    except Exception as e:
        print(f"!!! ERROR during request to OpenAI: {e}")
        return Response('{"error": "Proxy server error: ' + str(e) + '"}', status=500, mimetype='application/json')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
