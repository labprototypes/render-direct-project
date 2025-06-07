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
        /* --- ОБЩИЕ НАСТРОЙКИ И ПЕРЕМЕННЫЕ --- */
        @font-face {
            font-family: 'ChangerFont';
            src: url("{{ url_for('static', filename='fonts/FONT_TEXT.woff2') }}") format('woff2');
            font-weight: normal;
            font-style: normal;
        }

        :root {
            --accent-color: #D9F47A;
            /* ПРАВКА: Текст теперь светлый для темного фона */
            --text-color-primary: #FFFFFF;
            --text-color-secondary: rgba(255, 255, 255, 0.7);
            
            --blur-intensity: 25px;
            --base-padding: 20px;
            --tile-border-radius: 28px;
            --button-border-radius: 20px;
            
            /* ПРАВКА: Тени адаптированы для темного фона */
            --shadow-light: rgba(255, 255, 255, 0.08);
            --shadow-dark: rgba(0, 0, 0, 0.5);
            
            --neumorphic-shadow-out: 
                -5px -5px 10px var(--shadow-light), 
                5px 5px 10px var(--shadow-dark);
            --neumorphic-shadow-in: 
                inset -5px -5px 10px var(--shadow-light), 
                inset 5px 5px 10px var(--shadow-dark);
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: 'ChangerFont', sans-serif;
            color: var(--text-color-primary);
            background-color: #1c1c1c; /* Темный фон для контраста */
            background-size: cover;
            background-position: center center;
            background-repeat: no-repeat;
            background-attachment: fixed;
            display: flex;
            flex-direction: column;
            min-height: 100vh;
            overflow: hidden;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }

        /* --- ФОН И ОСНОВНЫЕ КОНТЕЙНЕРЫ --- */
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
            width: 100%; max-width: 1200px; margin: 0 auto;
            padding: var(--base-padding);
            padding-top: 100px; /* Отступ для шапки */
            display: flex; flex-direction: row; align-items: flex-start;
            justify-content: flex-start; gap: var(--base-padding); height: 100vh;
        }

        /* --- ШАПКА (HEADER) --- */
        .page-header-container {
            position: fixed; top: 0; left: 0; right: 0; width: 100%;
            z-index: 105; padding: var(--base-padding);
            display: flex; justify-content: center;
        }
        .page-header-inner {
            width: 100%; max-width: 1200px;
            display: flex; justify-content: space-between; align-items: center;
        }
        .app-logo-link .logo { height: 35px; }
        .top-right-nav { display: flex; align-items: center; gap: 10px; }

        /* --- СТИЛЬ "СТЕКЛОМОРФИЗМ" --- */
        .glass-ui-element {
            /* ПРАВКА: Подложка теперь темная */
            background: rgba(51, 51, 51, 0.45);
            backdrop-filter: blur(var(--blur-intensity));
            -webkit-backdrop-filter: blur(var(--blur-intensity));
            border: 1px solid rgba(255, 255, 255, 0.1);
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
            transition: background 0.3s ease, box-shadow 0.3s ease;
        }

        /* --- ОСНОВНОЙ КОНТЕНТ И СЕТКА ПЛИТОК --- */
        .content-wrapper {
            width: 100%; max-width: 420px; height: fit-content;
            border-radius: var(--tile-border-radius);
            padding: var(--base-padding);
        }

        #edit-view, #upscale-view {
            width: 100%; display: grid;
            gap: var(--base-padding); grid-template-columns: 1fr;
        }

        .ui-tile {
            border-radius: var(--button-border-radius); padding: 15px;
            display: flex; flex-direction: column; gap: 15px;
            background: rgba(0, 0, 0, 0.2);
            border: 1px solid rgba(255, 255, 255, 0.05);
            box-shadow: var(--neumorphic-shadow-out);
        }

        /* --- КНОПКИ И ИНТЕРАКТИВНЫЕ ЭЛЕМЕНТЫ --- */
        .button-group { display: flex; gap: 10px; width: 100%; }
        
        .neumorphic-btn {
            flex-grow: 1; padding: 12px; border-radius: 16px;
            border: none; cursor: pointer;
            font-family: 'ChangerFont', sans-serif; font-size: 0.9rem;
            text-align: center; color: var(--text-color-secondary);
            transition: all 0.2s ease-in-out;
            background: linear-gradient(145deg, #3a3a3a, #2c2c2c);
            box-shadow: var(--neumorphic-shadow-out);
        }
        .neumorphic-btn:hover {
            color: var(--text-color-primary);
            /* ПРАВКА: Свечение при наведении */
            box-shadow: var(--neumorphic-shadow-out), 0 0 15px 2px rgba(217, 244, 122, 0.3);
        }
        .neumorphic-btn.active {
            color: #1c1c1c; /* Темный текст на светлом активном фоне */
            box-shadow: var(--neumorphic-shadow-in);
            background: var(--accent-color);
        }

        /* --- ЗАГРУЗКА ИЗОБРАЖЕНИЙ --- */
        .image-drop-area {
            width: 100%; aspect-ratio: 16 / 10; cursor: pointer;
            position: relative; overflow: hidden;
            display: flex; flex-direction: column; justify-content: center; align-items: center;
            border-radius: 16px;
            background: linear-gradient(145deg, #2a2a2a, #333333);
            box-shadow: var(--neumorphic-shadow-in);
            transition: box-shadow 0.3s ease;
        }
        .image-drop-area.dragover {
            box-shadow: var(--neumorphic-shadow-in), 0 0 0 2px var(--accent-color);
        }
        /* ПРАВКА: Текстовый плейсхолдер вместо картинки */
        .drop-placeholder-text {
            font-size: 1rem;
            color: var(--text-color-secondary);
        }
        .image-drop-area .image-preview-img {
            display: none; width: 100%; height: 100%; object-fit: cover;
            border-radius: inherit;
        }

        /* --- ПОЛЕ ВВОДА ПРОМПТА --- */
        .input-area {
            display: flex; align-items: center; width: 100%;
            border-radius: 16px; padding: 4px;
            background: linear-gradient(145deg, #2a2a2a, #333333);
            box-shadow: var(--neumorphic-shadow-in);
        }
        #prompt {
            flex-grow: 1; border: none; padding: 10px 12px;
            font-size: 0.9rem; background-color: transparent; outline: none;
            color: var(--text-color-primary);
            font-family: 'ChangerFont', sans-serif;
        }
        #prompt::placeholder { color: var(--text-color-secondary); }

        /* ПРАВКА: Кнопка генерации */
        .submit-button {
            width: 100%;
            padding: 16px;
            font-size: 1.1rem;
            color: #1c1c1c;
            background: var(--accent-color);
            border-radius: 16px;
            border: none;
            cursor: pointer;
            font-family: 'ChangerFont', sans-serif;
            transition: all 0.2s ease-in-out;
            box-shadow: 0 4px 15px rgba(217, 244, 122, 0.2);
        }
        .submit-button:hover {
            box-shadow: 0 6px 20px rgba(217, 244, 122, 0.4);
            transform: translateY(-2px);
        }

        /* ПРАВКА: Слайдеры */
        .slider-tile label {
            display: flex;
            justify-content: space-between;
            width: 100%;
            color: var(--text-color-secondary);
        }
        input[type="range"] {
            -webkit-appearance: none; appearance: none;
            width: 100%; height: 8px;
            background: rgba(0, 0, 0, 0.4);
            border-radius: 5px; outline: none;
            transition: opacity 0.2s;
        }
        input[type="range"]::-webkit-slider-thumb {
            -webkit-appearance: none; appearance: none;
            width: 22px; height: 22px;
            border-radius: 50%;
            background: var(--accent-color);
            cursor: pointer;
            border: 4px solid #333;
            box-shadow: 0 0 5px rgba(0,0,0,0.5);
        }
        input[type="range"]::-moz-range-thumb {
            width: 22px; height: 22px;
            border-radius: 50%;
            background: var(--accent-color);
            cursor: pointer;
            border: 4px solid #333;
            box-shadow: 0 0 5px rgba(0,0,0,0.5);
        }

        /* --- ПРАВАЯ ПАНЕЛЬ РЕЗУЛЬТАТА --- */
        #result-area-right {
            flex: 1; height: 100%;
            display: none; justify-content: center; align-items: center;
        }
        .result-image-wrapper {
             position: relative; width: 100%; height: 100%;
             display: flex; justify-content: center; align-items: center;
        }
        #result-image {
            max-width: 100%; max-height: 100%;
            object-fit: contain; border-radius: var(--tile-border-radius);
            box-shadow: 0 16px 40px rgba(0,0,0,0.4);
        }

        /* --- ПРОЧЕЕ --- */
        .loader-container { display: flex; justify-content: center; align-items: center; }
        .pulsating-dot {
            width: 80px; height: 80px; background-color: var(--accent-color);
            border-radius: 50%; animation: pulse 1.5s infinite ease-in-out;
        }
        @keyframes pulse {
            0%, 100% { transform: scale(0.8); opacity: 0.7; }
            50% { transform: scale(1.2); opacity: 1; }
        }
        .error-message {
            position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
            padding: 12px 20px; border-radius: 12px;
            background-color: #ff3b30; color: white; z-index: 110;
        }
        
        /* --- АДАПТИВНОСТЬ --- */
        @media (max-width: 768px) {
            .app-container {
                flex-direction: column; height: auto; padding-top: 80px;
                padding-left: 10px; padding-right: 10px;
            }
            .content-wrapper { max-width: 100%; }
            #result-area-right { width: 100%; height: 50vh; flex: none; }
            .page-header-container { padding: 10px; }
        }
    </style>
