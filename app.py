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
            --background-color: #FAF3D4; /* Примерный бежевый */
            --primary-blue: #192E8C;    /* Примерный синий */
            --input-area-bg: #FFFFFF;
            --input-icon-bg: #F0F0F0; /* Светло-серый для иконки загрузки */
            --text-placeholder: #757575;
            --button-text-color: var(--primary-blue);
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
            padding: 20px;
            text-align: center;
        }

        .app-container {
            width: 100%;
            max-width: 900px; /* Максимальная ширина для десктопа */
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 30px; /* Расстояние между основными блоками */
        }

        .app-header {
            margin-bottom: 10px; /* Уменьшил отступ снизу лого */
        }

        .logo {
            height: 40px; /* Примерная высота, подберите по макету */
        }

        .app-main {
            width: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 30px;
        }

        .initial-view {
            width: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 30px;
        }

        .main-text img {
            width: 100%;
            max-width: 700px; /* Ограничение для десктопа */
            height: auto;
        }

        .desktop-main-text { display: block; }
        .mobile-main-text { display: none; }

        .action-buttons {
            display: flex;
            justify-content: center;
            gap: 15px;
            flex-wrap: wrap;
        }

        .action-btn {
            background-image: url("{{ url_for('static', filename='images/bubble.svg') }}");
            background-size: 100% 100%;
            background-repeat: no-repeat;
            background-color: transparent;
            border: none;
            color: var(--button-text-color);
            padding: 12px 28px; /* Примерные отступы, подберите по bubble.svg */
            font-family: 'ChangerFont', sans-serif;
            font-size: 1rem; /* 16px */
            cursor: pointer;
            min-width: 130px; /* Минимальная ширина */
            text-align: center;
            line-height: 1.2; /* Для центрирования текста, если нужно */
        }
        .action-btn:hover {
            opacity: 0.8;
        }

        .result-view {
            width: 100%;
            /* Changer_Desktop_2: Максимальная высота файла как на изображении */
            /* Находится на равном расстоянии от логотипа и поля ввода - это будет сложно сделать чисто CSS без JS */
            /* Попробуем задать максимальную высоту и отступы */
            max-height: 60vh; /* Пример */
            margin-top: 20px;
            margin-bottom: 20px;
            display: flex;
            justify-content: center;
            align-items: center;
            background-color: #E9E9E9; /* Серый фон как на макете */
            border-radius: 8px; /* Небольшое скругление */
        }

        #result-image {
            max-width: 100%;
            max-height: 100%; /* Чтобы вписывалась в контейнер */
            object-fit: contain; /* Сохраняет пропорции */
            border-radius: 4px;
        }
        
        .input-area-wrapper { /* Новый враппер для отступов */
            width: 100%;
            display: flex;
            justify-content: center;
            padding: 0 20px; /* Отступы по бокам для мобильных */
        }

        .input-area {
            display: flex;
            align-items: center;
            background-color: var(--input-area-bg);
            border-radius: 12px;
            padding: 8px; /* Уменьшил общий паддинг */
            width: 100%;
            max-width: 600px; /* Максимальная ширина инпут-области */
            box-shadow: 0 2px 5px rgba(0,0,0,0.05);
        }

        #image-file {
            display: none; /* Скрываем стандартный input type="file" */
        }

        .file-upload-label {
            cursor: pointer;
            padding: 12px; /* Отступы внутри "кнопки" загрузки */
            background-color: var(--input-icon-bg);
            border-radius: 8px;
            margin-right: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0; /* Чтобы не сжималась */
        }

        .upload-icon {
            height: 28px; /* Размер иконки */
            width: 28px;
        }
        
        #file-name-display {
            margin-left: 10px;
            font-size: 0.8em;
            color: var(--text-placeholder);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            max-width: 120px; /* Ограничение для имени файла */
        }


        #prompt {
            flex-grow: 1;
            border: none;
            padding: 15px 10px;
            font-size: 1rem;
            background-color: transparent;
            outline: none;
            color: var(--primary-blue);
        }
        #prompt::placeholder {
            color: var(--text-placeholder);
            opacity: 0.7;
        }

        .magic-button {
            background-color: transparent;
            border: none;
            cursor: pointer;
            padding: 10px; /* Отступы для кнопки "магии" */
            margin-left: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }

        .magic-button img {
            height: 30px; /* Размер иконки "магии" */
            width: 30px;
        }
        .magic-button:hover img {
            opacity: 0.8;
        }

        .loader-message, .error-message {
            margin-top: 20px;
            font-size: 1rem;
        }
        .error-message {
            color: #d93025; /* Красный для ошибок */
            background-color: #fdd;
            padding: 10px;
            border-radius: 8px;
        }

        /* Мобильная версия */
        @media (max-width: 768px) {
            .app-container, .app-main, .initial-view {
                gap: 20px; /* Уменьшаем отступы */
            }
            .logo {
                height: 30px;
            }
            .desktop-main-text { display: none; }
            .mobile-main-text { display: block; }
            .mobile-main-text img {
                max-width: 100%; /* На всю ширину */
            }

            .action-buttons {
                flex-direction: row; /* Кнопки в ряд и переносятся */
                gap: 10px;
                justify-content: center;
            }
            .action-btn {
                font-size: 0.9rem;
                padding: 10px 20px; /* Чуть меньше кнопки */
                min-width: auto; /* Авто ширина для мобильных */
                flex-basis: calc(50% - 5px); /* Две кнопки в ряд */
            }

            .input-area-wrapper {
                padding: 0 10px; /* Меньше отступы по бокам */
            }
            .input-area {
                padding: 10px; /* Внутренний паддинг */
                max-width: 100%;
            }
            .file-upload-label {
                padding: 10px;
            }
            .upload-icon { height: 24px; width: 24px; }
            #prompt { padding: 12px 8px; font-size: 0.9rem;}
            .magic-button { padding: 8px; }
            .magic-button img { height: 26px; width: 26px; }
            
            #file-name-display { display: none; } /* Скроем имя файла на мобильных для экономии места */

            .result-view {
                /* Changer_MOB_2: По горизонтали файл не может быть шире чем левая и правая грани поля ввода. */
                /* По высоте не может доходить до нижней границы логитипа и верхней границы поля ввода */
                width: calc(100% - 20px); /* С учетом отступов .input-area-wrapper */
                max-width: calc(100% - 20px);
                max-height: 50vh; /* Примерное ограничение по высоте */
                margin: 15px 0;
            }
        }
         @media (max-width: 400px) {
             .action-btn {
                flex-basis: 100%; /* Одна кнопка в ряд на очень маленьких экранах */
            }
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

                <nav class="action-buttons">
                    <button class="action-btn">CREATE</button>
                    <button class="action-btn">RELIGHT</button>
                    <button class="action-btn">REMOVE</button>
                    <button class="action-btn">CHANGE</button>
                </nav>
            </div>

            <div class="result-view" style="display: none;">
                <img id="result-image" src="" alt="Generated Image">
            </div>
            
            <div class="input-area-wrapper">
                <form id="edit-form" class="input-area">
                    <label for="image-file" class="file-upload-label">
                        <img src="{{ url_for('static', filename='images/Icon.png') }}" alt="Upload Icon" class="upload-icon">
                        <span id="file-name-display"></span>
                    </label>
                    <input type="file" id="image-file" accept="image/*" required>
                    
                    <input type="text" id="prompt" placeholder="TYPE WHAT YOU WANT TO CHANGE" required>
                    
                    <button type="submit" id="submit-button" class="magic-button">
                        <img src="{{ url_for('static', filename='images/Magic.png') }}" alt="Generate">
                    </button>
                </form>
            </div>

            <div id="loader" class="loader-message" style="display: none;">Обработка... это может занять больше времени ⏳</div>
            <div id="error-box" class="error-message" style="display: none;"></div>
        </main>
    </div>

    <script>
        const imageFileInput = document.getElementById('image-file');
        const fileNameDisplay = document.getElementById('file-name-display');
        const editForm = document.getElementById('edit-form');
        
        const initialView = document.querySelector('.initial-view');
        const resultView = document.querySelector('.result-view');
        const resultImage = document.getElementById('result-image');
        
        const loader = document.getElementById('loader');
        const errorBox = document.getElementById('error-box');
        const submitButton = document.getElementById('submit-button');

        imageFileInput.addEventListener('change', function() {
            if (this.files && this.files.length > 0) {
                // Показываем имя файла, если нужно (на десктопе)
                if (window.innerWidth > 768) {
                     fileNameDisplay.textContent = this.files[0].name;
                } else {
                    fileNameDisplay.textContent = ''; // Скрываем на мобильных, если там нет места
                }
            } else {
                fileNameDisplay.textContent = '';
            }
        });

        editForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            const promptInput = document.getElementById('prompt');
            
            submitButton.disabled = true;
            loader.style.display = 'block';
            resultImage.src = '';
            resultImage.style.display = 'none';
            errorBox.style.display = 'none';
            errorBox.textContent = ''; 
            
            // Прячем начальный вид и показываем контейнер результата (пока пустой)
            initialView.style.display = 'none';
            resultView.style.display = 'flex'; // Используем flex для центрирования изображения

            const formData = new FormData();
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
                errorBox.textContent = "Произошла ошибка. Попробуйте еще раз.";
                errorBox.style.display = 'block';
                // Если ошибка, возможно, стоит вернуть начальный вид? Или оставить как есть.
                // initialView.style.display = 'flex'; 
                // resultView.style.display = 'none';
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
    # Если пользователь захочет начать заново, он просто обновит страницу, 
    # или мы можем добавить кнопку "New" которая делает GET запрос на '/'
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
        object_name = f"{uuid.uuid4()}-{image_file.filename}"
        
        s3_client.upload_fileobj(
            image_file,
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

if __name__ == '__main__': # Это для локального запуска, Render использует Gunicorn
    app.run(debug=True, port=os.getenv("PORT", 5000))
