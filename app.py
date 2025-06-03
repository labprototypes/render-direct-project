import os
import boto3
import uuid
import requests
import time
import openai
from flask import Flask, request, jsonify, render_template_string, url_for

# --- Настройки для подключения к Amazon S3 ---
AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
AWS_S3_BUCKET_NAME = os.environ.get('AWS_S3_BUCKET_NAME')
AWS_S3_REGION = os.environ.get('AWS_S3_REGION')

# Инициализируем Flask приложение
app = Flask(__name__)
app.static_folder = 'static' # Убедитесь, что папка static существует

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
            --blur-intensity: 8px;
            --mob-spacing-unit: 20px;
            --desktop-spacing-unit: 30px;
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'ChangerFont', sans-serif;
            color: var(--text-accent-color); /* Default text color, though most elements will override */
            background-size: cover;
            background-position: center center;
            background-repeat: no-repeat;
            background-attachment: fixed;
            display: flex;
            flex-direction: column;
            min-height: 100vh;
            overflow-x: hidden;
            transition: filter 0.4s ease-in-out; /* Smoother blur transition */
        }
        body.bg-blur {
            /* Filter applied by JS to the body for background blur */
            /* The actual background image is on .app-container to allow content to be unblurred */
        }
        
        .app-container-wrapper { /* New wrapper for background image */
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background-size: cover;
            background-position: center center;
            background-repeat: no-repeat;
            z-index: -1; /* Behind all content */
            transition: filter 0.4s ease-in-out;
        }
        .app-container-wrapper.bg-blur {
             filter: blur(var(--blur-intensity));
        }


        .app-container {
            width: 100%;
            max-width: 1200px;
            margin: 0 auto;
            padding: var(--mob-spacing-unit);
            display: flex;
            flex-direction: column;
            align-items: center;
            flex-grow: 1;
            position: relative;
            z-index: 1; /* Above background wrapper */
        }

        .app-header {
            width: 100%;
            padding-top: 10px; /* Reduced top padding */
            text-align: center;
            position: absolute;
            top: var(--mob-spacing-unit);
            left: 50%;
            transform: translateX(-50%);
            z-index: 100;
        }

        .logo {
            height: 30px; /* Mobile logo height from brief */
            cursor: pointer;
        }

        .app-main {
            width: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: flex-start; /* Changed for mobile layout */
            flex-grow: 1;
            padding-top: calc(30px + var(--mob-spacing-unit) + 15px + var(--mob-spacing-unit)); /* logo height + header top + header padding + safety */
            padding-bottom: calc(70px + var(--mob-spacing-unit) + var(--mob-spacing-unit)); /* Space for footer + extra */
            gap: var(--mob-spacing-unit); /* Vertical spacing for mobile */
            text-align: center;
        }
        
        .initial-content, .result-image-wrapper, .loader-container {
            display: none; /* Controlled by JS */
            flex-direction: column;
            align-items: center;
            gap: inherit; /* Inherit gap from app-main */
            width: 100%;
        }
        .initial-content.active, .result-image-wrapper.active, .loader-container.active {
            display: flex;
        }

        .main-text-display-img { /* MOB_MAIN.svg or DESK_MAIN.png */
            width: auto; 
            max-width: 90%; /* Adjusted for better fit */
            height: auto;
            object-fit: contain; /* Ensure the image scales down nicely if it's too wide for its height */
        }
        .desktop-main-text-img { display: none; }
        .mobile-main-text-img { display: block; max-height: 20vh; }


        .image-drop-area-mobile { /* Label for mobile */
            width: 80%;
            max-width: 280px; /* Adjusted for MOB_DROP.png visual */
            aspect-ratio: 300 / 350; /* Maintain if MOB_DROP.png is this ratio */
            background-color: transparent; 
            border-radius: 25px; /* From preview */
            display: flex;
            justify-content: center;
            align-items: center;
            cursor: pointer;
            position: relative;
            overflow: hidden; 
        }
        .image-drop-area-mobile .mob-drop-placeholder-img {
            width: 100%;
            height: 100%;
            object-fit: contain; /* Show full MOB_DROP.png */
            position: absolute; 
            top: 0; left: 0;
        }
         .image-drop-area-mobile::before { 
            content: "";
            position: absolute;
            top: 0; left: 0; right: 0; bottom: 0;
            background-color: rgba(248, 248, 248, 0.1); 
            backdrop-filter: blur(4px); 
            -webkit-backdrop-filter: blur(4px);
            z-index: -1; 
            border-radius: inherit;
        }

        .image-drop-area-mobile .image-preview-mobile-img {
            display: none;
            width: 100%;
            height: 100%;
            object-fit: cover;
            border-radius: inherit; 
            position: relative; 
            z-index: 1;
        }

        .action-buttons { 
            display: flex;
            justify-content: center; 
            align-items: center;
            gap: 8px; 
            flex-wrap: wrap;
            width: 100%;
            max-width: 320px; 
        }
        .action-btn img {
            height: 45px; 
            cursor: pointer;
            transition: transform 0.2s ease;
        }
        .action-btn img:hover {
            transform: scale(1.05);
        }

        .result-image-wrapper {
             justify-content: center; 
        }
        #result-image {
            max-width: 90%; 
            max-height: 55vh; 
            object-fit: contain;
            border-radius: 12px; 
            box-shadow: 0 6px 20px rgba(0,0,0,0.25); 
            position: relative;
        }
        #result-image.result-aspect-portrait { 
        }
        #result-image.result-aspect-landscape { 
            max-width: 100%; 
        }


        .download-action-link {
            display: none; 
            position: absolute;
            top: 12px;
            right: 12px;
            z-index: 10;
            cursor: pointer;
            background-color: rgba(0,0,0,0.4);
            border-radius: 50%;
            padding: 6px;
        }
        .download-button-icon {
            height: 20px;
            width: 20px;
            display: block;
            filter: invert(1);
        }

        .loader-container {
            justify-content: center;
            align-items: center;
            min-height: 200px; 
            z-index: 101;
        }
        .pulsating-dot {
            width: 25px; 
            height: 25px;
            background-color: var(--text-accent-color);
            border-radius: 50%;
            position: relative; 
            animation: elegantPulse 1.8s infinite ease-out;
        }
        .pulsating-dot::before, .pulsating-dot::after { 
            content: '';
            position: absolute;
            left: 50%;
            top: 50%;
            transform: translate(-50%, -50%);
            border-radius: 50%;
            background-color: var(--text-accent-color);
            opacity: 0.7;
        }
        .pulsating-dot::before {
            width: 100%;
            height: 100%;
            animation: elegantPulseOuter1 1.8s infinite ease-out;
            animation-delay: 0.2s;
        }
        .pulsating-dot::after {
            width: 100%;
            height: 100%;
            animation: elegantPulseOuter2 1.8s infinite ease-out;
            animation-delay: 0.4s;
        }

        @keyframes elegantPulse { 
            0%, 100% { transform: scale(0.8); opacity: 0.8; }
            50% { transform: scale(1); opacity: 1; }
        }
        @keyframes elegantPulseOuter1 { 
            0%, 20% { transform: scale(1); opacity: 0.5; }
            100% { transform: scale(2.5); opacity: 0; }
        }
        @keyframes elegantPulseOuter2 { 
            0%, 40% { transform: scale(1); opacity: 0.3; }
            100% { transform: scale(3.5); opacity: 0; }
        }


        .app-footer {
            width: calc(100% - calc(2 * var(--mob-spacing-unit))); 
            max-width: 500px; 
            padding: 0; 
            position: fixed;
            bottom: var(--mob-spacing-unit);
            left: 50%;
            transform: translateX(-50%);
            z-index: 100;
        }
        .input-area { 
            display: flex;
            align-items: center;
            background-color: rgba(248, 248, 248, 0.8); 
            backdrop-filter: blur(10px); 
            -webkit-backdrop-filter: blur(10px);
            border-radius: 50px; 
            padding: 6px 8px; 
            width: 100%;
            box-shadow: 0 4px 15px rgba(0,0,0,0.1);
        }
        #image-file-common { display: none; } 

        .file-upload-label-desktop { 
            display: none; 
        }
        
        #prompt {
            flex-grow: 1;
            border: none;
            padding: 12px 10px; 
            font-size: 0.9rem; 
            background-color: transparent;
            outline: none;
            color: #333333; 
            font-family: 'ChangerFont', sans-serif; 
            line-height: 1.3;
        }
        #prompt::placeholder {
            color: #888888; 
            opacity: 1;
        }

        .submit-button-element { 
            background-color: transparent;
            border: none;
            cursor: pointer;
            padding: 0;
            margin-left: 8px; 
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0; 
        }
        .submit-button-icon-img { 
            height: 40px; 
            width: 40px;
        }
        .submit-button-text-content { 
            display: none; 
            color: #000000; 
            font-size: 0.9rem;
            font-family: 'ChangerFont', sans-serif; 
            padding: 10px 15px;
            white-space: nowrap;
        }


        .error-message {
            display: none;
            margin-top: 10px;
            font-size: 0.9rem;
            color: var(--text-accent-color); 
            background-color: rgba(0,0,0,0.65); 
            backdrop-filter: blur(5px);
            padding: 10px 15px;
            border-radius: 8px;
            position: fixed;
            bottom: calc(70px + var(--mob-spacing-unit) + 10px); 
            left: 50%;
            transform: translateX(-50%);
            width: calc(100% - calc(4 * var(--mob-spacing-unit)));
            max-width: 480px;
            z-index: 105; 
            text-align: center;
        }

        /* --- Desktop Styles --- */
        @media (min-width: 769px) {
            :root {
            }
             .app-container-wrapper {
                background-image: url("{{ url_for('static', filename='images/DESK_BACK.png') }}");
            }
            .app-container {
                padding: var(--desktop-spacing-unit);
            }
            .logo {
                height: 35px; 
            }
            .app-header {
                 top: var(--desktop-spacing-unit);
            }
            .app-main {
                padding-top: calc(35px + var(--desktop-spacing-unit) + 15px + var(--desktop-spacing-unit));
                padding-bottom: calc(100px + var(--desktop-spacing-unit)); 
                gap: var(--desktop-spacing-unit);
                justify-content: center; 
            }

            .mobile-main-text-img { display: none; }
            .desktop-main-text-img {
                display: block;
                max-width: 650px; 
                height: auto; /* Let max-height control the height primarily */
                max-height: 35vh; /* Added to control vertical size */
                object-fit: contain; /* Ensures the image scales down nicely */
                margin-bottom: var(--desktop-spacing-unit); 
            }

            .image-drop-area-mobile { display: none; } 
            
            .action-buttons {
                max-width: 520px; 
                gap: 15px; 
            }
            .action-btn img {
                height: 48px; 
            }

            #result-image {
                max-height: 60vh; 
            }
             #result-image.result-aspect-landscape { 
                max-width: min(600px, 90%); 
            }

            .app-footer {
                max-width: 700px; 
                bottom: var(--desktop-spacing-unit);
            }
            .input-area {
                padding: 10px 12px; 
                border-radius: 30px; 
            }
            .file-upload-label-desktop { 
                display: flex; 
                cursor: pointer;
                padding: 0;
                margin-right: 12px;
                align-items: center;
                justify-content: center;
                position: relative;
                width: 56px; 
                height: 56px;
                background-color: transparent; 
                border-radius: 18px; 
                flex-shrink: 0;
                overflow: hidden;
            }
            .upload-icon-desktop-img { 
                height: 100%; 
                width: 100%;
                object-fit: contain;
            }
            .image-preview-desktop-img { 
                display: none;
                width: 100%;
                height: 100%;
                object-fit: cover;
                border-radius: inherit;
            }

            #prompt {
                padding: 15px 15px;
                font-size: 1rem;
            }
            .submit-button-icon-img { 
                height: 48px; 
                width: 48px;
            }
            .submit-button-text-content { 
                 font-size: 1rem;
            }
        }
         /* Mobile specific layout for initial view using space-between for app-main children */
        @media (max-width: 768px) {
            .app-container-wrapper {
                 background-image: url("{{ url_for('static', filename='images/MOB_BACK.png') }}");
            }
            .app-main {
                justify-content: space-between; 
                min-height: calc(100vh - (30px + var(--mob-spacing-unit) + 15px) - (70px + var(--mob-spacing-unit)) - (2 * var(--mob-spacing-unit)) );
            }
             .initial-content.active {
                gap: var(--mob-spacing-unit); 
            }
            .result-image-wrapper.active {
                flex-grow: 1; 
                display: flex;
                align-items: center; 
                justify-content: center; 
            }
             #result-image.result-aspect-landscape {
                max-width: calc(100% - 20px); 
                width: auto; 
                max-height: 35vh; 
            }

        }
    </style>
