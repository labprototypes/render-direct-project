import os
import requests
from flask import Flask, request, jsonify

# Создаем приложение
app = Flask(__name__)

# Получаем ключи из переменных окружения на Render
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
# Этот секретный ключ мы придумали сами для защиты нашего прокси
PROXY_SECRET_KEY = os.environ.get('PROXY_SECRET_KEY') 

# Определяем endpoint для OpenAI
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

@app.route('/proxy/openai', methods=['POST'])
def proxy_openai():
    # 1. Проверяем секретный ключ, чтобы никто чужой не мог использовать наш прокси
    auth_header = request.headers.get('Authorization')
    if not auth_header or auth_header != f"Bearer {PROXY_SECRET_KEY}":
        return jsonify({"error": "Unauthorized"}), 403

    # 2. Получаем данные, которые прислал наш российский сервер
    incoming_data = request.json
    if not incoming_data:
        return jsonify({"error": "Missing JSON body"}), 400

    # 3. Готовим и отправляем запрос уже в сам OpenAI
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(OPENAI_API_URL, json=incoming_data, headers=headers)
        response.raise_for_status()  # Вызовет ошибку, если OpenAI вернет плохой статус
        
        # 4. Возвращаем ответ от OpenAI обратно нашему российскому серверу
        return jsonify(response.json())

    except requests.exceptions.HTTPError as http_err:
        # Если OpenAI вернул ошибку, пересылаем ее дальше
        return jsonify(http_err.response.json()), http_err.response.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Если вы запускаете этот файл напрямую на Render, вам может понадобиться эта часть
if __name__ == '__main__':
    # Render обычно предоставляет порт через переменную PORT
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
