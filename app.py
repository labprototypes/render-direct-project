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
        /* --- VISUAL UPDATE 2025 --- */
        @font-face {
            font-family: 'ChangerFont';
            src: url("{{ url_for('static', filename='fonts/FONT_TEXT.woff2') }}") format('woff2');
            font-weight: normal;
            font-style: normal;
        }

        :root {
            --accent-color: #D9F47A;
            --base-dark-color: #333333;
            --text-primary-color: #FFFFFF;
            --text-secondary-color: #A0A0A0;
            
            --blur-intensity: 18px;
            --base-border-radius: 28px;
            --medium-border-radius: 20px;
            --small-border-radius: 16px;
            --pill-border-radius: 50px;

            --header-logo-height-mob: 32px;
            --header-logo-height-desk: 38px;
        }

        * { 
            margin: 0; 
            padding: 0; 
            box-sizing: border-box; 
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }

        body {
            font-family: 'ChangerFont', sans-serif;
            color: var(--text-primary-color);
            background-color: #111;
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
            z-index: -1; transition: filter 0.5s ease-in-out;
            background-image: url("{{ url_for('static', filename='images/MOB_BACK.png') }}");
        }
        .app-container-wrapper.bg-blur { 
            filter: blur(var(--blur-intensity)); 
            transform: scale(1.05);
        }

        .app-container {
            width: 100%;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            padding-top: calc(var(--header-logo-height-mob) + 40px);
            display: flex;
            flex-direction: row;
            align-items: flex-start;
            justify-content: flex-start;
            gap: 20px;
            height: 100vh;
        }

        /* --- HEADER --- */
        .page-header-container {
            position: fixed; top: 0; left: 0; right: 0; width: 100%; z-index: 105;
            display: flex; justify-content: center;
        }
        .page-header-inner {
            width: 100%; max-width: 1200px; padding: 20px;
            display: flex; justify-content: space-between; align-items: center;
        }
        .app-logo-link { display: inline-block; line-height: 0; }
        .logo { height: var(--header-logo-height-mob); cursor: pointer; display: block;}

        .top-right-nav { display: flex; align-items: center; gap: 10px; }

        .user-controls-loggedin, .user-controls-loggedout {
            display: flex; align-items: center;
            background: rgba(35, 35, 35, 0.45);
            backdrop-filter: blur(var(--blur-intensity));
            -webkit-backdrop-filter: blur(var(--blur-intensity));
            border-radius: var(--pill-border-radius);
            border: 1px solid rgba(255, 255, 255, 0.1);
            box-shadow: 0 4px 20px rgba(0,0,0,0.2);
        }
        .user-controls-loggedin { padding: 6px; gap: 8px; }
        .user-controls-loggedout { padding: 10px 20px; gap: 12px; }

        .token-display {
            display: flex; align-items: center; color: var(--text-primary-color);
            font-size: 0.9rem; padding-left: 12px;
        }
        .token-coin {
            width: 18px; height: 18px; background-color: var(--accent-color);
            border-radius: 50%; margin-left: 6px;
        }
        
        .burger-menu-btn {
            background-color: var(--accent-color); border: none; border-radius: 50%;
            padding: 0; cursor: pointer; width: 38px; height: 38px;
            display: flex; align-items: center; justify-content: center;
            transition: transform 0.3s ease;
        }
        .burger-menu-btn:hover { transform: scale(1.1); }
        .burger-menu-btn svg { stroke: var(--base-dark-color); stroke-width:10; stroke-linecap:round; }
        .burger-menu-btn .burger-icon, .burger-menu-btn .close-icon {
            position: absolute; top: 50%; left: 50%;
            transform: translate(-50%, -50%);
            transition: transform 0.3s ease-in-out, opacity 0.3s ease-in-out;
        }
        .burger-menu-btn .burger-icon { width: 16px; height: 12px; opacity: 1; }
        .burger-menu-btn .close-icon { width: 14px; height: 14px; opacity: 0; }
        .burger-menu-btn.open .burger-icon { opacity: 0; transform: translate(-50%, -50%) rotate(45deg); }
        .burger-menu-btn.open .close-icon { opacity: 1; transform: translate(-50%, -50%) rotate(0deg); }

        .dropdown-menu {
            position: absolute; top: calc(100% + 10px); right: 0;
            background: rgba(45, 45, 45, 0.6);
            backdrop-filter: blur(var(--blur-intensity)); -webkit-backdrop-filter: blur(var(--blur-intensity));
            border-radius: var(--medium-border-radius);
            border: 1px solid rgba(255, 255, 255, 0.1);
            box-shadow: 0 8px 30px rgba(0,0,0,0.25);
            padding: 12px; width: 230px; z-index: 1000;
            opacity: 0; visibility: hidden; transform: translateY(-10px);
            transition: all 0.25s ease;
        }
        .dropdown-menu.open { opacity: 1; visibility: visible; transform: translateY(0); }
        .dropdown-header {
            padding: 0 8px 10px 8px; margin-bottom: 8px; 
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }
        .dropdown-user-email {
            color: var(--text-primary-color); font-size: 0.95rem; font-weight: bold;
        }
        .dropdown-menu ul { list-style: none; }
        .dropdown-menu li a {
            display: block; padding: 10px 8px; color: var(--text-secondary-color); text-decoration: none;
            font-size: 0.95rem; transition: all 0.2s ease; border-radius: 8px;
        }
        .dropdown-menu li a:hover { color: var(--text-primary-color); background-color: rgba(255,255,255,0.08); }

        .user-controls-loggedout .auth-button {
            color: var(--text-primary-color); text-decoration: none; font-size: 0.9rem;
            transition: color 0.2s;
        }
        .user-controls-loggedout .auth-button:hover { color: var(--accent-color); }
        .user-controls-loggedout .auth-separator { color: var(--text-secondary-color); opacity: 0.5; }

        /* --- MAIN CONTENT --- */
        .content-wrapper {
            width: 100%;
            max-width: 450px;
            height: auto;
            background: rgba(35, 35, 35, 0.45);
            backdrop-filter: blur(var(--blur-intensity));
            -webkit-backdrop-filter: blur(var(--blur-intensity));
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: var(--base-border-radius);
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
            padding: 25px;
            transition: opacity 0.4s, filter 0.4s;
            display: flex;
            flex-direction: column;
            gap: 20px;
        }
        .content-wrapper.disabled { opacity: 0.5; pointer-events: none; filter: grayscale(50%); }

        #upscale-view, #edit-view {
            width: 100%; display: flex; flex-direction: column; gap: 25px;
        }

        /* --- GLOBAL CONTROL STYLES --- */
        .mode-selector, .edit-mode-selector {
            display: flex;
            padding: 5px;
            border-radius: var(--pill-border-radius);
            background: rgba(0, 0, 0, 0.25);
            width: fit-content;
        }
        .mode-btn, .edit-mode-btn {
            background-color: transparent;
            border: none;
            padding: 8px 20px;
            border-radius: var(--pill-border-radius);
            cursor: pointer;
            font-family: 'ChangerFont', sans-serif;
            font-size: 0.9rem;
            color: var(--text-secondary-color);
            transition: all 0.3s ease;
        }
        .mode-btn.active, .edit-mode-btn.active {
            background-color: var(--accent-color);
            color: var(--base-dark-color);
            font-weight: bold;
            box-shadow: 0 2px 10px rgba(0,0,0,0.2);
        }
        
        .mode-description {
            font-size: 0.85rem; color: var(--text-secondary-color);
            text-align: center; width: 100%; line-height: 1.5; min-height: 3em;
        }

        .control-group { width: 100%; display: flex; flex-direction: column; gap: 12px; }
        .control-group > label { font-size: 1rem; color: var(--text-primary-color); margin-left: 5px; }
        
        /* --- IMAGE INPUTS --- */
        .image-inputs-container { display: flex; justify-content: center; gap: 15px; width: 100%; }
        .image-inputs-container.merge-mode .image-drop-area { flex: 1; max-width: none; }

        .image-drop-area {
            width: 100%; max-width: 300px; height: 180px;
            border-radius: var(--medium-border-radius);
            background: rgba(0, 0, 0, 0.2);
            border: 1px dashed rgba(255, 255, 255, 0.2);
            display: flex; flex-direction: column; justify-content: center; align-items: center;
            cursor: pointer; position: relative; overflow: hidden;
            transition: all 0.3s ease;
        }
        .image-drop-area:hover, .image-drop-area.dragover {
            border-color: var(--accent-color);
            box-shadow: 0 0 25px rgba(217, 244, 122, 0.3);
            background: rgba(217, 244, 122, 0.05);
        }
        .image-drop-area .drop-placeholder-img { width: auto; max-width: 60%; max-height: 35%; height: auto; object-fit: contain; filter: invert(0.8); }
        .image-drop-area .image-preview-img {
            display: none; width: 100%; height: 100%; object-fit: cover;
            position: absolute; top: 0; left: 0; z-index: 1;
        }

        /* --- EDIT VIEW CONTROLS --- */
        .template-selector {
            display: flex; flex-wrap: wrap; gap: 10px;
        }
        .template-btn {
            flex-grow: 1; padding: 10px 15px; border-radius: var(--small-border-radius);
            border: 1px solid rgba(255, 255, 255, 0.15);
            background-color: rgba(255, 255, 255, 0.05);
            color: var(--text-secondary-color); cursor: pointer;
            font-family: 'ChangerFont', sans-serif; font-size: 0.85rem;
            transition: all 0.2s ease;
        }
        .template-btn:hover {
            border-color: var(--accent-color); color: var(--accent-color);
        }

        .input-area {
            display: flex; align-items: center;
            background: rgba(0, 0, 0, 0.25);
            border-radius: var(--small-border-radius); padding: 8px 15px; width: 100%;
            border: 1px solid rgba(255, 255, 255, 0.1);
            transition: all 0.2s ease;
        }
        .input-area:focus-within {
             border-color: var(--accent-color);
             box-shadow: 0 0 15px rgba(217, 244, 122, 0.2);
        }
        
        #prompt {
            flex-grow: 1; border: none; padding: 8px 0;
            font-size: 0.95rem; background-color: transparent; outline: none;
            color: var(--text-primary-color); font-family: 'ChangerFont', sans-serif;
        }
        #prompt::placeholder { color: var(--text-secondary-color); opacity: 0.8; }

        /* --- UPSCALE VIEW CONTROLS --- */
        .resolution-selector { display: flex; gap: 10px; width: 100%; }
        .resolution-btn {
            flex-grow: 1; padding: 12px; border-radius: var(--medium-border-radius);
            border: 1px solid rgba(255, 255, 255, 0.15);
            background-color: rgba(255, 255, 255, 0.05);
            color: var(--text-primary-color); cursor: pointer;
            font-family: 'ChangerFont', sans-serif; font-size: 0.9rem;
            transition: all 0.2s;
        }
        .resolution-btn:hover { background-color: rgba(255, 255, 255, 0.1); }
        .resolution-btn.active {
            background-color: var(--accent-color); border-color: var(--accent-color);
            color: var(--base-dark-color); font-weight: bold;
        }

        .slider-container { width: 100%; }
        .slider-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
        .slider-container label { font-weight: normal; font-size: 0.9rem; color: var(--text-secondary-color); }
        .slider-value { font-weight: bold; color: var(--text-primary-color); }
        .slider-container input[type="range"] {
            -webkit-appearance: none; appearance: none;
            width: 100%; height: 6px; background: rgba(0, 0, 0, 0.3);
            border-radius: 5px; outline: none;
        }
        .slider-container input[type="range"]::-webkit-slider-thumb {
            -webkit-appearance: none; appearance: none;
            width: 22px; height: 22px; border-radius: 50%;
            background: var(--text-primary-color); cursor: pointer;
            border: 4px solid var(--accent-color);
            box-shadow: 0 0 5px rgba(0,0,0,0.2);
            transition: transform 0.2s;
        }
        .slider-container input[type="range"]::-webkit-slider-thumb:hover { transform: scale(1.1); }
        .slider-container input[type="range"]::-moz-range-thumb {
            width: 22px; height: 22px; border-radius: 50%;
            background: var(--text-primary-color); cursor: pointer;
            border: 4px solid var(--accent-color);
        }

        /* --- SUBMIT & COST --- */
        .submit-action-group {
            display: flex; flex-direction: column; align-items: center;
            gap: 12px; margin-top: 15px;
        }
        .submit-button-wrapper { line-height: 0; }
        .submit-button-element {
            background-color: transparent; border: none; cursor: pointer; padding: 0;
            display: flex; align-items: center; justify-content: center;
        }
        .submit-button-icon-img {
            height: 64px; width: 64px; transition: transform 0.25s cubic-bezier(0.175, 0.885, 0.32, 1.275);
        }
        .submit-button-element:hover .submit-button-icon-img { transform: scale(1.1); }
        .submit-button-element:active .submit-button-icon-img { transform: scale(0.95); }

        .token-cost {
            display: flex; justify-content: center; align-items: center; gap: 6px;
            font-size: 0.9rem; color: var(--text-secondary-color);
        }
        .token-cost .token-coin { width: 14px; height: 14px; margin: 0; }

        /* --- RESULT AREA --- */
        #result-area-right {
            flex: 1; height: 100%; display: none;
            justify-content: center; align-items: center;
            padding-top: 45px; padding-left: 15px; padding-right: 30px; padding-bottom: 75px;
        }
        .result-image-wrapper {
             justify-content: center; display: flex; align-items: center;
             width: 100%; height: 100%; position: relative;
        }
        #result-image {
            max-width: 100%; max-height: 100%;
            object-fit: contain;
            border-radius: var(--medium-border-radius);
            box-shadow: 0 10px 40px rgba(0,0,0,0.35);
            background: rgba(0,0,0,0.2);
        }
        .download-action-link {
            display: flex; position: absolute; top: calc(100% + 15px); right: 0; z-index: 10;
            cursor: pointer; line-height: 0;
            background: rgba(35, 35, 35, 0.5);
            backdrop-filter: blur(10px);
            border-radius: 50%; padding: 10px;
            border: 1px solid rgba(255,255,255,0.1);
            transition: all 0.2s ease;
        }
        .download-action-link:hover { transform: scale(1.1); background: rgba(217, 244, 122, 0.2); }
        .download-button-icon { height: 28px; width: 28px; display: block; }
        
        .loader-container {
            width: 100%; height: 100%;
            justify-content: center; align-items: center;
            z-index: 101; display: flex;
        }
        .pulsating-dot {
            width: 100px; height: 100px; background-color: var(--accent-color);
            border-radius: 50%; position: relative;
            animation: pulse 1.5s infinite ease-in-out;
        }
        @keyframes pulse {
            0%, 100% { transform: scale(0.8); opacity: 0.7; }
            50% { transform: scale(1.2); opacity: 1; }
        }

        .error-message {
            display: none; font-size: 0.9rem; color: var(--text-primary-color);
            background: #e74c3c;
            padding: 12px 20px; border-radius: var(--small-border-radius); position: fixed;
            bottom: 20px; left: 50%; transform: translateX(-50%);
            width: calc(100% - 40px);
            max-width: 480px; z-index: 105; text-align: center;
            box-shadow: 0 4px 20px rgba(0,0,0,0.2);
        }

        @media (max-width: 768px) {
            .app-container {
                flex-direction: column; align-items: center; height: auto;
                overflow-y: auto; overflow-x: hidden; padding-top: calc(var(--header-logo-height-mob) + 30px);
            }
            #result-area-right {
                width: 100%; height: 60vh; flex: none; padding: 20px 0;
            }
            .template-selector { display: none; }
        }
        
        @media (min-width: 769px) {
            .logo { height: var(--header-logo-height-desk); }
            .user-controls-loggedin { padding: 8px; gap: 12px; }
            .token-display { font-size: 1rem; padding-left: 18px; }
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
            <a href="{{ url_for('index') }}" class="app-logo-link">
                <img src="{{ url_for('static', filename='images/LOGO_CHANGER.svg') }}" alt="Changer Logo" class="logo">
            </a>
            <div class="top-right-nav">
                <div class="mode-selector">
                    <button class="mode-btn active" data-mode="edit">Edit</button>
                    <button class="mode-btn" data-mode="upscale">Upscale</button>
                </div>
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
                         </div>
                        <ul>
                            <li><a href="{{ url_for('buy_tokens_page') }}">Пополнить баланс</a></li>
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
                <div class="control-group" style="align-items: center; gap: 15px;">
                    <div class="edit-mode-selector">
                        <button class="edit-mode-btn active" data-edit-mode="edit" data-description="Добавляйте или удаляйте объекты, меняйте стиль и освещение. Используйте шаблоны или свой запрос.">Edit</button>
                        <button class="edit-mode-btn" data-edit-mode="merge" data-description="Объединяйте два изображения, интегрируйте новые элементы или перенесите стиль с референса.">Merge</button>
                        <button class="edit-mode-btn" data-edit-mode="autofix" data-description="Просто загрузите фото для автоматического удаления артефактов и улучшения качества.">Autofix</button>
                    </div>
                    <p id="edit-mode-description" class="mode-description"></p>
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
                            <button class="template-btn" data-prompt="hyperrealistic photo of a modern object">Создать</button>
                            <button class="template-btn" data-prompt="dramatic studio lighting, cinematic relighting">Освещение</button>
                            <button class="template-btn" data-prompt="remove the main object">Удалить</button>
                            <button class="template-btn" data-prompt="change background to a detailed city street">Фон</button>
                        </div>
                    </div>

                    <form id="edit-form" class="input-area">
                         <input type="text" id="prompt" name="prompt" placeholder="Что вы хотите изменить?">
                    </form>
                </div>

                <div class="submit-action-group">
                    <div class="submit-button-wrapper">
                        <button type="submit" id="submit-button-edit" class="submit-button-element">
                            <img src="{{ url_for('static', filename='images/MAGIC_GREEN.png') }}" alt="Generate" class="submit-button-icon-img">
                        </button>
                    </div>
                    <div class="token-cost">
                        <span>Стоимость: 1</span>
                        <span class="token-coin"></span>
                    </div>
                </div>
            </div>

            <div id="upscale-view" style="display: none;">
                 <div class="control-group">
                    <label>Разрешение</label>
                    <div class="resolution-selector">
                        <button class="resolution-btn active" data-value="x2">x2</button>
                        <button class="resolution-btn" data-value="x4">x4</button>
                        <button class="resolution-btn" data-value="x8">x8</button>
                    </div>
                </div>
                
                <label for="image-file-upscale" class="image-drop-area">
                    <img src="{{ url_for('static', filename='images/JDTI.png') }}" alt="Just drop the image" class="drop-placeholder-img">
                    <img id="image-preview-upscale" src="#" alt="Preview" class="image-preview-img">
                </label>
                <input type="file" id="image-file-upscale" name="image" accept="image/*" style="display: none;">

                <div class="control-group">
                     <div class="slider-container">
                        <div class="slider-header">
                            <label for="creativity-slider">Креативность</label>
                            <span class="slider-value" id="creativity-value">70</span>
                        </div>
                        <input type="range" id="creativity-slider" min="0" max="100" value="70" class="custom-slider">
                    </div>
                </div>

                <div class="control-group">
                     <div class="slider-container">
                         <div class="slider-header">
                            <label for="resemblance-slider">Сходство с оригиналом</label>
                            <span class="slider-value" id="resemblance-value">80</span>
                        </div>
                        <input type="range" id="resemblance-slider" min="0" max="100" value="80" class="custom-slider">
                    </div>
                </div>

                <div class="submit-action-group">
                    <div class="submit-button-wrapper">
                        <button type="submit" id="submit-button-upscale" class="submit-button-element">
                            <img src="{{ url_for('static', filename='images/MAGIC_GREEN.png') }}" alt="Generate" class="submit-button-icon-img">
                        </button>
                    </div>
                    <div class="token-cost">
                        <span>Стоимость: 5</span>
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
    // --- JS ЛОГИКА ОСТАЛАСЬ БЕЗ ИЗМЕНЕНИЙ ---
    document.addEventListener('DOMContentLoaded', () => {

    const tokenBalanceDisplaySpan = document.getElementById('token-balance-display');
    const burgerMenuToggle = document.getElementById('burger-menu-toggle');
    const dropdownMenu = document.getElementById('dropdown-menu');

    const mainContentWrapper = document.getElementById('main-content-wrapper');
    const resultAreaRight = document.getElementById('result-area-right');

    const appModeButtons = document.querySelectorAll('.mode-btn');
    const editView = document.getElementById('edit-view');
    const upscaleView = document.getElementById('upscale-view');

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

    const templateButtons = document.querySelectorAll('.template-btn');
    const promptInput = document.getElementById('prompt');
    templateButtons.forEach(button => {
        button.addEventListener('click', () => {
            templateButtons.forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');
            promptInput.value = button.dataset.prompt;
            promptInput.focus();
        });
    });

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
        dropArea.addEventListener('dragleave', (e) => { e.preventDefault(); e.stopPropagation(); dropArea.classList.remove('dragover'); });
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
            showError("Пожалуйста, загрузите изображение.");
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
                let errorDetail = data.error || data.detail || 'Неизвестная ошибка сервера';
                if (response.status === 403) {
                     errorDetail = 'Недостаточно токенов. Пожалуйста, пополните баланс.';
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
                showError("Не удалось загрузить сгенерированное изображение.");
                showView('main');
                appBgWrapper.classList.remove('bg-blur');
            };
            tempImg.src = data.output_url;

        } catch (error) {
            showError("Произошла ошибка: " + error.message);
            showView('main');
            appBgWrapper.classList.remove('bg-blur');
        }
    }

    document.getElementById('submit-button-edit').addEventListener('click', (e) => {
        e.preventDefault();
        handleImageProcessing(e.currentTarget);
    });
     document.getElementById('submit-button-upscale').addEventListener('click', (e) => {
        e.preventDefault();
        // Здесь должна быть своя функция для upscale, пока просто заглушка
        showError("Режим Upscale в разработке!");
    });

    document.querySelector('.logo').addEventListener('click', (e) => {
        e.preventDefault();
        window.location.href = "{{ url_for('index') }}";
        // showView('main'); // Можно использовать, если не хотим перезагружать страницу
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
    # Placeholder - You can create a proper template for this
    return render_template_string("""
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <title>Покупка токенов</title>
            <style>
                body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background-color: #1c1c1e; color: #fff; }
                .container { background-color: #2c2c2e; padding: 40px; border-radius: 12px; text-align: center; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }
                h1 { margin-bottom: 20px; }
                p { margin: 10px 0; font-size: 1.1em; }
                strong { color: #D9F47A; }
                a { display: inline-block; margin-top: 30px; padding: 12px 25px; background-color: #D9F47A; color: #1c1c1e; text-decoration: none; border-radius: 8px; font-weight: bold; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Купить токены</h1>
                <p>Привет, {{ current_user.email or current_user.username }}!</p>
                <p>Ваш текущий баланс: <strong>{{ current_user.token_balance }}</strong> токенов.</p>
                <p>Здесь будет информация о пакетах токенов и кнопка для перехода к оплате.</p>
                <a href="{{ url_for('index') }}">Вернуться на главную</a>
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
