import os
import boto3
import uuid
import requests
import time
import openai
from flask import Flask, request, jsonify, render_template_string, url_for, redirect, flash
from flask_sqlalchemy import SQLAlchemy
from flask_security import Security, SQLAlchemyUserDatastore, UserMixin, RoleMixin, login_required, current_user, roles_accepted, roles_required
from flask_security.utils import hash_password
from flask_mail import Mail # Flask-Security-Too использует Flask-Mail
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import relationship, backref
from flask_security import AsaList


# --- Настройки для подключения к Amazon S3 ---
AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
AWS_S3_BUCKET_NAME = os.environ.get('AWS_S3_BUCKET_NAME')
AWS_S3_REGION = os.environ.get('AWS_S3_REGION')

# Инициализируем Flask приложение
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'super-secret-key-for-dev-only') 
app.config['SECURITY_PASSWORD_SALT'] = os.environ.get('FLASK_SECURITY_PASSWORD_SALT', 'super-secret-salt-for-dev-only') 

app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///site.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

app.config['SECURITY_REGISTERABLE'] = True 
app.config['SECURITY_SEND_REGISTER_EMAIL'] = False 
app.config['SECURITY_RECOVERABLE'] = True 
app.config['SECURITY_CHANGEABLE'] = True 
app.config['SECURITY_CONFIRMABLE'] = False 
app.config['SECURITY_USERNAME_ENABLE'] = True 
app.config['SECURITY_USERNAME_REQUIRED'] = False 
app.config['SECURITY_EMAIL_VALIDATOR_ARGS'] = {"check_deliverability": False} 
app.config['SECURITY_POST_LOGIN_VIEW'] = '/' # Куда перенаправлять после логина
app.config['SECURITY_POST_LOGOUT_VIEW'] = '/' # Куда перенаправлять после выхода
app.config['SECURITY_POST_REGISTER_VIEW'] = '/' # Куда перенаправлять после регистрации


app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.googlemail.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', 'true').lower() == 'true'
app.config['MAIL_USE_SSL'] = os.environ.get('MAIL_USE_SSL', 'false').lower() == 'true'
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER', 'noreply@example.com')
app.config['SECURITY_EMAIL_SENDER'] = app.config['MAIL_DEFAULT_SENDER']


db = SQLAlchemy(app)
mail = Mail(app)


roles_users = db.Table(
    "roles_users",
    db.Column("user_id", db.Integer(), db.ForeignKey("user.id")),
    db.Column("role_id", db.Integer(), db.ForeignKey("role.id")),
)

class Role(db.Model, RoleMixin):
    id = db.Column(db.Integer(), primary_key=True)
    name = db.Column(db.String(80), unique=True)
    description = db.Column(db.String(255))
    permissions = db.Column(MutableList.as_mutable(AsaList()), nullable=True)

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    username = db.Column(db.String(255), unique=True, nullable=True) 
    password = db.Column(db.String(255), nullable=False)
    active = db.Column(db.Boolean(), default=True)
    fs_uniquifier = db.Column(db.String(255), unique=True, nullable=False) 
    confirmed_at = db.Column(db.DateTime())
    roles = db.relationship(
        "Role", secondary=roles_users, backref=db.backref("users", lazy="dynamic")
    )
    token_balance = db.Column(db.Integer, default=10, nullable=False)

user_datastore = SQLAlchemyUserDatastore(db, User, Role)
security = Security(app, user_datastore)


