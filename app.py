import os
import replicate
from flask import Flask, request, jsonify, render_template_string

# Инициализируем Flask приложение
app = Flask(__name__)

# Получаем API токен из переменных окружения
REPLICATE_API_TOKEN = os.environ.get('REPLICATE_API_TOKEN')

# HTML-шаблон для нашей главной страницы. Весь визуал находится здесь.
INDEX_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>Редактор изображений</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: #f4f4f9; color: #333; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; padding: 1rem;}
.container { background: #fff; padding: 2rem; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.1); width: 100%; max-width: 500px; text-align: center; }
h1 { margin-bottom: 1.5rem; }
form { display: flex; flex-direction: column; gap: 1rem; }
input[type="file"], input[type="text"] { padding: 0.75rem; border: 1px solid #ccc; border-radius: 8px; font-size: 1rem; }
button { background-color: #007bff; color: white; padding: 0.75rem; border: none; border-radius: 8px; font-size: 1rem; cursor: pointer; transition: background-color 0.2s; }
button:hover { background-color: #0056b3; }
button:disabled { background-color: #ccc; cursor: not-allowed; }
.result-container { margin-top: 2rem; min-height: 100px; }
img { max-width: 100%; border-radius: 8px; margin-top: 1rem; }
#loader { display: none; font-size: 1.2rem; margin-top: 1rem; }
</style>
</head>
<body>
<div class="container">
<h1>Загрузи картинку и измени её</h1>
<form id="edit-form">
<input type="file" id="image-file" accept="image/*" required>
<input type="text" id="prompt" placeholder="Например: 'сделай фон в стиле Ван Гога'" required>
<button type="submit" id="submit-button">Сгенерировать</button>
</form>
<div class="result-container">
<div id="loader">Обработка... это может занять до минуты ⏳</div>
<img id="result-image" src="" alt="Результат появится здесь">
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

            submitButton.disabled = true;
            loader.style.display = 'block';
            resultImage.src = '';
            resultImage.alt = '';

            const formData = new FormData();
            formData.append('image', fileInput.files[0]);
            formData.append('prompt', promptInput.value);

            try {
                const response = await fetch('/process-image', {
                    method: 'POST',
                    body: formData
                });

                if (!response.ok) { throw new Error(`Ошибка сервера: ${response.statusText}`); }
                const data = await response.json();
                if (data.output_url) { resultImage.src = data.output_url; } 
                else { throw new Error(data.error || 'Не удалось получить результат'); }

            } catch (error) {
                console.error('Ошибка:', error);
                resultImage.alt = `Произошла ошибка: ${error.message}`;
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
    model_version = "timothybrooks/instruct-pix2pix:30c1d0b916a6f8efce20493f5d61ee27491ab2a60437c13c588468b981b423aa"

    try:
        output = replicate.run(
            model_version,
            input={"image": image_file, "prompt": prompt_text}
        )
        output_url = output[0] if output else None
        if not output_url: return jsonify({'error': 'API Replicate не вернуло результат'}), 500
        return jsonify({'output_url': output_url})
    except Exception as e:
        return jsonify({'error': f'Внутренняя ошибка сервера: {e}'}), 500
