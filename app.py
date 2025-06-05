import os
import boto3
import uuid
import requests
import time
import openai
from flask import Flask, request, jsonify, render_template_string, url_for, redirect
from flask_sqlalchemy import SQLAlchemy
# --- ИЗМЕНЕНИЯ В ИМПОРТАХ ---
from flask_security import Security, SQLAlchemyUserDatastore, UserMixin, RoleMixin, login_required, current_user, utils as security_utils
from flask_security.forms import LoginForm, RegisterForm, ChangePasswordForm
# -----------------------------
from flask_mail import Mail
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import relationship
from flask_security import AsaList
from flask_babel import Babel

# --- Настройки для подключения к Amazon S3 ---
AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
AWS_S3_BUCKET_NAME = os.environ.get('AWS_S3_BUCKET_NAME')
AWS_S3_REGION = os.environ.get('AWS_S3_REGION')

# Инициализируем Flask приложение
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'YOUR_VERY_SECRET_KEY_HERE_CHANGE_ME_IN_PROD')
app.config['SECURITY_PASSWORD_SALT'] = os.environ.get('FLASK_SECURITY_PASSWORD_SALT', 'YOUR_VERY_SECRET_SALT_HERE_CHANGE_ME_IN_PROD')

app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///site.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# --- ИЗМЕНЕНИЯ В НАСТРОЙКАХ FLASK-SECURITY ---
app.config['SECURITY_REGISTERABLE'] = True
app.config['SECURITY_SEND_REGISTER_EMAIL'] = False
app.config['SECURITY_RECOVERABLE'] = True
app.config['SECURITY_CHANGEABLE'] = True
app.config['SECURITY_CONFIRMABLE'] = False
app.config['SECURITY_USERNAME_ENABLE'] = True
app.config['SECURITY_USERNAME_REQUIRED'] = False
app.config['SECURITY_EMAIL_VALIDATOR_ARGS'] = {"check_deliverability": False}

# Указываем, куда перенаправлять пользователя после действий
app.config['SECURITY_POST_LOGIN_VIEW'] = '/'
app.config['SECURITY_POST_LOGOUT_VIEW'] = '/'
app.config['SECURITY_POST_REGISTER_VIEW'] = '/'
app.config['SECURITY_CHANGE_URL'] = '/change-password' # Оставляем, чтобы POST запросы работали

# Удаляем указание на старые шаблоны - ОНИ БОЛЬШЕ НЕ НУЖНЫ
# app.config['SECURITY_LOGIN_TEMPLATE'] = '...'
# app.config['SECURITY_REGISTER_TEMPLATE'] = '...'
# app.config['SECURITY_CHANGE_PASSWORD_TEMPLATE'] = '...'
# --- КОНЕЦ ИЗМЕНЕНИЙ В НАСТРОЙКАХ ---


app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.googlemail.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', 'true').lower() == 'true'
app.config['MAIL_USE_SSL'] = os.environ.get('MAIL_USE_SSL', 'false').lower() == 'true'
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER', ("Changer AI", 'noreply@example.com'))
app.config['SECURITY_EMAIL_SENDER'] = app.config['MAIL_DEFAULT_SENDER']

db = SQLAlchemy(app)
mail = Mail(app)