</head>
<body>
    <div class="app-container-wrapper" id="app-bg-wrapper"></div>
    <header class="page-header-container">
        </header>
    
    <div class="app-container">
        <div class="content-wrapper glass-ui-element">
            
            <div class="mode-selector glass-ui-element" style="width: 100%; margin-bottom: 20px; padding: 6px; border-radius: 20px;">
                <div class="button-group">
                    <button type="button" class="neumorphic-btn active" data-mode="edit">Edit</button>
                    <button type="button" class="neumorphic-btn" data-mode="upscale">Upscale</button>
                </div>
            </div>

            <div id="edit-view">
                 <div class="ui-tile">
                    <div class="button-group">
                        <button type="button" class="neumorphic-btn active" data-edit-mode="edit">Edit</button>
                        <button type="button" class="neumorphic-btn" data-edit-mode="merge">Merge</button>
                        <button type="button" class="neumorphic-btn" data-edit-mode="autofix">Auto fix</button>
                    </div>
                 </div>

                <div class="ui-tile">
                    <div class="image-inputs-container">
                        <label for="image-file-edit-1" id="image-drop-area-edit-1" class="image-drop-area">
                            <span class="drop-placeholder-text">Drop The Image Here</span>
                            <img id="image-preview-edit-1" src="#" alt="Preview" class="image-preview-img">
                        </label>
                        <label for="image-file-edit-2" id="image-drop-area-edit-2" class="image-drop-area" style="display: none;">
                            <span class="drop-placeholder-text">Drop The Image Here</span>
                            <img id="image-preview-edit-2" src="#" alt="Preview" class="image-preview-img">
                        </label>
                    </div>
                </div>

                <input type="file" id="image-file-edit-1" name="image1" accept="image/*" style="display: none;">
                <input type="file" id="image-file-edit-2" name="image2" accept="image/*" style="display: none;">
                
                <div id="edit-controls-container" class="ui-tile">
                    <form id="edit-form" class="input-area">
                         <input type="text" id="prompt" name="prompt" placeholder="Type what you want to change...">
                    </form>
                    <div class="button-group template-selector">
                        <button type="button" class="neumorphic-btn template-btn" data-prompt="hyperrealistic photo">Create</button>
                        <button type="button" class="neumorphic-btn template-btn" data-prompt="dramatic lighting">Relight</button>
                        <button type="button" class="neumorphic-btn template-btn" data-prompt="remove object">Remove</button>
                    </div>
                </div>

                <div class="submit-action-group">
                    <button id="submit-button-edit" class="submit-button">Generate</button>
                </div>
            </div>

            <div id="upscale-view" style="display: none;">
                 <div class="ui-tile">
                    <div class="button-group">
                        <button type="button" class="neumorphic-btn resolution-btn active" data-value="x2">x2</button>
                        <button type="button" class="neumorphic-btn resolution-btn" data-value="x4">x4</button>
                        <button type="button" class="neumorphic-btn resolution-btn" data-value="x8">x8</button>
                    </div>
                </div>

                 <div class="ui-tile slider-tile">
                    <label for="creativity-slider">
                        <span>Creativity</span>
                        <span id="creativity-value">70</span>
                    </label>
                    <input type="range" id="creativity-slider" min="0" max="100" value="70">
                </div>

                <div class="ui-tile slider-tile">
                    <label for="resemblance-slider">
                        <span>Resemblance</span>
                        <span id="resemblance-value">80</span>
                    </label>
                    <input type="range" id="resemblance-slider" min="0" max="100" value="80">
                </div>

                <div class="ui-tile slider-tile">
                    <label for="hdr-slider">
                        <span>HDR</span>
                        <span id="hdr-value">50</span>
                    </label>
                    <input type="range" id="hdr-slider" min="0" max="100" value="50">
                </div>

                <div class="ui-tile">
                    <label for="image-file-upscale" class="image-drop-area">
                        <span class="drop-placeholder-text">Drop The Image Here</span>
                        <img id="image-preview-upscale" src="#" alt="Preview" class="image-preview-img">
                    </label>
                </div>
                <input type="file" id="image-file-upscale" name="image" accept="image/*" style="display: none;">

                <div class="submit-action-group">
                    <button id="submit-button-upscale" class="submit-button">Generate</button>
                </div>
            </div>
        </div>
        
        <div id="result-area-right">
             </div>
    </div>

    <div id="error-box" class="error-message" style="display:none;"></div>

    <script>
    document.addEventListener('DOMContentLoaded', () => {

        const appBgWrapper = document.getElementById('app-bg-wrapper');
        const mainContentWrapper = document.querySelector('.content-wrapper');
        const resultAreaRight = document.getElementById('result-area-right');
        const loader = resultAreaRight.querySelector('.loader-container');
        const resultImageWrapper = resultAreaRight.querySelector('.result-image-wrapper');
        const resultImage = document.getElementById('result-image');
        const downloadLink = document.getElementById('download-action');
        
        const editView = document.getElementById('edit-view');
        const upscaleView = document.getElementById('upscale-view');

        const imageFileInputEdit1 = document.getElementById('image-file-edit-1');
        const imageFileInputEdit2 = document.getElementById('image-file-edit-2');
        const upscaleImageInput = document.getElementById('image-file-upscale');
        const promptInput = document.getElementById('prompt');

        function showView(viewName) {
            const isResultOrLoading = viewName === 'loading' || viewName === 'result';
            mainContentWrapper.style.opacity = isResultOrLoading ? '0.6' : '1';
            mainContentWrapper.style.pointerEvents = isResultOrLoading ? 'none' : 'auto';
            appBgWrapper.classList.toggle('bg-blur', isResultOrLoading);
            if (isResultOrLoading) {
                resultAreaRight.style.display = 'flex';
                loader.style.display = viewName === 'loading' ? 'flex' : 'none';
                resultImageWrapper.style.display = viewName === 'result' ? 'flex' : 'none';
            } else {
                resultAreaRight.style.display = 'none';
                resetImagePreviews();
                promptInput.value = '';
            }
        }

        document.querySelectorAll('.mode-selector .neumorphic-btn').forEach(button => {
            button.addEventListener('click', () => {
                document.querySelectorAll('.mode-selector .neumorphic-btn').forEach(btn => btn.classList.remove('active'));
                button.classList.add('active');
                const mode = button.dataset.mode;
                editView.style.display = mode === 'edit' ? 'grid' : 'none';
                upscaleView.style.display = mode === 'upscale' ? 'grid' : 'none';
                showView('main');
            });
        });

        const editModeButtons = document.querySelectorAll('#edit-view .ui-tile .neumorphic-btn');
        const imageDropArea2 = document.getElementById('image-drop-area-edit-2');
        editModeButtons.forEach(button => {
            button.addEventListener('click', (e) => {
                editModeButtons.forEach(btn => btn.classList.remove('active'));
                e.currentTarget.classList.add('active');
            });
        });

        document.querySelectorAll('.template-btn').forEach(button => {
            button.addEventListener('click', () => {
                promptInput.value = button.dataset.prompt;
                promptInput.focus();
            });
        });

        document.querySelectorAll('#upscale-view .resolution-btn').forEach(button => {
            button.addEventListener('click', (e) => {
                document.querySelectorAll('#upscale-view .resolution-btn').forEach(btn => btn.classList.remove('active'));
                e.currentTarget.classList.add('active');
            });
        });
        
        const setupSlider = (sliderId, valueId) => {
            const slider = document.getElementById(sliderId);
            const valueDisplay = document.getElementById(valueId);
            if(slider) slider.addEventListener('input', e => valueDisplay.textContent = e.target.value);
        };
        setupSlider('creativity-slider', 'creativity-value');
        setupSlider('resemblance-slider', 'resemblance-value');
        setupSlider('hdr-slider', 'hdr-value'); // ПРАВКА: Инициализация нового слайдера

        function handleFileSelect(file, previewElementId) {
            const previewEl = document.getElementById(previewElementId);
            const dropArea = previewEl.parentElement;
            const placeholder = dropArea.querySelector('.drop-placeholder-text');
            const reader = new FileReader();
            reader.onload = e => {
                previewEl.src = e.target.result;
                previewEl.style.display = 'block';
                if (placeholder) placeholder.style.display = 'none';
            }
            reader.readAsDataURL(file);
        }

        function setupDragAndDrop(dropArea, fileInput) {
            if (!dropArea || !fileInput) return;
            const previewImgId = dropArea.querySelector('.image-preview-img').id;
            ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
                dropArea.addEventListener(eventName, e => { e.preventDefault(); e.stopPropagation(); });
            });
            dropArea.addEventListener('dragover', () => dropArea.classList.add('dragover'));
            dropArea.addEventListener('dragleave', () => dropArea.classList.remove('dragover'));
            dropArea.addEventListener('drop', e => {
                dropArea.classList.remove('dragover');
                const file = e.dataTransfer.files[0];
                if (file) {
                    fileInput.files = e.dataTransfer.files;
                    handleFileSelect(file, previewImgId);
                }
            });
            fileInput.addEventListener('change', () => {
                if (fileInput.files[0]) handleFileSelect(fileInput.files[0], previewImgId);
            });
        }
        setupDragAndDrop(document.getElementById('image-drop-area-edit-1'), imageFileInputEdit1);
        setupDragAndDrop(document.getElementById('image-drop-area-edit-2'), imageFileInputEdit2);
        setupDragAndDrop(document.querySelector('#upscale-view .image-drop-area'), upscaleImageInput);

        function resetImagePreviews() {
            document.querySelectorAll('.image-preview-img').forEach(img => {
                img.src = '#'; img.style.display = 'none';
            });
            document.querySelectorAll('.drop-placeholder-text').forEach(p => p.style.display = 'block');
            [imageFileInputEdit1, imageFileInputEdit2, upscaleImageInput].forEach(input => input.value = '');
        }

        const errorBox = document.getElementById('error-box');
        function showError(message) {
            errorBox.textContent = message;
            errorBox.style.display = 'block';
            setTimeout(() => { errorBox.style.display = 'none'; }, 4000);
        }
        
        // ПРАВКА: Добавлен event.preventDefault()
        async function handleImageProcessing(event) {
            event.preventDefault();
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
                    method: 'POST', body: formData
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.error || 'Unknown server error');

                const tempImg = new Image();
                tempImg.onload = () => {
                    resultImage.src = data.output_url;
                    downloadLink.href = data.output_url;
                    if (data.new_token_balance !== undefined) {
                        const tokenDisplay = document.getElementById('token-balance-display');
                        if(tokenDisplay) tokenDisplay.textContent = data.new_token_balance;
                    }
                    showView('result');
                };
                tempImg.onerror = () => {
                    showError("Failed to load generated image.");
                    showView('main');
                };
                tempImg.src = data.output_url;

            } catch (error) {
                showError("An error occurred: " + error.message);
                showView('main');
            }
        }

        // ПРАВКА: Обработчик передает event
        document.getElementById('submit-button-edit').addEventListener('click', handleImageProcessing);
        document.getElementById('submit-button-upscale').addEventListener('click', (e) => { 
            e.preventDefault();
            showError("Upscale logic not implemented yet.");
        });

        document.querySelector('.mode-selector .neumorphic-btn[data-mode="edit"]').click();
        showView('main');
    });
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    # ... остальной код Python без изменений
    return render_template_string(INDEX_HTML)

@app.route('/buy-tokens')
@login_required
def buy_tokens_page():
    # ... остальной код Python без изменений
    return render_template_string("""...""")


def improve_prompt_with_openai(user_prompt):
    # ... остальной код Python без изменений
    return user_prompt

@app.route('/process-image', methods=['POST'])
@login_required
def process_image():
    # ... остальной код Python без изменений
    if current_user.token_balance < 1:
        return jsonify({'error': 'Недостаточно токенов'}), 403

    if 'image' not in request.files or 'prompt' not in request.form:
        return jsonify({'error': 'Отсутствует изображение или промпт'}), 400

    image_file = request.files['image']
    original_prompt_text = request.form['prompt']
    final_prompt_text = improve_prompt_with_openai(original_prompt_text)
    
    # ... остальной код Python без изменений ...

    return jsonify({'output_url': 'some_url', 'new_token_balance': current_user.token_balance})


with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get("PORT", 5001)))
