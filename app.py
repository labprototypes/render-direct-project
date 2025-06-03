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
            font-family: 'ChangerFont';
            src: url("{{ url_for('static', filename='fonts/FONT_TEXT.woff2') }}") format('woff2');
            font-weight: normal;
            font-style: normal;
        }

        :root {
            --text-accent-color: #D9F47A;
            --controls-bg-color: #F8F8F8;
            --primary-blue-text: #192E8C; /* Не используется в новом дизайне, но оставим */
            --mob-spacing-unit: 20px;
            --desktop-spacing-unit: 30px; /* Для десктопных отступов */
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'ChangerFont', sans-serif;
            color: var(--text-accent-color);
            background-size: cover;
            background-position: center center;
            background-repeat: no-repeat;
            background-attachment: fixed;
            display: flex;
            flex-direction: column;
            min-height: 100vh;
            overflow-x: hidden;
            transition: filter 0.3s ease-in-out;
        }
        body.bg-blur {
            filter: blur(8px); /* Увеличил блюр */
        }

        .app-container {
            width: 100%;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px 15px;
            display: flex;
            flex-direction: column;
            align-items: center;
            flex-grow: 1;
            position: relative;
        }

        .app-header {
            width: 100%;
            padding: 15px 0;
            text-align: center;
            position: absolute;
            top: 20px; /* Отступ лого от верха */
            left: 50%;
            transform: translateX(-50%);
            z-index: 100; 
        }

        .logo {
            height: 35px; /* Размер лого */
        }

        .app-main {
            width: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            flex-grow: 1;
            padding-top: 90px; /* Отступ от логотипа (35px logo + 20px top + 15px padding + 20px margin) */
            gap: var(--desktop-spacing-unit);
        }
        
        .initial-view-elements {
            display: flex;
            flex-direction: column;
            align-items: center;
            width: 100%;
            gap: var(--desktop-spacing-unit); /* Отступ между текстом и кнопками */
        }

        .main-text-display img {
            width: 100%;
            height: auto;
        }
        .desktop-main-text { display: none; } 
        .mobile-main-text { display: block; } 


        .image-drop-area {
            width: 100%;
            max-width: 300px;
            aspect-ratio: 300 / 350;
            background-color: rgba(248, 248, 248, 0.8);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            border-radius: 20px;
            display: flex;
            justify-content: center;
            align-items: center;
            cursor: pointer;
            position: relative;
            overflow: hidden;
            background-size: contain;
            background-repeat: no-repeat;
            background-position: center;
        }
        .mob-drop-placeholder { width: 100%; height: 100%; object-fit: contain; }
        #image-preview-mobile { display: none; width: 100%; height: 100%; object-fit: cover; }

        .action-buttons { /* Контейнер для SVG кнопок */
            display: flex;
            justify-content: center;
            gap: 10px;
            flex-wrap: wrap;
            width: 100%;
            max-width: 500px; /* Ширина на десктопе для 4х кнопок */
             margin-top: var(--desktop-spacing-unit); /* Отступ от основного текста */
        }
        .action-btn img {
            height: 48px; /* Размер кнопок на десктопе */
            cursor: pointer;
            transition: transform 0.2s ease;
        }
        .action-btn img:hover { transform: scale(1.05); }

        .result-view {
            width: 100%;
            display: none; 
            flex-direction: column;
            align-items: center;
            justify-content: center;
            flex-grow: 1;
            padding-bottom: 120px; /* Место для футера */
            position: relative; 
        }
        #result-image {
            max-width: 90%; /* Чтобы не прилипало к краям контейнера */
            max-height: 60vh;
            object-fit: contain;
            border-radius: 12px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.2);
        }
        .download-action-link { 
             display: none;
             position: absolute;
             top: 10px;  /* Отступ от верха картинки/result-view */
             right: 10px; /* Отступ от правого края картинки/result-view */
             z-index: 10;
             cursor: pointer;
             background-color: rgba(0,0,0,0.3); /* полупрозрачный фон для видимости */
             border-radius: 50%;
             padding: 5px;
        }
        .download-button-icon {
            height: 24px; 
            width: 24px;
            display: block; /* чтобы padding работал корректно */
            filter: invert(1); /* делаем иконку белой, если она темная */
        }
        .download-action-link:hover .download-button-icon { opacity: 0.8; }
        
        .loader-container {
            display: none;
            justify-content: center;
            align-items: center;
            position: absolute; 
            top: 0; left: 0; width: 100%; height: 100%;
            z-index: 101; /* Выше чем header */
        }
        .pulsating-dot { /* УВЕЛИЧЕНА ТОЧКА */
            width: 80px; 
            height: 80px; 
            background-color: var(--text-accent-color);
            border-radius: 50%;
            animation: pulse 1.5s infinite ease-in-out;
        }
        @keyframes pulse {
            0%, 100% { transform: scale(0.8); opacity: 0.7; }
            50% { transform: scale(1.2); opacity: 1; }
        }

        .app-footer {
            width: 100%;
            max-width: 700px; /* Ширина инпут-бара на десктопе */
            padding: 15px;
            position: fixed; 
            bottom: 30px; /* Отступ снизу на десктопе */
            left: 50%;
            transform: translateX(-50%);
            z-index: 100; /* Выше чем фон */
        }
        .input-area {
            display: flex;
            align-items: center;
            background-color: rgba(248, 248, 248, 0.8); 
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            border-radius: 30px; /* Более овальная */
            padding: 10px 15px;
            width: 100%;
            box-shadow: 0 4px 15px rgba(0,0,0,0.1);
        }
        #image-file-common { display: none; }

        .file-upload-label-desktop { 
            cursor: pointer;
            padding: 0; 
            margin-right: 12px; /* Отступ от поля промпта */
            display: flex; 
            align-items: center;
            justify-content: center;
            position: relative;
            width: 80px; height: 80px; /* УВЕЛИЧЕН РАЗМЕР ПРЕВЬЮ */
            background-color: var(--input-icon-bg);
            border-radius: 18px; /* Более скругленный */
            flex-shrink: 0;
            overflow: hidden;
        }
        .upload-icon-desktop { 
            height: 40px; /* Увеличена иконка */
            width: 40px;
            display: block;
        }
        #image-preview-desktop {
            display: none; width: 100%; height: 100%; object-fit: cover; 
        }

        #prompt {
            flex-grow: 1;
            border: none;
            padding: 15px 10px; /* Увеличил вертикальный паддинг */
            font-size: 1rem;
            background-color: transparent;
            outline: none;
            color: #333; 
        }
        #prompt::placeholder { color: #888; }

        .magic-button {
            background-color: transparent;
            border: none;
            cursor: pointer;
            padding: 0; 
            margin-left: 12px; /* Отступ от поля промпта */
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .magic-button img { height: 48px;  }


        .error-message {
            display: none; /* Скрыто по умолчанию */
            margin-top: 10px;
            font-size: 0.9rem;
            color: #d93025;
            background-color: rgba(255,221,221,0.8);
            backdrop-filter: blur(5px);
            padding: 10px;
            border-radius: 8px;
            position: fixed; 
            bottom: 120px; /* над полем ввода, с запасом */
            left: 50%;
            transform: translateX(-50%);
            width: calc(100% - 30px);
            max-width: 480px;
            z-index: 90; /* Ниже чем футер и лоадер */
        }
        
        /* --- Десктопная версия --- */
        @media (min-width: 769px) {
            body {
                background-image: url("{{ url_for('static', filename='images/DESK_BACK.png') }}");
            }
            .desktop-main-text { display: block; }
            .mobile-main-text { display: none; }
            .main-text-display.desktop-main-text img { max-width: 650px; } /* УМЕНЬШЕН ТЕКСТ */
            
            .image-drop-area { display: none; } 
            .file-upload-label-desktop { display: flex; } 

            .action-buttons { display: flex; } /* Показываем кнопки на десктопе */

        }
        /* Мобильная версия (основные стили уже mobile-first) */
        @media (max-width: 768px) {
            body {
                background-image: url("{{ url_for('static', filename='images/MOB_BACK.png') }}");
            }
            .app-header { top: 15px;}
            .logo { height: 30px; }
             .app-main {
                justify-content: space-around; /* Распределение пространства на мобильном */
                padding-top: 60px; 
                padding-bottom: 100px; 
                gap: var(--mob-spacing-unit);
            }
            .initial-view-elements {
                justify-content: flex-start; 
                flex-grow: 0; 
                gap: var(--mob-spacing-unit);
            }
            .main-text-display.mobile-main-text { margin-bottom: 0; } /* Отступ управляется gap родителя */
             .action-buttons { margin-top: 0; } /* Отступ управляется gap родителя */
             .action-btn img { height: 38px; }


            .file-upload-label-desktop { display: none; } 
            .image-drop-area { display: flex; } /* Показываем мобильную зону загрузки */


            .app-footer { max-width: calc(100% - 30px); bottom: 15px; }
            .input-area { padding: 6px 8px; border-radius: 20px;}
            #prompt { font-size: 0.85rem; padding: 10px 8px;}
            .magic-button img { height: 38px; }
            .file-upload-label-desktop { /* Стили для десктопной иконки не нужны тут, они скрыты*/ }
        }
    </style>
</head>
<body>
    <div class="app-container">
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

            <div class="result-view">
                <img id="result-image" src="" alt="Generated Image">
                <a href="#" id="download-action" class="download-action-link" download="generated_image.png">
                    <img src="{{ url_for('static', filename='images/Download.png') }}" alt="Download" class="download-button-icon">
                </a>
            </div>
            
            <div id="loader" class="loader-container">
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
        const downloadLink = document.getElementById('download-action');
        
        const loader = document.getElementById('loader');
        const errorBox = document.getElementById('error-box');
        const submitButton = document.getElementById('submit-button'); 
        const magicButtonIcon = document.getElementById('magic-button-icon'); // Если нужно будет менять иконку
        const promptInput = document.getElementById('prompt');

        function isDesktopView() {
            return window.innerWidth > 768;
        }

        // Показываем/скрываем нужные элементы при загрузке и ресайзе
        function handleViewElementsDisplay() {
            if (isDesktopView()) {
                document.querySelector('.desktop-main-text').style.display = 'block';
                document.querySelector('.mobile-main-text').style.display = 'none';
                if (mobileDropArea) mobileDropArea.style.display = 'none'; // Скрываем мобильную зону на десктопе
                if (desktopUploadLabel) desktopUploadLabel.style.display = 'flex'; // Показываем десктопную
            } else {
                document.querySelector('.desktop-main-text').style.display = 'none';
                document.querySelector('.mobile-main-text').style.display = 'block';
                if (mobileDropArea) mobileDropArea.style.display = 'flex'; // Показываем мобильную зону
                if (desktopUploadLabel) desktopUploadLabel.style.display = 'none'; // Скрываем десктопную
            }
        }
        
        handleViewElementsDisplay(); // При первой загрузке
        window.addEventListener('resize', handleViewElementsDisplay);


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
                    if (isDesktopView()) {
                        if (desktopImagePreview) {
                            desktopImagePreview.src = e.target.result;
                            desktopImagePreview.style.display = 'block';
                        }
                        if (desktopUploadIcon) desktopUploadIcon.style.display = 'none';
                    } else {
                        if (mobileImagePreview) {
                            mobileImagePreview.src = e.target.result;
                            mobileImagePreview.style.display = 'block';
                        }
                        if (mobileDropPlaceholder) mobileDropPlaceholder.style.display = 'none';
                    }
                }
                reader.readAsDataURL(this.files[0]);
            } else {
                resetPreview();
            }
        });

        function resetPreview() {
            if (mobileImagePreview && mobileDropPlaceholder) {
                mobileImagePreview.src = '#';
                mobileImagePreview.style.display = 'none';
                mobileDropPlaceholder.style.display = 'block';
            }
            if (desktopImagePreview && desktopUploadIcon) {
                desktopImagePreview.src = '#';
                desktopImagePreview.style.display = 'none';
                desktopUploadIcon.style.display = 'block';
            }
            imageFileInput.value = ''; 
        }
        
        function showLoader(isLoading) {
            if (isLoading) {
                loader.style.display = 'flex';
                document.body.classList.add('bg-blur');
                if (initialViewElements) initialViewElements.style.display = 'none';
                if (resultView) resultView.style.display = 'none';
                if(downloadLink) downloadLink.style.display = 'none';
            } else {
                loader.style.display = 'none';
                // Блюр убирается только при возврате к начальному виду или при ошибке (если не показываем результат)
            }
        }
        
        function showInitialView() {
            if (initialViewElements) initialViewElements.style.display = 'flex';
            if (resultView) resultView.style.display = 'none';
            if(downloadLink) downloadLink.style.display = 'none';
            submitButton.dataset.action = "generate";
            promptInput.value = '';
            resetPreview();
            document.body.classList.remove('bg-blur'); 
            handleViewElementsDisplay(); // Восстанавливаем отображение текста/загрузчика
        }

        editForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            
            if (submitButton.dataset.action === "startover") { // Логика для "Start Over"
                showInitialView();
                return;
            }

            submitButton.disabled = true;
            errorBox.style.display = 'none';
            errorBox.textContent = ''; 
            showLoader(true);

            const formData = new FormData();
            if (!imageFileInput.files || imageFileInput.files.length === 0) {
                errorBox.textContent = "Пожалуйста, выберите файл для загрузки.";
                errorBox.style.display = 'block';
                showLoader(false);
                document.body.classList.remove('bg-blur'); 
                if (initialViewElements) initialViewElements.style.display = 'flex'; 
                if (resultView) resultView.style.display = 'none';
                submitButton.disabled = false;
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
                // Блюр НЕ убираем здесь, если хотим его оставить при показе результата
                showLoader(false); 
                
                if (resultView) resultView.style.display = 'flex';
                resultImage.src = data.output_url;
                resultImage.style.display = 'block';
                if(downloadLink) {
                    downloadLink.href = data.output_url;
                    downloadLink.style.display = 'block'; 
                }
                
                if (window.innerWidth <= 768) { 
                     submitButton.dataset.action = "startover";
                     // Тут можно было бы поменять иконку MAGIC_GREEN.png на иконку "Start Over"
                     // magicButtonIcon.src = "новый_путь_к_иконке_start_over.png";
                }

            } catch (error) {
                console.error('Ошибка:', error);
                showLoader(false); 
                document.body.classList.remove('bg-blur'); // Убираем блюр при ошибке
                errorBox.textContent = "Произошла ошибка: " + error.message;
                errorBox.style.display = 'block';
                if (resultView) resultView.style.display = 'flex'; 
            } finally {
                submitButton.disabled = false;
            }
        });

        const actionButtons = document.querySelectorAll('.action-btn');
        actionButtons.forEach(button => {
            button.addEventListener('click', (e) => {
                const currentTarget = e.currentTarget; 
                const action = currentTarget.dataset.action; // Если у div.action-btn есть data-action
                console.log("Action button clicked:", action);
            });
        });
        
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
