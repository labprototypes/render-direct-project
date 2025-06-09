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
            --surface-glass: rgba(28, 28, 31, 0.4); 
            --primary-text-color: #EAEAEA;
            --secondary-text-color: #888888;
            --accent-text-color: #1A1A1A;
            --border-color: rgba(255, 255, 255, 0.1);
            --shadow-color: rgba(0, 0, 0, 0.5);

            --blur-intensity: 22px; 
            --content-border-radius: 24px;
            --element-border-radius: 16px;
            --button-border-radius: 14px;
            --transition-speed: 0.3s;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

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
            /* --- Изменены отступы, добавлен нижний --- */
            padding: 100px 25px 40px;
            display: flex; flex-direction: row; align-items: flex-start;
            gap: 25px; height: 100vh;
            position: relative;
            z-index: 2;
        }

        .page-header-container {
            position: fixed; top: 0; left: 0; right: 0; width: 100%; z-index: 105;
            display: flex; justify-content: center; padding: 12px 0;
            /* --- Убрана подложка, теперь только стекло --- */
            background-color: transparent;
            backdrop-filter: blur(var(--blur-intensity));
            -webkit-backdrop-filter: blur(var(--blur-intensity));
            border-bottom: 1px solid var(--border-color);
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
            color: var(--secondary-text-color);
            transition: all var(--transition-speed) ease;
        }
        .mode-btn.active {
            background-color: var(--accent-color); color: var(--accent-text-color);
            font-weight: 700;
            box-shadow: 0 0 15px var(--accent-glow);
        }
        .mode-btn:not(.active):hover { color: var(--primary-text-color); }

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
        /* --- Правка анимации бургера для центрирования крестика --- */
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
        }
        .content-wrapper.disabled { opacity: 0.5; pointer-events: none; filter: blur(4px); }
        
        #upscale-view, #edit-view {
            width: 100%; display: flex; flex-direction: column;
            align-items: center; justify-content: flex-start; gap: 20px;
        }
        
        .image-inputs-container {
            display: flex; justify-content: center; gap: 15px; width: 100%;
        }
        .image-inputs-container.remix-mode .image-drop-area { flex: 1; max-width: none; }
        
        .image-drop-area {
            width: 100%; height: 160px; /* --- Уменьшена высота --- */ 
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
            /* --- Изменена высота с учетом отступов --- */
            height: calc(100vh - 140px); 
            display: none;
            justify-content: center; align-items: center;
            background-color: rgba(0,0,0,0.2);
            border-radius: var(--content-border-radius);
            border: 1px solid var(--border-color);
            padding: 20px;
        }
        .result-image-wrapper {
             justify-content: center; display: flex; align-items: center;
             width: 100%; height: 100%; position: relative;
        }
        #result-image {
            max-width: 100%; max-height: 100%; object-fit: contain; border-radius: var(--element-border-radius);
            box-shadow: 0 10px 40px var(--shadow-color); display: block;
        }
        .download-action-link {
            display: flex; position: absolute; top: calc(100% + 15px); right: 0;
            z-index: 10; cursor: pointer;
            transition: transform var(--transition-speed) ease;
        }
        .download-action-link:hover { transform: scale(1.1); }
        .download-button-icon { height: 36px; width: 36px; display: block; filter: drop-shadow(0 4px 8px var(--shadow-color)); }

        .loader-container {
            width: 100%; height: 100%; justify-content: center; align-items: center; z-index: 101; display: flex;
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
            width: 100%; display: flex; flex-direction: column; align-items: center; gap: 15px; margin-top: 10px;
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
            font-size: 0.8rem; /* --- Уменьшен шрифт --- */
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
        /* --- Удалена кастомная стилизация для монетки в 'cost', теперь она как в шапке --- */
        .token-cost .token-coin { width: 14px; height: 14px; margin-left: 0; box-shadow: none; }

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
            .app-container {
                flex-direction: column; align-items: center; height: auto;
                overflow-y: auto; padding-top: 100px; padding-bottom: 25px;
            }
             #result-area-right {
                width: 100%; height: auto; aspect-ratio: 16/10; flex: none; min-height: 300px;
                margin-bottom: 15px;
            }
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
                                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="m2.25 12 8.954-8.955c.44-.439 1.152-.439 1.591 0L21.75 12M2.25 12a8.963 8.963 0 0 1 4.282-7.532" /></svg>
                                    Change
                                </button>
                            </div>
                        </div>
                        <div id="templates-for-remix" style="display: none;">
                             <div class="template-selector">
                                <button class="template-btn" data-prompt="professional product shot, clean background">
                                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-6 h-6"><path stroke-linecap="round" stroke-linejoin="round" d="M20.25 7.5l-.625 10.632a2.25 2.25 0 01-2.247 2.118H6.622a2.25 2.25 0 01-2.247-2.118L3.75 7.5M10 11.25h4M3.375 7.5h17.25c.621 0 1.125-.504 1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125H3.375c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125z" /></svg>
                                    Product shot
                                </button>
                                <button class="template-btn" data-prompt="consistent character, same face, different pose">
                                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-6 h-6"><path stroke-linecap="round" stroke-linejoin="round" d="M15.75 6a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0zM4.501 20.118a7.5 7.5 0 0114.998 0A17.933 17.933 0 0112 21.75c-2.676 0-5.216-.584-7.499-1.632z" /></svg>
                                    Consistent character
                                </button>
                                <button class="template-btn" data-prompt="virtual try-on, wearing the new clothing item from the second image">
                                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-6 h-6"><path stroke-linecap="round" stroke-linejoin="round" d="M21 11.25v8.25a1.5 1.5 0 01-1.5 1.5H5.25a1.5 1.5 0 01-1.5-1.5v-8.25M12 4.875A2.625 2.625 0 1012 10.125A2.625 2.625 0 0012 4.875z" /><path stroke-linecap="round" stroke-linejoin="round" d="M12 4.875c-1.39-1.39-2.834-2.404-4.416-2.525C4.94 2.228 2.25 4.43 2.25 7.5c0 4.015 3.86 5.625 6.444 8.25" /><path stroke-linecap="round" stroke-linejoin="round" d="M12 4.875c1.39-1.39 2.834-2.404 4.416-2.525C19.06 2.228 21.75 4.43 21.75 7.5c0 4.015-3.86 5.625-6.444 8.25" /></svg>
                                    Try-on
                                </button>
                                <button class="template-btn" data-prompt="apply the artistic style of the second image to the first image">
                                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-6 h-6"><path stroke-linecap="round" stroke-linejoin="round" d="M9.53 16.122a3 3 0 00-5.78 1.128 2.25 2.25 0 01-2.4 2.245 4.5 4.5 0 008.4-2.245c0-.399-.078-.78-.22-1.128zm0 0a15.998 15.998 0 003.388-1.62m-5.043-.025a15.998 15.998 0 011.622-3.385m5.043.025a2.25 2.25 0 012.4-2.245 4.5 4.5 0 00-8.4-2.245c0 .399.078.78.22 1.128zm0 0a15.998 15.998 0 01-3.388-1.62m5.043-.025a15.998 15.998 0 00-1.622-3.385" /></svg>
                                    Style transfer
                                </button>
                            </div>
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
                <div class="control-group">
                     <div class="slider-container">
                         <div class="slider-header">
                            <label for="hdr-slider">HDR</label>
                            <span class="slider-value" id="hdr-value">50</span>
                        </div>
                        <input type="range" id="hdr-slider" min="0" max="100" value="50" class="custom-slider">
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
                <a href="#" id="download-action" class="download-action-link" download="generated_image.png" target="_blank" rel="noopener noreferrer">
                    <img src="{{ url_for('static', filename='images/Download.png') }}" alt="Download" class="download-button-icon">
                </a>
            </div>
        </div>
    </div>

    <div id="error-box" class="error-message"></div>

    <script>
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
                const activeEditMode = document.querySelector('.edit-mode-btn.active');
                if (activeEditMode) activeEditMode.click();
            }
        });
    });

    const editModeButtons = document.querySelectorAll('.edit-mode-btn');
    const editModeDescription = document.getElementById('edit-mode-description');
    const imageInputsContainer = document.querySelector('.image-inputs-container');
    const imageDropArea2 = document.getElementById('image-drop-area-edit-2');
    const editControlsContainer = document.getElementById('edit-controls-container');
    const templatesForEdit = document.getElementById('templates-for-edit');
    const templatesForRemix = document.getElementById('templates-for-remix');

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

            if (showControls) {
                templatesForEdit.style.display = (editMode === 'edit') ? 'block' : 'none';
                templatesForRemix.style.display = (editMode === 'remix') ? 'block' : 'none';
            }
        });
    });
    
    // --- Template Buttons Logic (applies to all template buttons) ---
    const allTemplateButtons = document.querySelectorAll('.template-btn');
    const promptInput = document.getElementById('prompt');
    
    allTemplateButtons.forEach(button => {
        button.addEventListener('click', () => {
            promptInput.value = button.dataset.prompt;
            promptInput.focus();
            // Deactivate all template buttons, then activate the clicked one
            allTemplateButtons.forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');
        });
    });
    
    promptInput.addEventListener('input', () => {
        allTemplateButtons.forEach(btn => btn.classList.remove('active'));
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
        if(slider && valueDisplay) {
            valueDisplay.textContent = slider.value;
            slider.addEventListener('input', (event) => {
                valueDisplay.textContent = event.target.value;
            });
        }
    };
    setupSlider('creativity-slider', 'creativity-value');
    setupSlider('resemblance-slider', 'resemblance-value');
    setupSlider('hdr-slider', 'hdr-value');

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
        setTimeout(() => { errorBox.style.display = 'none'; }, 5000);
    }

    function showView(viewName) {
        if (viewName === 'main') {
            mainContentWrapper.classList.remove('disabled');
            resultAreaRight.style.display = 'none';
            resetImagePreviews();
            promptInput.value = '';
            allTemplateButtons.forEach(btn => btn.classList.remove('active'));
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

    setupDragAndDrop(document.getElementById('image-drop-area-edit-1'), imageFileInputEdit1);
    setupDragAndDrop(document.getElementById('image-drop-area-edit-2'), imageFileInputEdit2);
    setupDragAndDrop(document.querySelector('#upscale-view .image-drop-area'), upscaleImageInput);

    function resetImagePreviews() {
        document.querySelectorAll('.image-preview-img').forEach(img => {
            img.src = '#';
            img.style.display = 'none';
        });
        document.querySelectorAll('.drop-placeholder').forEach(p => {
            if (p) p.style.display = 'flex';
        });
        imageFileInputEdit1.value = '';
        imageFileInputEdit2.value = '';
        upscaleImageInput.value = '';
    }

    async function handleImageProcessing(submitButton) {
        const currentMode = document.querySelector('.mode-btn.active').dataset.mode;

        showView('loading');

        const formData = new FormData();
        let url = '';

        if (currentMode === 'edit') {
            const editMode = document.querySelector('.edit-mode-btn.active').dataset.editMode;

            if (!imageFileInputEdit1.files[0]) {
                showError("Please select an image to " + editMode + ".");
                showView('main'); return;
            }
            url = "{{ url_for('process_image') }}";
            formData.append('image', imageFileInputEdit1.files[0]);
            formData.append('prompt', promptInput.value);

            if (editMode === 'remix' && imageFileInputEdit2.files[0]) {
                formData.append('image2', imageFileInputEdit2.files[0]);
            }

        } else if (currentMode === 'upscale') {
            showError("Upscale functionality is not yet connected.");
            showView('main');
            return;
        }

        try {
            const response = await fetch(url, {
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
    
    document.getElementById('submit-button-upscale').addEventListener('click', (e) => {
        e.preventDefault();
        handleImageProcessing(e.currentTarget);
    });

    document.querySelector('.app-logo-link').addEventListener('click', (e) => {
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
            <title>Buy Tokens</title>
            <style>
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
                    --bg-gradient-start: #0b0c0e;
                    --bg-gradient-end: #1a1b1e;
                    --surface-color: #1c1c1f;
                    --primary-text-color: #EAEAEA;
                    --secondary-text-color: #888888;
                    --accent-text-color: #1A1A1A;
                    --border-color: rgba(255, 255, 255, 0.1);
                    --shadow-color: rgba(0, 0, 0, 0.5);
                    --content-border-radius: 24px;
                }
                body {
                    font-family: 'Norms', sans-serif;
                    background: linear-gradient(135deg, var(--bg-gradient-start), var(--bg-gradient-end));
                    color: var(--primary-text-color);
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    min-height: 100vh;
                    margin: 0;
                    font-weight: 400;
                }
                .container {
                    max-width: 600px;
                    margin: auto;
                    background-color: var(--surface-color);
                    padding: 40px;
                    border-radius: var(--content-border-radius);
                    box-shadow: 0 10px 40px var(--shadow-color);
                    text-align: center;
                    border: 1px solid var(--border-color);
                }
                h1 { 
                    color: var(--primary-text-color); 
                    margin-bottom: 20px; 
                    font-weight: 700;
                }
                p { font-size: 1.1rem; line-height: 1.6; color: var(--secondary-text-color); font-weight: 400; }
                .balance {
                    font-size: 1.2rem;
                    color: var(--accent-text-color);
                    background-color: var(--accent-color);
                    padding: 8px 15px;
                    border-radius: 10px;
                    display: inline-block;
                    margin: 10px 0 20px;
                    box-shadow: 0 0 15px var(--accent-glow);
                    font-weight: 700;
                }
                .button {
                    display: inline-block;
                    padding: 12px 25px;
                    background-color: transparent;
                    color: var(--accent-color);
                    border: 1px solid var(--accent-color);
                    border-radius: 12px;
                    text-decoration: none;
                    margin-top: 25px;
                    font-weight: 700;
                    font-size: 1.1rem;
                    transition: all 0.3s ease;
                }
                .button:hover {
                    background-color: var(--accent-color);
                    color: var(--accent-text-color);
                    transform: scale(1.05);
                    box-shadow: 0 0 20px var(--accent-glow);
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Purchase Tokens</h1>
                <p>Hello, {{ current_user.email or current_user.username }}!</p>
                <p>Your current balance is: <strong class="balance">{{ current_user.token_balance }} tokens</strong>.</p>
                <p>Payment system integration coming soon.</p>
                <a href="{{ url_for('index') }}" class="button">Back to Main Page</a>
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