app.static_folder = 'static'

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
        /* ... (предыдущие стили остаются здесь, я их не удаляю) ... */
        @font-face {
            font-family: 'ChangerFont';
            src: url("{{ url_for('static', filename='fonts/FONT_TEXT.woff2') }}") format('woff2');
            font-weight: normal;
            font-style: normal;
        }

        :root {
            --text-accent-color: #D9F47A; /* Основной акцентный цвет (желто-зеленый) */
            --controls-bg-color: #F8F8F8; /* Светлый фон для контролов */
            --blur-intensity: 8px;
            --mob-spacing-unit: 20px;
            --desktop-spacing-unit: 30px;
            --download-icon-size: 28px; 
            --footer-height: 70px; 
            --action-buttons-height: 60px; 
            --header-elements-bg: rgba(248, 248, 248, 0.1); /* Полупрозрачный фон для элементов хедера */
            --header-elements-blur: 5px; /* Блюр для элементов хедера */
            --header-text-color: #FFFFFF; /* Белый текст для хедера на темном фоне */
            --header-border-radius: 25px; /* Скругление для элементов хедера */
            --coin-color: #D9F47A; /* Цвет монетки */
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: 'ChangerFont', sans-serif;
            color: var(--text-accent-color); /* Это основной цвет текста, но для хедера будет другой */
            background-size: cover;
            background-position: center center;
            background-repeat: no-repeat;
            background-attachment: fixed;
            display: flex;
            flex-direction: column;
            min-height: 100vh;
            overflow-x: hidden;
            transition: filter 0.4s ease-in-out;
        }
        
        .app-container-wrapper {
            position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background-size: cover; background-position: center center; background-repeat: no-repeat;
            z-index: -1; transition: filter 0.4s ease-in-out;
            background-image: url("{{ url_for('static', filename='images/MOB_BACK.png') }}"); 
        }
        .app-container-wrapper.bg-blur { filter: blur(var(--blur-intensity)); }

        .app-container {
            width: 100%; max-width: 1200px; margin: 0 auto; padding: var(--mob-spacing-unit);
            display: flex; flex-direction: column; align-items: center;
            flex-grow: 1; position: relative; z-index: 1;
        }

        .app-header { /* Контейнер для лого и навигации справа */
            width: 100%;
            padding: 0 var(--mob-spacing-unit); /* Паддинги по бокам */
            display: flex;
            justify-content: space-between; /* Лого слева, навигация справа */
            align-items: center;
            position: absolute;
            top: var(--mob-spacing-unit);
            left: 0; 
            right: 0;
            z-index: 100;
            max-width: 1200px; /* Чтобы совпадало с app-container */
            margin: 0 auto; /* Центрирование, если app-container уже */
        }

        .logo { height: 30px; cursor: pointer; }

        /* Новый блок для элементов в правом верхнем углу */
        .top-right-nav {
            position: relative; /* Для позиционирования выпадающего меню */
            display: flex;
            align-items: center;
        }

        .user-controls-loggedin {
            display: flex;
            align-items: center;
            background-color: var(--header-elements-bg);
            backdrop-filter: blur(var(--header-elements-blur));
            -webkit-backdrop-filter: blur(var(--header-elements-blur));
            padding: 8px 8px 8px 15px; /* Паддинг слева больше для баланса */
            border-radius: var(--header-border-radius);
            gap: 10px;
        }

        .token-display, .token-display-menu {
            display: flex;
            align-items: center;
            color: var(--header-text-color);
            font-size: 0.9rem;
            font-weight: bold;
        }
        .token-coin {
            width: 18px;
            height: 18px;
            background-color: var(--coin-color);
            border-radius: 50%;
            margin-left: 6px;
            box-shadow: 0 0 5px rgba(217, 244, 122, 0.7);
        }

        .burger-menu-btn {
            background-color: var(--text-accent-color);
            border: none;
            border-radius: 50%; /* Круглая кнопка для бургера */
            padding: 0;
            cursor: pointer;
            width: 38px;
            height: 38px;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: background-color 0.3s ease;
        }
        .burger-menu-btn:hover {
            background-color: #c8e070; /* Темнее при наведении */
        }
        .burger-menu-btn svg {
            display: block;
            transition: transform 0.3s ease-in-out, opacity 0.3s ease-in-out;
             fill: #333; /* Темный цвет для линий бургера/крестика */
        }
         .burger-menu-btn svg line, .burger-menu-btn svg rect {
            stroke: #333; /* Для SVG с line */
            fill: #333; /* Для SVG с rect */
        }

        .burger-menu-btn .burger-icon line, .burger-menu-btn .burger-icon rect { transition: transform 0.3s 0.1s ease-in-out; }
        .burger-menu-btn.open .burger-icon .line1 { transform: rotate(45deg) translate(18px, -18px); }
        .burger-menu-btn.open .burger-icon .line2 { opacity: 0; transform: translateX(-20px); }
        .burger-menu-btn.open .burger-icon .line3 { transform: rotate(-45deg) translate(18px, 18px); }
        
        .burger-menu-btn.open .close-icon { display: block; transform: rotate(0deg); opacity: 1;}
        .burger-menu-btn .close-icon { display: none; transform: rotate(-45deg); opacity: 0;}


        .dropdown-menu {
            position: absolute;
            top: calc(100% + 10px); /* 10px отступ снизу от кнопки */
            right: 0;
            background-color: rgba(248, 248, 248, 0.85); /* Чуть менее прозрачный для читаемости */
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border-radius: 15px;
            box-shadow: 0 5px 25px rgba(0,0,0,0.2);
            padding: 15px;
            width: 220px; /* Фиксированная ширина меню */
            z-index: 1000;
            opacity: 0;
            visibility: hidden;
            transform: translateY(-10px);
            transition: opacity 0.3s ease, transform 0.3s ease, visibility 0s 0.3s linear;
        }
        .dropdown-menu.open {
            opacity: 1;
            visibility: visible;
            transform: translateY(0);
            transition: opacity 0.3s ease, transform 0.3s ease, visibility 0s 0s linear;
        }
        .dropdown-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 1px solid rgba(0,0,0,0.1);
        }
         .dropdown-header .token-display-menu .token-coin {
             box-shadow: none; /* Убрать тень у монетки в меню */
         }
        .dropdown-header .token-display-menu { color: #333; } /* Темный текст для баланса в меню */

        .close-menu-btn {
            background: none; border: none; padding: 0; cursor: pointer;
            display: flex; align-items: center; justify-content: center;
        }
        .close-menu-btn svg { stroke: #555; } /* Цвет крестика закрытия */

        .dropdown-menu ul { list-style: none; padding: 0; margin: 0; }
        .dropdown-menu li a {
            display: block;
            padding: 10px 0;
            color: #333; /* Темный текст для ссылок */
            text-decoration: none;
            font-size: 0.95rem;
            transition: color 0.2s ease;
        }
        .dropdown-menu li a:hover { color: var(--text-accent-color); }

        .user-controls-loggedout {
            background-color: var(--header-elements-bg);
            backdrop-filter: blur(var(--header-elements-blur));
            -webkit-backdrop-filter: blur(var(--header-elements-blur));
            padding: 10px 20px;
            border-radius: var(--header-border-radius);
            display: flex;
            align-items: center;
        }
        .user-controls-loggedout .auth-button {
            color: var(--header-text-color);
            text-decoration: none;
            font-size: 0.9rem;
            font-weight: bold;
        }
        .user-controls-loggedout .auth-button:hover { text-decoration: underline; }
        .user-controls-loggedout .auth-separator {
            color: var(--header-text-color);
            margin: 0 8px;
            opacity: 0.7;
        }

        /* Остальные стили остаются без изменений */
        .app-main { /* Увеличим отступ снизу, чтобы фиксированные элементы не перекрывали контент */
             padding-bottom: calc(var(--footer-height) + var(--action-buttons-height) + var(--mob-spacing-unit) * 3 + 20px); 
        }
        .action-buttons { bottom: calc(var(--footer-height) + var(--mob-spacing-unit) + 25px); } /* Поднимаем баблы еще выше */

        @media (min-width: 769px) {
            .logo { height: 35px; }
            .app-header { padding: 0 var(--desktop-spacing-unit); top: var(--desktop-spacing-unit); }
            .app-main { padding-bottom: calc(var(--footer-height) + var(--action-buttons-height) + var(--desktop-spacing-unit) * 3 + 20px); }
            .action-buttons { bottom: calc(var(--footer-height) + var(--desktop-spacing-unit) + 25px); }
            .user-controls-loggedin { gap: 15px; padding: 10px 10px 10px 20px; }
            .token-display, .token-display-menu { font-size: 1rem; }
            .token-coin { width: 20px; height: 20px; }
            .burger-menu-btn { width: 42px; height: 42px; }
            .user-controls-loggedout { padding: 12px 25px; }
            .user-controls-loggedout .auth-button { font-size: 1rem; }
        }
        /* ... (остальные ваши стили) ... */
    </style>
</head>
<body>
    <!-- Новый блок для навигации в правом верхнем углу -->
    <div class="top-right-nav">
        {% if current_user.is_authenticated %}
            <div class="user-controls-loggedin">
                <span class="token-display">
                    <span id="token-balance-display">{{ current_user.token_balance }}</span>
                    <span class="token-coin"></span>
                </span>
                <button class="burger-menu-btn" id="burger-menu-toggle" aria-label="Меню пользователя" aria-expanded="false">
                    <svg class="burger-icon" viewBox="0 0 100 80" width="24" height="24">
                        <rect class="line1" width="100" height="12" rx="6"></rect>
                        <rect class="line2" y="34" width="100" height="12" rx="6"></rect>
                        <rect class="line3" y="68" width="100" height="12" rx="6"></rect>
                    </svg>
                </button>
            </div>
            <div class="dropdown-menu" id="dropdown-menu">
                <div class="dropdown-header">
                     <span class="token-display-menu">
                        <span id="token-balance-dropdown">{{ current_user.token_balance }}</span>
                        <span class="token-coin"></span>
                    </span>
                    <button class="close-menu-btn" id="close-menu-btn-inner" aria-label="Закрыть меню">
                         <svg viewBox="0 0 100 100" width="20" height="20">
                            <line x1="10" y1="10" x2="90" y2="90" stroke-width="12" stroke-linecap="round"/>
                            <line x1="10" y1="90" x2="90" y2="10" stroke-width="12" stroke-linecap="round"/>
                        </svg>
                    </button>
                </div>
                <ul>
                    <li><a href="{{ url_for('buy_tokens_page') }}">пополнить баланс</a></li>
                    <li><a href="{{ url_for_security('change_password') }}">Сменить пароль</a></li>
                    <li><a href="{{ url_for_security('logout') }}">Выйти</a></li>
                </ul>
            </div>
        {% else %}
            <div class="user-controls-loggedout">
                <a href="{{ url_for_security('login') }}" class="auth-button">Логин</a>
                <span class="auth-separator">|</span>
                <a href="{{ url_for_security('register') }}" class="auth-button">Регистрация</a>
            </div>
        {% endif %}
    </div>

    <div class="app-container-wrapper" id="app-bg-wrapper"></div>
    <div class="app-container">
        <header class="app-header">
            <img src="{{ url_for('static', filename='images/LOGO_CHANGER.svg') }}" alt="Changer Logo" class="logo">
            <!-- Элементы .top-right-nav теперь будут здесь, если они не абсолютно позиционированы относительно viewport -->
        </header>

        <main class="app-main auth-required-content">
            <!-- ... (остальной HTML вашего .app-main) ... -->
             <div class="initial-top-group">
                <img src="{{ url_for('static', filename='images/DESK_MAIN.png') }}" alt="Change Everything" class="main-text-display-img desktop-main-text-img">
                <img src="{{ url_for('static', filename='images/MOB_MAIN.svg') }}" alt="Change Everything" class="main-text-display-img mobile-main-text-img">

                <label for="image-file-common" class="image-drop-area-mobile">
                    <img src="{{ url_for('static', filename='images/JDTI.png') }}" alt="Just drop the image" class="mob-drop-placeholder-img">
                    <img id="image-preview-mobile" src="#" alt="Preview" class="image-preview-mobile-img">
                </label>
            </div>

            <div class="result-image-wrapper">
                <img id="result-image" src="" alt="Generated Image">
                <a href="#" id="download-action" class="download-action-link" download="generated_image.png" target="_blank" rel="noopener noreferrer">
                    <img src="{{ url_for('static', filename='images/Download.png') }}" alt="Скачать" class="download-button-icon">
                </a>
            </div>
            
            <div id="loader" class="loader-container">
                <div class="pulsating-dot"></div>
            </div>

            <div class="action-buttons">
                <div class="action-btn" data-action="create"><img src="{{ url_for('static', filename='images/Create.png') }}" alt="Create"></div>
                <div class="action-btn" data-action="relight"><img src="{{ url_for('static', filename='images/relight.png') }}" alt="Relight"></div>
                <div class="action-btn" data-action="remove"><img src="{{ url_for('static', filename='images/remove.png') }}" alt="Remove"></div>
                <div class="action-btn" data-action="change"><img src="{{ url_for('static', filename='images/change.png') }}" alt="Change"></div>
            </div>
        </main>

        <footer class="app-footer auth-required-content">
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
    const tokenBalanceSpan = document.getElementById('token-balance-display'); // Обновленный ID
    const tokenBalanceDropdownSpan = document.getElementById('token-balance-dropdown');
    const burgerMenuToggle = document.getElementById('burger-menu-toggle');
    const dropdownMenu = document.getElementById('dropdown-menu');
    const closeMenuBtnInner = document.getElementById('close-menu-btn-inner');

    function updateTokenBalanceDisplay(newBalance) {
        if (tokenBalanceSpan) {
            tokenBalanceSpan.textContent = newBalance;
        }
        if (tokenBalanceDropdownSpan) {
            tokenBalanceDropdownSpan.textContent = newBalance;
        }
    }

    if (burgerMenuToggle && dropdownMenu) {
        burgerMenuToggle.addEventListener('click', () => {
            const isOpen = burgerMenuToggle.getAttribute('aria-expanded') === 'true';
            burgerMenuToggle.setAttribute('aria-expanded', !isOpen);
            dropdownMenu.classList.toggle('open');
            burgerMenuToggle.classList.toggle('open'); // Для анимации бургера в крестик
        });
    }
    if (closeMenuBtnInner && dropdownMenu && burgerMenuToggle) {
         closeMenuBtnInner.addEventListener('click', () => {
            burgerMenuToggle.setAttribute('aria-expanded', 'false');
            dropdownMenu.classList.remove('open');
            burgerMenuToggle.classList.remove('open');
        });
    }
    
    // Закрытие меню по клику вне его
    document.addEventListener('click', function(event) {
        if (dropdownMenu && burgerMenuToggle && dropdownMenu.classList.contains('open')) {
            const isClickInsideMenu = dropdownMenu.contains(event.target);
            const isClickOnBurger = burgerMenuToggle.contains(event.target);
            if (!isClickInsideMenu && !isClickOnBurger) {
                burgerMenuToggle.setAttribute('aria-expanded', 'false');
                dropdownMenu.classList.remove('open');
                burgerMenuToggle.classList.remove('open');
            }
        }
    });


    // --- Остальной JavaScript код ---
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
    const inputArea = document.querySelector('.input-area'); 
    const submitButton = document.getElementById('submit-button');
    const magicButtonIconImg = document.getElementById('magic-button-icon-img');
    const submitButtonTextContent = document.getElementById('submit-button-text-content');

    const initialTopGroup = document.querySelector('.initial-top-group'); 
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
        if(errorBox) {
            errorBox.textContent = message;
            errorBox.style.display = 'block';
            setTimeout(() => { errorBox.style.display = 'none'; }, 4000);
        } else {
            console.error("Error box not found for message:", message);
        }
    }

    function updateView(viewName) {
        currentView = viewName;
        
        if(appBgWrapper) appBgWrapper.classList.remove('bg-blur');
        
        if (initialTopGroup) initialTopGroup.style.display = 'none';
        if (resultImageWrapper) resultImageWrapper.style.display = 'none';
        if (loaderContainer) loaderContainer.style.display = 'none';
        if (downloadLink) downloadLink.style.display = 'none'; 
        
        if (actionButtonsContainer) {
            if (viewName === 'loading') {
                actionButtonsContainer.style.display = 'none';
            } else {
                actionButtonsContainer.style.display = 'flex';
            }
        }

        if (mobileMainTextImg) mobileMainTextImg.style.display = 'none'; 
        if (desktopMainTextImg) desktopMainTextImg.style.display = 'none'; 
        if (mobileDropArea) mobileDropArea.style.display = 'none'; 

        if (viewName === 'initial') {
            if (initialTopGroup) initialTopGroup.style.display = 'flex';
            if (isDesktopView()) {
                if (desktopMainTextImg) desktopMainTextImg.style.display = 'block';
            } else { 
                if (mobileMainTextImg) mobileMainTextImg.style.display = 'block';
                if (mobileDropArea) mobileDropArea.style.display = 'flex';
            }
            if (magicButtonIconImg) magicButtonIconImg.style.display = 'block';
            if (submitButtonTextContent) submitButtonTextContent.style.display = 'none';
            if (submitButton) submitButton.dataset.action = "generate";
            if (promptInput) promptInput.value = ''; 
            resetImagePreviews();
        } else if (viewName === 'loading') {
            if (loaderContainer) loaderContainer.style.display = 'flex';
            if (appBgWrapper) appBgWrapper.classList.add('bg-blur'); 
            if (initialTopGroup) initialTopGroup.style.display = 'none';
            if (resultImageWrapper) resultImageWrapper.style.display = 'none';
        } else if (viewName === 'result') {
            if (resultImageWrapper) resultImageWrapper.style.display = 'inline-flex'; 
            if (downloadLink) downloadLink.style.display = 'block'; 
            if (appBgWrapper) appBgWrapper.classList.add('bg-blur'); 

            if (!isDesktopView()) { 
                if (magicButtonIconImg) magicButtonIconImg.style.display = 'none';
                if (submitButtonTextContent) submitButtonTextContent.style.display = 'block';
                if (submitButton) submitButton.dataset.action = "startover";
            } else { 
                if (magicButtonIconImg) magicButtonIconImg.style.display = 'block';
                if (submitButtonTextContent) submitButtonTextContent.style.display = 'none';
                if (submitButton) submitButton.dataset.action = "generate"; 
            }
        }
        adjustLayoutForResultImage();
    }
    
    function adjustLayoutForResultImage() {
        if (currentView === 'result' && resultImage && resultImage.src && resultImage.src !== window.location.href + "#") {
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
    
    function handleFileSelect(file) {
        if (file && imageFileInput) { 
            const dataTransfer = new DataTransfer();
            dataTransfer.items.add(file);
            imageFileInput.files = dataTransfer.files;
            
            const event = new Event('change', { bubbles: true });
            imageFileInput.dispatchEvent(event);
        }
    }

    function setupDragAndDrop(dropZoneElement, isPromptArea = false) {
        if (!dropZoneElement) return;

        dropZoneElement.addEventListener('dragover', (event) => {
            event.preventDefault();
            event.stopPropagation();
            dropZoneElement.classList.add('dragover');
        });

        dropZoneElement.addEventListener('dragleave', (event) => {
            event.preventDefault();
            event.stopPropagation();
            dropZoneElement.classList.remove('dragover');
        });

        dropZoneElement.addEventListener('drop', (event) => {
            event.preventDefault();
            event.stopPropagation();
            dropZoneElement.classList.remove('dragover');
            
            if (event.dataTransfer.files && event.dataTransfer.files[0]) {
                handleFileSelect(event.dataTransfer.files[0]);
                if (isPromptArea && isDesktopView()) {
                    if (desktopImagePreviewImg && desktopUploadIconImg) {
                        const reader = new FileReader();
                        reader.onload = function(e_preview) {
                            desktopImagePreviewImg.src = e_preview.target.result;
                            desktopImagePreviewImg.style.display = 'block';
                            desktopUploadIconImg.style.display = 'none';
                        }
                        reader.readAsDataURL(event.dataTransfer.files[0]);
                    }
                }
            }
        });
    }

    if (mobileDropArea) setupDragAndDrop(mobileDropArea);
    if (desktopUploadLabel) setupDragAndDrop(desktopUploadLabel);
    if (inputArea) setupDragAndDrop(inputArea, true); 


    if (imageFileInput) {
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
    }


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
        if (imageFileInput) imageFileInput.value = ''; 
    }

    if (editForm) {
        editForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            
            if (submitButton.dataset.action === "startover") {
                updateView('initial');
                return;
            }

            if (!imageFileInput || !imageFileInput.files || imageFileInput.files.length === 0) {
                showError("Пожалуйста, выберите файл для загрузки.");
                return;
            }
            if (!promptInput || !promptInput.value.trim()) {
                showError("Пожалуйста, введите текстовый промпт.");
                return;
            }

            if(submitButton) submitButton.disabled = true;
            if (errorBox) errorBox.style.display = 'none';
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
                    let errorDetail = data.error || data.detail || 'Неизвестная ошибка сервера';
                    if (response.status === 403 && (data.error === 'Недостаточно токенов' || data.detail === 'Недостаточно токенов')) { // Adjusted to check data.detail too
                         errorDetail = 'У вас недостаточно токенов для генерации. Пожалуйста, пополните баланс.';
                    }
                    throw new Error(errorDetail);
                }


                if(resultImage) resultImage.src = data.output_url;
                if(downloadLink) downloadLink.href = data.output_url;
                if (data.new_token_balance !== undefined) { // Check if new_token_balance is in response
                    updateTokenBalanceDisplay(data.new_token_balance);
                }
                
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
                console.error('Ошибка при отправке/обработке:', error);
                showError("Произошла ошибка: " + error.message);
                updateView('initial'); 
            } finally {
                if(submitButton) submitButton.disabled = false;
            }
        });
    }

    document.querySelectorAll('.action-btn').forEach(button => {
        button.addEventListener('click', (e) => {
            const action = e.currentTarget.dataset.action;
            let prefillText = "";
            if (action === "create") {
                prefillText = "Based on a [object] from the image create [describe the scene]";
            } else if (action === "relight") {
                prefillText = "Relight [object or scene] and make it [describe the light]";
            } else if (action === "remove") {
                prefillText = "Remove [object] from the image";
            } else if (action === "change") {
                prefillText = "Change the [object you want to be changed] to the [object you want to be added]";
            }
            
            if(promptInput) {
                promptInput.value = prefillText;
                promptInput.focus();
            }
        });
    });
    
    const logoElement = document.querySelector('.logo');
    if (logoElement) {
        logoElement.addEventListener('click', () => {
            if (currentView !== 'loading') { 
                 updateView('initial');
            }
        });
    }
    
    // Initial setup of view based on authentication status (passed from Flask)
    // This is a simplified way; ideally, Flask-Security handles redirects for protected routes.
    // const isAuthenticated = {{ current_user.is_authenticated | tojson }};
    // if (!isAuthenticated) {
    //    // Hide main content if not logged in, or redirect (Flask-Security handles redirects better)
    //    const mainContentElements = document.querySelectorAll('.auth-required-content');
    //    mainContentElements.forEach(el => el.style.display = 'none');
    // } else {
    //    updateView('initial');
    // }
    // For now, let's assume Flask-Security's @login_required handles access control.
    // We just need to ensure the correct elements are shown/hidden by updateView.
    updateView('initial');


    </script>
