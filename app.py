import os
import boto3
import uuid
import requests
import time
import openai
from flask import Flask, request, jsonify, render_template, render_template_string, url_for, redirect, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, BooleanField
from wtforms.validators import DataRequired, Email, EqualTo, Length

# --- Настройки ---
AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
AWS_S3_BUCKET_NAME = os.environ.get('AWS_S3_BUCKET_NAME')
AWS_S3_REGION = os.environ.get('AWS_S3_REGION')

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'a-very-secret-key-for-flask-login')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///site.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- Настройка Flask-Login ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = "Пожалуйста, войдите, чтобы получить доступ к этой странице."
login_manager.login_message_category = "info"

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- Модели ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    username = db.Column(db.String(255), unique=True, nullable=True)
    password = db.Column(db.String(255), nullable=False)
    token_balance = db.Column(db.Integer, default=10, nullable=False)
    marketing_consent = db.Column(db.Boolean, nullable=False, default=True)
    subscription_status = db.Column(db.String(50), default='inactive', nullable=False)
    stripe_customer_id = db.Column(db.String(255), nullable=True, unique=True)
    stripe_subscription_id = db.Column(db.String(255), nullable=True, unique=True)

    @property
    def is_active(self):
        return True

# --- Формы ---
class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Пароль', validators=[DataRequired()])
    remember = BooleanField('Запомнить меня')
    submit = SubmitField('Войти')

class RegisterForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    username = StringField('Имя пользователя (опционально)')
    password = PasswordField('Пароль', validators=[DataRequired(), Length(min=6)])
    password_confirm = PasswordField('Подтвердите пароль', validators=[DataRequired(), EqualTo('password', message='Пароли должны совпадать')])
    accept_tos = BooleanField(
        'Я принимаю условия использования сервиса',
        validators=[DataRequired(message="Вы должны принять условия использования.")]
    )
    marketing_consent = BooleanField(
        'Я согласен на получение маркетинговых сообщений',
        default=True
    )
    submit = SubmitField('Регистрация')

class ChangePasswordForm(FlaskForm):
    old_password = PasswordField('Текущий пароль', validators=[DataRequired()])
    new_password = PasswordField('Новый пароль', validators=[DataRequired(), Length(min=6)])
    new_password_confirm = PasswordField('Подтвердите новый пароль', validators=[DataRequired(), EqualTo('new_password', message='Пароли должны совпадать')])
    submit = SubmitField('Сменить пароль')

app.static_folder = 'static'

REPLICATE_API_TOKEN = os.environ.get('REPLICATE_API_TOKEN')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

if OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY
else:
    print("!!! ВНИМАНИЕ: OPENAI_API_KEY не найден. Улучшение промптов не будет работать.")

# --- МАРШРУТЫ АУТЕНТИФИКАЦИИ ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user and check_password_hash(user.password, form.password.data):
            login_user(user, remember=form.remember.data)
            return redirect(url_for('index'))
        else:
            flash('Неверный email или пароль.', 'error')
    return render_template('custom_login_user.html', form=form)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = RegisterForm()
    if form.validate_on_submit():
        existing_user = User.query.filter_by(email=form.email.data).first()
        if existing_user:
            flash('Пользователь с таким email уже существует.', 'error')
            return redirect(url_for('register'))

        hashed_password = generate_password_hash(form.password.data, method='pbkdf2:sha256')
        new_user = User(
            email=form.email.data,
            username=form.username.data,
            password=hashed_password,
            marketing_consent=form.marketing_consent.data
        )
        db.session.add(new_user)
        db.session.commit()
        login_user(new_user)
        return redirect(url_for('index'))
    return render_template('custom_register_user.html', form=form)

@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not check_password_hash(current_user.password, form.old_password.data):
            flash('Неверный текущий пароль.', 'error')
            return redirect(url_for('change_password'))

        current_user.password = generate_password_hash(form.new_password.data, method='pbkdf2:sha256')
        db.session.commit()
        flash('Ваш пароль успешно изменен!', 'success')
        return redirect(url_for('index'))
    return render_template('custom_change_password.html', form=form)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

