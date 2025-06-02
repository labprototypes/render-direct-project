import os
import requests # <-- Наша ключевая библиотека
import time
from flask import Flask, request, jsonify, render_template_string

# Инициализируем Flask приложение
app = Flask(__name__)

# API токен Replicate
REPLICATE_API_TOKEN = os.environ.get('REPLICATE_API_TOKEN')

# HTML-шаблон
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
    
    model_version_id = "0b9c317b23e79a9a0d8b9602ff4d04030d433055927fb7c4b91c44234a6818c4"
    
    try:
        # --- Этап 1: Загрузка файла на публичный хостинг 0x0.st ---
        files = {'file': (image_file.filename, image_file.read(), image_file.mimetype)}
        upload_response = requests.post('https://0x0.st', files=files)
        upload_response.raise_for_status()
        hosted_image_url = upload_response.text.strip()
        print(f"!!! Изображение загружено на 0x0.st: {hosted_image_url}")

        # --- Этап 2: Прямой вызов Replicate API через requests ---
        headers = {"Authorization": f"Bearer {REPLICATE_API_TOKEN}", "Content-Type": "application/json"}
        post_payload = {
            "version": model_version_id,
            "input": {"input_image": hosted_image_url, "prompt": prompt_text}
        }
        
        start_response = requests.post("https://api.replicate.com/v1/predictions", json=post_payload, headers=headers)
        start_response.raise_for_status()
        prediction_data = start_response.json()
        
        get_url = prediction_data["urls"]["get"]
        
        output_url = None
        for _ in range(100):
            time.sleep(2)
            get_response = requests.get(get_url, headers=headers)
            get_response.raise_for_status()
            status_data = get_response.json()
            
            print(f"Статус генерации: {status_data['status']}")
            
            if status_data["status"] == "succeeded":
                output_url = status_data["output"][0] if isinstance(status_data["output"], list) else str(status_data["output"])
                break
            elif status_data["status"] in ["failed", "canceled"]:
                raise Exception(f"Генерация не удалась: {status_data['error']}")
        
        if not output_url:
            return jsonify({'error': 'Генерация заняла слишком много времени.'}), 500
            
        return jsonify({'output_url': output_url})
        
    except Exception as e:
        print(f"!!! ОШИБКА В ПРОДАКШЕНЕ:\n{e}")
        return jsonify({'error': f'Произошла внутренняя ошибка сервера. {e}'}), 500
