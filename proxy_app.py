# УЛУЧШЕННАЯ ВЕРСИЯ ПРОКСИ-ПРИЛОЖЕНИЯ
import os
import requests
from flask import Flask, request, Response

app = Flask(__name__)

OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
PROXY_SECRET_KEY = os.environ.get('PROXY_SECRET_KEY') 
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

@app.route('/proxy/openai', methods=['POST'])
def proxy_openai():
    # 1. Проверяем наш внутренний секретный ключ
    auth_header = request.headers.get('Authorization')
    if not auth_header or auth_header != f"Bearer {PROXY_SECRET_KEY}":
        return Response('{"error": "Unauthorized"}', status=403, mimetype='application/json')

    # 2. Получаем "сырые" данные из тела запроса
    raw_body = request.get_data()
    if not raw_body:
        return Response('{"error": "Missing request body"}', status=400, mimetype='application/json')

    # 3. Готовим заголовки для запроса в OpenAI
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        # 4. Отправляем "сырые" данные напрямую в OpenAI
        response = requests.post(OPENAI_API_URL, data=raw_body, headers=headers)
        
        # 5. Возвращаем ответ от OpenAI (включая статус и заголовки) 
        # нашему российскому серверу
        return Response(response.content, status=response.status_code, content_type=response.headers['Content-Type'])

    except Exception as e:
        return Response('{"error": "Proxy server error: ' + str(e) + '"}', status=500, mimetype='application/json')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