# --- Главная страница и API ---

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
            --accent-color: #D9F47A;
            --bg-color: #1A1A1A;
            --surface-color: #2C2C2C;
            --surface-color-glass: rgba(44, 44, 44, 0.7);
            --primary-text-color: #F0F0F0;
            --secondary-text-color: #A0A0A0;
            --accent-text-color: #2C2C2C;
            --border-color: rgba(255, 255, 255, 0.15);
            --border-hover-color: rgba(217, 244, 122, 0.7);

            --blur-intensity: 20px;
            --content-border-radius: 24px;
            --element-border-radius: 16px;
            --button-border-radius: 14px;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: 'ChangerFont', sans-serif;
            color: var(--primary-text-color);
            background-color: var(--bg-color);
            display: flex; flex-direction: column;
            min-height: 100vh; overflow: hidden;
        }

        .app-container-wrapper {
            position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: -1;
            background: linear-gradient(45deg, #2f213a, #1a1a1a, #2c2c2c, #1f3438, #1a1a1a);
            background-size: 400% 400%;
            animation: gradientBG 20s ease infinite;
        }

        @keyframes gradientBG {
            0% { background-position: 0% 50%; }
            50% { background-position: 100% 50%; }
            100% { background-position: 0% 50%; }
        }

        .app-container {
            width: 100%; max-width: 1200px; margin: 0 auto;
            padding: 100px 25px 25px;
            display: flex; flex-direction: row; align-items: flex-start;
            gap: 25px; height: 100vh;
        }

        .page-header-container {
            position: fixed; top: 0; left: 0; right: 0; width: 100%; z-index: 105;
            display: flex; justify-content: center; padding: 12px 0;
            background: rgba(26, 26, 26, 0.5);
            backdrop-filter: blur(var(--blur-intensity));
            -webkit-backdrop-filter: blur(var(--blur-intensity));
        }
        .page-header-inner {
            width: 100%; max-width: 1200px; padding: 0 25px;
            display: flex; justify-content: space-between; align-items: center;
        }
        .header-left-group { display: flex; align-items: center; gap: 20px; }
        .logo { height: 35px; }

        .top-right-nav { position: relative; display: flex; align-items: center; }

        .mode-selector {
            display: flex; align-items: center; background-color: var(--bg-color);
            padding: 6px; border-radius: var(--content-border-radius); gap: 6px;
        }
        .mode-btn {
            background-color: transparent; border: none; padding: 8px 18px;
            border-radius: 18px; cursor: pointer;
            font-family: 'ChangerFont', sans-serif; font-size: 0.9rem;
            color: var(--secondary-text-color);
            transition: background-color 0.3s ease, color 0.3s ease;
        }
        .mode-btn.active {
            background-color: var(--accent-color); color: var(--accent-text-color);
        }
        .mode-btn:not(.active):hover { color: var(--primary-text-color); }
        
        /* ... Other header styles from previous version ... */
        .user-controls-loggedin {
            display: flex; align-items: center; background-color: var(--surface-color);
            padding: 8px 8px 8px 18px; border-radius: var(--content-border-radius); gap: 12px;
        }
        .token-display {
            display: flex; align-items: center; color: var(--primary-text-color);
            font-size: 1rem;
        }
        .token-coin {
            width: 18px; height: 18px; background-color: var(--accent-color);
            border-radius: 50%; margin-left: 8px;
        }
        .burger-menu-btn {
            background-color: var(--accent-color); border: none; border-radius: 50%;
            padding: 0; cursor: pointer; width: 38px; height: 38px;
            display: flex; align-items: center; justify-content: center;
            transition: transform 0.3s ease; position: relative;
        }
        .burger-menu-btn:hover { transform: scale(1.1); }
        .burger-menu-btn svg {
            display: block; position: absolute; top: 50%; left: 50%;
            transform: translate(-50%, -50%);
            transition: transform 0.3s ease-in-out, opacity 0.3s ease-in-out;
        }
        .burger-menu-btn svg .line { stroke: var(--accent-text-color); stroke-width:10; stroke-linecap:round; transition: transform 0.3s 0.05s ease-in-out, opacity 0.2s ease-in-out; transform-origin: 50% 50%;}
        .burger-menu-btn.open .burger-icon .line1 { transform: translateY(10px) rotate(45deg) scaleX(1.2); }
        .burger-menu-btn.open .burger-icon .line2 { opacity: 0; }
        .burger-menu-btn.open .burger-icon .line3 { transform: translateY(-10px) rotate(-45deg) scaleX(1.2); }
        .dropdown-menu {
            position: absolute; top: calc(100% + 10px); right: 0;
            background-color: rgba(60, 60, 60, 0.95);
            backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
            border-radius: var(--element-border-radius); box-shadow: 0 8px 30px rgba(0,0,0,0.3);
            border: 1px solid rgba(255,255,255,0.1); padding: 12px; width: 240px; z-index: 1000;
            opacity: 0; visibility: hidden; transform: translateY(-10px) scale(0.95);
            transform-origin: top right;
            transition: opacity 0.25s ease, transform 0.25s ease, visibility 0.25s;
        }
        .dropdown-menu.open { opacity: 1; visibility: visible; transform: translateY(0) scale(1); }
        .user-controls-loggedout {
            background-color: var(--surface-color); padding: 8px 20px; border-radius: var(--content-border-radius);
        }

        .content-wrapper {
            width: 100%; max-width: 440px; height: auto;
            padding: 32px;
            background: var(--surface-color-glass);
            border-radius: var(--content-border-radius);
            backdrop-filter: blur(var(--blur-intensity));
            -webkit-backdrop-filter: blur(var(--blur-intensity));
            border: 1px solid rgba(255, 255, 255, 0.1);
            box-shadow: 0 15px 35px rgba(0, 0, 0, 0.3);
            transition: opacity 0.3s, filter 0.3s;
        }
        .content-wrapper.disabled { opacity: 0.5; pointer-events: none; }

        #upscale-view, #edit-view {
            width: 100%; display: flex; flex-direction: column;
            align-items: center; justify-content: flex-start; gap: 24px;
        }

        .image-inputs-container {
            display: flex; justify-content: center; gap: 16px; width: 100%;
        }
        .image-inputs-container.merge-mode .image-drop-area { flex: 1; max-width: none; }

        .image-drop-area {
            width: 100%; max-width: 320px; height: 180px;
            background-color: rgba(0, 0, 0, 0.2);
            border-radius: var(--element-border-radius);
            display: flex; flex-direction: column; justify-content: center; align-items: center;
            cursor: pointer; position: relative; overflow: hidden;
            border: 1px dashed var(--border-color);
            transition: border-color 0.3s ease, background-color 0.3s ease;
        }
        .image-drop-area:hover, .image-drop-area.dragover {
             border-color: var(--border-hover-color);
             background-color: rgba(217, 244, 122, 0.05);
        }
        .drop-placeholder-text {
            color: var(--secondary-text-color); font-size: 1rem; font-weight: 300;
        }
        .image-preview-img {
            display: none; width: 100%; height: 100%; object-fit: cover;
            position: absolute; z-index: 1;
        }

        #result-area-right {
            flex: 1; height: 100%; display: none;
            justify-content: center; align-items: center; padding: 20px;
        }
        #result-image {
            max-width: 100%; max-height: 100%;
            object-fit: contain; border-radius: 12px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.5);
        }
        
        .loader-container { display: flex; } /* Simplified */

        .input-area {
            display: flex; align-items: center; background-color: rgba(0,0,0,0.2);
            border-radius: var(--button-border-radius); padding: 4px; width: 100%;
            border: 1px solid var(--border-color); transition: border-color 0.3s, background-color 0.3s;
        }
        .input-area:focus-within { border-color: var(--accent-color); background-color: rgba(0,0,0,0.3); }
        #prompt {
            flex-grow: 1; border: none; padding: 12px 15px; font-size: 0.95rem;
            background-color: transparent; outline: none; color: var(--primary-text-color);
            font-family: 'ChangerFont', sans-serif;
        }
        #prompt::placeholder { color: var(--secondary-text-color); font-weight: 300; }

        .submit-action-group {
            width: 100%; display: flex; flex-direction: column; align-items: center; gap: 12px; margin-top: 16px;
        }
        .submit-button-element {
            width: 100%; background-color: var(--accent-color); color: var(--accent-text-color);
            border: none; cursor: pointer; padding: 16px;
            border-radius: var(--button-border-radius); font-size: 1.1rem; font-weight: 600;
            font-family: 'ChangerFont', sans-serif;
            box-shadow: 0 5px 15px rgba(217, 244, 122, 0.2);
            transition: all 0.2s cubic-bezier(0.25, 0.8, 0.25, 1);
        }
        .submit-button-element:hover {
            transform: translateY(-3px);
            box-shadow: 0 8px 20px rgba(217, 244, 122, 0.25);
        }
        .submit-button-element:active { transform: translateY(0); }

        .control-group { width: 100%; display: flex; flex-direction: column; gap: 12px; }
        
        .mode-description {
            font-size: 0.9rem; color: var(--secondary-text-color); text-align: center;
            width: 100%; line-height: 1.6; min-height: 3.5em; font-weight: 300;
        }

        .edit-mode-selector, .template-selector, .resolution-selector {
            display: flex; gap: 10px; width: 100%;
        }
        .template-selector { flex-wrap: wrap; }

        .edit-mode-btn, .template-btn, .resolution-btn {
            flex: 1 1 auto; padding: 12px;
            border-radius: var(--button-border-radius);
            background-color: transparent;
            border: 1px solid var(--border-color);
            color: var(--secondary-text-color);
            cursor: pointer; font-family: 'ChangerFont', sans-serif;
            font-size: 0.85rem;
            transition: all 0.25s ease;
            text-align: center;
        }
        .edit-mode-btn:hover, .template-btn:hover, .resolution-btn:hover {
            color: var(--primary-text-color);
            background-color: rgba(255, 255, 255, 0.05);
            border-color: rgba(255, 255, 255, 0.25);
        }
        .edit-mode-btn.active, .template-btn.active, .resolution-btn.active {
            background-color: var(--accent-color);
            border-color: var(--accent-color);
            color: var(--accent-text-color);
            font-weight: 600;
        }
        
        .slider-container { width: 100%; }
        /* Slider styles are good, can remain as is */

        .token-cost {
            display: flex; justify-content: center; align-items: center; gap: 8px;
            font-size: 0.85rem; color: var(--secondary-text-color); font-weight: 300;
        }
        .token-cost .token-coin { width: 12px; height: 12px; margin: 0; }
        
        /* Other styles like error messages, media queries etc. can remain as is */
    </style>
