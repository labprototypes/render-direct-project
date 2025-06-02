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
            --background-color: #DDD290; /* ИЗМЕНЕННЫЙ ЦВЕТ ФОНА */
            --primary-blue: #192E8C;    
            --input-area-bg: #FFFFFF;   
            --input-icon-bg: #F0F0F0; 
            --text-placeholder: #757575;
            --mobile-spacing-unit: 25px; 
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
            min-height: 100vh;
            align-items: center;
            padding: 30px 15px; 
            text-align: center;
        }

        .app-container {
            width: 100%;
            max-width: 850px; 
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 30px; 
            flex-grow: 1; 
        }

        .app-header {
             margin-bottom: 20px; 
        }

        .logo {
            height: 45px; 
        }

        .app-main {
            width: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 45px; 
            flex-grow: 1; 
        }

        .initial-view {
            width: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 45px; 
        }

        .main-text img {
            width: 100%;
            height: auto;
        }

        .desktop-main-text img {
             max-width: 1150px; /* УВЕЛИЧЕН РАЗМЕР */
        }
        .desktop-main-text { display: block; }
        .mobile-main-text { display: none; }

        .action-buttons-wrapper {
            width: 100%;
            max-width: 680px; 
            margin: 0 auto; 
        }
        
        .action-buttons-container {
            position: relative; 
            width: 100%;
            padding-bottom: 12%; 
        }

        .action-buttons-svg {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            object-fit: contain; 
        }

        .action-button-overlays {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            display: flex; 
            justify-content: space-around; 
        }

        .overlay-btn {
            background-color: transparent;
            border: none; 
            cursor: pointer;
            width: 23%; 
            height: 100%; 
        }

        .result-view {
            width: 100%;
            max-width: 700px; 
            max-height: 55vh; 
            margin-top: 0; 
            margin-bottom: 0;
            display: flex;
            justify-content: center;
            align-items: center;
            background-color: #E9E9E9; 
            border-radius: 12px; 
            padding: 10px; 
        }

        #result-image {
            max-width: 100%;
            max-height: calc(55vh - 20px); 
            object-fit: contain; 
            border-radius: 8px;
        }
        
        .input-area-wrapper { 
            width: 100%;
            display: flex;
            justify-content: center;
            margin-top: auto; 
            padding: 20px 0; 
        }

        .input-area {
            display: flex;
            align-items: center;
            background-color: var(--input-area-bg);
            border-radius: 12px; 
            padding: 10px 12px;
            width: 100%;
            max-width: 600px; 
            box-shadow: 0 3px 8px rgba(0,0,0,0.1);
        }

        #image-file { display: none; }

        .file-upload-label {
            cursor: pointer;
            width: 60px; 
            height: 60px; 
            background-color: var(--input-icon-bg);
            border-radius: 8px;
            margin-right: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            overflow: hidden; 
            position: relative; 
        }

        .upload-icon { 
            height: 28px; 
            width: 28px;
            display: block; 
        }
        #image-preview {
            display: none; 
            width: 100%;
            height: 100%;
            object-fit: cover; 
        }
        
        #prompt {
            flex-grow: 1;
            border: none;
            padding: 12px 10px;
            font-size: 1rem; 
            background-color: transparent;
            outline: none;
            color: var(--primary-blue);
            /* white-space: pre-wrap;  Убираем для однострочного плейсхолдера по умолчанию */
            line-height: 1.4;
        }
        #prompt::placeholder { 
            color: var(--text-placeholder); 
            opacity: 0.9;
            /* white-space: pre-wrap; Убираем для однострочного плейсхолдера по умолчанию */
        }

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
        .magic-button img { height: 32px; width: 32px; }
        .magic-button:hover img { transform: scale(1.1); }


        .loader-message, .error-message { margin-top: 20px; font-size: 1rem; }
        .error-message { color: #d93025; background-color: #fdd; padding: 10px; border-radius: 8px;}

        /* Мобильная версия */
        @media (max-width: 768px) {
            body { padding: 20px 15px 30px 15px; }
            .app-container { gap: 20px; } 
            
            .app-header { 
                margin-bottom: 0; 
            }
             .app-main { 
                gap: 0; 
            }
            .initial-view { 
                gap: 0; 
                width:100%;
            }

            .logo { height: 35px; margin-bottom: calc(var(--mobile-spacing-unit) * 1.2); } /* УВЕЛИЧЕН ОТСТУП ОТ ЛОГО */

            .desktop-main-text { display: none; }
            .mobile-main-text { display: block; margin-bottom: var(--mobile-spacing-unit); } /* ОТСТУП B */
            .mobile-main-text img { max-width: 100%; }

            .action-buttons-wrapper { 
                max-width: 100%; 
                margin-bottom: calc(var(--mobile-spacing-unit) * 1.5); /* ОТСТУП C = 1.5 * B (УМЕНЬШЕН ВДВОЕ) */
            }
             .action-buttons-container { padding-bottom: 14%; } 


            .input-area-wrapper { 
                padding: 0 10px; /* Уменьшены боковые отступы для input-area-wrapper */
                margin-top: auto; 
                margin-bottom: 10px; 
            }
            .input-area { 
                max-width: 100%; 
            }
            .file-upload-label { width: 50px; height: 50px; } 
            .upload-icon { height: 24px; width: 24px; }
            #prompt { 
                padding: 12px 8px; 
                font-size: 0.85rem; /* УМЕНЬШЕН ШРИФТ ВВОДА НА МОБИЛКЕ */
            }
            .magic-button img { height: 28px; width: 28px; }
            
            .result-view {
                width: calc(100% - 20px); 
                max-width: calc(100% - 20px);
                max-height: 45vh; 
                margin: 10px auto; 
            }
            #result-image { max-height: calc(45vh - 20px); }
        }
         @media (max-width: 480px) {
            .logo { height: 30px; margin-bottom: calc(var(--mobile-spacing-unit) * 0.8); }
            .mobile-main-text { margin-bottom: calc(var(--mobile-spacing-unit) * 0.8); }
            .action-buttons-wrapper { margin-bottom: calc(var(--mobile-spacing-unit) * 0.8 * 1.5); } /* Сохраняем пропорцию */
            .action-buttons-container { padding-bottom: 16%; } 
            .file-upload-label { width: 45px; height: 45px; }
            .upload-icon { height: 20px; width: 20px; }
            .magic-button img { height: 26px; width: 26px; }
            #prompt { font-size: 0.8rem; } /* Еще чуть меньше на самых маленьких экранах */
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
                        <img id="image-preview" src="#" alt="Image preview" style="display: none;">
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
        const uploadIcon = document.querySelector('.file-upload-label .upload-icon');
        const imagePreview = document.getElementById('image-preview');
        const editForm = document.getElementById('edit-form');
        
        const initialView = document.querySelector('.initial-view');
        const resultView = document.querySelector('.result-view');
        const resultImage = document.getElementById('result-image');
        
        const loader = document.getElementById('loader');
        const errorBox = document.getElementById('error-box');
        const submitButton = document.getElementById('submit-button'); 
        const promptInput = document.getElementById('prompt');

        // Убрали JavaScript для многострочного плейсхолдера, возвращаем стандартный
        // const mobilePlaceholderText = "TYPE WHAT YOU WANT\\nTO CHANGE";
        // const desktopPlaceholderText = "TYPE WHAT YOU WANT TO CHANGE";
        // function updatePromptPlaceholder() { ... }

        promptInput.placeholder = "TYPE WHAT YOU WANT TO CHANGE"; // Стандартный плейсхолдер

        imageFileInput.addEventListener('change', function() {
            if (this.files && this.files[0]) {
                const reader = new FileReader();
                reader.onload = function(e) {
                    imagePreview.src = e.target.result;
                    imagePreview.style.display = 'block';
                    if(uploadIcon) uploadIcon.style.display = 'none';
                }
                reader.readAsDataURL(this.files[0]);
            } else {
                imagePreview.src = '#';
                imagePreview.style.display = 'none';
                if(uploadIcon) uploadIcon.style.display = 'block';
            }
        });

        function resetUploadArea() {
            imagePreview.src = '#';
            imagePreview.style.display = 'none';
            if(uploadIcon) uploadIcon.style.display = 'block';
            imageFileInput.value = ''; 
        }

        editForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            
            submitButton.disabled = true;
            loader.style.display = 'block';
            resultImage.src = '';
            resultImage.style.display = 'none';
            errorBox.style.display = 'none';
            errorBox.textContent = ''; 
            
            initialView.style.display = 'none';
            resultView.style.display = 'flex'; 

            const formData = new FormData();
            if (!imageFileInput.files || imageFileInput.files.length === 0) {
                errorBox.textContent = "Пожалуйста, выберите файл для загрузки.";
                errorBox.style.display = 'block';
                loader.style.display = 'none';
                submitButton.disabled = false;
                initialView.style.display = 'flex'; 
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
                errorBox.textContent = "Произошла ошибка. Попробуйте еще раз.";
                errorBox.style.display = 'block';
            } finally {
                loader.style.display = 'none';
                submitButton.disabled = false;
            }
        });

        const overlayButtons = document.querySelectorAll('.overlay-btn');
        overlayButtons.forEach(button => {
            button.addEventListener('click', (e) => {
                const action = e.target.dataset.action;
                console.log("Action button clicked:", action);
            });
        });

        const logo = document.querySelector('.logo');
        if (logo) {
            logo.addEventListener('click', () => {
                initialView.style.display = 'flex';
                resultView.style.display = 'none';
                resultImage.src = '';
                resultImage.style.display = 'none';
                errorBox.style.display = 'none';
                resetUploadArea();
                promptInput.value = ''; 
                promptInput.placeholder = "TYPE WHAT YOU WANT TO CHANGE"; // Восстанавливаем стандартный плейсхолдер
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
