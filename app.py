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
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>Changer AI</title>
    <style>
        @font-face {
            font-family: 'ChangerFont'; /* Используем это имя в CSS */
            src: url("{{ url_for('static', filename='fonts/FONT_TEXT.woff2') }}") format('woff2');
            font-weight: normal;
            font-style: normal;
        }

        :root {
            --text-accent-color: #D9F47A; /* Зелено-желтый */
            --controls-bg-color: #F8F8F8; /* Светло-серый/белый */
            --primary-blue-text: #192E8C; /* Для старого варианта, если понадобится */
            
            /* Размеры отступов для мобильной версии (базовый) */
            --mob-spacing-unit: 20px; /* Можно подбирать */
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'ChangerFont', sans-serif;
            color: var(--text-accent-color); /* Основной цвет текста теперь D9F47A */
            background-size: cover;
            background-position: center center;
            background-repeat: no-repeat;
            background-attachment: fixed; /* Фон не скроллится */
            display: flex;
            flex-direction: column;
            min-height: 100vh;
            overflow-x: hidden; /* Предотвратить горизонтальный скролл */
            transition: filter 0.3s ease-in-out; /* Для блюра фона */
        }
        body.bg-blur {
            filter: blur(5px); /* Блюр для фона во время загрузки */
        }

        .app-container {
            width: 100%;
            max-width: 1200px; /* Общий максимальный контейнер для десктопа */
            margin: 0 auto;
            padding: 20px 15px;
            display: flex;
            flex-direction: column;
            align-items: center;
            flex-grow: 1; /* Занимает все доступное пространство по высоте */
            position: relative; /* Для позиционирования лоадера */
        }

        .app-header {
            width: 100%;
            padding: 15px 0; /* Отступы для лого */
            text-align: center;
            position: absolute; /* Поверх фона */
            top: 20px;
            left: 50%;
            transform: translateX(-50%);
            z-index: 10;
        }

        .logo {
            height: 30px; /* Подберите по макету */
        }

        .app-main {
            width: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center; /* Центрируем контент */
            flex-grow: 1;
            padding-top: 80px; /* Отступ от логотипа */
            gap: var(--mob-spacing-unit); /* Базовый отступ между блоками */
        }
        
        /* --- Начальный вид --- */
        .initial-view-elements {
            display: flex;
            flex-direction: column;
            align-items: center;
            width: 100%;
            gap: var(--mob-spacing-unit);
        }

        .main-text-display img {
            width: 100%;
            height: auto;
        }
        .desktop-main-text { display: none; } /* Скрыт по умолчанию, показывается через JS или медиа-запрос */
        .mobile-main-text { display: block; } /* Показан по умолчанию для mobile-first */


        .image-drop-area { /* Для MOB_DROP.png */
            width: 100%;
            max-width: 300px; /* Подберите по макету MOB_DROP */
            aspect-ratio: 300 / 350; /* Примерное соотношение сторон MOB_DROP, подберите */
            background-color: rgba(248, 248, 248, 0.8); /* F8F8F8 с 80% непрозрачности */
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            border-radius: 20px; /* Скругление как на макете */
            display: flex;
            justify-content: center;
            align-items: center;
            cursor: pointer;
            position: relative;
            overflow: hidden;
            background-size: contain; /* Для MOB_DROP.png как фона */
            background-repeat: no-repeat;
            background-position: center;
        }
        .mob-drop-placeholder { /* Сам MOB_DROP.png */
            width: 100%;
            height: 100%;
            object-fit: contain;
        }
        #image-preview-mobile { /* Миниатюра */
            display: none;
            width: 100%;
            height: 100%;
            object-fit: cover; /* Чтобы заполняла область */
        }

        .action-buttons {
            display: flex;
            justify-content: center;
            gap: 10px; /* Отступ между кнопками */
            flex-wrap: wrap; /* Перенос на новую строку если не влезают */
            width: 100%;
            max-width: 340px; /* Ограничиваем ширину для мобильных */
        }
        .action-btn img {
            height: 40px; /* Примерная высота кнопок, подберите */
            cursor: pointer;
            transition: transform 0.2s ease;
        }
        .action-btn img:hover {
            transform: scale(1.05);
        }

        /* --- Область результата --- */
        .result-view {
            width: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center; /* Центрирование по вертикали */
            flex-grow: 1; /* Занять доступное пространство */
            padding-bottom: 100px; /* Место для нижнего контрола */
        }
        #result-image {
            max-width: 100%;
            max-height: 60vh; /* Ограничение высоты */
            object-fit: contain;
            border-radius: 12px; /* Скругление для картинки */
            box-shadow: 0 4px 15px rgba(0,0,0,0.1);
        }
        #download-button { /* Пока скрыта, но стилизуем */
             display: none;
             margin-top: 20px;
             height: 40px;
             cursor: pointer;
        }
        
        /* --- Лоадер --- */
        .loader-container {
            display: flex;
            justify-content: center;
            align-items: center;
            position: absolute; /* Поверх всего */
            top: 0; left: 0; width: 100%; height: 100%;
            z-index: 20;
        }
        .pulsating-dot {
            width: 20px;
            height: 20px;
            background-color: var(--text-accent-color);
            border-radius: 50%;
            animation: pulse 1.5s infinite ease-in-out;
        }
        @keyframes pulse {
            0%, 100% { transform: scale(0.8); opacity: 0.7; }
            50% { transform: scale(1.2); opacity: 1; }
        }

        /* --- Нижняя панель ввода --- */
        .app-footer {
            width: 100%;
            max-width: 500px; /* Ширина для десктопа */
            padding: 15px;
            position: fixed; /* Фиксируем внизу */
            bottom: 20px; /* Отступ снизу */
            left: 50%;
            transform: translateX(-50%);
            z-index: 10;
        }
        .input-area {
            display: flex;
            align-items: center;
            background-color: rgba(248, 248, 248, 0.8); /* F8F8F8 с 80% */
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            border-radius: 25px; /* Более овальная форма */
            padding: 8px 10px;
            width: 100%;
            box-shadow: 0 4px 15px rgba(0,0,0,0.1);
        }
        /* Скрытый инпут для файлов */
        #image-file-common, #image-file-desktop-actual { display: none; }

        .file-upload-label-desktop { /* Иконка загрузки для десктопа */
            cursor: pointer;
            padding: 8px;
            margin-right: 8px;
            display: none; /* Показывается только на десктопе */
            align-items: center;
            justify-content: center;
            position: relative;
            width: 44px; height: 44px; /* Размер как у кнопки Magic */
            background-color: var(--input-icon-bg);
            border-radius: 50%;
        }
        .upload-icon-desktop { height: 24px; }
        #image-preview-desktop {
            display: none; width: 100%; height: 100%; object-fit: cover; border-radius: 50%;
        }


        #prompt {
            flex-grow: 1;
            border: none;
            padding: 10px;
            font-size: 0.9rem;
            background-color: transparent;
            outline: none;
            color: #333; /* Темный цвет для контраста на светлом фоне */
        }
        #prompt::placeholder { color: #888; }

        .magic-button {
            background-color: transparent;
            border: none;
            cursor: pointer;
            padding: 0; /* Убираем внутренние отступы, т.к. сама картинка кнопка */
            margin-left: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .magic-button img { height: 44px; /* Размер кнопки */ }


        .error-message {
            margin-top: 10px;
            font-size: 0.9rem;
            color: #d93025;
            background-color: rgba(255,221,221,0.8);
            backdrop-filter: blur(5px);
            padding: 10px;
            border-radius: 8px;
            position: fixed; /* поверх всего */
            bottom: 100px; /* над полем ввода */
            left: 50%;
            transform: translateX(-50%);
            width: calc(100% - 30px);
            max-width: 480px;
            z-index: 5;
        }
        
        /* --- Десктопная версия --- */
        @media (min-width: 769px) {
            body {
                /* DESK_BACK.png as background */
                background-image: url("{{ url_for('static', filename='images/DESK_BACK.png') }}");
            }
            .app-header { top: 30px; }
            .logo { height: 35px; } /* Лого на десктопе поменьше, т.к. есть фон */
            .app-main { padding-top: 100px; gap: 30px; } /* Больше отступ от лого, меньше gap */

            .desktop-main-text { display: block; }
            .mobile-main-text { display: none; }
            .main-text-display.desktop-main-text img { max-width: 700px; /* Размер для DESK_MAIN.png */ }
            
            .image-drop-area { display: none; } /* Скрываем мобильную зону загрузки */

            .action-buttons-wrapper { max-width: 500px; /* Ширина для 4х кнопок в ряд */ }
            .action-buttons { gap: 15px; }
            .action-btn img { height: 48px; }

            .app-footer { max-width: 700px; bottom: 30px; }
            .input-area { padding: 10px 15px; border-radius: 30px;}
            .file-upload-label-desktop { display: flex; } /* Показываем десктопную иконку */
            #prompt { font-size: 1rem; }
            .magic-button img { height: 48px; }

            .result-view { 
                max-width: 800px; /* Больше для десктопа */
                max-height: 65vh; 
                padding-bottom: 80px; /* Место для нижнего контрола */
                 /* Положение как на Changer_Desktop_NEW3/4 - сложно без JS, если размеры динамические */
                 /* Пока просто центрируем */
            }
             #result-image { max-height: calc(65vh - 20px); }
        }
        /* Мобильная версия (основные стили уже mobile-first) */
        @media (max-width: 768px) {
            body {
                /* MOB_BACK.png as background */
                background-image: url("{{ url_for('static', filename='images/MOB_BACK.png') }}");
            }
             .app-main {
                justify-content: space-between; /* Равные отступы + прижатие футера */
                padding-top: 70px; /* Отступ от лого */
                padding-bottom: 100px; /* Место для фиксированного футера */
            }
            .initial-view-elements {
                 /* Равные отступы между элементами */
                justify-content: space-around;
                flex-grow: 1; /* Занять все пространство */
            }
            .file-upload-label-desktop { display: none; } /* Скрываем десктопную иконку */
            .app-footer { max-width: calc(100% - 30px); bottom: 15px; }
        }


    </style>
</head>
<body>
    <div class="background-layer"></div> <div class="app-container">
        <header class="app-header">
            <img src="{{ url_for('static', filename='images/change.svg') }}" alt="Changer Logo" class="logo">
        </header>

        <main class="app-main">
            <div class="initial-view-elements">
                <img src="{{ url_for('static', filename='images/DESK_MAIN.png') }}" alt="Change Everything" class="main-text-display desktop-main-text">
                <img src="{{ url_for('static', filename='images/MOB_MAIN.svg') }}" alt="Change Everything" class="main-text-display mobile-main-text">

                <label for="image-file-common" class="image-drop-area">
                    <img src="{{ url_for('static', filename='images/MOB_DROP.png') }}" alt="Just drop the image" class="mob-drop-placeholder">
                    <img id="image-preview-mobile" src="#" alt="Preview">
                </label>

                <div class="action-buttons">
                    <div class="action-btn" data-action="create"><img src="{{ url_for('static', filename='images/Create.svg') }}" alt="Create"></div>
                    <div class="action-btn" data-action="relight"><img src="{{ url_for('static', filename='images/Relight.svg') }}" alt="Relight"></div>
                    <div class="action-btn" data-action="remove"><img src="{{ url_for('static', filename='images/Remove.svg') }}" alt="Remove"></div>
                    <div class="action-btn" data-action="change"><img src="{{ url_for('static', filename='images/Change.svg') }}" alt="Change"></div>
                </div>
            </div>

            <div class="result-view" style="display: none;">
                <img id="result-image" src="" alt="Generated Image">
                <img src="{{ url_for('static', filename='images/Download.png') }}" alt="Download" id="download-button">
            </div>
            
            <div id="loader" class="loader-container" style="display: none;">
                <div class="pulsating-dot"></div>
            </div>
        </main>

        <footer class="app-footer">
            <form id="edit-form" class="input-area">
                <label for="image-file-common" class="file-upload-label-desktop">
                    <img src="{{ url_for('static', filename='images/DESK_UPLOAD.png') }}" alt="Upload Icon" class="upload-icon-desktop">
                    <img id="image-preview-desktop" src="#" alt="Preview">
                </label>
                <input type="file" id="image-file-common" name="image" accept="image/*" required>
                
                <input type="text" id="prompt" name="prompt" placeholder="TYPE WHAT YOU WANT TO CHANGE" required>
                
                <button type="submit" id="submit-button" class="magic-button">
                    <img src="{{ url_for('static', filename='images/MAGIC_GREEN.png') }}" alt="Generate" id="magic-button-icon">
                </button>
            </form>
        </footer>
        <div id="error-box" class="error-message" style="display: none;"></div>
    </div>

    <script>
        const imageFileInput = document.getElementById('image-file-common');
        
        // Элементы для превью
        const mobileDropArea = document.querySelector('.image-drop-area');
        const mobileDropPlaceholder = document.querySelector('.mob-drop-placeholder');
        const mobileImagePreview = document.getElementById('image-preview-mobile');
        
        const desktopUploadLabel = document.querySelector('.file-upload-label-desktop');
        const desktopUploadIcon = document.querySelector('.upload-icon-desktop');
        const desktopImagePreview = document.getElementById('image-preview-desktop');

        const editForm = document.getElementById('edit-form');
        
        const initialViewElements = document.querySelector('.initial-view-elements');
        const resultView = document.querySelector('.result-view');
        const resultImage = document.getElementById('result-image');
        const downloadButton = document.getElementById('download-button');
        
        const loader = document.getElementById('loader');
        const errorBox = document.getElementById('error-box');
        const submitButton = document.getElementById('submit-button'); 
        const magicButtonIcon = document.getElementById('magic-button-icon');
        const promptInput = document.getElementById('prompt');

        // Триггер для общего файлового инпута
        if (mobileDropArea) {
            mobileDropArea.addEventListener('click', () => imageFileInput.click());
        }
        if (desktopUploadLabel) {
            desktopUploadLabel.addEventListener('click', () => imageFileInput.click());
        }

        imageFileInput.addEventListener('change', function() {
            if (this.files && this.files[0]) {
                const reader = new FileReader();
                reader.onload = function(e) {
                    // Обновляем оба превью, CSS скроет ненужное
                    if (mobileImagePreview) {
                        mobileImagePreview.src = e.target.result;
                        mobileImagePreview.style.display = 'block';
                    }
                    if (mobileDropPlaceholder) mobileDropPlaceholder.style.display = 'none';
                    
                    if (desktopImagePreview) {
                        desktopImagePreview.src = e.target.result;
                        desktopImagePreview.style.display = 'block';
                    }
                    if (desktopUploadIcon) desktopUploadIcon.style.display = 'none';
                }
                reader.readAsDataURL(this.files[0]);
            } else {
                resetPreview();
            }
        });

        function resetPreview() {
            if (mobileImagePreview) {
                mobileImagePreview.src = '#';
                mobileImagePreview.style.display = 'none';
            }
            if (mobileDropPlaceholder) mobileDropPlaceholder.style.display = 'block';

            if (desktopImagePreview) {
                desktopImagePreview.src = '#';
                desktopImagePreview.style.display = 'none';
            }
            if (desktopUploadIcon) desktopUploadIcon.style.display = 'block';
            imageFileInput.value = ''; 
        }
        
        function showLoader(isLoading) {
            if (isLoading) {
                loader.style.display = 'flex';
                document.body.classList.add('bg-blur');
                if (initialViewElements) initialViewElements.style.display = 'none';
                if (resultView) resultView.style.display = 'none';
            } else {
                loader.style.display = 'none';
                document.body.classList.remove('bg-blur');
            }
        }
        
        function showInitialView() {
            if (initialViewElements) initialViewElements.style.display = 'flex';
            if (resultView) resultView.style.display = 'none';
            downloadButton.style.display = 'none';
            magicButtonIcon.src = "{{ url_for('static', filename='images/MAGIC_GREEN.png') }}"; // Возвращаем иконку генерации
            submitButton.dataset.action = "generate"; // Устанавливаем действие по умолчанию
            promptInput.value = '';
            resetPreview();
        }

        editForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            
            if (submitButton.dataset.action === "startover") {
                showInitialView();
                return;
            }

            submitButton.disabled = true;
            errorBox.style.display = 'none';
            showLoader(true);

            const formData = new FormData();
            if (!imageFileInput.files || imageFileInput.files.length === 0) {
                errorBox.textContent = "Пожалуйста, выберите файл для загрузки.";
                errorBox.style.display = 'block';
                showLoader(false);
                submitButton.disabled = false;
                // initialViewElements.style.display = 'flex'; 
                // resultView.style.display = 'none';
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
                showLoader(false);
                if (!response.ok) {
                    throw new Error(data.error || 'Неизвестная ошибка сервера');
                }
                if (resultView) resultView.style.display = 'flex';
                resultImage.src = data.output_url;
                resultImage.style.display = 'block';
                downloadButton.href = data.output_url; // Для скачивания
                downloadButton.style.display = 'block'; 
                
                // Меняем кнопку на "Start Over" только для мобильных (по вашей логике)
                if (window.innerWidth <= 768) {
                     magicButtonIcon.src = "{{ url_for('static', filename='images/MAGIC_GREEN.png') }}"; // Предположим, есть иконка "start over" или меняем текст
                     // Если хотите текст "Start Over", то нужно будет кнопку сделать текстовой или добавить текст рядом с иконкой.
                     // Пока что просто меняем действие. Для обновления страницы.
                     submitButton.dataset.action = "startover";
                }


            } catch (error) {
                console.error('Ошибка:', error);
                showLoader(false);
                errorBox.textContent = "Произошла ошибка: " + error.message;
                errorBox.style.display = 'block';
                if (resultView) resultView.style.display = 'flex'; // Показываем блок с ошибкой
            } finally {
                submitButton.disabled = false;
            }
        });

        const actionButtons = document.querySelectorAll('.action-btn');
        actionButtons.forEach(button => {
            button.addEventListener('click', (e) => {
                const currentTarget = e.currentTarget; // Используем currentTarget
                const action = currentTarget.dataset.action;
                console.log("Action button clicked:", action);
                // alert("Нажата кнопка: " + action); 
            });
        });
        
        // Клик по логотипу для сброса (как один из вариантов "Start Over" для всех)
        const logo = document.querySelector('.logo');
        if (logo) {
            logo.addEventListener('click', () => {
                showInitialView();
            });
        }

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
        _, f_ext = os.path.splitext(image_file.filename)
        object_name = f"{uuid.uuid4()}{f_ext}"
        
        s3_client.upload_fileobj(image_file.stream, AWS_S3_BUCKET_NAME, object_name)
        
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
    app.run(debug=True, port=int(os.environ.get("PORT", 5001)))