</head>
<body>
    <div class="app-container-wrapper" id="app-bg-wrapper"></div>
    <div class="page-header-container">
        <div class="page-header-inner">
            <div class="header-left-group">
                <a href="{{ url_for('index') }}" class="app-logo-link">
                    <img src="{{ url_for('static', filename='images/LOGO_CHANGER.svg') }}" alt="Changer Logo" class="logo">
                </a>
                <div class="mode-selector">
                    <button class="mode-btn active" data-mode="edit">Edit</button>
                    <button class="mode-btn" data-mode="upscale">Upscale</button>
                </div>
            </div>
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
                         <ul>
                            <li><a href="{{ url_for('buy_tokens_page') }}">пополнить баланс</a></li>
                            <li><a href="{{ url_for('change_password') }}">Сменить пароль</a></li>
                            <li><a href="{{ url_for('logout') }}">Выйти</a></li>
                        </ul>
                    </div>
                {% else %}
                    <div class="user-controls-loggedout">
                        <a href="{{ url_for('login') }}" class="auth-button">Логин</a>
                        <span class="auth-separator">/</span>
                        <a href="{{ url_for('register') }}" class="auth-button">Регистрация</a>
                    </div>
                {% endif %}
            </div>
        </div>
    </div>

    <div class="app-container">
        <div class="content-wrapper" id="main-content-wrapper">
            <div id="edit-view">
                <p id="edit-mode-description" class="mode-description"></p>
                <div class="control-group">
                    <div class="edit-mode-selector">
                        <button class="edit-mode-btn active" data-edit-mode="edit" data-description="Use this tool to add or remove objects, and to modify the style or lighting of your image.">Edit</button>
                        <button class="edit-mode-btn" data-edit-mode="merge" data-description="Merge two images, integrate new items into your shot, or transfer the style from a reference image.">Merge</button>
                        <button class="edit-mode-btn" data-edit-mode="autofix" data-description="Simply upload your image for automatic artifact removal and quality enhancement.">Auto fix</button>
                    </div>
                </div>

                <div class="image-inputs-container">
                    <label for="image-file-edit-1" id="image-drop-area-edit-1" class="image-drop-area">
                        <span class="drop-placeholder-text">just drop the image</span>
                        <img id="image-preview-edit-1" src="#" alt="Preview" class="image-preview-img">
                    </label>
                    <label for="image-file-edit-2" id="image-drop-area-edit-2" class="image-drop-area" style="display: none;">
                        <span class="drop-placeholder-text">just drop the image</span>
                        <img id="image-preview-edit-2" src="#" alt="Preview" class="image-preview-img">
                    </label>
                </div>
                <input type="file" id="image-file-edit-1" name="image1" accept="image/*" style="display: none;">
                <input type="file" id="image-file-edit-2" name="image2" accept="image/*" style="display: none;">

                <div id="edit-controls-container" style="width:100%; display:flex; flex-direction:column; gap: 15px;">
                    <div class="control-group">
                         <div class="template-selector">
                            <button class="template-btn" data-prompt="hyperrealistic photo of a modern object">Create</button>
                            <button class="template-btn" data-prompt="dramatic studio lighting, cinematic relighting">Relight</button>
                            <button class="template-btn" data-prompt="remove the main object">Remove</button>
                            <button class="template-btn" data-prompt="change background to a detailed city street">Change</button>
                        </div>
                    </div>

                    <form id="edit-form" class="input-area">
                         <input type="text" id="prompt" name="prompt" placeholder="Type what you want to change...">
                    </form>
                </div>

                <div class="submit-action-group">
                    <button type="submit" id="submit-button-edit" class="submit-button-element">Generate</button>
                    <div class="token-cost">
                        <span>Estimated cost: 1</span>
                        <span class="token-coin"></span>
                    </div>
                </div>
            </div>

            <div id="upscale-view" style="display: none;">
                <div class="image-inputs-container">
                    <label for="image-file-upscale" class="image-drop-area">
                        <span class="drop-placeholder-text">just drop the image</span>
                        <img id="image-preview-upscale" src="#" alt="Preview" class="image-preview-img">
                    </label>
                </div>
                <input type="file" id="image-file-upscale" name="image" accept="image/*" style="display: none;">

                <div class="control-group">
                    <div class="resolution-selector">
                        <button class="resolution-btn active" data-value="x2">x2</button>
                        <button class="resolution-btn" data-value="x4">x4</button>
                        <button class="resolution-btn" data-value="x8">x8</button>
                    </div>
                </div>
                
                <div class="submit-action-group">
                    <button type="submit" id="submit-button-upscale" class="submit-button-element">Upscale</button>
                    <div class="token-cost">
                        <span>Estimated cost: 5</span>
                        <span class="token-coin"></span>
                    </div>
                </div>
            </div>
        </div>

        <div id="result-area-right">
             <div class="loader-container">
                <div class="pulsating-dot"></div>
            </div>
            <div class="result-image-wrapper">
                <img id="result-image" src="" alt="Generated Image">
            </div>
        </div>
    </div>
    
    <div id="error-box" class="error-message"></div>

    <script>
    // The entire JavaScript block from the previous version should be pasted here.
    // It is omitted for brevity in this display, but will be in the final code block.
    // No functional changes are needed in the script.
    document.addEventListener('DOMContentLoaded', () => {

    const tokenBalanceDisplaySpan = document.getElementById('token-balance-display');
    const burgerMenuToggle = document.getElementById('burger-menu-toggle');
    const dropdownMenu = document.getElementById('dropdown-menu');
    const mainContentWrapper = document.getElementById('main-content-wrapper');
    const resultAreaRight = document.getElementById('result-area-right');
    const appModeButtons = document.querySelectorAll('.mode-btn');
    const editView = document.getElementById('edit-view');
    const upscaleView = document.getElementById('upscale-view');

    // --- Burger Menu Logic ---
    if (burgerMenuToggle) {
        burgerMenuToggle.addEventListener('click', (e) => {
            e.stopPropagation();
            const isOpen = burgerMenuToggle.classList.toggle('open');
            burgerMenuToggle.setAttribute('aria-expanded', String(isOpen));
            dropdownMenu.classList.toggle('open');
        });
    }

    document.addEventListener('click', (event) => {
        if (dropdownMenu && dropdownMenu.classList.contains('open')) {
            if (!dropdownMenu.contains(event.target) && !burgerMenuToggle.contains(event.target)) {
                burgerMenuToggle.classList.remove('open');
                burgerMenuToggle.setAttribute('aria-expanded', 'false');
                dropdownMenu.classList.remove('open');
            }
        }
    });

    // --- App Mode Switching Logic (Edit/Upscale) ---
    appModeButtons.forEach(button => {
        button.addEventListener('click', () => {
            const currentMode = button.dataset.mode;
            appModeButtons.forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');

            editView.style.display = (currentMode === 'edit') ? 'flex' : 'none';
            upscaleView.style.display = (currentMode === 'upscale') ? 'flex' : 'none';

            showView('main');
            if(currentMode === 'edit') {
                const activeEditMode = document.querySelector('.edit-mode-btn.active');
                if (activeEditMode) activeEditMode.click();
            }
        });
    });

    // --- Edit View Sub-Mode Logic ---
    const editModeButtons = document.querySelectorAll('.edit-mode-btn');
    const editModeDescription = document.getElementById('edit-mode-description');
    const imageInputsContainer = document.querySelector('.image-inputs-container');
    const imageDropArea2 = document.getElementById('image-drop-area-edit-2');
    const editControlsContainer = document.getElementById('edit-controls-container');

    editModeButtons.forEach(button => {
        button.addEventListener('click', (e) => {
            const editMode = e.currentTarget.dataset.editMode;
            editModeButtons.forEach(btn => btn.classList.remove('active'));
            e.currentTarget.classList.add('active');

            editModeDescription.textContent = e.currentTarget.dataset.description;

            const showSecondImage = (editMode === 'merge');
            const showPrompt = (editMode === 'edit' || editMode === 'merge');

            imageDropArea2.style.display = showSecondImage ? 'flex' : 'none';
            imageInputsContainer.classList.toggle('merge-mode', showSecondImage);
            editControlsContainer.style.display = showPrompt ? 'flex' : 'none';
        });
    });

    // --- Template Buttons Logic ---
    const templateButtons = document.querySelectorAll('.template-btn');
    const promptInput = document.getElementById('prompt');
    templateButtons.forEach(button => {
        button.addEventListener('click', () => {
            promptInput.value = button.dataset.prompt;
            promptInput.focus();
            templateButtons.forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');
        });
    });

    // --- Upscale UI Logic ---
    document.querySelectorAll('.resolution-btn').forEach(button => {
        button.addEventListener('click', () => {
            document.querySelectorAll('.resolution-btn').forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');
        });
    });
    
    // --- Shared Logic and View JS ---
    const imageFileInputEdit1 = document.getElementById('image-file-edit-1');
    const imageFileInputEdit2 = document.getElementById('image-file-edit-2');
    const upscaleImageInput = document.getElementById('image-file-upscale');

    const resultImageWrapper = resultAreaRight.querySelector('.result-image-wrapper');
    const resultImage = document.getElementById('result-image');
    const loader = resultAreaRight.querySelector('.loader-container');
    const errorBox = document.getElementById('error-box');

    function showError(message) {
        errorBox.textContent = message;
        errorBox.style.display = 'block';
        setTimeout(() => { errorBox.style.display = 'none'; }, 4000);
    }

    function showView(viewName) {
        if (viewName === 'main') {
            mainContentWrapper.classList.remove('disabled');
            resultAreaRight.style.display = 'none';
            resetImagePreviews();
            promptInput.value = '';
            templateButtons.forEach(btn => btn.classList.remove('active'));
        } else if (viewName === 'loading') {
            mainContentWrapper.classList.add('disabled');
            resultAreaRight.style.display = 'flex';
            resultImageWrapper.style.display = 'none';
            loader.style.display = 'flex';
        } else if (viewName === 'result') {
            mainContentWrapper.classList.remove('disabled');
            resultAreaRight.style.display = 'flex';
            loader.style.display = 'none';
            resultImageWrapper.style.display = 'flex';
        }
    }

    function handleFileSelect(file, previewElementId) {
        const previewElement = document.getElementById(previewElementId);
        const dropArea = previewElement.parentElement;
        const placeholder = dropArea.querySelector('.drop-placeholder-text');

        const reader = new FileReader();
        reader.onload = (e) => {
            previewElement.src = e.target.result;
            previewElement.style.display = 'block';
            if (placeholder) placeholder.style.display = 'none';
        }
        reader.readAsDataURL(file);
    }

    function setupDragAndDrop(dropArea, fileInputElement) {
        if (!dropArea || !fileInputElement) return;

        const previewImgId = dropArea.querySelector('.image-preview-img').id;

        ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
            dropArea.addEventListener(eventName, e => { e.preventDefault(); e.stopPropagation(); }, false);
        });

        dropArea.addEventListener('dragenter', () => dropArea.classList.add('dragover'));
        dropArea.addEventListener('dragleave', () => dropArea.classList.remove('dragover'));
        dropArea.addEventListener('drop', (e) => {
            dropArea.classList.remove('dragover');
            if (e.dataTransfer.files && e.dataTransfer.files[0]) {
                fileInputElement.files = e.dataTransfer.files;
                handleFileSelect(fileInputElement.files[0], previewImgId);
            }
        });
        fileInputElement.addEventListener('change', () => {
            if (fileInputElement.files && fileInputElement.files[0]) {
                 handleFileSelect(fileInputElement.files[0], previewImgId);
            }
        });
    }

    setupDragAndDrop(document.getElementById('image-drop-area-edit-1'), imageFileInputEdit1);
    setupDragAndDrop(document.getElementById('image-drop-area-edit-2'), imageFileInputEdit2);
    setupDragAndDrop(document.querySelector('#upscale-view .image-drop-area'), upscaleImageInput);

    function resetImagePreviews() {
        document.querySelectorAll('.image-preview-img').forEach(img => {
            img.src = '#';
            img.style.display = 'none';
        });
        document.querySelectorAll('.drop-placeholder-text').forEach(p => {
            if (p) p.style.display = 'block';
        });
        imageFileInputEdit1.value = '';
        imageFileInputEdit2.value = '';
        upscaleImageInput.value = '';
    }

    async function handleImageProcessing(submitButton) {
        if (!imageFileInputEdit1.files[0]) {
            showError("Please select an image to upload.");
            return;
        }

        showView('loading');

        const formData = new FormData();
        formData.append('image', imageFileInputEdit1.files[0]);
        formData.append('prompt', promptInput.value);

        try {
            const response = await fetch("{{ url_for('process_image') }}", {
                method: 'POST',
                body: formData
            });
            const data = await response.json();

            if (!response.ok) {
                let errorDetail = data.error || data.detail || 'Unknown server error';
                if (response.status === 403) {
                     errorDetail = 'Not enough tokens. Please top up your balance.';
                }
                throw new Error(errorDetail);
            }

            resultImage.src = data.output_url;
            if (data.new_token_balance !== undefined && tokenBalanceDisplaySpan) {
                tokenBalanceDisplaySpan.textContent = data.new_token_balance;
            }

            const tempImg = new Image();
            tempImg.onload = () => showView('result');
            tempImg.onerror = () => {
                showError("Failed to load the generated image.");
                showView('main');
            };
            tempImg.src = data.output_url;

        } catch (error) {
            showError("An error occurred: " + error.message);
            showView('main');
        }
    }

    document.getElementById('submit-button-edit').addEventListener('click', (e) => {
        e.preventDefault();
        handleImageProcessing(e.currentTarget);
    });

    document.querySelector('.logo').addEventListener('click', (e) => {
        e.preventDefault();
        window.location.href = "{{ url_for('index') }}";
    });

    // Initial setup on page load
    appModeButtons[0].click();
    showView('main');

    });
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(INDEX_HTML)

@app.route('/buy-tokens')
@login_required
def buy_tokens_page():
    return render_template_string("""
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <title>Покупка токенов</title>
        </head>
        <body>
            <div class="container">
                <h1>Купить токены</h1>
                <p>Привет, {{ current_user.email or current_user.username }}!</p>
                <p>Ваш текущий баланс: <strong>{{ current_user.token_balance }}</strong> токенов.</p>
                <p>Здесь будет информация о пакетах токенов и кнопка для перехода к оплате.</p>
                <a href="{{ url_for('index') }}" class="button">Вернуться на главную</a>
            </div>
        </body>
        </html>
    """, current_user=current_user)


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
        return jsonify({'error': 'Недостаточно токенов'}), 403

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

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get("PORT", 5001)))
