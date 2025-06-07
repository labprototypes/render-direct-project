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
            --text-accent-color: #D9F47A;
            /* ПРАВКА: Цвет подложек изменен на темный */
            --controls-bg-color: #333333;
            --controls-bg-color-transparent: rgba(51, 51, 51, 0.8);
            --blur-intensity: 8px;
            --mob-spacing-unit: 5px;
            --desktop-spacing-unit: 8px;
            --download-icon-size: 28px;

            /* ПРАВКА: Цвет текста на подложках изменен на белый */
            --header-text-color-on-light-bg: #FFFFFF;
            --header-border-radius: 22px;
            --coin-color: #D9F47A;
            --header-vertical-padding: 15px; /* Базовый отступ */
            --header-logo-height-mob: 30px;
            --header-logo-height-desk: 35px;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

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
            overflow: hidden;
        }

        .app-container-wrapper {
            position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background-size: cover; background-position: center center; background-repeat: no-repeat;
            z-index: -1; transition: filter 0.4s ease-in-out;
            background-image: url("{{ url_for('static', filename='images/MOB_BACK.png') }}");
        }
        .app-container-wrapper.bg-blur { filter: blur(var(--blur-intensity)); }

        .app-container {
            width: 100%;
            max-width: 1200px;
            margin: 0 auto;
            padding-left: 25px;
            padding-right: 25px;
            padding-top: calc(var(--header-logo-height-mob) + var(--header-vertical-padding) * 2 + var(--mob-spacing-unit));
            padding-bottom: 20px;
            display: flex;
            flex-direction: row;
            align-items: flex-start;
            justify-content: flex-start;
            gap: 25px;
            height: 100vh;
        }

        .page-header-container {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            width: 100%;
            z-index: 105;
            display: flex;
            justify-content: center;
        }
        .page-header-inner {
            width: 100%;
            max-width: 1200px;
            padding: var(--header-vertical-padding) 25px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .header-left-group {
            display: flex;
            flex-direction: column;
            align-items: flex-start;
            gap: 15px;
        }

        .app-logo-link { display: inline-block; line-height: 0; }
        .logo { height: var(--header-logo-height-mob); cursor: pointer; display: block;}

        .top-right-nav { position: relative; display: flex; align-items: center; }

        .mode-selector {
            display: flex;
            align-items: center;
            background-color: var(--controls-bg-color-transparent);
            backdrop-filter: blur(var(--blur-intensity));
            -webkit-backdrop-filter: blur(var(--blur-intensity));
            padding: 6px;
            border-radius: var(--header-border-radius);
            gap: 6px;
        }
        .mode-btn {
            background-color: transparent;
            border: none;
            padding: 6px 14px;
            border-radius: 18px;
            cursor: pointer;
            font-family: 'ChangerFont', sans-serif;
            font-size: 0.9rem;
            color: var(--header-text-color-on-light-bg);
            transition: background-color 0.3s ease, color 0.3s ease;
        }
        .mode-btn.active {
            background-color: var(--text-accent-color);
            /* ПРАВКА: Цвет текста на активной кнопке для контраста */
            color: #333333;
        }
        .mode-btn:not(.active):hover {
             /* ПРАВКА: Цвет ховера для темного фона */
            background-color: rgba(255,255,255,0.1);
        }

        .user-controls-loggedin {
            display: flex; align-items: center;
            background-color: var(--controls-bg-color-transparent);
            backdrop-filter: blur(var(--blur-intensity));
            -webkit-backdrop-filter: blur(var(--blur-intensity));
            padding: 6px 6px 6px 12px;
            border-radius: var(--header-border-radius);
            gap: 8px;
        }
        .token-display {
            display: flex; align-items: center; color: var(--header-text-color-on-light-bg);
            font-size: 0.85rem; font-weight: normal;
        }
        .token-coin {
            width: 16px; height: 16px; background-color: var(--coin-color);
            border-radius: 50%; margin-left: 5px;
        }
        .burger-menu-btn {
            background-color: var(--text-accent-color); border: none; border-radius: 50%;
            padding: 0; cursor: pointer; width: 34px; height: 34px;
            display: flex; align-items: center; justify-content: center;
            transition: background-color: 0.3s ease, transform 0.3s ease;
            position: relative;
        }
        .burger-menu-btn:hover { background-color: #c8e070; }
        .burger-menu-btn svg {
            display: block;
            position: absolute;
            top: 50%; left: 50%;
            transform: translate(-50%, -50%);
            transition: transform 0.3s ease-in-out, opacity 0.3s ease-in-out;
        }
        .burger-menu-btn svg .line { stroke: #333; stroke-width:10; stroke-linecap:round; transition: transform 0.3s 0.05s ease-in-out, opacity 0.2s ease-in-out; transform-origin: 50% 50%;}

        .burger-menu-btn .burger-icon { width: 16px; height: 12px; }
        .burger-menu-btn .close-icon { width: 14px; height: 14px; opacity: 0; transform: translate(-50%, -50%) rotate(-45deg); }

        .burger-menu-btn.open .burger-icon .line1 { transform: translateY(10px) rotate(45deg) scaleX(1.2); }
        .burger-menu-btn.open .burger-icon .line2 { opacity: 0; }
        .burger-menu-btn.open .burger-icon .line3 { transform: translateY(-10px) rotate(-45deg) scaleX(1.2); }

        .burger-menu-btn.open .burger-icon { opacity: 0; transform: translate(-50%, -50%) rotate(45deg); }
        .burger-menu-btn.open .close-icon { opacity: 1; transform: translate(-50%, -50%) rotate(0deg); }


        .dropdown-menu {
            position: absolute; top: calc(100% + 8px); right: 0;
            /* ПРАВКА: Цвет меню изменен на темный */
            background-color: rgba(51, 51, 51, 0.9);
            backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
            border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.15);
            padding: 12px; width: 220px; z-index: 1000;
            opacity: 0; visibility: hidden; transform: translateY(-8px) scale(0.95);
            transform-origin: top right;
            transition: opacity 0.25s ease, transform 0.25s ease, visibility 0s 0.25s linear;
        }
        .dropdown-menu.open {
            opacity: 1; visibility: visible; transform: translateY(0) scale(1);
            transition: opacity 0.25s ease, transform 0.25s ease, visibility 0s 0s linear;
        }
        .dropdown-header {
            display: flex; justify-content: space-between; align-items: center;
            margin-bottom: 10px; padding-bottom: 8px; border-bottom: 1px solid rgba(255,255,255,0.15);
        }
        .dropdown-user-email {
            color: var(--header-text-color-on-light-bg); font-size: 0.9rem; font-weight: bold;
            overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
            flex-grow: 1;
        }
        .close-menu-btn { background: none; border: none; padding: 0; cursor: pointer; display: flex; align-items: center; justify-content: center; width:20px; height:20px;}
        .close-menu-btn svg { stroke: #DDD; stroke-width:10; stroke-linecap:round; }
        .dropdown-menu ul { list-style: none; padding: 0; margin: 0; }
        .dropdown-menu li a {
            display: block; padding: 8px 0; color: var(--header-text-color-on-light-bg); text-decoration: none;
            font-size: 0.9rem; transition: color 0.2s ease;
        }
        .dropdown-menu li a:hover { color: var(--text-accent-color); }

        .user-controls-loggedout {
            background-color: var(--controls-bg-color-transparent);
            backdrop-filter: blur(var(--blur-intensity));
            -webkit-backdrop-filter: blur(var(--blur-intensity));
            padding: 8px 15px; border-radius: var(--header-border-radius); display: flex; align-items: center;
        }
        .user-controls-loggedout .auth-button {
            color: var(--header-text-color-on-light-bg);
            text-decoration: none; font-size: 0.85rem; font-weight: normal;
        }
        .user-controls-loggedout .auth-button:hover { text-decoration: underline; }
        .user-controls-loggedout .auth-separator { color: var(--header-text-color-on-light-bg); margin: 0 6px; opacity: 0.6; }

        .content-wrapper {
            width: 100%;
            max-width: 420px;
            height: auto;
            /* ПРАВКА (Десктоп): Отступ сверху изменен на 25px */
            margin-top: 25px;
            padding: 25px;
            background: var(--controls-bg-color-transparent);
            border-radius: var(--header-border-radius);
            backdrop-filter: blur(var(--blur-intensity));
            -webkit-backdrop-filter: blur(var(--blur-intensity));
            transition: opacity 0.3s, filter 0.3s;
        }
        .content-wrapper.disabled {
            opacity: 0.6;
            pointer-events: none;
            filter: grayscale(50%);
        }

        #upscale-view, #edit-view {
            width: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: flex-start;
            gap: 15px;
        }

        .image-inputs-container {
            display: flex;
            justify-content: center;
            gap: 15px;
            width: 100%;
            margin-top: 15px;
        }
        .image-inputs-container.merge-mode .image-drop-area {
            flex: 1;
            max-width: none;
        }


        .image-drop-area {
            width: 100%; max-width: 280px; height: 165px; background-color: transparent;
            border-radius: 25px; display: flex; flex-direction: column; justify-content: center; align-items: center;
            cursor: pointer;
            position: relative;
            overflow: hidden;
            border: 2px dashed rgba(248, 248, 248, 0.3);
            flex-shrink: 0;
        }
        .image-drop-area.dragover { border-color: var(--text-accent-color); background-color: rgba(217, 244, 122, 0.1); }
        .image-drop-area .drop-placeholder-img {
            width: auto; max-width: 80%; max-height: 40%; height: auto; object-fit: contain;
        }
        .image-drop-area::before {
            content: ""; position: absolute; top: 0; left: 0; right: 0; bottom: 0;
            background-color: rgba(248, 248, 248, 0.1);
            backdrop-filter: blur(4px); -webkit-backdrop-filter: blur(4px);
            z-index: -1; border-radius: inherit;
        }
        .image-drop-area .image-preview-img {
            display: none; width: 100%; height: 100%; object-fit: cover;
            border-radius: inherit; position: relative; z-index: 1;
        }

        #result-area-right {
            flex: 1;
            height: 100%;
            display: none;
            justify-content: center;
            align-items: center;
            padding-top: 45px;
            padding-left: 15px;
            padding-right: 30px;
            padding-bottom: 75px;
        }
        
        .result-image-wrapper {
             justify-content: center;
             display: flex;
             align-items: center;
             width: 100%;
             height: 100%;
             position: relative;
        }
        #result-image {
            max-width: 100%;
            max-height: 100%;
            object-fit: contain;
            border-radius: 12px;
            box-shadow: 0 6px 20px rgba(0,0,0,0.25);
            display: block;
        }

        .download-action-link {
            display: flex;
            position: absolute;
            top: 100%;
            right: 0;
            margin-top: var(--header-vertical-padding);
            z-index: 10;
            cursor: pointer;
            line-height: 0;
        }
        .download-button-icon {
            height: var(--download-icon-size); width: var(--download-icon-size); display: block;
        }

        .loader-container {
            width: 100%; height: 100%;
            justify-content: center; align-items: center;
            z-index: 101; display: flex;
        }
        .pulsating-dot {
            width: 100px; height: 100px; background-color: var(--text-accent-color);
            border-radius: 50%; position: relative;
            animation: pulse 1.5s infinite ease-in-out;
        }
        @keyframes pulse {
            0%, 100% { transform: scale(0.8); opacity: 0.7; }
            50% { transform: scale(1.2); opacity: 1; }
        }

        .input-area {
            display: flex; align-items: center;
            background-color: var(--controls-bg-color-transparent);
            backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
            border-radius: 50px; padding: 6px 8px; width: 100%;
            box-shadow: 0 4px 15px rgba(0,0,0,0.1);
            border: 2px dashed transparent;
        }
        
        #prompt {
            flex-grow: 1; border: none; padding: 10px 8px;
            font-size: 0.85rem;
            background-color: transparent; outline: none;
            /* ПРАВКА: Цвет текста в поле ввода */
            color: #FFFFFF;
            font-family: 'ChangerFont', sans-serif; line-height: 1.3;
        }
        /* ПРАВКА: Цвет плейсхолдера в поле ввода */
        #prompt::placeholder { color: #AAAAAA; opacity: 1; }

        .submit-action-group {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 8px;
            margin: 15px auto 0 auto;
        }
        
        .submit-button-element {
            background-color: transparent; border: none; cursor: pointer; padding: 0;
            display: flex; align-items: center; justify-content: center;
            flex-shrink: 0;
        }
        .submit-button-icon-img { height: 38px; width: 38px; }

        .submit-button-wrapper {
            background: var(--controls-bg-color-transparent);
            border-radius: var(--header-border-radius);
            backdrop-filter: blur(var(--blur-intensity));
            -webkit-backdrop-filter: blur(var(--blur-intensity));
            padding: 8px;
        }

        .control-group {
            width: 100%;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .control-group label {
            display: block;
            font-size: 0.9rem;
            color: var(--header-text-color-on-light-bg);
            margin-bottom: 0;
        }
        
        .edit-mode-selector, .template-selector, .resolution-selector {
            display: flex;
            gap: 10px;
            width: 100%;
        }
        
        .template-selector {
            flex-wrap: wrap;
        }

        .edit-mode-btn, .template-btn, .resolution-btn {
            flex-grow: 1;
            padding: 10px;
            border-radius: 8px;
            /* ПРАВКА: Стили кнопок для темной темы */
            border: 1px solid rgba(255, 255, 255, 0.2);
            background-color: transparent;
            color: #FFFFFF;
            cursor: pointer;
            font-family: 'ChangerFont', sans-serif;
            font-size: 0.85rem;
            transition: background-color 0.2s, color 0.2s, border-color 0.2s;
            text-align: center;
        }
        .edit-mode-btn:hover, .template-btn:hover, .resolution-btn:hover {
            /* ПРАВКА: Стили ховера для кнопок */
            border-color: rgba(255, 255, 255, 0.4);
            background-color: rgba(255, 255, 255, 0.05);
        }
        .edit-mode-btn.active, .template-btn.active, .resolution-btn.active {
            background-color: var(--text-accent-color);
            border-color: var(--text-accent-color);
            /* ПРАВКА: Цвет текста на активной кнопке для контраста */
            color: #333333;
        }
        
        .mode-description {
            font-size: 0.8rem;
            color: var(--header-text-color-on-light-bg);
            text-align: center;
            width: 100%;
            padding: 0 10px;
            line-height: 1.4;
            min-height: 4em;
        }
        
        .slider-container {
            width: 100%;
        }
        .slider-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
        }
        .slider-container label {
             font-weight: normal;
        }
        .slider-value {
            font-weight: bold;
            color: var(--header-text-color-on-light-bg);
        }

        .slider-container input[type="range"] {
            -webkit-appearance: none;
            appearance: none;
            width: 100%;
            height: 6px;
            /* ПРАВКА: Цвет трека слайдера для темной темы */
            background: rgba(255, 255, 255, 0.3);
            border-radius: 5px;
            outline: none;
        }
        .slider-container input[type="range"]::-webkit-slider-thumb {
            -webkit-appearance: none;
            appearance: none;
            width: 20px;
            height: 20px;
            border-radius: 50%;
            background: var(--text-accent-color);
            cursor: pointer;
            border: 3px solid var(--controls-bg-color);
            box-shadow: 0 0 5px rgba(0,0,0,0.2);
        }
        .slider-container input[type="range"]::-moz-range-thumb {
            width: 20px;
            height: 20px;
            border-radius: 50%;
            background: var(--text-accent-color);
            cursor: pointer;
            border: 3px solid var(--controls-bg-color);
            box-shadow: 0 0 5px rgba(0,0,0,0.2);
        }
        
        .token-cost {
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 5px;
            font-size: 0.9rem;
            /* ПРАВКА: Цвет стоимости для темной темы */
            color: #AAAAAA;
        }

        .error-message {
            display: none; margin-top: 10px; font-size: 0.9rem; color: var(--text-accent-color);
            background-color: rgba(0,0,0,0.65); backdrop-filter: blur(5px);
            padding: 10px 15px; border-radius: 8px; position: fixed;
            bottom: 20px;
            left: 50%; transform: translateX(-50%);
            width: calc(100% - 40px);
            max-width: 480px; z-index: 105; text-align: center;
        }

        @media (max-width: 768px) {
            .app-container {
                flex-direction: column;
                align-items: center;
                height: auto;
                overflow-y: auto;
            }
            #result-area-right {
                width: 100%;
                height: 50vh; /* Задаем высоту на мобильных */
                flex: none; /* Убираем гибкость */
            }
            /* ПРАВКА (Мобайл): Скрываем кнопки-шаблоны */
            .template-selector {
                display: none;
            }
        }
        
        @media (min-width: 769px) {
            .submit-button-icon-img { height: 42px; width: 42px;}
            .user-controls-loggedin { gap: 15px; padding: 10px 10px 10px 20px; }
            .token-display { font-size: 1rem; }
            .token-coin { width: 20px; height: 20px; }
            .burger-menu-btn { width: 42px; height: 42px; }
            .user-controls-loggedout { padding: 12px 25px; }
            .user-controls-loggedout .auth-button { font-size: 1rem; }
        }
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
                        <div class="dropdown-header">
                             <span class="dropdown-user-email">{{ current_user.email or current_user.username }}</span>
                            <button class="close-menu-btn" id="close-menu-btn-inner" aria-label="Закрыть меню">
                                 <svg viewBox="0 0 100 100" width="18" height="18"><line x1="10" y1="10" x2="90" y2="90"/><line x1="10" y1="90" x2="90" y2="10"/></svg>
                            </button>
                        </div>
                        <ul>
                            <li><a href="{{ url_for('buy_tokens_page') }}">пополнить баланс</a></li>
                            <li><a href="{{ url_for('change_password') }}">Сменить пароль</a></li>
                            <li><a href="{{ url_for('logout') }}">Выйти</a></li>
                        </ul>
                    </div>
                {% else %}
                    <div class="user-controls-loggedout">
                        <a href="{{ url_for('login') }}" class="auth-button">Логин</a>
                        <span class="auth-separator">|</span>
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
                        <button class="edit-mode-btn active" data-edit-mode="edit" data-description="Use this tool to add or remove objects, and to modify the style or lighting of your image. Feel free to use the templates below or write a custom prompt in any language.">Edit</button>
                        <button class="edit-mode-btn" data-edit-mode="merge" data-description="Merge two images, integrate new items into your shot, or transfer the style from a reference image. Prompts can be written in any language.">Merge</button>
                        <button class="edit-mode-btn" data-edit-mode="autofix" data-description="Simply upload your image for automatic artifact removal and quality enhancement.">Auto fix</button>
                    </div>
                </div>

                <div class="image-inputs-container">
                    <label for="image-file-edit-1" id="image-drop-area-edit-1" class="image-drop-area">
                        <img src="{{ url_for('static', filename='images/JDTI.png') }}" alt="Just drop the image" class="drop-placeholder-img">
                        <img id="image-preview-edit-1" src="#" alt="Preview" class="image-preview-img">
                    </label>
                    <label for="image-file-edit-2" id="image-drop-area-edit-2" class="image-drop-area" style="display: none;">
                        <img src="{{ url_for('static', filename='images/JDTI.png') }}" alt="Just drop the image" class="drop-placeholder-img">
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
                         <input type="text" id="prompt" name="prompt" placeholder="TYPE WHAT YOU WANT TO CHANGE">
                    </form>
                </div>

                <div class="submit-action-group">
                    <div class="submit-button-wrapper">
                        <button type="submit" id="submit-button-edit" class="submit-button-element">
                            <img src="{{ url_for('static', filename='images/MAGIC_GREEN.png') }}" alt="Generate" class="submit-button-icon-img">
                        </button>
                    </div>
                    <div class="token-cost">
                        <span>1</span>
                        <span class="token-coin"></span>
                    </div>
                </div>
            </div>

            <div id="upscale-view" style="display: none;">
                <div class="control-group">
                    <label>Resolution</label>
                    <div class="resolution-selector">
                        <button class="resolution-btn active" data-value="x2">x2</button>
                        <button class="resolution-btn" data-value="x4">x4</button>
                        <button class="resolution-btn" data-value="x8">x8</button>
                    </div>
                </div>

                <div class="control-group">
                     <div class="slider-container">
                        <div class="slider-header">
                            <label for="creativity-slider">Creativity</label>
                            <span class="slider-value" id="creativity-value">70</span>
                        </div>
                        <input type="range" id="creativity-slider" min="0" max="100" value="70" class="custom-slider">
                    </div>
                </div>

                <div class="control-group">
                     <div class="slider-container">
                         <div class="slider-header">
                            <label for="resemblance-slider">Resemblance</label>
                            <span class="slider-value" id="resemblance-value">80</span>
                        </div>
                        <input type="range" id="resemblance-slider" min="0" max="100" value="80" class="custom-slider">
                    </div>
                </div>

                <label for="image-file-upscale" class="image-drop-area">
                    <img src="{{ url_for('static', filename='images/JDTI.png') }}" alt="Just drop the image" class="drop-placeholder-img">
                    <img id="image-preview-upscale" src="#" alt="Preview" class="image-preview-img">
                </label>
                <input type="file" id="image-file-upscale" name="image" accept="image/*" style="display: none;">

                <div class="submit-action-group">
                    <div class="submit-button-wrapper">
                        <button type="submit" id="submit-button-upscale" class="submit-button-element">
                            <img src="{{ url_for('static', filename='images/MAGIC_GREEN.png') }}" alt="Generate" class="submit-button-icon-img">
                        </button>
                    </div>
                    <div class="token-cost">
                        <span>5</span>
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
                <a href="#" id="download-action" class="download-action-link" download="generated_image.png" target="_blank" rel="noopener noreferrer">
                    <img src="{{ url_for('static', filename='images/Download.png') }}" alt="Скачать" class="download-button-icon">
                </a>
            </div>
        </div>
    </div>

    <div id="error-box" class="error-message"></div>

    <script>
    document.addEventListener('DOMContentLoaded', () => {

    // --- DOM Elements ---
    const tokenBalanceDisplaySpan = document.getElementById('token-balance-display');
    const burgerMenuToggle = document.getElementById('burger-menu-toggle');
    const dropdownMenu = document.getElementById('dropdown-menu');
    const closeMenuBtnInner = document.getElementById('close-menu-btn-inner');

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
    if (closeMenuBtnInner) {
         closeMenuBtnInner.addEventListener('click', (e) => {
            e.stopPropagation();
            burgerMenuToggle.setAttribute('aria-expanded', 'false');
            dropdownMenu.classList.remove('open');
            burgerMenuToggle.classList.remove('open');
        });
    }
    document.addEventListener('click', (event) => {
        if (dropdownMenu && dropdownMenu.classList.contains('open')) {
            if (!dropdownMenu.contains(event.target) && !burgerMenuToggle.contains(event.target)) {
                burgerMenuToggle.setAttribute('aria-expanded', 'false');
                dropdownMenu.classList.remove('open');
                burgerMenuToggle.classList.remove('open');
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
                document.querySelector('.edit-mode-btn[data-edit-mode="edit"]').click();
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
        });
    });

    // --- Upscale UI Logic ---
    document.querySelectorAll('.resolution-btn').forEach(button => {
        button.addEventListener('click', () => {
            document.querySelectorAll('.resolution-btn').forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');
        });
    });
    
    const setupSlider = (sliderId, valueId) => {
        const slider = document.getElementById(sliderId);
        const valueDisplay = document.getElementById(valueId);
        if(slider) {
            slider.addEventListener('input', (event) => {
                valueDisplay.textContent = event.target.value;
            });
        }
    };
    setupSlider('creativity-slider', 'creativity-value');
    setupSlider('resemblance-slider', 'resemblance-value');

    // --- Shared Logic and View JS ---
    const appBgWrapper = document.getElementById('app-bg-wrapper');
    const imageFileInputEdit1 = document.getElementById('image-file-edit-1');
    const imageFileInputEdit2 = document.getElementById('image-file-edit-2');
    const upscaleImageInput = document.getElementById('image-file-upscale');

    const resultImageWrapper = resultAreaRight.querySelector('.result-image-wrapper');
    const resultImage = document.getElementById('result-image');
    const downloadLink = document.getElementById('download-action');
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
            appBgWrapper.classList.remove('bg-blur');
            resetImagePreviews();
            promptInput.value = '';
        } else if (viewName === 'loading') {
            mainContentWrapper.classList.add('disabled');
            resultAreaRight.style.display = 'flex';
            resultImageWrapper.style.display = 'none';
            loader.style.display = 'flex';
            appBgWrapper.classList.add('bg-blur');
        } else if (viewName === 'result') {
            mainContentWrapper.classList.remove('disabled');
            resultAreaRight.style.display = 'flex';
            loader.style.display = 'none';
            resultImageWrapper.style.display = 'flex';
            appBgWrapper.classList.add('bg-blur');
        }
    }

    function handleFileSelect(file, previewElementId) {
        const previewElement = document.getElementById(previewElementId);
        const dropArea = previewElement.parentElement;
        const placeholder = dropArea.querySelector('.drop-placeholder-img');
        
        const reader = new FileReader();
        reader.onload = (e) => {
            previewElement.src = e.target.result;
            previewElement.style.display = 'block';
            if (placeholder) placeholder.style.display = 'none';
        }
        reader.readAsDataURL(file);
    }

    function setupDragAndDrop(dropAreaId, fileInputElement) {
        const dropArea = document.getElementById(dropAreaId);
        if (!dropArea || !fileInputElement) return;

        const previewImgId = dropArea.querySelector('.image-preview-img').id;

        dropArea.addEventListener('dragover', (e) => { e.preventDefault(); e.stopPropagation(); dropArea.classList.add('dragover'); });
        dropArea.addEventListener('dragleave', (e) => { e.preventDefault(); e.stopPropagation(); dropArea.classList.remove('dragleave'); });
        dropArea.addEventListener('drop', (e) => {
            e.preventDefault(); e.stopPropagation();
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

    setupDragAndDrop('image-drop-area-edit-1', imageFileInputEdit1);
    setupDragAndDrop('image-drop-area-edit-2', imageFileInputEdit2);
    // For upscale, the label itself is the drop area
    setupDragAndDrop(document.querySelector('#upscale-view .image-drop-area').id, upscaleImageInput);

    function resetImagePreviews() {
        document.querySelectorAll('.image-preview-img').forEach(img => {
            img.src = '#';
            img.style.display = 'none';
        });
        document.querySelectorAll('.drop-placeholder-img').forEach(p => {
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
            downloadLink.href = data.output_url;
            if (data.new_token_balance !== undefined && tokenBalanceDisplaySpan) {
                tokenBalanceDisplaySpan.textContent = data.new_token_balance;
            }

            const tempImg = new Image();
            tempImg.onload = () => showView('result');
            tempImg.onerror = () => {
                showError("Failed to load the generated image.");
                showView('main');
                appBgWrapper.classList.remove('bg-blur');
            };
            tempImg.src = data.output_url;

        } catch (error) {
            showError("An error occurred: " + error.message);
            showView('main');
            appBgWrapper.classList.remove('bg-blur');
        }
    }

    document.getElementById('submit-button-edit').addEventListener('click', (e) => {
        e.preventDefault();
        handleImageProcessing(e.currentTarget);
    });

    document.querySelector('.logo').addEventListener('click', (e) => {
        e.preventDefault();
        showView('main');
        appBgWrapper.classList.remove('bg-blur');
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
            <style>
                body { font-family: sans-serif; margin: 20px; background-color: #f4f4f4; color: #333; }
                .container { max-width: 600px; margin: auto; background-color: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 0 10px rgba(0,0,0,0.1); text-align: center; }
                h1 { color: #333; }
                p { font-size: 1.1rem; line-height: 1.6; }
                strong { font-size: 1.2rem; color: #D9F47A; background-color: #333; padding: 2px 8px; border-radius: 4px; }
                a { color: #007bff; text-decoration: none; }
                a:hover { text-decoration: underline; }
                .button { display: inline-block; padding: 12px 25px; background-color: #D9F47A; color: #333; border-radius: 5px; text-decoration: none; margin-top: 20px; font-weight: bold; font-size: 1.1rem;}
            </style>
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
