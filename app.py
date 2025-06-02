import os
import boto3 # <-- Наша новая библиотека для работы с S3
import uuid  # <-- Модуль для создания уникальных имен файлов
from flask import Flask, request, jsonify, render_template_string

# --- Настройки для подключения к Selectel S3 ---
S3_ACCESS_KEY_ID = os.environ.get('S3_ACCESS_KEY_ID')
S3_SECRET_ACCESS_KEY = os.environ.get('S3_SECRET_ACCESS_KEY')
S3_CONTAINER_NAME = os.environ.get('S3_CONTAINER_NAME')
S3_ENDPOINT_URL = 'https://s3.ru-7.storage.selcloud.ru'
S3_REGION = 'ru-7'

# Инициализируем Flask приложение
app = Flask(__name__)

# Получаем API токен из переменных окружения
REPLICATE_API_TOKEN = os.environ.get('REPLICATE_API_TOKEN')

# HTML-шаблон (без изменений)
INDEX_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI-Редактор Изображений</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: #f4f4f9; color: #333; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; padding: 1rem;}
        .container { background: #fff; padding: 2rem; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.1); width: 100%; max-width: 500px; text-align: center; }
        h1 { margin-bottom: 0.5rem; }
        p { margin-bottom: 1.5rem; color: #666; font-size: 0.9rem; }
        form { display: flex; flex-direction: column; gap: 1rem; }
        input[type="file"], input[type="text"] { padding: 0.75rem; border: 1px solid #ccc; border-radius: 8px; font-size: 1rem; }
        button { background-color: #007bff; color: white; padding: 0.75rem; border: none; border-radius: 8px; font-size: 1rem; cursor: pointer; transition: background-color 0.2s; }
        button:hover { background-color: #0056b3; }
        button:disabled { background-color: #ccc; cursor: not-allowed; }
        .result-container { margin-top: 2rem; min-height: 256px; }
        img { max-width: 100%; border-radius: 8px; margin-top: 1rem; }
        #loader { display: none; text-align: center; font-family: -apple-system, BlinkMacSystemFont, sans-serif; font-size: 1.2rem; margin-top: 1rem; }
        #error-box { display: none; text-align: center; color: #d93025; margin-top: 1rem; }
    </style>
</head>
<body>
    <div class="container">
        <h1>AI-Редактор</h1>
        <p>Загрузите изображение и опишите, что вы хотите получить в итоге.</p>
        <form id="edit-form">
            <input type="file" id="image-file" accept="image/*" required>
            <input type="text" id="prompt" placeholder="Например: 'кот в очках, стиль киберпанк'" required>
            <button type="submit" id="submit-button">Сгенерировать</button>
        </form>
        <div class="result-container">
            <div id="loader">Обработка... это может занять больше времени ⏳</div>
            <img id="result-image" src="">
            <div id="error-box"></div>
        </div>
    </div>

    <script>
        document.getElementById('edit-form').addEventListener('submit', async (event) => {
            event.preventDefault();
            const fileInput = document.getElementById('image-file');
            const promptInput = document.getElementById('prompt');
            const submitButton = document.getElementById('submit-button');
            const loader = document.getElementById('loader');
            const resultImage = document.getElementById('result-image');
            const errorBox = document.getElementById('error-box');
            
            submitButton.disabled = true;
            loader.style.display = 'block';
            resultImage.src = '';
            resultImage.style.display = 'none';
            errorBox.style.display = 'none';

            const formData = new FormData();
            formData.append('image', fileInput.files[0]);
            formData.append('prompt', promptInput.value);
            try {
                const response = await fetch('/process-image', {
                    method: 'POST',
                    body: formData
                });
                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.error || 'Неизвестная ошибка сервера');
                }
                resultImage.src = data.output_url;
                resultImage.style.display = 'block';

            } catch (error) {
                console.error('Ошибка:', error);
                errorBox.textContent = "Произошла ошибка. Попробуйте еще раз.";
                errorBox.style.display = 'block';
            } finally {
                loader.style.display = 'none';
                submitButton.disabled = false;
            }
        });
    </script>
</body>
</html>
"""

# Маршрут для главной страницы
@app.route('/')
def index():
    return render_template_string(INDEX_HTML)

# Маршрут для обработки изображения
@app.route('/process-image', methods=['POST'])
def process_image():
    if 'image' not in request.files or 'prompt' not in request.form:
        return jsonify({'error': 'Отсутствует изображение или промпт'}), 400

    image_file = request.files['image']
    prompt_text = request.form['prompt']
    
    model_version = "black-forest-labs/flux-kontext-max:0b9c317b23e79a9a0d8b9602ff4d04030d433055927fb7c4b91c44234a6818c4"
    
    try:
        # --- НАША НОВАЯ НАДЕЖНАЯ ЛОГИКА С SELECTEL S3 ---
        
        # 1. Создаем S3 клиент для работы с хранилищем Selectel
        s3_client = boto3.client(
            's3',
            endpoint_url=S3_ENDPOINT_URL,
            region_name=S3_REGION,
            aws_access_key_id=S3_ACCESS_KEY_ID,
            aws_secret_access_key=S3_SECRET_ACCESS_KEY
        )
        
        # 2. Генерируем уникальное имя для файла, чтобы не было конфликтов
        object_name = f"{uuid.uuid4()}-{image_file.filename}"
        
        # 3. Загружаем файл в ваш контейнер 'labcontainer'
        s3_client.upload_fileobj(
            image_file,
            S3_CONTAINER_NAME,
            object_name,
            ExtraArgs={'ACL': 'public-read'} # Делаем файл публично доступным
        )
        
        # 4. Формируем публичную ссылку на загруженный файл
        hosted_image_url = f"https://{S3_CONTAINER_NAME}.s3.ru-7.storage.selcloud.ru/{object_name}"
        print(f"!!! Изображение загружено на Selectel: {hosted_image_url}")

        # 5. Отправляем в Replicate промпт и ПУБЛИЧНУЮ ССЫЛКУ на картинку
        output = replicate.run(
            model_version,
            input={
                "image": hosted_image_url,
                "prompt": prompt_text
            }
        )
        
        output_url = str(output) if output else None
        
        if not output_url:
            return jsonify({'error': 'API Replicate не вернуло результат'}), 500
        return jsonify({'output_url': output_url})
        
    except Exception as e:
        print(f"!!! ОШИБКА В ПРОДАКШЕНЕ:\n{e}")
        return jsonify({'error': f'Произошла внутренняя ошибка сервера. {e}'}), 500
