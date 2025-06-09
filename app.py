import os
import boto3
import uuid
import requests
import time
import openai
from flask import Flask, request, jsonify, render_template, render_template_string, url_for, redirect, flash, g
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

# Простое внутрипроцессное хранилище для результатов.
# Для продакшена лучше использовать Redis или базу данных.
prediction_results = {}

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
    openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
else:
    openai_client = None
    print("!!! ВНИМАНИЕ: OPENAI_API_KEY не найден. Улучшение промптов и Autofix не будут работать.")

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
        existing_user_by_email = User.query.filter_by(email=form.email.data).first()
        if existing_user_by_email:
            flash('Пользователь с таким email уже существует.', 'error')
            return redirect(url_for('register'))

        hashed_password = generate_password_hash(form.password.data, method='pbkdf2:sha256')

        username_value = form.username.data if form.username.data and form.username.data.strip() else None

        if username_value and User.query.filter_by(username=username_value).first():
            flash('Это имя пользователя уже занято. Пожалуйста, выберите другое.', 'error')
            return redirect(url_for('register'))

        new_user = User(
            email=form.email.data,
            username=username_value,
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

# --- HTML ШАБЛОН И JS ---
INDEX_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>Changer AI</title>
    <style>
        /* CSS без изменений, оставлен для полноты */
        @font-face {
            font-family: 'Norms';
            src: url("{{ url_for('static', filename='fonts/norms_light.woff2') }}") format('woff2');
            font-weight: 300;
            font-style: normal;
        }
        @font-face {
            font-family: 'Norms';
            src: url("{{ url_for('static', filename='fonts/norms_regular.woff2') }}") format('woff2');
            font-weight: 400;
            font-style: normal;
        }
        @font-face {
            font-family: 'Norms';
            src: url("{{ url_for('static', filename='fonts/norms_medium.woff2') }}") format('woff2');
            font-weight: 500;
            font-style: normal;
        }
        @font-face {
            font-family: 'Norms';
            src: url("{{ url_for('static', filename='fonts/norms_bold.woff2') }}") format('woff2');
            font-weight: 700;
            font-style: normal;
        }
        @font-face {
            font-family: 'Norms';
            src: url("{{ url_for('static', filename='fonts/norms_black.woff2') }}") format('woff2');
            font-weight: 900;
            font-style: normal;
        }

        :root {
            --accent-color: #D9F47A;
            --accent-glow: rgba(217, 244, 122, 0.7);
            --base-bg-color: #0c0d10;
            --surface-color: #1c1c1f;
            --surface-glass: rgba(35, 35, 38, 0.6); 
            --primary-text-color: #EAEAEA;
            --secondary-text-color: #888888;
            --accent-text-color: #1A1A1A;
            --border-color: rgba(255, 255, 255, 0.1);
            --shadow-color: rgba(0, 0, 0, 0.5);

            --blur-intensity: 25px; 
            --content-border-radius: 24px;
            --element-border-radius: 16px;
            --button-border-radius: 14px;
            --transition-speed: 0.3s;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }
       
        ::-webkit-scrollbar { width: 8px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background-color: rgba(255, 255, 255, 0.1); border-radius: 10px; }
        ::-webkit-scrollbar-thumb:hover { background-color: rgba(255, 255, 255, 0.2); }


        body {
            font-family: 'Norms', sans-serif;
            font-weight: 400; 
            color: var(--primary-text-color);
            background-color: var(--base-bg-color); 
            display: flex;
            flex-direction: column;
            min-height: 100vh;
            overflow: hidden;
            position: relative;
           
            background-image: url("{{ url_for('static', filename='images/desktop_background.webp') }}");
            background-size: cover;
            background-position: center center;
            background-repeat: no-repeat;
            background-attachment: fixed;
        }
       
        .app-container {
            width: 100%; max-width: 1200px; margin: 0 auto;
            padding: 100px 25px 40px;
            display: flex; flex-direction: row; align-items: flex-start;
            gap: 25px; height: 100vh;
            position: relative;
            z-index: 2;
        }

        .page-header-container {
            position: fixed; top: 0; left: 0; right: 0; width: 100%; z-index: 105;
            display: flex; justify-content: center; padding: 12px 0;
            background-color: var(--surface-glass);
            backdrop-filter: blur(var(--blur-intensity));
            -webkit-backdrop-filter: blur(var(--blur-intensity));
            border-bottom: 1px solid var(--border-color);
            box-shadow: inset 0 -1px 2px rgba(255, 255, 255, 0.1);
        }
        .page-header-inner {
            width: 100%; max-width: 1200px; padding: 0 25px;
            display: flex; justify-content: space-between; align-items: center;
        }

        .header-left-group { display: flex; align-items: center; gap: 25px; }
        .app-logo-link { display: inline-block; transition: transform var(--transition-speed) ease; }
        .app-logo-link:hover { transform: scale(1.05); }
        .logo { height: 38px; cursor: pointer; display: block; }
       
        .mode-selector {
            display: flex; align-items: center; background-color: rgba(0,0,0,0.2);
            padding: 6px; border-radius: var(--button-border-radius); gap: 6px;
            border: 1px solid var(--border-color);
        }
        .mode-btn {
            background-color: transparent; border: none; padding: 8px 20px;
            border-radius: 10px; cursor: pointer;
            font-family: 'Norms', sans-serif; font-size: 0.9rem;
            font-weight: 500;
            color: var(--primary-text-color); 
            opacity: 0.7; 
            transition: all var(--transition-speed) ease;
        }
        .mode-btn.active {
            background-color: var(--accent-color); color: var(--accent-text-color);
            font-weight: 700;
            opacity: 1;
            box-shadow: 0 0 15px var(--accent-glow);
        }
        .mode-btn:not(.active):hover { opacity: 1; color: var(--primary-text-color); }

        .top-right-nav { position: relative; display: flex; align-items: center; }

        .user-controls-loggedin {
            display: flex; align-items: center; background-color: var(--surface-color);
            padding: 8px 8px 8px 18px; border-radius: var(--content-border-radius); gap: 12px;
            border: 1px solid var(--border-color);
        }
        .token-display {
            display: flex; align-items: center; color: var(--primary-text-color);
            font-size: 1rem; font-weight: 500;
        }
        .token-coin {
            width: 18px; height: 18px; background-color: var(--accent-color);
            border-radius: 50%; margin-left: 8px;
            box-shadow: 0 0 10px var(--accent-glow);
        }
        .burger-menu-btn {
            background-color: transparent; border: 1px solid var(--border-color); border-radius: 50%;
            padding: 0; cursor: pointer; width: 40px; height: 40px;
            display: flex; align-items: center; justify-content: center;
            transition: all var(--transition-speed) ease; position: relative;
        }
        .burger-menu-btn:hover { transform: scale(1.1); border-color: var(--accent-glow); }
        .burger-menu-btn svg .line { stroke: var(--primary-text-color); stroke-width:10; stroke-linecap:round; transition: transform 0.3s 0.05s ease-in-out, opacity 0.2s ease-in-out; transform-origin: 50% 50%;}
        .burger-menu-btn .burger-icon { width: 16px; height: 12px; }
        .burger-menu-btn.open .burger-icon .line1 { transform: translateY(5.5px) rotate(45deg); }
        .burger-menu-btn.open .burger-icon .line2 { opacity: 0; }
        .burger-menu-btn.open .burger-icon .line3 { transform: translateY(-5.5px) rotate(-45deg); }
       
        .dropdown-menu {
            position: absolute; top: calc(100% + 10px); right: 0;
            background-color: var(--surface-glass);
            backdrop-filter: blur(var(--blur-intensity)); -webkit-backdrop-filter: blur(var(--blur-intensity));
            border-radius: var(--element-border-radius); box-shadow: 0 10px 40px var(--shadow-color);
            border: 1px solid var(--border-color); padding: 12px; width: 240px; z-index: 1000;
            opacity: 0; visibility: hidden; transform: translateY(-10px);
            transition: all var(--transition-speed) ease;
        }
        .dropdown-menu.open { opacity: 1; visibility: visible; transform: translateY(0); }
        .dropdown-header { padding: 5px; margin-bottom: 10px; border-bottom: 1px solid var(--border-color); }
        .dropdown-user-email { 
            color: var(--primary-text-color); font-size: 0.9rem; font-weight: 500;
        }
        .dropdown-menu ul { list-style: none; }
        .dropdown-menu li a {
            display: block; padding: 10px 5px; color: var(--primary-text-color); text-decoration: none;
            font-size: 0.95rem; font-weight: 400;
            transition: all var(--transition-speed) ease;
            border-radius: 8px;
        }
        .dropdown-menu li a:hover { color: var(--accent-color); background-color: rgba(255,255,255,0.05);}
       
        .user-controls-loggedout {
            display: flex; align-items: center; gap: 15px;
        }
        .user-controls-loggedout .auth-button {
            color: var(--primary-text-color); text-decoration: none; font-size: 0.9rem; font-weight: 500;
            transition: color var(--transition-speed) ease; padding: 10px 18px;
            border: 1px solid var(--border-color); border-radius: var(--button-border-radius);
            background-color: rgba(255,255,255,0.05);
        }
        .user-controls-loggedout .auth-button:hover { color: var(--accent-text-color); background-color: var(--accent-color); border-color: var(--accent-color); font-weight: 700; }

        .content-wrapper {
            width: 100%; max-width: 440px; padding: 25px;
            background-color: var(--surface-color);
            border-radius: var(--content-border-radius);
            transition: opacity var(--transition-speed), filter var(--transition-speed);
            border: 1px solid var(--border-color);
            box-shadow: 0 10px 40px var(--shadow-color);
            overflow-y: auto;
        }
        .content-wrapper.disabled { opacity: 0.5; pointer-events: none; filter: blur(4px); }
       
        #upscale-view, #edit-view {
            width: 100%; display: flex; flex-direction: column;
            align-items: center; justify-content: flex-start; gap: 20px;
            min-height: 500px; /* Для предотвращения сжатия при переключении */
        }
       
        .image-inputs-container {
            display: flex; justify-content: center; gap: 15px; width: 100%;
        }
        .image-inputs-container.remix-mode .image-drop-area { flex: 1; max-width: none; }
       
        .image-drop-area {
            width: 100%; height: 160px; 
            background-color: rgba(0,0,0,0.25);
            border-radius: var(--element-border-radius); display: flex; flex-direction: column;
            justify-content: center; align-items: center; cursor: pointer;
            position: relative; overflow: hidden; border: 1px dashed var(--border-color);
            transition: all var(--transition-speed) ease;
        }
        .image-drop-area:hover, .image-drop-area.dragover {
             border-color: var(--accent-glow); background-color: rgba(217, 244, 122, 0.05);
             transform: scale(1.02);
        }
        .drop-placeholder { display: flex; flex-direction: column; align-items: center; gap: 12px; pointer-events: none; }
        .drop-placeholder-icon { width: 32px; height: 32px; color: var(--secondary-text-color); transition: color var(--transition-speed) ease; }
        .image-drop-area:hover .drop-placeholder-icon { color: var(--accent-color); }
        .drop-placeholder-text { 
            color: var(--secondary-text-color); font-size: 0.85rem; font-weight: 400; text-align: center;
        }
        .image-drop-area .image-preview-img {
            display: none; width: 100%; height: 100%; object-fit: cover;
            border-radius: inherit; position: absolute; z-index: 1;
        }
       
        #result-area-right {
            flex: 1; 
            height: calc(100vh - 140px); 
            display: flex; 
            flex-direction: column;
            gap: 20px;
            background-color: rgba(0,0,0,0.2);
            border-radius: var(--content-border-radius);
            border: 1px solid var(--border-color);
            padding: 20px;
            overflow-y: auto; 
        }
        #history-placeholder {
            display: flex; flex-direction: column; justify-content: center;
            align-items: center; width: 100%; height: 100%; color: var(--secondary-text-color);
            text-align: center; gap: 15px; font-size: 0.9rem;
        }
        #history-placeholder svg { width: 48px; height: 48px; }
       
        .history-item {
              justify-content: center; display: flex; align-items: center;
              width: 100%; position: relative;
              flex-shrink: 0;
        }
        .history-item-image {
            width: 100%; height: auto; object-fit: contain; border-radius: var(--element-border-radius);
            box-shadow: 0 10px 40px var(--shadow-color); display: block;
        }
        .download-action-link {
            display: flex; position: absolute; bottom: 15px; right: 15px;
            z-index: 10; cursor: pointer;
            transition: transform var(--transition-speed) ease;
            background-color: rgba(0,0,0,0.4);
            backdrop-filter: blur(10px);
            border-radius: 50%;
            padding: 8px;
        }
        .download-action-link:hover { transform: scale(1.1); }
        .download-button-icon { height: 24px; width: 24px; display: block; }

        .loader-container {
            width: 100%; padding: 40px 0; justify-content: center; align-items: center; z-index: 101; display: flex;
            flex-shrink: 0;
        }
        .pulsating-dot {
            width: 80px; height: 80px; background-color: var(--accent-color);
            border-radius: 50%; position: relative;
            box-shadow: 0 0 40px var(--accent-glow);
            animation: pulse 1.5s infinite ease-in-out;
        }
        @keyframes pulse { 0%, 100% { transform: scale(0.9); opacity: 0.8; } 50% { transform: scale(1); opacity: 1; } }
       
        .input-area {
            display: flex; align-items: center; background-color: rgba(0,0,0,0.25);
            border-radius: var(--button-border-radius); padding: 6px 8px; width: 100%;
            border: 1px solid var(--border-color); transition: all var(--transition-speed) ease;
        }
        .input-area:focus-within { border-color: var(--accent-color); box-shadow: 0 0 15px rgba(217, 244, 122, 0.3); }
        #prompt {
            flex-grow: 1; border: none; padding: 10px 15px; font-size: 0.9rem;
            background-color: transparent; outline: none; color: var(--primary-text-color);
            font-family: 'Norms', sans-serif;
            font-weight: 400;
        }
        #prompt::placeholder { color: var(--secondary-text-color); opacity: 1; }
       
        .submit-action-group {
            width: 100%; display: flex; flex-direction: column; align-items: center; gap: 15px; margin-top: auto; padding-top: 20px;
        }
        .submit-button-element {
            width: 100%; background-color: transparent; color: var(--accent-color);
            border: 1px solid var(--accent-color); cursor: pointer; padding: 16px;
            border-radius: var(--button-border-radius); font-size: 1.1rem;
            font-family: 'Norms', sans-serif;
            font-weight: 700; 
            transition: all var(--transition-speed) ease-out;
            position: relative; overflow: hidden;
            letter-spacing: 0.5px;
        }
        .submit-button-element:hover {
            transform: translateY(-3px);
            background-color: var(--accent-color);
            color: var(--accent-text-color);
            box-shadow: 0 5px 20px var(--accent-glow);
        }
        .submit-button-element:active { transform: translateY(0); }

        .control-group { width: 100%; display: flex; flex-direction: column; gap: 12px; }
        .control-group > label {
            font-size: 0.9rem; color: var(--primary-text-color); margin-bottom: 0; padding-left: 5px; 
            font-weight: 700;
        }
        .edit-mode-selector, .resolution-selector {
            display: flex; gap: 10px; width: 100%; background-color: rgba(0,0,0,0.25);
            padding: 5px; border-radius: var(--button-border-radius); border: 1px solid var(--border-color);
        }
        .template-selector {
            display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; width: 100%;
        }
        .edit-mode-btn, .resolution-btn {
            flex: 1; padding: 12px;
            border-radius: 10px;
            border: none; background-color: transparent;
            color: var(--secondary-text-color); cursor: pointer; font-family: 'Norms', sans-serif;
            font-size: 0.85rem; font-weight: 500;
            transition: all var(--transition-speed) ease; text-align: center;
        }
        .edit-mode-btn:hover, .resolution-btn:hover {
            color: var(--primary-text-color); background-color: rgba(255,255,255,0.05);
        }
        .edit-mode-btn.active, .resolution-btn.active {
            background-color: var(--accent-color); border-color: var(--accent-color);
            color: var(--accent-text-color); box-shadow: 0 0 15px var(--accent-glow);
            font-weight: 700;
        }

        .template-btn {
            display: flex; flex-direction: column; align-items: center; justify-content: center;
            gap: 8px; padding: 12px; border-radius: var(--element-border-radius);
            border: 1px solid var(--border-color); background-color: rgba(0,0,0,0.25);
            color: var(--primary-text-color); cursor: pointer; font-family: 'Norms', sans-serif;
            font-size: 0.8rem;
            font-weight: 500; 
            transition: all var(--transition-speed) ease; text-align: center;
        }
        .template-btn:hover {
             border-color: var(--accent-color); transform: translateY(-3px);
             background-color: rgba(217, 244, 122, 0.1);
        }
        .template-btn.active {
            border-color: var(--accent-color); background-color: rgba(217, 244, 122, 0.15);
            box-shadow: 0 0 15px rgba(217, 244, 122, 0.2); color: var(--accent-color);
            font-weight: 700;
        }
        .template-btn svg { width: 22px; height: 22px; margin-bottom: 5px; color: var(--secondary-text-color); transition: all var(--transition-speed) ease; }
        .template-btn:hover svg, .template-btn.active svg { color: var(--accent-color); }
       
        .mode-description {
            font-size: 0.85rem; color: var(--secondary-text-color); text-align: center;
            width: 100%; padding: 0 10px; line-height: 1.5; min-height: 2.5em;
            font-weight: 300;
        }

        .slider-container { width: 100%; padding: 10px; background-color: rgba(0,0,0,0.25); border-radius: var(--element-border-radius); border: 1px solid var(--border-color);}
        .slider-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; }
        .slider-container label { font-weight: 500; color: var(--primary-text-color); font-size: 0.9rem; }
        .slider-value { font-weight: 700; color: var(--primary-text-color); }
        .slider-container input[type="range"] {
            -webkit-appearance: none; appearance: none;
            width: 100%; height: 4px; background: var(--border-color);
            border-radius: 5px; outline: none; transition: background var(--transition-speed) ease;
        }
        .slider-container input[type="range"]::-webkit-slider-thumb {
            -webkit-appearance: none; appearance: none;
            width: 20px; height: 20px; border-radius: 50%;
            background: var(--accent-color); cursor: pointer;
            border: none;
            box-shadow: 0 0 10px var(--accent-glow);
            transition: transform var(--transition-speed) ease;
        }
        .slider-container input[type="range"]::-webkit-slider-thumb:hover { transform: scale(1.2); }
        .slider-container input[type="range"]::-moz-range-thumb {
            width: 20px; height: 20px; border-radius: 50%;
            background: var(--accent-color); cursor: pointer; border: none;
            box-shadow: 0 0 10px var(--accent-glow);
            transition: transform var(--transition-speed) ease;
        }
        .slider-container input[type="range"]::-moz-range-thumb:hover { transform: scale(1.2); }
       
        .token-cost {
            display: flex; justify-content: center; align-items: center; gap: 8px;
            font-size: 0.9rem; color: var(--secondary-text-color); font-weight: 500;
        }
        .token-cost .token-coin { 
            width: 14px; 
            height: 14px; 
            margin-left: 0; 
            background-color: var(--accent-color); 
            box-shadow: 0 0 8px var(--accent-glow);
        }

        .error-message {
            display: none; font-size: 0.9rem; color: #F0F0F0;
            background-color: rgba(229, 62, 62, 0.5); backdrop-filter: blur(10px);
            padding: 12px 18px; border-radius: 12px; position: fixed;
            bottom: 20px; left: 50%; transform: translateX(-50%);
            border: 1px solid rgba(229, 62, 62, 0.8);
            width: auto; max-width: 480px; z-index: 105; text-align: center;
            box-shadow: 0 4px 15px rgba(229, 62, 62, 0.4);
            transition: all var(--transition-speed) ease;
            font-weight: 500;
        }
       
        @media (max-width: 992px) {
            body { overflow-y: auto; }
            .app-container {
                flex-direction: column; align-items: center; height: auto;
                overflow-y: visible; padding-top: 100px; padding-bottom: 25px;
            }
             #result-area-right {
                width: 100%; height: auto; flex: none; min-height: 300px;
                margin-bottom: 15px; max-height: 60vh;
            }
            .content-wrapper { overflow-y: visible; }
        }

        @media (max-width: 768px) {
            .app-container { padding-top: 150px; }
            .header-left-group { flex-direction: column; align-items: flex-start; gap: 15px; }
            .page-header-inner { align-items: flex-start; }
            .content-wrapper { max-width: 100%; }

            body {
                background-image: url("{{ url_for('static', filename='images/mobile_background.webp') }}");
                background-attachment: scroll;
            }
        }
    </style>