</head>
<body>
    <div class="app-container-wrapper" id="app-bg-wrapper"></div>
    <div class="app-container">
        <header class="app-header">
            <img src="{{ url_for('static', filename='images/change.svg') }}" alt="Changer Logo" class="logo">
        </header>

        <main class="app-main">
            <div class="initial-content">
                <img src="{{ url_for('static', filename='images/DESK_MAIN.png') }}" alt="Change Everything" class="main-text-display-img desktop-main-text-img">
                <img src="{{ url_for('static', filename='images/MOB_MAIN.svg') }}" alt="Change Everything" class="main-text-display-img mobile-main-text-img">

                <label for="image-file-common" class="image-drop-area-mobile">
                    <img src="{{ url_for('static', filename='images/MOB_DROP.png') }}" alt="Just drop the image" class="mob-drop-placeholder-img">
                    <img id="image-preview-mobile" src="#" alt="Preview" class="image-preview-mobile-img">
                </label>
            </div>

            <div class="result-image-wrapper">
                <img id="result-image" src="" alt="Generated Image">
                <a href="#" id="download-action" class="download-action-link" download="generated_image.png">
                    <img src="{{ url_for('static', filename='images/Download.png') }}" alt="Download" class="download-button-icon">
                </a>
            </div>
            
            <div id="loader" class="loader-container">
                <div class="pulsating-dot"></div>
            </div>

            <div class="action-buttons">
                <div class="action-btn" data-action="create"><img src="{{ url_for('static', filename='images/Create.svg') }}" alt="Create"></div>
                <div class="action-btn" data-action="relight"><img src="{{ url_for('static', filename='images/Relight.svg') }}" alt="Relight"></div>
                <div class="action-btn" data-action="remove"><img src="{{ url_for('static', filename='images/Remove.svg') }}" alt="Remove"></div>
                <div class="action-btn" data-action="change"><img src="{{ url_for('static', filename='images/Change.svg') }}" alt="Change"></div>
            </div>
        </main>

        <footer class="app-footer">
            <form id="edit-form" class="input-area">
                <label for="image-file-common" class="file-upload-label-desktop">
                    <img src="{{ url_for('static', filename='images/DESK_UPLOAD.png') }}" alt="Upload Icon" class="upload-icon-desktop-img">
                    <img id="image-preview-desktop" src="#" alt="Preview" class="image-preview-desktop-img">
                </label>
                <input type="file" id="image-file-common" name="image" accept="image/*">
                
                <input type="text" id="prompt" name="prompt" placeholder="TYPE WHAT YOU WANT TO CHANGE">
                
                <button type="submit" id="submit-button" class="submit-button-element">
                    <img src="{{ url_for('static', filename='images/MAGIC_GREEN.png') }}" alt="Generate" id="magic-button-icon-img" class="submit-button-icon-img">
                    <span id="submit-button-text-content" class="submit-button-text-content">Start over</span>
                </button>
            </form>
        </footer>
        <div id="error-box" class="error-message"></div>
    </div>

    <script>
    // --- DOM Elements ---
    const appBgWrapper = document.getElementById('app-bg-wrapper');
    const imageFileInput = document.getElementById('image-file-common');
    
    const mobileDropArea = document.querySelector('.image-drop-area-mobile');
    const mobileDropPlaceholderImg = document.querySelector('.mob-drop-placeholder-img');
    const mobileImagePreviewImg = document.getElementById('image-preview-mobile');
    
    const desktopUploadLabel = document.querySelector('.file-upload-label-desktop');
    const desktopUploadIconImg = document.querySelector('.upload-icon-desktop-img');
    const desktopImagePreviewImg = document.getElementById('image-preview-desktop');

    const editForm = document.getElementById('edit-form');
    const promptInput = document.getElementById('prompt');
    const submitButton = document.getElementById('submit-button');
    const magicButtonIconImg = document.getElementById('magic-button-icon-img');
    const submitButtonTextContent = document.getElementById('submit-button-text-content');

    const initialContent = document.querySelector('.initial-content');
    const resultImageWrapper = document.querySelector('.result-image-wrapper');
    const resultImage = document.getElementById('result-image');
    const downloadLink = document.getElementById('download-action');
    const loaderContainer = document.getElementById('loader');
    const actionButtonsContainer = document.querySelector('.action-buttons');

    const mobileMainTextImg = document.querySelector('.mobile-main-text-img');
    const desktopMainTextImg = document.querySelector('.desktop-main-text-img');
    const errorBox = document.getElementById('error-box');

    let currentView = 'initial'; 

    function isDesktopView() {
        return window.innerWidth > 768;
    }

    function showError(message) {
        errorBox.textContent = message;
        errorBox.style.display = 'block';
        setTimeout(() => { errorBox.style.display = 'none'; }, 4000);
    }

    function updateView(viewName) {
        currentView = viewName;
        
        appBgWrapper.classList.remove('bg-blur');
        initialContent.classList.remove('active');
        resultImageWrapper.classList.remove('active');
        loaderContainer.classList.remove('active');
        
        if (mobileMainTextImg) mobileMainTextImg.style.display = 'none';
        if (desktopMainTextImg) desktopMainTextImg.style.display = 'none';
        if (mobileDropArea) mobileDropArea.style.display = 'none'; 
        downloadLink.style.display = 'none';
        actionButtonsContainer.style.display = 'flex'; 

        if (viewName === 'initial') {
            initialContent.classList.add('active');
            if (isDesktopView()) {
                if (desktopMainTextImg) desktopMainTextImg.style.display = 'block';
            } else {
                if (mobileMainTextImg) mobileMainTextImg.style.display = 'block';
                if (mobileDropArea) mobileDropArea.style.display = 'flex';
            }
            magicButtonIconImg.style.display = 'block';
            submitButtonTextContent.style.display = 'none';
            submitButton.dataset.action = "generate";
            promptInput.value = ''; 
            resetImagePreviews();
        } else if (viewName === 'loading') {
            loaderContainer.classList.add('active');
            appBgWrapper.classList.add('bg-blur'); 
             if (!isDesktopView()) {
                if (mobileMainTextImg) mobileMainTextImg.style.display = 'none';
                if (mobileDropArea) mobileDropArea.style.display = 'none';
            } else {
                 if (desktopMainTextImg) desktopMainTextImg.style.display = 'none';
            }
        } else if (viewName === 'result') {
            resultImageWrapper.classList.add('active');
            appBgWrapper.classList.add('bg-blur'); 
            downloadLink.style.display = 'block'; 

            if (!isDesktopView()) { 
                magicButtonIconImg.style.display = 'none';
                submitButtonTextContent.style.display = 'block';
                submitButton.dataset.action = "startover";
            } else { 
                magicButtonIconImg.style.display = 'block';
                submitButtonTextContent.style.display = 'none';
                submitButton.dataset.action = "generate"; 
            }
        }
        adjustLayoutForResultImage();
    }
    
    function adjustLayoutForResultImage() {
        if (currentView === 'result' && resultImage.src && resultImage.src !== window.location.href + "#") {
            const img = new Image();
            img.onload = () => {
                resultImage.classList.remove('result-aspect-portrait', 'result-aspect-landscape');
                if (img.naturalWidth < img.naturalHeight) { 
                    resultImage.classList.add('result-aspect-portrait');
                } else { 
                    resultImage.classList.add('result-aspect-landscape');
                }
            }
            img.src = resultImage.src;
        }
    }

    window.addEventListener('resize', () => {
        updateView(currentView); 
    });

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
                    if (desktopImagePreviewImg) {
                        desktopImagePreviewImg.src = e.target.result;
                        desktopImagePreviewImg.style.display = 'block';
                    }
                    if (desktopUploadIconImg) desktopUploadIconImg.style.display = 'none';
                } else { 
                    if (mobileImagePreviewImg) {
                        mobileImagePreviewImg.src = e.target.result;
                        mobileImagePreviewImg.style.display = 'block';
                        if(mobileDropPlaceholderImg) mobileDropPlaceholderImg.style.display = 'none';
                    }
                }
            }
            reader.readAsDataURL(this.files[0]);
        } else {
            resetImagePreviews();
        }
    });

    function resetImagePreviews() {
        if (mobileImagePreviewImg && mobileDropPlaceholderImg) {
            mobileImagePreviewImg.src = '#';
            mobileImagePreviewImg.style.display = 'none';
            mobileDropPlaceholderImg.style.display = 'block'; 
        }
        if (desktopImagePreviewImg && desktopUploadIconImg) {
            desktopImagePreviewImg.src = '#';
            desktopImagePreviewImg.style.display = 'none';
            desktopUploadIconImg.style.display = 'block';
        }
        imageFileInput.value = ''; 
    }

    editForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        
        if (submitButton.dataset.action === "startover") {
            updateView('initial');
            return;
        }

        if (!imageFileInput.files || imageFileInput.files.length === 0) {
            showError("Пожалуйста, выберите файл для загрузки.");
            return;
        }
        if (!promptInput.value.trim()) {
            showError("Пожалуйста, введите текстовый промпт.");
            return;
        }

        submitButton.disabled = true;
        errorBox.style.display = 'none';
        updateView('loading');

        const formData = new FormData();
        formData.append('image', imageFileInput.files[0]);
        formData.append('prompt', promptInput.value);
        
        try {
            const response = await fetch("{{ url_for('process_image') }}", {
                method: 'POST',
                body: formData
            });
            const data = await response.json();
            
            if (!response.ok) {
                throw new Error(data.error || 'Неизвестная ошибка сервера');
            }

            resultImage.src = data.output_url;
            downloadLink.href = data.output_url;
            const tempImg = new Image();
            tempImg.onload = () => {
                updateView('result');
            };
            tempImg.onerror = () => { 
                showError("Не удалось загрузить сгенерированное изображение.");
                updateView('initial');
            };
            tempImg.src = data.output_url;
            
        } catch (error) {
            console.error('Ошибка:', error);
            showError("Произошла ошибка: " + error.message);
            updateView('initial'); 
        } finally {
            submitButton.disabled = false;
        }
    });

    document.querySelectorAll('.action-btn').forEach(button => {
        button.addEventListener('click', (e) => {
            const action = e.currentTarget.dataset.action;
            let prefillText = "";
            if (action === "create") prefillText = "Create ";
            else if (action === "relight") prefillText = "Relight the image to ";
            else if (action === "remove") prefillText = "Remove ";
            else if (action === "change") prefillText = "Change to ";
            
            promptInput.value = prefillText;
            promptInput.focus();
        });
    });
    
    document.querySelector('.logo').addEventListener('click', () => {
        if (currentView !== 'loading') { 
             updateView('initial');
        }
    });

    updateView('initial');
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
    
    model_version_id = "lucataco/sdxl-lcm-lora:a27457f2b866969151b3e6c1434add815009910d89c6006f0932a9abd572789b"


    try:
        if not all([AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_S3_BUCKET_NAME, AWS_S3_REGION]):
            print("!!! ОШИБКА: Не все переменные AWS S3 настроены.")
            return jsonify({'error': 'Ошибка конфигурации сервера для загрузки изображений.'}), 500
        
        s3_client = boto3.client('s3', region_name=AWS_S3_REGION, aws_access_key_id=AWS_ACCESS_KEY_ID, aws_secret_access_key=AWS_SECRET_ACCESS_KEY)
        _, f_ext = os.path.splitext(image_file.filename)
        object_name = f"uploads/{uuid.uuid4()}{f_ext}" 
        
        s3_client.upload_fileobj(image_file.stream, AWS_S3_BUCKET_NAME, object_name, ExtraArgs={'ContentType': image_file.content_type})
        
        hosted_image_url = f"https://{AWS_S3_BUCKET_NAME}.s3.{AWS_S3_REGION}.amazonaws.com/{object_name}"
        print(f"!!! Изображение загружено на Amazon S3: {hosted_image_url}")

        if not REPLICATE_API_TOKEN:
            print("!!! ОШИБКА: REPLICATE_API_TOKEN не найден.")
            return jsonify({'error': 'Ошибка конфигурации сервера для генерации изображений.'}), 500

        headers = {"Authorization": f"Bearer {REPLICATE_API_TOKEN}", "Content-Type": "application/json"}
        post_payload = {
            "version": model_version_id,
            "input": {"image": hosted_image_url, "prompt": final_prompt_text} 
        }
        
        start_response = requests.post("https://api.replicate.com/v1/predictions", json=post_payload, headers=headers)
        start_response.raise_for_status() 
        prediction_data = start_response.json()
        
        get_url = prediction_data["urls"]["get"]
        
        output_url = None
        max_retries = 60 
        retries = 0
        while retries < max_retries:
            time.sleep(2) 
            get_response = requests.get(get_url, headers=headers)
            get_response.raise_for_status()
            status_data = get_response.json()
            
            print(f"Статус генерации Replicate: {status_data['status']}")
            
            if status_data["status"] == "succeeded":
                if isinstance(status_data["output"], list): 
                    output_url = status_data["output"][0] 
                else: 
                    output_url = str(status_data["output"]) 
                break
            elif status_data["status"] in ["failed", "canceled"]:
                error_detail = status_data.get('error', 'неизвестная ошибка Replicate')
                raise Exception(f"Генерация Replicate не удалась: {error_detail}")
            retries += 1
        
        if not output_url:
            return jsonify({'error': 'Генерация Replicate заняла слишком много времени или не вернула результат.'}), 500
            
        return jsonify({'output_url': output_url})
        
    except requests.exceptions.HTTPError as http_err:
        print(f"!!! HTTP ОШИБКА при обращении к Replicate: {http_err} - {http_err.response.text}")
        return jsonify({'error': f'Ошибка связи с сервисом генерации: {http_err.response.status_code}'}), 500
    except Exception as e:
        print(f"!!! ОБЩАЯ ОШИБКА в process_image:\n{e}")
        error_message_detail = str(e)
        if hasattr(e, 'response') and e.response is not None:
             try:
                error_details_from_replicate = e.response.json()
                error_message_detail = error_details_from_replicate.get("detail", str(e))
             except ValueError: 
                error_message_detail = e.response.text if e.response.text else str(e)

        return jsonify({'error': f'Произошла внутренняя ошибка сервера: {error_message_detail}'}), 500

if __name__ == '__main__':  
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get("PORT", 5001)))