</body>
</html>
"""

# Маршрут для главной страницы
@app.route('/')
# @login_required # Если главная страница требует логина, раскомментируйте
def index():
    return render_template_string(INDEX_HTML)

@app.route('/buy-tokens')
@login_required
def buy_tokens_page():
    # Здесь будет ваша логика для отображения опций покупки токенов
    # и интеграции с Tilda или другой платежной системой.
    # Пока это просто заглушка.
    # Вы можете передать URL для возврата после "оплаты" через Tilda, например.
    return """
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <title>Покупка токенов</title>
            <style>
                body { font-family: sans-serif; margin: 20px; background-color: #f4f4f4; color: #333; }
                .container { background-color: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }
                h1 { color: #333; }
                a { color: #007bff; text-decoration: none; }
                a:hover { text-decoration: underline; }
                .button { display: inline-block; padding: 10px 20px; background-color: #D9F47A; color: #333; border-radius: 5px; text-decoration: none; margin-top:15px;}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Купить токены</h1>
                <p>Привет, {{ current_user.email }}!</p>
                <p>Ваш текущий баланс: <strong>{{ current_user.token_balance }}</strong> токенов.</p>
                <p>Здесь будет информация о пакетах токенов и кнопка для перехода к оплате (например, на Tilda).</p>
                
                <!-- Пример кнопки, которая может вести на Tilda -->
                <!-- <a href="YOUR_TILDA_PAYMENT_PAGE_URL?user_id={{current_user.id}}" class="button">Перейти к покупке</a> -->
                
                <p>Для теста, администратор может вручную пополнить ваш баланс.</p>
                <p><a href="{{ url_for('index') }}">Вернуться на главную</a></p>
            </div>
        </body>
        </html>
    """

# Python-часть для обработки запросов
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
@login_required 
def process_image():
    if current_user.token_balance < 1:
        return jsonify({'error': 'Недостаточно токенов'}), 403 # Forbidden

    if 'image' not in request.files or 'prompt' not in request.form:
        return jsonify({'error': 'Отсутствует изображение или промпт'}), 400

    image_file = request.files['image']
    original_prompt_text = request.form['prompt']
    final_prompt_text = improve_prompt_with_openai(original_prompt_text)
    
    model_version_id = "black-forest-labs/flux-kontext-max:0b9c317b23e79a9a0d8b9602ff4d04030d433055927fb7c4b91c44234a6818c4"

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
            "input": {"input_image": hosted_image_url, "prompt": final_prompt_text} 
        }
        
        start_response = requests.post("https://api.replicate.com/v1/predictions", json=post_payload, headers=headers)
        
        if start_response.status_code >= 400:
            print(f"!!! Ошибка от Replicate при запуске предсказания: {start_response.status_code} - {start_response.text}")
            try:
                error_data = start_response.json()
                detail = error_data.get("detail", start_response.text)
                return jsonify({'error': f'Ошибка API Replicate: {detail}'}), start_response.status_code
            except ValueError: 
                 return jsonify({'error': f'Ошибка API Replicate: {start_response.text}'}), start_response.status_code

        prediction_data = start_response.json()
        get_url = prediction_data["urls"]["get"]
        
        output_url = None
        max_retries = 60 
        retries = 0
        while retries < max_retries:
            time.sleep(2) 
            get_response = requests.get(get_url, headers=headers)
            if get_response.status_code >= 400:
                print(f"!!! Ошибка от Replicate при получении статуса: {get_response.status_code} - {get_response.text}")
                try:
                    error_data = get_response.json()
                    detail = error_data.get("detail", get_response.text)
                    raise Exception(f"Ошибка API Replicate при проверке статуса: {detail}")
                except ValueError:
                    raise Exception(f"Ошибка API Replicate при проверке статуса: {get_response.text}")

            status_data = get_response.json()
            print(f"Статус генерации Replicate: {status_data['status']}")
            
            if status_data["status"] == "succeeded":
                if isinstance(status_data["output"], list): 
                    output_url = status_data["output"][0] 
                else: 
                    output_url = str(status_data["output"]) 
                
                current_user.token_balance -= 1
                db.session.commit()
                break
            elif status_data["status"] in ["failed", "canceled"]:
                error_detail = status_data.get('error', 'неизвестная ошибка Replicate')
                raise Exception(f"Генерация Replicate не удалась: {error_detail}")
            retries += 1
        
        if not output_url:
            return jsonify({'error': 'Генерация Replicate заняла слишком много времени или не вернула результат.'}), 500
            
        return jsonify({'output_url': output_url, 'new_token_balance': current_user.token_balance})
        
    except Exception as e:
        print(f"!!! ОБЩАЯ ОШИБКА в process_image:\n{e}")
        return jsonify({'error': f'Произошла внутренняя ошибка сервера: {str(e)}'}), 500

if __name__ == '__main__':  
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get("PORT", 5001)))
