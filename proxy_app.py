# ФИНАЛЬНАЯ ОТЛАДОЧНАЯ ВЕРСИЯ ПРОКСИ
import os
import requests
from flask import Flask, request, Response

app = Flask(__name__)

OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
PROXY_SECRET_KEY = os.environ.get('PROXY_SECRET_KEY') 
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

@app.route('/proxy/openai', methods=['POST'])
def proxy_openai():
    print("\n--- PROXY V3 REQUEST RECEIVED ---")

    auth_header = request.headers.get('Authorization')
    if not auth_header or auth_header != f"Bearer {PROXY_SECRET_KEY}":
        return Response('{"error": "Unauthorized"}', status=403, mimetype='application/json')

    raw_body = request.get_data()
    if not raw_body:
        return Response('{"error": "Missing request body"}', status=400, mimetype='application/json')

    headers_to_openai = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        print("--- FORWARDING REQUEST TO OPENAI ---")
        print(f"--- Payload sent to OpenAI: {raw_body.decode('utf-8')} ---") # Логируем то, что отправляем

        response = requests.post(OPENAI_API_URL, data=raw_body, headers=headers_to_openai, timeout=30)

        # САМОЕ ВАЖНОЕ: ЛОГИРУЕМ ТЕЛО ОТВЕТА ОТ OPENAI
        print(f"--- OPENAI RESPONSE STATUS: {response.status_code} ---")
        print(f"--- OPENAI RESPONSE BODY: {response.text} ---")

        return Response(response.content, status=response.status_code, content_type=response.headers.get('Content-Type'))

    except requests.exceptions.RequestException as e:
        print(f"!!! ERROR during request to OpenAI: {e}")
        return Response('{"error": "Proxy server error: ' + str(e) + '"}', status=502, mimetype='application/json')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
