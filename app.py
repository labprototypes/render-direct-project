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
            --primary-blue-text: #192E8C; 
            --mob-spacing-unit: 20px; 
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
            filter: blur(5px); 
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
            top: 20px;
            left: 50%;
            transform: translateX(-50%);
            z-index: 100; /* Увеличил z-index */
        }

        .logo {
            height: 30px; /* Уменьшил немного для баланса с фоном */
        }

        .app-main {
            width: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center; 
            flex-grow: 1;
            padding-top: 70px; /* Отступ от логотипа */
            gap: var(--mob-spacing-unit); 
        }
        
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
        .mob-drop-placeholder { 
            width: 100%;
            height: 100%;
            object-fit: contain;
        }
        #image-preview-mobile { 
            display: none;
            width: 100%;
            height: 100%;
            object-fit: cover; 
        }

        .action-buttons {
            display: flex;
            justify-content: center;
            gap: 10px; 
            flex-wrap: wrap; 
            width: 100%;
            max-width: 340px; 
            margin-top: var(--mob-spacing-unit); /* Отступ от image-drop-area */
        }
        .action-btn img {
            height: 40px; 
            cursor: pointer;
            transition: transform 0.2s ease;
        }
        .action-btn img:hover {
            transform: scale(1.05);
        }

        .result-view {
            width: 100%;
            display: none; /* Изначально скрыт */
            flex-direction: column;
            align-items: center;
            justify-content: center; 
            flex-grow: 1; 
            padding-bottom: 100px; 
            position: relative; /* Для позиционирования кнопки скачать */
        }
        #result-image {
            max-width: 100%;
            max-height: 60vh; 
            object-fit: contain;
            border-radius: 12px; 
            box-shadow: 0 4px 15px rgba(0,0,0,0.1);
        }
        .download-action-link { 
             display: none; /* Показывается через JS */
             position: absolute;
             top: 15px;
             right: 15px;
             z-index: 10;
             cursor: pointer;
        }
        .download-button-icon {
            height: 32px; /* Размер иконки скачать */
            width: 32px;
            opacity: 0.8;
            transition: opacity 0.2s ease;
        }
        .download-action-link:hover .download-button-icon {
            opacity: 1;
        }
        
        .loader-container {
            display: none; /* Изначально скрыт */
            justify-content: center;
            align-items: center;
            position: absolute; 
            top: 0; left: 0; width: 100%; height: 100%;
            z-index: 20;
        }
        .pulsating-dot {
            width: 80px; /* УВЕЛИЧЕН РАЗМЕР В 4 РАЗА */
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
            max-width: 500px; 
            padding: 15px;
            position: fixed; 
            bottom: 20px; 
            left: 50%;
            transform: translateX(-50%);
            z-index: 10;
        }
        .input-area {
            display: flex;
            align-items: center;
            background-color: rgba(248, 248, 248, 0.8); 
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            border-radius: 25px; 
            padding: 8px 10px;
            width: 100%;
            box-shadow: 0 4px 15px rgba(0,0,0,0.1);
        }
        #image-file-common, #image-file-desktop-actual { display: none; }

        .file-upload-label-desktop { 
            cursor: pointer;
            padding: 0; /* Убрали внутренний паддинг, т.к. размеры заданы */
            margin-right: 8px;
            display: none; 
            align-items: center;
            justify-content: center;
            position: relative;
            width: 88px; height: 88px; /* УВЕЛИЧЕНО В 2 РАЗА */
            background-color: var(--input-icon-bg);
            border-radius: 12px; /* Скруглил побольше */
            flex-shrink: 0;
            overflow: hidden;
        }
        .upload-icon-desktop { 
            height: 48px; /* УВЕЛИЧЕНО ПРОПОРЦИОНАЛЬНО */
            width: 48px;
            display: block;
        }
        #image-preview-desktop {
            display: none; width: 100%; height: 100%; object-fit: cover; 
        }


        #prompt {
            flex-grow: 1;
            border: none;
            padding: 10px;
            font-size: 0.9rem;
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
            margin-left: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .magic-button img { height: 44px;  }


        .error-message {
            margin-top: 10px;
            font-size: 0.9rem;
            color: #d93025;
            background-color: rgba(255,221,221,0.8);
            backdrop-filter: blur(5px);
            padding: 10px;
            border-radius: 8px;
            position: fixed; 
            bottom: 100px; 
            left: 50%;
            transform: translateX(-50%);
            width: calc(100% - 30px);
            max-width: 480px;
            z-index: 5;
        }
        
        /* --- Десктопная версия --- */
        @media (min-width: 769px) {
            body {
                background-image: url("{{ url_for('static', filename='images/DESK_BACK.png') }}");
            }
            .app-header { top: 30px; }
            .logo { height: 35px; } 
            .app-main { 
                padding-top: 100px; /* Увеличил отступ от лого */
                gap: 30px; 
                justify-content: space-between; /* Чтобы прижать футер */
            }

            .desktop-main-text { display: block; }
            .mobile-main-text { display: none; }
            /* ИЗМЕНЕН РАЗМЕР ОСНОВНОГО ТЕКСТА */
            .main-text-display.desktop-main-text img { max-width: 650px; } 
            
            .image-drop-area { display: none; } 

            /* КНОПКИ ДЕЙСТВИЙ НА ДЕСКТОПЕ - должны быть видимы */
            .action-buttons { 
                display: flex;
                max-width: 600px; /* Ширина для 4х кнопок в ряд */
                margin-top: 20px; /* Отступ от текста */
                margin-bottom: 30px; /* Отступ до поля ввода */
            }
            .action-btn img { height: 48px; }

            .app-footer { max-width: 700px; bottom: 40px; /* Поднял немного футер */ }
            .input-area { padding: 10px 15px; border-radius: 30px;}
            .file-upload-label-desktop { display: flex; } 
            #prompt { font-size: 1rem; }
            .magic-button img { height: 48px; }

            .result-view { 
                max-width: 800px; 
                max-height: 60vh; /* Уменьшил немного, чтобы не перекрывало */
                /* Для позиционирования по центру между верхом (лого) и низом (кнопки/инпут) */
                /* Это сложно без JS, если высота лого и инпута динамическая. */
                /* Пробуем так: */
                margin-top: 0; 
                margin-bottom: 0; /* Управляется через gap в .app-main */
            }
             #result-image { max-height: calc(60vh - 20px); }
        }
        /* Мобильная версия */
        @media (max-width: 768px) {
            body {
                background-image: url("{{ url_for('static', filename='images/MOB_BACK.png') }}");
            }
             .app-main {
                justify-content: space-between; 
                padding-top: 80px; /* Отступ от лого для мобилки */
                padding-bottom: 120px; /* Место для фиксированного футера */
            }
            .initial-view-elements {
                justify-content: flex-start; /* Элементы сверху */
                flex-grow: 0; 
                gap: var(--mob-spacing-unit); /* Используем переменную для отступов */
            }
            .app-header .logo { margin-bottom: 0; } /* Убираем если есть, управляем gap */
             .mobile-main-text { margin-bottom: 0; } /* Управляется через gap */

            .file-upload-label-desktop { display: none; } 
            .image-drop-area {
                margin-top: var(--mob-spacing-unit); /* Отступ от текста до MOB_DROP */
            }
            .action-buttons {
                margin-top: var(--mob-spacing-unit); /* Отступ от MOB_DROP до кнопок */
            }

            .app-footer { max-width: calc(100% - 30px); bottom: 20px; }
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

            <div class="result-view"> <img id="result-image" src="" alt="Generated Image">
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
        const magicButtonIcon = document.getElementById('magic-button-icon');
        const promptInput = document.getElementById('prompt');

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
                    if (mobileImagePreview && mobileDropPlaceholder) {
                        mobileImagePreview.src = e.target.result;
                        mobileImagePreview.style.display = 'block';
                        mobileDropPlaceholder.style.display = 'none';
                    }
                    if (desktopImagePreview && desktopUploadIcon) {
                        desktopImagePreview.src = e.target.result;
                        desktopImagePreview.style.display = 'block';
                        desktopUploadIcon.style.display = 'none';
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
                // Блюр убирается только при возврате к начальному виду
            }
        }
        
        function showInitialView() {
            if (initialViewElements) initialViewElements.style.display = 'flex';
            if (resultView) resultView.style.display = 'none';
            if(downloadLink) downloadLink.style.display = 'none';
            // magicButtonIcon.src = "{{ url_for('static', filename='images/MAGIC_GREEN.png') }}"; // Если бы меняли иконку
            submitButton.dataset.action = "generate";
            promptInput.value = '';
            resetPreview();
            document.body.classList.remove('bg-blur'); 
        }

        editForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            
            if (submitButton.dataset.action === "startover") {
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
                document.body.classList.remove('bg-blur'); // Убираем блюр если нет файла
                if (initialViewElements) initialViewElements.style.display = 'flex'; // Возвращаем начальный вид
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
                // Блюр убирается здесь только если НЕ ошибка
                if (!response.ok) {
                    throw new Error(data.error || 'Неизвестная ошибка сервера');
                }
                document.body.classList.remove('bg-blur'); // Убираем блюр после успешной загрузки
                showLoader(false); // Убираем лоадер ДО показа результата
                
                if (resultView) resultView.style.display = 'flex';
                resultImage.src = data.output_url;
                resultImage.style.display = 'block';
                if(downloadLink) {
                    downloadLink.href = data.output_url;
                    downloadLink.style.display = 'block'; 
                }
                
                if (window.innerWidth <= 768) { // Только для мобильных меняем кнопку
                     submitButton.dataset.action = "startover";
                     // Можно добавить изменение текста/иконки для "Start Over" если есть такая иконка
                }

            } catch (error) {
                console.error('Ошибка:', error);
                showLoader(false); // Убираем лоадер
                document.body.classList.remove('bg-blur'); // Убираем блюр при ошибке
                errorBox.textContent = "Произошла ошибка: " + error.message;
                errorBox.style.display = 'block';
                if (resultView) resultView.style.display = 'flex'; // Показываем блок для ошибки
            } finally {
                submitButton.disabled = false;
            }
        });

        const actionButtons = document.querySelectorAll('.action-btn');
        actionButtons.forEach(button => {
            button.addEventListener('click', (e) => {
                const currentTarget = e.currentTarget; 
                const action = currentTarget.dataset.action;
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