app.config['BABEL_DEFAULT_LOCALE'] = 'ru'
babel = Babel(app)

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
# --- ИЗМЕНЕНИЕ В ИНИЦИАЛИЗАЦИИ SECURITY ---
# Передаем классы форм, чтобы Flask-Security знал, как их валидировать
security = Security(app, user_datastore,
                    login_form=LoginForm,
                    register_form=RegisterForm,
                    change_password_form=ChangePasswordForm)

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
        @font-face {
            font-family: 'ChangerFont';
            src: url("{{ url_for('static', filename='fonts/FONT_TEXT.woff2') }}") format('woff2');
            font-weight: normal;
            font-style: normal;
        }
        :root {
            --text-accent-color: #D9F47A; --controls-bg-color: #F8F8F8; --controls-bg-color-transparent: rgba(248, 248, 248, 0.8);
            --blur-intensity: 8px; --mob-spacing-unit: 5px; --desktop-spacing-unit: 8px; --download-icon-size: 28px;
            --header-text-color-on-light-bg: #333333; --header-border-radius: 22px; --coin-color: #D9F47A;
            --header-vertical-padding: 15px; --header-logo-height-mob: 30px; --header-logo-height-desk: 35px;
            --footer-height-mob: 70px; --action-buttons-height-mob: 50px; --footer-height-desk: 80px; --action-buttons-height-desk: 60px;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'ChangerFont', sans-serif; color: var(--text-accent-color); background-size: cover;
            background-position: center center; background-repeat: no-repeat; background-attachment: fixed;
            display: flex; flex-direction: column; min-height: 100vh; overflow-x: hidden;
        }
        .app-container-wrapper {
            position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background-size: cover; background-position: center center; background-repeat: no-repeat;
            z-index: -1; transition: filter 0.4s ease-in-out;
            background-image: url("{{ url_for('static', filename='images/MOB_BACK.png') }}");
        }
        .app-container-wrapper.bg-blur { filter: blur(var(--blur-intensity)); }
        .app-container {
            width: 100%; max-width: 1200px; margin: 0 auto; padding-left: var(--mob-spacing-unit); padding-right: var(--mob-spacing-unit);
            padding-top: calc(var(--header-logo-height-mob) + var(--header-vertical-padding) * 2 + var(--mob-spacing-unit));
            padding-bottom: calc(10vh + var(--footer-height-mob) + var(--action-buttons-height-mob));
            display: flex; flex-direction: column; align-items: center; flex-grow: 1; position: relative; z-index: 1;
        }
        .page-header-container { position: fixed; top: 0; left: 0; right: 0; width: 100%; z-index: 105; display: flex; justify-content: center; }
        .page-header-inner { width: 100%; max-width: 1200px; padding: var(--header-vertical-padding) 25px; display: flex; justify-content: space-between; align-items: center; }
        .app-logo-link { display: inline-block; line-height: 0; }
        .logo { height: var(--header-logo-height-mob); cursor: pointer; display: block;}
        .top-right-nav { position: relative; display: flex; align-items: center; }
        .user-controls-loggedin {
            display: flex; align-items: center; background-color: var(--controls-bg-color-transparent);
            backdrop-filter: blur(var(--blur-intensity)); -webkit-backdrop-filter: blur(var(--blur-intensity));
            padding: 6px 6px 6px 12px; border-radius: var(--header-border-radius); gap: 8px;
        }
        .token-display { display: flex; align-items: center; color: var(--header-text-color-on-light-bg); font-size: 0.85rem; font-weight: normal; }
        .token-coin { width: 16px; height: 16px; background-color: var(--coin-color); border-radius: 50%; margin-left: 5px; }
        .burger-menu-btn {
            background-color: var(--text-accent-color); border: none; border-radius: 50%; padding: 0; cursor: pointer;
            width: 34px; height: 34px; display: flex; align-items: center; justify-content: center;
            transition: background-color: 0.3s ease, transform 0.3s ease; position: relative;
        }
        .dropdown-menu {
            position: absolute; top: calc(100% + 8px); right: 0; background-color: rgba(248, 248, 248, 0.9);
            backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); border-radius: 12px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.15); padding: 12px; width: 220px; z-index: 1000;
            opacity: 0; visibility: hidden; transform: translateY(-8px) scale(0.95); transform-origin: top right;
            transition: opacity 0.25s ease, transform 0.25s ease, visibility 0s 0.25s linear;
        }
        .dropdown-menu.open { opacity: 1; visibility: visible; transform: translateY(0) scale(1); transition: opacity 0.25s ease, transform 0.25s ease, visibility 0s 0s linear; }
        .dropdown-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; padding-bottom: 8px; border-bottom: 1px solid rgba(0,0,0,0.08); }
        .dropdown-user-email { color: #333; font-size: 0.9rem; font-weight: bold; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex-grow: 1; }
        .dropdown-menu ul { list-style: none; padding: 0; margin: 0; }
        .dropdown-menu li a { display: block; padding: 8px 0; color: #333; text-decoration: none; font-size: 0.9rem; transition: color 0.2s ease; }
        .dropdown-menu li a:hover { color: var(--text-accent-color); }
        .user-controls-loggedout {
            background-color: var(--controls-bg-color-transparent); backdrop-filter: blur(var(--blur-intensity));
            -webkit-backdrop-filter: blur(var(--blur-intensity)); padding: 8px 15px; border-radius: var(--header-border-radius);
            display: flex; align-items: center;
        }
        .user-controls-loggedout .auth-button { color: var(--header-text-color-on-light-bg); text-decoration: none; font-size: 0.85rem; font-weight: normal; }
        .user-controls-loggedout .auth-button:hover { text-decoration: underline; }
        .user-controls-loggedout .auth-separator { color: var(--header-text-color-on-light-bg); margin: 0 6px; opacity: 0.6; }

        /* --- СТИЛИ КОНТЕНТА, СКОПИРОВАНЫ ИЗ ВАШЕГО ФАЙЛА --- */
        .app-main { width: 100%; display: flex; flex-direction: column; align-items: center; justify-content: flex-start; flex-grow: 1; gap: var(--mob-spacing-unit); }
        .initial-top-group { width: 100%;}
        .desktop-main-text-img { display: none; max-width: 1200px; width: auto; max-height: 85vh; object-fit: contain; margin-top: 12vh; }
        .mobile-main-text-img { display: block; max-height: 20vh; max-width: 90%; object-fit: contain; position: fixed; top: calc(var(--header-logo-height-mob) + var(--header-vertical-padding) * 2 + 20px); left: 50%; transform: translateX(-50%); z-index: 10; }
        .image-drop-area-mobile { width: 80%; max-width: 280px; height: 165px; background-color: transparent; border-radius: 25px; display: flex; justify-content: center; align-items: center; cursor: pointer; position: fixed; overflow: hidden; border: 2px dashed rgba(248, 248, 248, 0.3); top: calc(var(--header-logo-height-mob) + var(--header-vertical-padding) * 2 + 20px + 20vh + 15px); left: 50%; transform: translateX(-50%); z-index: 10; }
        .action-buttons { display: flex; justify-content: center; align-items: center; gap: 10px; flex-wrap: nowrap; width: calc(100% - calc(2 * var(--mob-spacing-unit))); max-width: 320px; padding: 5px 0; position: fixed; bottom: calc(5vh + var(--footer-height-mob) - 10px); left: 50%; transform: translateX(-50%); z-index: 99; }
        .result-image-wrapper { justify-content: center; display: inline-flex; align-items: center; width: auto; max-width: 100%; position: relative; }
        #result-image { max-width: 100%; max-height: 60vh; object-fit: contain; border-radius: 12px; box-shadow: 0 6px 20px rgba(0,0,0,0.25); display: block; }
        .download-action-link { display: none; position: absolute; bottom: calc(-1 * (var(--download-icon-size) + 5px)); right: 0; z-index: 10; cursor: pointer; padding: 5px; line-height: 0; }
        .loader-container { justify-content: center; align-items: center; min-height: 200px; z-index: 101; flex-grow: 1; display: flex; }
        .app-footer { width: calc(100% - calc(2 * var(--mob-spacing-unit))); max-width: 500px; padding: 0; position: fixed; bottom: 5vh; left: 50%; transform: translateX(-50%); z-index: 100; }
        .input-area { display: flex; align-items: center; background-color: var(--controls-bg-color-transparent); backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); border-radius: 50px; padding: 6px 8px; width: 100%; box-shadow: 0 4px 15px rgba(0,0,0,0.1); border: 2px dashed transparent; }
        #prompt { flex-grow: 1; border: none; padding: 12px 10px; font-size: 0.9rem; background-color: transparent; outline: none; color: #333333; font-family: 'ChangerFont', sans-serif; line-height: 1.3; }
        .error-message { display: none; margin-top: 10px; font-size: 0.9rem; color: var(--text-accent-color); background-color: rgba(0,0,0,0.65); backdrop-filter: blur(5px); padding: 10px 15px; border-radius: 8px; position: fixed; bottom: calc(var(--footer-height-mob) + var(--action-buttons-height-mob) + var(--mob-spacing-unit) * 2); left: 50%; transform: translateX(-50%); width: calc(100% - calc(4 * var(--mob-spacing-unit))); max-width: 480px; z-index: 105; text-align: center; }

        /* --- НОВЫЕ СТИЛИ ДЛЯ ФОРМ АВТОРИЗАЦИИ --- */
        .auth-container {
            width: 100%;
            display: flex;
            justify-content: center;
            align-items: center;
            flex-grow: 1;
            padding: 20px;
        }
        .auth-form-wrapper {
            background-color: var(--controls-bg-color-transparent);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            padding: 30px 40px;
            border-radius: var(--header-border-radius);
            box-shadow: 0 8px 30px rgba(0,0,0,0.2);
            width: 100%;
            max-width: 450px;
            text-align: center;
        }
        .auth-form-wrapper h2 {
            font-family: 'ChangerFont', sans-serif;
            color: var(--header-text-color-on-light-bg);
            margin-bottom: 25px;
            font-size: 1.8rem;
        }
        .auth-form-wrapper .form-control {
            font-family: 'ChangerFont', sans-serif;
            width: 100%;
            padding: 12px 10px;
            margin-bottom: 15px;
            border: 1px solid rgba(0,0,0,0.1);
            border-radius: 8px;
            background-color: rgba(255,255,255,0.7);
            color: var(--header-text-color-on-light-bg);
            font-size: 0.9rem;
        }
        .auth-form-wrapper .form-control::placeholder { color: #888; }
        .auth-form-wrapper .btn {
            font-family: 'ChangerFont', sans-serif;
            background-color: var(--text-accent-color);
            color: var(--header-text-color-on-light-bg);
            border: none;
            padding: 12px 20px;
            border-radius: 25px;
            cursor: pointer;
            font-size: 1rem;
            transition: background-color 0.3s ease;
            width: 100%;
            max-width: 200px;
            display: inline-block;
            text-decoration: none;
            margin-top: 10px;
        }
        .auth-form-wrapper .btn:hover { background-color: #c8e070; }
        .auth-form-wrapper ul { list-style: none; padding: 0; margin: 20px 0 0 0; }
        .auth-form-wrapper ul li { margin-bottom: 8px; }
        .auth-form-wrapper ul li a {
            font-family: 'ChangerFont', sans-serif;
            color: #555;
            text-decoration: none;
            font-size: 0.85rem;
        }
        .auth-form-wrapper ul li a:hover { text-decoration: underline; }
        .auth-form-wrapper ul.fs-errorlist {
            list-style: none;
            padding: 0;
            margin: 0 0 15px 0;
        }
        .auth-form-wrapper ul.fs-errorlist li {
            background-color: rgba(220, 53, 69, 0.1);
            color: #dc3545;
            padding: 8px 12px;
            border-radius: 6px;
            font-size: 0.85rem;
        }

        @media (min-width: 769px) {
            .app-container-wrapper { background-image: url("{{ url_for('static', filename='images/DESK_BACK.png') }}"); }
            .logo { height: var(--header-logo-height-desk); }
            .user-controls-loggedout .auth-button { font-size: 1rem; }
            /* ... и другие ваши @media стили, которые я не трогал ... */
        }
    </style>
</head>
<body>
    <div class="app-container-wrapper" id="app-bg-wrapper"></div>
    <div class="page-header-container">
        <div class="page-header-inner">
            <a href="{{ url_for('index') }}" class="app-logo-link">
                <img src="{{ url_for('static', filename='images/LOGO_CHANGER.svg') }}" alt="Changer Logo" class="logo">
            </a>
            <div class="top-right-nav">
                {% if current_user.is_authenticated %}
                    <div class="user-controls-loggedin">
                        <span class="token-display">
                            <span id="token-balance-display">{{ current_user.token_balance }}</span>
                            <span class="token-coin"></span>
                        </span>
                        <button class="burger-menu-btn" id="burger-menu-toggle" aria-label="Меню пользователя" aria-expanded="false">
                            <svg class="burger-icon" viewBox="0 0 100 80"><rect class="line line1" x="0" y="0" width="100" height="12" rx="6"></rect><rect class="line line2" x="0" y="34" width="100" height="12" rx="6"></rect><rect class="line line3" x="0" y="68" width="100" height="12" rx="6"></rect></svg>
                            <svg class="close-icon" viewBox="0 0 80 80"><line class="line" x1="20" y1="20" x2="60" y2="60"/><line class="line" x1="60" y1="20" x2="20" y2="60"/></svg>
                        </button>
                    </div>
                    <div class="dropdown-menu" id="dropdown-menu">
                        <div class="dropdown-header">
                            <span class="dropdown-user-email">{{ current_user.email or current_user.username }}</span>
                            <button class="close-menu-btn" id="close-menu-btn-inner" aria-label="Закрыть меню"><svg viewBox="0 0 100 100" width="18" height="18"><line x1="10" y1="10" x2="90" y2="90"/><line x1="10" y1="90" x2="90" y2="10"/></svg></button>
                        </div>
                        <ul>
                            <li><a href="{{ url_for('buy_tokens_page') }}">пополнить баланс</a></li>
                            <li><a href="{{ url_for('index', form='change_password') }}">Сменить пароль</a></li>
                            <li><a href="{{ url_for_security('logout') }}">Выйти</a></li>
                        </ul>
                    </div>
                {% else %}
                    <div class="user-controls-loggedout">
                        <a href="{{ url_for('index', form='login') }}" class="auth-button">Логин</a>
                        <span class="auth-separator">|</span>
                        <a href="{{ url_for('index', form='register') }}" class="auth-button">Регистрация</a>
                    </div>
                {% endif %}
            </div>
        </div>
    </div>
    <div class="app-container">
        <div id="main-app-content" style="display: {% if view == 'main' %}flex{% else %}none{% endif %}; flex-direction: column; align-items: center; width: 100%; flex-grow: 1;">
            <main class="app-main auth-required-content">
                <div class="initial-top-group"> <img src="{{ url_for('static', filename='images/DESK_MAIN.png') }}" class="main-text-display-img desktop-main-text-img"> <img src="{{ url_for('static', filename='images/MOB_MAIN.svg') }}" class="main-text-display-img mobile-main-text-img"> <label for="image-file-common" class="image-drop-area-mobile"> <img src="{{ url_for('static', filename='images/JDTI.png') }}" class="mob-drop-placeholder-img"> <img id="image-preview-mobile" src="#" class="image-preview-mobile-img"> </label> </div> <div class="result-image-wrapper"> <img id="result-image" src="" alt="Generated Image"> <a href="#" id="download-action" class="download-action-link" download="generated_image.png" target="_blank" rel="noopener noreferrer"> <img src="{{ url_for('static', filename='images/Download.png') }}" class="download-button-icon"> </a> </div> <div id="loader" class="loader-container"> <div class="pulsating-dot"></div> </div> <div class="action-buttons"> <div class="action-btn" data-action="create"><img src="{{ url_for('static', filename='images/Create.png') }}"></div> <div class="action-btn" data-action="relight"><img src="{{ url_for('static', filename='images/relight.png') }}"></div> <div class="action-btn" data-action="remove"><img src="{{ url_for('static', filename='images/remove.png') }}"></div> <div class="action-btn" data-action="change"><img src="{{ url_for('static', filename='images/change.png') }}"></div> </div>
            </main>
            <footer class="app-footer auth-required-content">
                <form id="edit-form" class="input-area"> <label for="image-file-common" class="file-upload-label-desktop"> <img src="{{ url_for('static', filename='images/DESK_UPLOAD.png') }}" class="upload-icon-desktop-img"> <img id="image-preview-desktop" src="#" class="image-preview-desktop-img"> </label> <input type="file" id="image-file-common" name="image" accept="image/*"> <input type="text" id="prompt" name="prompt" placeholder="TYPE WHAT YOU WANT TO CHANGE"> <button type="submit" id="submit-button" class="submit-button-element"> <img src="{{ url_for('static', filename='images/MAGIC_GREEN.png') }}" id="magic-button-icon-img" class="submit-button-icon-img"> <span id="submit-button-text-content" class="submit-button-text-content">Start over</span> </button> </form>
            </footer>
        </div>
        <div id="auth-container" class="auth-container" style="display: {% if view != 'main' %}flex{% else %}none{% endif %};">
            <div class="auth-form-wrapper">
                {# --- Форма Логина --- #}
                {% if view == 'login' and login_form %}
                    <h2>Вход</h2>
                    {% if login_form.errors %}
                        <ul class="fs-errorlist">
                        {% for field, errors in login_form.errors.items() %}
                            {% if field != 'csrf_token' %}
                                {% for error in errors %}<li>{{ error }}</li>{% endfor %}
                            {% endif %}
                        {% endfor %}
                        </ul>
                    {% endif %}
                    <form action="{{ url_for_security('login') }}" method="POST" name="login_user_form">
                        {{ login_form.hidden_tag() }}
                        <div class="form-group {% if login_form.email.errors %}has-errors{% endif %}">
                            {{ login_form.email(class="form-control", placeholder="Email или имя пользователя") }}
                        </div>
                        <div class="form-group {% if login_form.password.errors %}has-errors{% endif %}">
                            {{ login_form.password(class="form-control", placeholder="Пароль") }}
                        </div>
                        {{ login_form.submit(class="btn", value="Войти") }}
                    </form>
                    <ul>
                        <li><a href="{{ url_for('index', form='register') }}">Создать аккаунт</a></li>
                        <li><a href="{{ url_for_security('forgot_password') }}">Забыли пароль?</a></li>
                    </ul>
                {% endif %}
                {# --- Форма Регистрации --- #}
                {% if view == 'register' and register_form %}
                    <h2>Регистрация</h2>
                     {% if register_form.errors %}
                        <ul class="fs-errorlist">
                        {% for field, errors in register_form.errors.items() %}
                             {% if field != 'csrf_token' %}
                                {% for error in errors %}<li>{{ error }}</li>{% endfor %}
                             {% endif %}
                        {% endfor %}
                        </ul>
                    {% endif %}
                    <form action="{{ url_for_security('register') }}" method="POST" name="register_user_form">
                        {{ register_form.hidden_tag() }}
                        <div class="form-group">{{ register_form.email(class="form-control", placeholder="Ваш Email") }}</div>
                        {% if security.username_enable %}
                            <div class="form-group">{{ register_form.username(class="form-control", placeholder="Имя пользователя (опционально)") }}</div>
                        {% endif %}
                        <div class="form-group">{{ register_form.password(class="form-control", placeholder="Пароль") }}</div>
                        {% if register_form.password_confirm %}
                            <div class="form-group">{{ register_form.password_confirm(class="form-control", placeholder="Подтвердите пароль") }}</div>
                        {% endif %}
                        {{ register_form.submit(class="btn", value="Зарегистрироваться") }}
                    </form>
                    <ul>
                        <li><a href="{{ url_for('index', form='login') }}">Уже есть аккаунт? Войти</a></li>
                    </ul>
                {% endif %}
                {# --- Форма Смены Пароля --- #}
                 {% if view == 'change_password' and change_password_form %}
                    <h2>Смена пароля</h2>
                    {% if change_password_form.errors %}
                        <ul class="fs-errorlist">
                        {% for field, errors in change_password_form.errors.items() %}
                             {% if field != 'csrf_token' %}
                                {% for error in errors %}<li>{{ error }}</li>{% endfor %}
                            {% endif %}
                        {% endfor %}
                        </ul>
                    {% endif %}
                    <form action="{{ url_for_security('change_password') }}" method="POST" name="change_password_form">
                        {{ change_password_form.hidden_tag() }}
                        <div class="form-group">{{ change_password_form.password(class="form-control", placeholder="Текущий пароль") }}</div>
                        <div class="form-group">{{ change_password_form.new_password(class="form-control", placeholder="Новый пароль") }}</div>
                        {% if change_password_form.new_password_confirm %}
                            <div class="form-group">{{ change_password_form.new_password_confirm(class="form-control", placeholder="Подтвердите новый пароль") }}</div>
                        {% endif %}
                        {{ change_password_form.submit(class="btn", value="Сменить пароль") }}
                    </form>
                {% endif %}
            </div>
        </div>
        <div id="error-box" class="error-message"></div>
    </div>
    <script>
        // ВАШ JAVASCRIPT НЕ ТРОНУТ
        // --- DOM Elements ---
        const tokenBalanceDisplaySpan = document.getElementById('token-balance-display');
        const burgerMenuToggle = document.getElementById('burger-menu-toggle');
        const dropdownMenu = document.getElementById('dropdown-menu');
        const closeMenuBtnInner = document.getElementById('close-menu-btn-inner');
        if (burgerMenuToggle && dropdownMenu) {
            burgerMenuToggle.addEventListener('click', (e) => { e.stopPropagation(); dropdownMenu.classList.toggle('open'); burgerMenuToggle.classList.toggle('open'); });
        }
        if (closeMenuBtnInner && dropdownMenu && burgerMenuToggle) {
            closeMenuBtnInner.addEventListener('click', (e) => { e.stopPropagation(); dropdownMenu.classList.remove('open'); burgerMenuToggle.classList.remove('open'); });
        }
        document.addEventListener('click', function(event) { if (dropdownMenu && burgerMenuToggle && dropdownMenu.classList.contains('open')) { const isClickInsideMenu = dropdownMenu.contains(event.target); const isClickOnBurger = burgerMenuToggle.contains(event.target); if (!isClickInsideMenu && !isClickOnBurger) { dropdownMenu.classList.remove('open'); burgerMenuToggle.classList.remove('open'); } } });
        // ... и весь остальной ваш JS ...
    </script>
</body>
</html>
"""

# --- ОБНОВЛЕННЫЙ МАРШРУТ ДЛЯ ГЛАВНОЙ СТРАНИЦЫ ---
@app.route('/')
def index():
    view_name = request.args.get('form', 'main')
    context = {"view": view_name}
    
    # После неудачной попытки входа/регистрации Flask-Security перенаправляет обратно,
    # и формы с ошибками уже находятся в контексте. Если же мы заходим по прямой
    # ссылке, нам нужно получить чистую форму.
    if view_name == 'login' and 'login_user_form' not in context:
        context['login_form'] = security_utils.get_form('login_form')
    elif view_name == 'register' and 'register_user_form' not in context:
        context['register_form'] = security_utils.get_form('register_form')
    elif view_name == 'change_password':
        if current_user.is_authenticated:
            if 'change_password_form' not in context:
                 context['change_password_form'] = security_utils.get_form('change_password_form')
        else:
            return redirect(url_for('index', form='login'))
            
    return render_template_string(INDEX_HTML, **context)


@app.route('/buy-tokens')
@login_required
def buy_tokens_page():
    # ВАШ КОД ОСТАЛСЯ БЕЗ ИЗМЕНЕНИЙ
    return """
        ...
    """

def improve_prompt_with_openai(user_prompt):
    # ВАШ КОД ОСТАЛСЯ БЕЗ ИЗМЕНЕНИЙ
    pass

@app.route('/process-image', methods=['POST'])
@login_required
def process_image():
    # ВАШ КОД ОСТАЛСЯ БЕЗ ИЗМЕНЕНИЙ
    pass

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get("PORT", 5001)))
