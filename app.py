import os
import boto3
import uuid
import requests
import time
import openai
from flask import Flask, request, jsonify, render_template_string

# --- Настройки для подключения к Amazon S3 ---
AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
AWS_S3_BUCKET_NAME = os.environ.get('AWS_S3_BUCKET_NAME')
AWS_S3_REGION = os.environ.get('AWS_S3_REGION')

# Инициализируем Flask приложение
app = Flask(__name__)

# API ключи из переменных окружения
REPLICATE_API_TOKEN = os.environ.get('REPLICATE_API_TOKEN')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

if OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY
else:
    print("!!! ВНИМАНИЕ: OPENAI_API_KEY не найден. Улучшение промптов не будет работать.")

INDEX_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Changer AI</title>
    <style>
        @font-face {
            font-family: 'ChangerFont';
            src: url("{{ url_for('static', filename='fonts/FONT_TEXT.woff2') }}") format('woff2');
            font-weight: normal;
            font-style: normal;
        }

        :root {
            --background-color: #FAF3D4; /* Бежевый фон */
            --primary-blue: #192E8C;    /* Синий для текста и лого */
            --input-area-bg: #FFFFFF;   /* Белый для области ввода */
            --input-icon-bg: #F0F0F0; 
            --text-placeholder: #757575;
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'ChangerFont', sans-serif;
            background-color: var(--background-color);
            color: var(--primary-blue);
            display: flex;
            flex-direction: column;
            align-items: center;
            min-height: 100vh;
            padding: 20px 15px; /* Добавил боковые отступы */
            text-align: center;
        }

        .app-container {
            width: 100%;
            max-width: 800px; 
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 40px; /* Увеличил основной вертикальный отступ */
        }

        .app-header {
            margin-bottom: 10px; 
        }

        .logo {
            height: 45px; 
        }

        .app-main {
            width: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 40px; /* Увеличил основной вертикальный отступ */
        }

        .initial-view {
            width: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 40px; /* Увеличил основной вертикальный отступ */
        }

        .main-text img {
            width: 100%;
            max-width: 750px; 
            height: auto;
        }

        .desktop-main-text { display: block; }
        .mobile-main-text { display: none; }

        /* --- Стили для блока с bubble.svg --- */
        .action-buttons-wrapper {
            width: 100%;
            max-width: 600px; /* Ширина SVG с кнопками, подберите по вашему SVG */
            margin: 0 auto; /* Центрирование */
        }
        
        .action-buttons-container {
            position: relative; /* Для позиционирования оверлеев */
            width: 100%;
            /* Сохраняем пропорции SVG - вам нужно будет узнать их у вашего bubble.svg */
            /* Например, если SVG 600px на 60px, то padding-bottom: 10% */
            /* Это нужно будет настроить под ваш конкретный SVG */
             padding-bottom: 12%; /* ПРИМЕРНОЕ ЗНАЧЕНИЕ, НАСТРОЙТЕ ПОД ВАШ SVG */
        }

        .action-buttons-svg {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            object-fit: contain; /* или cover, в зависимости от SVG */
        }

        .action-button-overlays {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            display: flex; /* Для расположения оверлеев */
            justify-content: space-around; /* Примерное распределение */
        }

        .overlay-btn {
            /* Прозрачные кнопки-оверлеи */
            background-color: transparent;
            border: none; 
            cursor: pointer;
            /* Эти размеры и позиционирование - самые сложные и зависят от вашего SVG */
            /* Я предполагаю 4 кнопки, занимающие примерно по 23-24% ширины каждая, с отступами */
            width: 23%; 
            height: 100%; 
            /* Для отладки можно добавить рамку: border: 1px solid red; */
        }
        /* Можно добавить data-action атрибуты в HTML и здесь задать отступы */
        /* .overlay-btn[data-action="create"] { left: 1%; } */
        /* .overlay-btn[data-action="relight"] { left: 26%; } ... и т.д. */


        .result-view {
            width: 100%;
            max-width: 700px; /* Ширина области результата */
            max-height: 60vh; 
            margin-top: 0; /* Убрал лишний отступ, теперь все через gap */
            margin-bottom: 0;
            display: flex;
            justify-content: center;
            align-items: center;
            background-color: #E9E9E9; 
            border-radius: 12px; 
            padding: 10px; /* небольшой внутренний отступ */
        }

        #result-image {
            max-width: 100%;
            max-height: calc(60vh - 20px); /* С учетом паддинга контейнера */
            object-fit: contain; 
            border-radius: 8px;
        }
        
        .input-area-wrapper { 
            width: 100%;
            display: flex;
            justify-content: center;
            padding: 0; /* Убрал, будет в .input-area */
        }

        .input-area {
            display: flex;
            align-items: center;
            background-color: var(--input-area-bg);
            border-radius: 12px; /* Скругление по макету */
            padding: 10px 12px;
            width: 100%;
            max-width: 550px; /* Ширина по макету */
            box-shadow: 0 3px 8px rgba(0,0,0,0.1);
        }

        #image-file { display: none; }

        .file-upload-label {
            cursor: pointer;
            padding: 10px; 
            background-color: var(--input-icon-bg);
            border-radius: 8px;
            margin-right: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0; 
        }

        .upload-icon { height: 28px; width: 28px; }
        
        #file-name-display {
            display: none; /* По макету имя файла не отображается */
        }

        #prompt {
            flex-grow: 1;
            border: none;
            padding: 12px 10px;
            font-size: 0.9rem; /* Немного меньше */
            background-color: transparent;
            outline: none;
            color: var(--primary-blue);
        }
        #prompt::placeholder { color: var(--text-placeholder); opacity: 0.9; }

        .magic-button {
            background-color: transparent;
            border: none;
            cursor: pointer;
            padding: 8px; 
            margin-left: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }
        .magic-button img { height: 32px; width: 32px; } /* Размер иконки магии */
        .magic-button:hover img { transform: scale(1.1); } /* Небольшой эффект при наведении */


        .loader-message, .error-message { margin-top: 20px; font-size: 1rem; }
        .error-message { color: #d93025; background-color: #fdd; padding: 10px; border-radius: 8px;}

        /* Мобильная версия */
        @media (max-width: 768px) {
            body { padding: 15px; }
            .app-container, .app-main, .initial-view { gap: 25px; }
            .logo { height: 35px; }
            
            .desktop-main-text { display: none; }
            .mobile-main-text { display: block; }
            .mobile-main-text img { max-width: 100%; }

            .action-buttons-wrapper { max-width: 100%; }
            /* padding-bottom для action-buttons-container может нуждаться в корректировке для мобильных */
            /* .action-buttons-container { padding-bottom: 15%; } */

            .input-area { flex-direction: row; /* оставляем в ряд, как на мобильном макете */ max-width: calc(100% - 20px); margin: 0 10px;}
            .file-upload-label { padding: 12px; margin-right:8px;}
            .upload-icon { height: 26px; width: 26px; }
            #prompt { padding: 12px 8px; font-size: 0.9rem; }
            .magic-button { padding: 10px; }
            .magic-button img { height: 28px; width: 28px; }
            
            .result-view {
                width: calc(100% - 20px); 
                max-width: calc(100% - 20px);
                max-height: 45vh; 
                margin: 15px 0;
            }
            #result-image { max-height: calc(45vh - 20px); }
        }
         @media (max-width: 480px) {
            .logo { height: 30px; }
            .app-container, .app-main, .initial-view { gap: 20px; }
            .main-text img { margin-bottom: 0px; } /* Уменьшим отступ текста */
            .action-buttons-wrapper { margin-top: -10px; } /* Придвинем кнопки к тексту */

            /* Для очень маленьких экранов, возможно, кнопки должны идти в два ряда. */
            /* Это потребует другого SVG или 4 отдельных кнопки, как мы обсуждали ранее */
            /* Пока оставляем один SVG, он будет масштабироваться */

            .input-area { padding: 8px 10px; }
            .file-upload-label { padding: 10px; }
            .upload-icon { height: 22px; width: 22px; }
            #prompt { font-size: 0.85rem; }
            .magic-button { padding: 8px; }
            .magic-button img { height: 24px; width: 24px; }
         }

    </style>
</head>
<body>
    <div class="app-container">
        <header class="app-header">
            <img src="{{ url_for('static', filename='images/LOGO_CHANGER.svg') }}" alt="Changer Logo" class="logo">
        </header>

        <main class="app-main">
            <div class="initial-view">
                <div class="main-text desktop-main-text">
                    <img src="{{ url_for('static', filename='images/MAIN_Desktop.svg') }}" alt="CHANGE ANYTHING JUST DROP THE IMAGE">
                </div>
                <div class="main-text mobile-main-text">
                    <img src="{{ url_for('static', filename='images/MAIN_MOB.svg') }}" alt="CHANGE ANYTHING JUST DROP THE IMAGE">
                </div>

                <div class="action-buttons-wrapper">
                    <div class="action-buttons-container">
                        <img src="{{ url_for('static', filename='images/bubble.svg') }}" alt="Action Buttons" class="action-buttons-svg">
                        <div class="action-button-overlays">
                            <button data-action="create" class="overlay-btn" aria-label="Create"></button>
                            <button data-action="relight" class="overlay-btn" aria-label="Relight"></button>
                            <button data-action="remove" class="overlay-btn" aria-label="Remove"></button>
                            <button data-action="change" class="overlay-btn" aria-label="Change"></button>
                        </div>
                    </div>
                </div>
            </div>

            <div class="result-view" style="display: none;">
                <img id="result-image" src="" alt="Generated Image">
            </div>
            
            <div class="input-area-wrapper">
                <form id="edit-form" class="input-area">
                    <label for="image-file" class="file-upload-label">
                        <img src="{{ url_for('static', filename='images/Icon.png') }}" alt="Upload Icon" class="upload-icon">
                        </label>
                    <input type="file" id="image-file" name="image" accept="image/*" required>
                    
                    <input type="text" id="prompt" name="prompt" placeholder="TYPE WHAT YOU WANT TO CHANGE" required>
                    
                    <button type="submit" id="submit-button" class="magic-button">
                        <img src="{{ url_for('static', filename='images/Magic.png') }}" alt="Generate">
                    </button>
                </form>
            </div>

            <div id="loader" class="loader-message" style="display: none;">Обработка...</div>
            <div id="error-box" class="error-message" style="display: none;"></div>
        </main>
    </div>

    <script>
        const imageFileInput = document.getElementById('image-file');
        // const fileNameDisplay = document.getElementById('file-name-display'); // Больше не используем по макету
        const editForm = document.getElementById('edit-form');
        
        const initialView = document.querySelector('.initial-view');
        const resultView = document.querySelector('.result-view');
        const resultImage = document.getElementById('result-image');
        
        const loader = document.getElementById('loader');
        const errorBox = document.getElementById('error-box');
        const submitButton = document.getElementById('submit-button'); // Это наша кнопка "Magic"

        // Убрали отображение имени файла, так как его нет на макетах
        // imageFileInput.addEventListener('change', function() { ... });

        editForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            const promptInput = document.getElementById('prompt');
            
            submitButton.disabled = true;
            loader.style.display = 'block';
            resultImage.src = '';
            resultImage.style.display = 'none';
            errorBox.style.display = 'none';
            errorBox.textContent = ''; 
            
            initialView.style.display = 'none';
            resultView.style.display = 'flex';

            const formData = new FormData();
            // Убедимся, что imageFileInput.files[0] существует
            if (!imageFileInput.files || imageFileInput.files.length === 0) {
                errorBox.textContent = "Пожалуйста, выберите файл для загрузки.";
                errorBox.style.display = 'block';
                loader.style.display = 'none';
                submitButton.disabled = false;
                initialView.style.display = 'flex'; // Возвращаем начальный вид
                resultView.style.display = 'none';
                return;
            }
            formData.append('image', imageFileInput.files[0]);
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
                errorBox.textContent = error.message; // Показываем ошибку с сервера
                errorBox.style.display = 'block';
                // Если ошибка при генерации, оставляем resultView видимым, чтобы показать ошибку
                // initialView.style.display = 'flex'; 
                // resultView.style.display = 'none';
            } finally {
                loader.style.display = 'none';
                submitButton.disabled = false;
            }
        });

        // Добавим обработчики для кнопок-оверлеев (пока просто для примера)
        const overlayButtons = document.querySelectorAll('.overlay-btn');
        overlayButtons.forEach(button => {
            button.addEventListener('click', (e) => {
                const action = e.target.dataset.action;
                console.log("Action button clicked:", action);
                // Здесь в будущем будет логика для этих кнопок
                // alert("Нажата кнопка: " + action); 
            });
        });

    </script>