</head>
<body>
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
                        </button>
                    </div>
                    <div class="dropdown-menu" id="dropdown-menu">
                        <div class="dropdown-header">
                             <span class="dropdown-user-email">{{ current_user.email or current_user.username }}</span>
                        </div>
                        <ul>
                            <li><a href="{{ url_for('buy_tokens_page') }}">Top Up Balance</a></li>
                            <li><a href="{{ url_for('change_password') }}">Change Password</a></li>
                            <li><a href="{{ url_for('logout') }}">Logout</a></li>
                        </ul>
                    </div>
                {% else %}
                    <div class="user-controls-loggedout">
                        <a href="{{ url_for('login') }}" class="auth-button">Login</a>
                        <a href="{{ url_for('register') }}" class="auth-button">Sign Up</a>
                    </div>
                {% endif %}
            </div>
        </div>
    </div>

    <div class="app-container">
        <div class="content-wrapper" id="main-content-wrapper">
            <div id="edit-view">
                 <div class="control-group">
                    <div class="edit-mode-selector">
                        <button class="edit-mode-btn active" data-edit-mode="edit" data-description="Add or remove objects, modify style or lighting.">Edit</button>
                        <button class="edit-mode-btn" data-edit-mode="remix" data-description="Remix two images, integrate new items or transfer style.">Remix</button>
                        <button class="edit-mode-btn" data-edit-mode="autofix" data-description="Automatic artifact removal and quality enhancement.">Autofix</button>
                    </div>
                </div>
                 <p id="edit-mode-description" class="mode-description"></p>

                <div class="image-inputs-container">
                    <label for="image-file-edit-1" id="image-drop-area-edit-1" class="image-drop-area">
                        <div class="drop-placeholder">
                             <svg class="drop-placeholder-icon" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5" /></svg>
                            <span class="drop-placeholder-text">Drop Image or Click</span>
                        </div>
                        <img id="image-preview-edit-1" src="#" alt="Preview" class="image-preview-img">
                    </label>
                    <label for="image-file-edit-2" id="image-drop-area-edit-2" class="image-drop-area" style="display: none;">
                        <div class="drop-placeholder">
                             <svg class="drop-placeholder-icon" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5" /></svg>
                            <span class="drop-placeholder-text">Drop Style or Click</span>
                        </div>
                        <img id="image-preview-edit-2" src="#" alt="Preview" class="image-preview-img">
                    </label>
                </div>
                <input type="file" id="image-file-edit-1" name="image1" accept="image/*" style="display: none;">
                <input type="file" id="image-file-edit-2" name="image2" accept="image/*" style="display: none;">

                <div id="edit-controls-container" style="width:100%; display:flex; flex-direction:column; gap: 15px;">
                    <div class="control-group">
                        <div id="templates-for-edit">
                             <div class="template-selector">
                                 <button class="template-btn" data-prompt="hyperrealistic photo of a modern object">
                                     <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4.5v15m7.5-7.5h-15" /></svg>
                                     Create
                                 </button>
                                 <button class="template-btn" data-prompt="dramatic studio lighting, cinematic relighting">
                                     <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M12 3v2.25m6.364.386-1.591 1.591M21 12h-2.25m-.386 6.364-1.591-1.591M12 18.75V21m-4.773-4.227-1.591 1.591M5.25 12H3m4.227-4.773L5.636 5.636M15.75 12a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0Z" /></svg>
                                     Relight
                                 </button>
                                 <button class="template-btn" data-prompt="remove the main object">
                                     <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0" /></svg>
                                     Remove
                                 </button>
                                 <button class="template-btn" data-prompt="change background to a detailed city street">
                                     <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M10.34 3.94A2.25 2.25 0 0 1 12 2.25a2.25 2.25 0 0 1 1.66.94m-3.32 0A2.25 2.25 0 0 0 12 2.25a2.25 2.25 0 0 0 1.66.94m0 0a2.25 2.25 0 0 1 2.25 2.25v5.169a2.25 2.25 0 0 1-2.25-2.25H8.34a2.25 2.25 0 0 1-2.25-2.25V6.44a2.25 2.25 0 0 1 2.25-2.25m6.062 0a2.25 2.25 0 0 0-1.66-.94m-3.32 0a2.25 2.25 0 0 1-1.66.94m12.334 10.035a2.25 2.25 0 0 1-2.25 2.25h-5.169a2.25 2.25 0 0 1-2.25-2.25v-5.169a2.25 2.25 0 0 1 2.25-2.25h5.169a2.25 2.25 0 0 1 2.25 2.25v5.169z" /><path stroke-linecap="round" stroke-linejoin="round" d="M12 6.87a1.125 1.125 0 1 0 0 2.25 1.125 1.125 0 0 0 0-2.25z" /></svg>
                                     Change
                                 </button>
                             </div>
                        </div>
                        <div id="templates-for-remix" style="display: none;">
                            </div>
                    </div>
                    <form id="edit-form" class="input-area">
                         <input type="text" id="prompt" name="prompt" placeholder="Describe your changes...">
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
                <label for="image-file-upscale" class="image-drop-area">
                    <div class="drop-placeholder">
                        <svg class="drop-placeholder-icon" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5" /></svg>
                        <span class="drop-placeholder-text">Drop Image to Upscale</span>
                    </div>
                    <img id="image-preview-upscale" src="#" alt="Preview" class="image-preview-img">
                </label>
                <input type="file" id="image-file-upscale" name="image" accept="image/*" style="display: none;">
               
                <div style="display: flex; flex-direction: column; gap: 15px; width: 100%;">
                    <div class="control-group">
                        <label>Resolution</label>
                        <div class="resolution-selector">
                            <button class="resolution-btn active" data-value="2">x2</button>
                            <button class="resolution-btn" data-value="4">x4</button>
                            <button class="resolution-btn" data-value="8">x8</button>
                        </div>
                    </div>
                    <div class="control-group">
                         <div class="slider-container">
                            <div class="slider-header">
                                <label for="creativity-slider">Creativity</label>
                                <span class="slider-value" id="creativity-value">35</span>
                            </div>
                            <input type="range" id="creativity-slider" min="0" max="100" value="35" class="custom-slider">
                        </div>
                    </div>
                    <div class="control-group">
                         <div class="slider-container">
                             <div class="slider-header">
                                <label for="resemblance-slider">Resemblance</label>
                                <span class="slider-value" id="resemblance-value">20</span>
                            </div>
                            <input type="range" id="resemblance-slider" min="0" max="100" value="20" class="custom-slider">
                        </div>
                    </div>
                    <div class="control-group">
                         <div class="slider-container">
                             <div class="slider-header">
                                <label for="hdr-slider">HDR</label>
                                <span class="slider-value" id="hdr-value">10</span>
                            </div>
                            <input type="range" id="hdr-slider" min="0" max="100" value="10" class="custom-slider">
                        </div>
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
             <div id="history-placeholder">
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" d="m2.25 15.75 5.159-5.159a2.25 2.25 0 0 1 3.182 0l5.159 5.159m-1.5-1.5 1.409-1.409a2.25 2.25 0 0 1 3.182 0l2.909 2.909m-18 3.75h16.5a1.5 1.5 0 0 0 1.5-1.5V6a1.5 1.5 0 0 0-1.5-1.5H3.75A1.5 1.5 0 0 0 2.25 6v12a1.5 1.5 0 0 0 1.5 1.5Zm10.5-11.25h.008v.008h-.008V8.25Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Z" />
                </svg>
                Your generations will appear here.
             </div>
        </div>
    </div>

    <div id="error-box" class="error-message"></div>

    <script>
    document.addEventListener('DOMContentLoaded', () => {
        // --- Глобальные переменные и элементы ---
        const tokenBalanceDisplaySpan = document.getElementById('token-balance-display');
        const mainContentWrapper = document.getElementById('main-content-wrapper');
        const resultAreaRight = document.getElementById('result-area-right');
        const historyPlaceholder = document.getElementById('history-placeholder');
        const errorBox = document.getElementById('error-box');

        let activePollingInterval = null;

        // --- Функции-помощники ---
        function showError(message) {
            errorBox.textContent = message;
            errorBox.style.display = 'block';
            setTimeout(() => { errorBox.style.display = 'none'; }, 5000);
        }
        
        function startLoadingUI(isNewGeneration = true) {
            mainContentWrapper.classList.add('disabled');
            if (isNewGeneration) {
                if (historyPlaceholder) historyPlaceholder.style.display = 'none';
                const loaderHtml = `<div class="loader-container" id="current-loader"><div class="pulsating-dot"></div></div>`;
                resultAreaRight.insertAdjacentHTML('afterbegin', loaderHtml);
            }
        }

        function stopLoadingUI() {
             mainContentWrapper.classList.remove('disabled');
        }

        function createHistoryItem(url) {
            const item = document.createElement('div');
            item.className = 'history-item';
            item.innerHTML = `
                <img src="${url}" alt="Generated Image" class="history-item-image">
                <a href="${url}" class="download-action-link" download="generated_image.png" target="_blank" rel="noopener noreferrer">
                    <img src="{{ url_for('static', filename='images/Download.png') }}" alt="Download" class="download-button-icon">
                </a>`;
            return item;
        }

        function updateResult(newUrl) {
            const loader = document.getElementById('current-loader');
            if (loader) {
                const newItem = createHistoryItem(newUrl);
                loader.replaceWith(newItem);
            }
        }

        function resetLeftPanel() {
            stopLoadingUI();
            document.querySelectorAll('.image-preview-img').forEach(img => {
                img.src = '#';
                img.style.display = 'none';
            });
            document.querySelectorAll('.drop-placeholder').forEach(p => p.style.display = 'flex');
            document.getElementById('image-file-edit-1').value = '';
            document.getElementById('image-file-edit-2').value = '';
            document.getElementById('image-file-upscale').value = '';
            document.getElementById('prompt').value = '';
            document.querySelectorAll('.template-btn').forEach(btn => btn.classList.remove('active'));
        }
        
        // --- Логика переключения режимов ---
        const appModeButtons = document.querySelectorAll('.mode-btn');
        const editView = document.getElementById('edit-view');
        const upscaleView = document.getElementById('upscale-view');
        
        appModeButtons.forEach(button => {
            button.addEventListener('click', () => {
                const currentMode = button.dataset.mode;
                appModeButtons.forEach(btn => btn.classList.remove('active'));
                button.classList.add('active');
                editView.style.display = (currentMode === 'edit') ? 'flex' : 'none';
                upscaleView.style.display = (currentMode === 'upscale') ? 'flex' : 'none';
                resetLeftPanel();
                if(currentMode === 'edit') {
                    document.querySelector('.edit-mode-btn.active')?.click();
                }
            });
        });

        // --- Асинхронная обработка ---
        async function handleImageProcessing() {
            startLoadingUI();

            // 1. Собираем данные формы
            const formData = new FormData();
            const currentMode = document.querySelector('.mode-btn.active').dataset.mode;
            formData.append('mode', currentMode);
            
            if (currentMode === 'edit') {
                const imageFile1 = document.getElementById('image-file-edit-1').files[0];
                if (!imageFile1) { showError("Please select an image."); stopLoadingUI(); return; }
                formData.append('image', imageFile1);
                formData.append('prompt', document.getElementById('prompt').value);
                const editMode = document.querySelector('.edit-mode-btn.active').dataset.editMode;
                formData.append('edit_mode', editMode);
                if (editMode === 'remix') {
                    const imageFile2 = document.getElementById('image-file-edit-2').files[0];
                    if (!imageFile2) { showError("Please select the second image for Remix."); stopLoadingUI(); return; }
                    formData.append('image2', imageFile2);
                }
            } else if (currentMode === 'upscale') {
                const imageFile = document.getElementById('image-file-upscale').files[0];
                if (!imageFile) { showError("Please select an image to upscale."); stopLoadingUI(); return; }
                formData.append('image', imageFile);
                const activeResBtn = document.querySelector('.resolution-btn.active');
                formData.append('scale_factor', activeResBtn ? activeResBtn.dataset.value : '2');
                formData.append('creativity', document.getElementById('creativity-slider').value);
                formData.append('resemblance', document.getElementById('resemblance-slider').value);
                formData.append('hdr', document.getElementById('hdr-slider').value);
            }

            try {
                // 2. Отправляем запрос на запуск задачи
                const startResponse = await fetch("{{ url_for('process_image') }}", { method: 'POST', body: formData });
                const startData = await startResponse.json();

                if (!startResponse.ok) throw new Error(startData.error || 'Failed to start processing.');

                if (tokenBalanceDisplaySpan && startData.new_token_balance !== undefined) {
                    tokenBalanceDisplaySpan.textContent = startData.new_token_balance;
                }

                // 3. Начинаем опрашивать статус
                pollForPredictionResult(startData.prediction_id);

            } catch (error) {
                showError("Error: " + error.message);
                const loader = document.getElementById('current-loader');
                if (loader) loader.remove();
                stopLoadingUI();
            }
        }

        function pollForPredictionResult(predictionId) {
            if (activePollingInterval) clearInterval(activePollingInterval);

            activePollingInterval = setInterval(async () => {
                try {
                    const statusResponse = await fetch(`/get-status/${predictionId}`);
                    const statusData = await statusResponse.json();

                    if (statusResponse.ok) {
                        if (statusData.status === 'succeeded') {
                            clearInterval(activePollingInterval);
                            updateResult(statusData.output_url);
                            stopLoadingUI();
                        } else if (statusData.status === 'failed' || statusData.status === 'canceled') {
                            clearInterval(activePollingInterval);
                            showError('Processing failed: ' + statusData.error);
                            const loader = document.getElementById('current-loader');
                            if (loader) loader.remove();
                            stopLoadingUI();
                        }
                        // Если статус 'processing' или 'starting', ничего не делаем, ждем следующего опроса
                    } else {
                        clearInterval(activePollingInterval);
                        showError(statusData.error || 'Failed to get status.');
                        stopLoadingUI();
                    }
                } catch (error) {
                    clearInterval(activePollingInterval);
                    showError('Error checking status: ' + error.message);
                    stopLoadingUI();
                }
            }, 3000); // Опрашиваем каждые 3 секунды
        }

        // --- Привязка событий ---
        document.getElementById('submit-button-edit').addEventListener('click', handleImageProcessing);
        document.getElementById('submit-button-upscale').addEventListener('click', handleImageProcessing);

        // --- Инициализация UI ---
        // (Drag-n-drop, sliders, etc. - без изменений)
        const setupSlider = (sliderId, valueId) => {
            const slider = document.getElementById(sliderId);
            const valueDisplay = document.getElementById(valueId);
            if(slider && valueDisplay) {
                valueDisplay.textContent = slider.value;
                slider.addEventListener('input', (event) => { valueDisplay.textContent = event.target.value; });
            }
        };
        setupSlider('creativity-slider', 'creativity-value');
        setupSlider('resemblance-slider', 'resemblance-value');
        setupSlider('hdr-slider', 'hdr-value');
        
        function handleFileSelect(file, previewElementId) {
            const previewElement = document.getElementById(previewElementId);
            const dropArea = previewElement.parentElement;
            const placeholder = dropArea.querySelector('.drop-placeholder');
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
        setupDragAndDrop(document.getElementById('image-drop-area-edit-1'), document.getElementById('image-file-edit-1'));
        setupDragAndDrop(document.getElementById('image-drop-area-edit-2'), document.getElementById('image-file-edit-2'));
        setupDragAndDrop(document.querySelector('#upscale-view .image-drop-area'), document.getElementById('image-file-upscale'));
        
        document.querySelectorAll('.resolution-btn').forEach(button => {
            button.addEventListener('click', () => {
                document.querySelectorAll('.resolution-btn').forEach(btn => btn.classList.remove('active'));
                button.classList.add('active');
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
                const showSecondImage = (editMode === 'remix');
                const showControls = (editMode === 'edit' || editMode === 'remix');
                imageDropArea2.style.display = showSecondImage ? 'flex' : 'none';
                imageInputsContainer.classList.toggle('remix-mode', showSecondImage);
                editControlsContainer.style.display = showControls ? 'flex' : 'none';
            });
        });
        
        appModeButtons[0].click();
    });
    </script>
</body>
</html>
"""

@app.route('/buy-tokens')
@login_required
def buy_tokens_page():
    # Этот маршрут без изменений
    return render_template_string("""...""", current_user=current_user)


def upload_file_to_s3(file_to_upload):
    if not all([AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_S3_BUCKET_NAME, AWS_S3_REGION]):
        raise Exception("Ошибка конфигурации сервера для загрузки изображений.")

    s3_client = boto3.client('s3', region_name=AWS_S3_REGION, aws_access_key_id=AWS_ACCESS_KEY_ID, aws_secret_access_key=AWS_SECRET_ACCESS_KEY)
    _, f_ext = os.path.splitext(file_to_upload.filename)
    object_name = f"uploads/{uuid.uuid4()}{f_ext}"
   
    file_to_upload.stream.seek(0)
    s3_client.upload_fileobj(file_to_upload.stream, AWS_S3_BUCKET_NAME, object_name, ExtraArgs={'ContentType': file_to_upload.content_type})
   
    hosted_image_url = f"https://{AWS_S3_BUCKET_NAME}.s3.{AWS_S3_REGION}.amazonaws.com/{object_name}"
    print(f"!!! Изображение загружено на Amazon S3: {hosted_image_url}")
    return hosted_image_url

def improve_prompt_with_openai(user_prompt):
    # Эта функция без изменений
    return user_prompt 


# --- НОВЫЕ И ОБНОВЛЕННЫЕ МАРШРУТЫ ДЛЯ АСИНХРОННОЙ РАБОТЫ ---

@app.route('/process-image', methods=['POST'])
@login_required
def process_image():
    mode = request.form.get('mode')
    token_cost = 1 if mode == 'edit' else 5
    
    if current_user.token_balance < token_cost:
        return jsonify({'error': 'Недостаточно токенов'}), 403

    if 'image' not in request.files:
        return jsonify({'error': 'Отсутствует изображение'}), 400

    try:
        s3_url = upload_file_to_s3(request.files['image'])
        replicate_input = {}
        model_version_id = ""
        
        # Определяем ID для вебхука
        prediction_uuid = str(uuid.uuid4())
        webhook_url = url_for('replicate_webhook', _external=True)

        if mode == 'edit':
            # Логика для edit остается синхронной, так как она быстрая
            edit_mode = request.form.get('edit_mode')
            prompt = request.form.get('prompt', '')
            if edit_mode == 'remix':
                if 'image2' not in request.files: return jsonify({'error': 'Для режима Remix нужно второе изображение'}), 400
                s3_url_2 = upload_file_to_s3(request.files['image2'])
                model_version_id = "flux-kontext-apps/multi-image-kontext-max:07a1361c469f64e2311855a4358a9842a2d7575459397985773b400902f37752"
                final_prompt = improve_prompt_with_openai(prompt)
                replicate_input = {"image_a": s3_url, "image_b": s3_url_2, "prompt": final_prompt}
            else: # Standard Edit or Autofix
                model_version_id = "black-forest-labs/flux-kontext-max:0b9c317b23e79a9a0d8b9602ff4d04030d433055927fb7c4b91c44234a6818c4"
                final_prompt = improve_prompt_with_openai(prompt)
                replicate_input = {"input_image": s3_url, "prompt": final_prompt}
        
        elif mode == 'upscale':
            model_version_id = "dfad41707589d68ecdccd1dfa600d55a208f9310748e44bfe35b4a6291453d5e"
            scale_factor = int(request.form.get('scale_factor', '2'))
            creativity = float(request.form.get('creativity', '35')) / 100.0
            resemblance = float(request.form.get('resemblance', '20')) / 100.0 * 3.0
            hdr_slider = float(request.form.get('hdr', '10'))
            dynamic_hdr = 1 + (hdr_slider / 100.0 * 49.0)

            replicate_input = {
                "image": s3_url,
                "scale_factor": scale_factor,
                "creativity": creativity,
                "resemblance": resemblance,
                "dynamic": dynamic_hdr,
                "prompt": "masterpiece, best quality, highres, <lora:more_details:0.5> <lora:SDXLrender_v2.0:1>"
            }

        if not model_version_id:
             return jsonify({'error': 'Режим не реализован'}), 400

        # Списываем токены сразу при запуске
        current_user.token_balance -= token_cost
        db.session.commit()

        headers = {"Authorization": f"Bearer {REPLICATE_API_TOKEN}", "Content-Type": "application/json"}
        # Добавляем webhook в запрос!
        post_payload = {
            "version": model_version_id,
            "input": replicate_input,
            "webhook": f"{webhook_url}?id={prediction_uuid}&user_id={current_user.id}",
            "webhook_events_filter": ["completed"]
        }
       
        print(f"!!! Отправка Payload в Replicate: {post_payload}")
        start_response = requests.post("https://api.replicate.com/v1/predictions", json=post_payload, headers=headers)
        start_response.raise_for_status()
        
        # Сразу возвращаем ID предсказания, не ждем результат
        return jsonify({'prediction_id': prediction_uuid, 'new_token_balance': current_user.token_balance})

    except Exception as e:
        print(f"!!! ОШИБКА в process_image: {type(e).__name__} - {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Произошла внутренняя ошибка сервера: {str(e)}'}), 500


@app.route('/replicate-webhook', methods=['POST'])
def replicate_webhook():
    data = request.json
    prediction_id = request.args.get('id')
    user_id = request.args.get('user_id')
    
    print(f"!!! Получен Webhook для ID: {prediction_id}, User: {user_id}")
    print(f"!!! Webhook Data: {data}")

    if data['status'] == 'succeeded':
        # Сохраняем успешный результат
        output_url = data['output'][0] if isinstance(data['output'], list) else data['output']
        prediction_results[prediction_id] = {
            "status": "succeeded",
            "output_url": output_url
        }
    else:
        # Сохраняем ошибку и возвращаем токены
        prediction_results[prediction_id] = {
            "status": "failed",
            "error": data.get('error', 'Unknown error from Replicate')
        }
        # Логика возврата токенов
        with app.app_context():
            user = User.query.get(user_id)
            if user:
                token_cost = 1 if data.get('input', {}).get('prompt') else 5
                user.token_balance += token_cost
                db.session.commit()
                print(f"Токены возвращены пользователю {user_id}")


    return jsonify(success=True), 200


@app.route('/get-status/<prediction_id>')
@login_required
def get_status(prediction_id):
    result = prediction_results.get(prediction_id)
    if result:
        # Как только результат отдан, его можно удалить из памяти
        # del prediction_results[prediction_id] 
        return jsonify(result)
    else:
        # Если результата еще нет, сообщаем, что задача в процессе
        return jsonify({"status": "processing"})


with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get("PORT", 5001)))
