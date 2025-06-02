import os
import replicate
import traceback
from flask import Flask, request, jsonify, render_template_string

# Инициализируем Flask приложение
app = Flask(__name__)

# Получаем API токен из переменных окружения
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
        .result-container { margin-top: 2rem; min-height: 100px; }
        img { max-width: 100%; border-radius: 8px; margin-top: 1rem; }
        #loader { display: none; text-align: center; font-family: -apple-system, BlinkMacSystemFont, sans-serif; font-size: 1.2rem; margin-top: 1rem; }
        #error-box { text-align: left; background: #eee; padding: 1rem; border-radius: 8px; font-family: monospace; white-space: pre-wrap; word-wrap: break-word; }
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
            <div id="error-box" style="display: none;"></div>
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
            errorBox.textContent = '';

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
                
                // !!! ИЗМЕНЕНИЕ ДЛЯ ДИАГНОСТИКИ ЗДЕСЬ !!!
                // Мы покажем полученный URL как текст, чтобы увидеть, что нам приходит
                errorBox.textContent = "Полученный URL: " + data.output_url;
                errorBox.style.display = 'block';

                resultImage.src = data.output_url;
                resultImage.style.display = 'block';

            } catch (error) {
                console.error('Ошибка:', error);
                errorBox.textContent = error.message;
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
    
    image_path = "temp_image.jpg"
    try:
        image_file.save(image_path)
        
        with open(image_path, "rb") as file_to_upload:
            output = replicate.run(
                model_version,
                input={
                    "image": file_to_upload,
                    "prompt": prompt_text
                }
            )
        
        output_url = str(output) if output else None
        
        if not output_url:
            return jsonify({'error': 'API Replicate не вернуло результат'}), 500
        return jsonify({'output_url': output_url})
        
    except Exception as e:
        tb_str = traceback.format_exc()
        print(f"!!! TRACEBACK:\n{tb_str}")
        return jsonify({'error': f'ПОЛНЫЙ ТЕКСТ ОШИБКИ:\n\n{tb_str}'}), 500
    finally:
        if os.path.exists(image_path):
            os.remove(image_path)