</body>
</html>
"""

# Маршрут для главной страницы
@app.route('/')
def index():
    return render_template_string(INDEX_HTML)

# Python-часть для обработки запросов (остается без изменений от последней рабочей версии)
def improve_prompt_with_openai(user_prompt):
    if not OPENAI_API_KEY:
        print("OpenAI API ключ не настроен, возвращаем оригинальный промпт.")
        return user_prompt
    try:
        completion = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are an expert prompt engineer for an image editing AI. A user will provide a request, possibly in any language, to modify an existing uploaded image. Your tasks are: 1. Understand the user's core intent for image modification. 2. Translate the request to concise and clear English if it's not already. 3. Rephrase it into a descriptive prompt focusing on visual attributes of the desired *final state* of the image. This prompt will be given to an AI that modifies the uploaded image based on this prompt. Be specific. For example, instead of 'make it better', describe *how* to make it better visually. The output should be only the refined prompt, no explanations or conversational fluff."},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.5, max_tokens=100
        )
        improved_prompt = completion.choices[0].message.content.strip()
        print(f"!!! Оригинальный промпт: {user_prompt}")
        print(f"!!! Улучшенный промпт: {improved_prompt}")
        return improved_prompt
    except Exception as e:
        print(f"Ошибка при обращении к OpenAI: {e}")
        return user_prompt

@app.route('/process-image', methods=['POST'])
def process_image():
    if 'image' not in request.files or 'prompt' not in request.form:
        return jsonify({'error': 'Отсутствует изображение или промпт'}), 400

    image_file = request.files['image']
    original_prompt_text = request.form['prompt']
    final_prompt_text = improve_prompt_with_openai(original_prompt_text)
    
    model_version_id = "black-forest-labs/flux-kontext-max:0b9c317b23e79a9a0d8b9602ff4d04030d433055927fb7c4b91c44234a6818c4"
    
    try:
        s3_client = boto3.client('s3', region_name=AWS_S3_REGION, aws_access_key_id=AWS_ACCESS_KEY_ID, aws_secret_access_key=AWS_SECRET_ACCESS_KEY)
        # Генерируем уникальное имя файла, чтобы избежать перезаписи в S3
        _, f_ext = os.path.splitext(image_file.filename)
        object_name = f"{uuid.uuid4()}{f_ext}" # Используем расширение исходного файла
        
        s3_client.upload_fileobj(
            image_file.stream, # Передаем поток байтов
            AWS_S3_BUCKET_NAME,
            object_name
        )
        
        hosted_image_url = f"https://{AWS_S3_BUCKET_NAME}.s3.{AWS_S3_REGION}.amazonaws.com/{object_name}"
        print(f"!!! Изображение загружено на Amazon S3: {hosted_image_url}")

        headers = {"Authorization": f"Bearer {REPLICATE_API_TOKEN}", "Content-Type": "application/json"}
        post_payload = {
            "version": model_version_id,
            "input": {"input_image": hosted_image_url, "prompt": final_prompt_text}
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
            
            print(f"Статус генерации Replicate: {status_data['status']}")
            
            if status_data["status"] == "succeeded":
                if isinstance(status_data["output"], list): output_url = status_data["output"][0] 
                else: output_url = str(status_data["output"])
                break
            elif status_data["status"] in ["failed", "canceled"]:
                error_detail = status_data.get('error', 'неизвестная ошибка Replicate')
                raise Exception(f"Генерация Replicate не удалась: {error_detail}")
        
        if not output_url:
            return jsonify({'error': 'Генерация Replicate заняла слишком много времени или не вернула результат.'}), 500
            
        return jsonify({'output_url': output_url})
        
    except Exception as e:
        print(f"!!! ОШИБКА:\n{e}")
        return jsonify({'error': 'Произошла внутренняя ошибка сервера. Пожалуйста, проверьте логи на Render для деталей.'}), 500

if __name__ == '__main__':
    # Эта строка нужна для локального запуска, Render будет использовать Gunicorn из Procfile
    app.run(debug=True, port=int(os.environ.get("PORT", 5001)))
