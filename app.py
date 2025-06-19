# FIX: Explicitly apply gevent monkey-patching at the very top of the file.
from gevent import monkey
monkey.patch_all()

# Standard library imports
import os
import uuid
import time
import io
import base64
import hashlib
import json
import mimetypes
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlparse, urljoin

# Third-party imports
import boto3
import openai
import requests
# import stripe # TODO: Закомментировано до интеграции с российским провайдером
from flask import Flask, request, jsonify, render_template, url_for, redirect, flash, session, g
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature
from authlib.integrations.flask_client import OAuth

# --- Настройки приложения ---
app = Flask(__name__)

# --- НОВАЯ КОНФИГУРАЦИЯ ---
# Загружаем конфигурацию из переменных окружения
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'a-very-secret-key-for-local-dev')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///site.db') # Используем DATABASE_URL от Selectel
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 280,
}
app.static_folder = 'static'

# --- НОВАЯ КОНФИГУРАЦИЯ ДЛЯ ПОЧТЫ (Яндекс) ---
app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.yandex.ru')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 465))
app.config['MAIL_USE_SSL'] = os.environ.get('MAIL_USE_SSL', 'true').lower() in ['true', '1', 't']
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', 'false').lower() in ['true', '1', 't']
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME') # noreply@pifly.io
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD') # Пароль приложения из Яндекса
app.config['MAIL_DEFAULT_SENDER'] = ('Pifly.io', os.environ.get('MAIL_USERNAME'))
app.config['YANDEX_CLIENT_ID'] = os.environ.get('YANDEX_CLIENT_ID')
app.config['YANDEX_CLIENT_SECRET'] = os.environ.get('YANDEX_CLIENT_SECRET')
app.config['TINKOFF_TERMINAL_KEY'] = os.environ.get('TINKOFF_TERMINAL_KEY')
app.config['TINKOFF_SECRET_KEY'] = os.environ.get('TINKOFF_SECRET_KEY')

# --- Конфигурация внешних сервисов (без изменений) ---
AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
AWS_S3_BUCKET_NAME = os.environ.get('AWS_S3_BUCKET_NAME') # Бакет из Selectel
AWS_S3_REGION = os.environ.get('AWS_S3_REGION')
REPLICATE_API_TOKEN = os.environ.get('REPLICATE_API_TOKEN')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

# --- Инициализация расширений ---
db = SQLAlchemy(app)
mail = Mail(app) # Инициализация Flask-Mail
login_manager = LoginManager()
oauth = OAuth(app)
login_manager.init_app(app)
login_manager.login_view = 'login' # Указываем view для входа
login_manager.login_message_category = "info"

# Сериализатор для токенов подтверждения
ts = URLSafeTimedSerializer(app.config["SECRET_KEY"])

if OPENAI_API_KEY:
    openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
else:
    openai_client = None
    print("!!! ВНИМАНИЕ: OPENAI_API_KEY не найден. Улучшение промптов и Autofix не будут работать.")

# --- ИЗМЕНЕННЫЕ МОДЕЛИ БАЗЫ ДАННЫХ ---
class User(db.Model, UserMixin):
    # ID теперь генерируется как UUID, а не приходит из Firebase
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    username = db.Column(db.String(255), nullable=True) # Можно использовать email или часть email как username
    password_hash = db.Column(db.String(255), nullable=False) # Новое поле для хэша пароля

    # Новые поля для подтверждения email
    email_confirmed = db.Column(db.Boolean, nullable=False, default=False)
    email_confirmed_at = db.Column(db.DateTime, nullable=True)
    yandex_id = db.Column(db.String(255), unique=True, nullable=True)
    token_balance = db.Column(db.Integer, default=100, nullable=False)
    marketing_consent = db.Column(db.Boolean, nullable=False, default=True)

    # Поля для подписок и биллинга (оставлены для будущей интеграции)
    subscription_status = db.Column(db.String(50), default='free', nullable=False)
    stripe_customer_id = db.Column(db.String(255), nullable=True, unique=True) # Будет заменен на ID российского провайдера
    stripe_subscription_id = db.Column(db.String(255), nullable=True, unique=True) # Будет заменен
    current_plan = db.Column(db.String(50), nullable=True, default='free')
    trial_used = db.Column(db.Boolean, default=False, nullable=False)
    subscription_ends_at = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        # Пользователь активен только если его email подтвержден
        return self.email_confirmed

# Модель Prediction остается без изменений
class Prediction(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    replicate_id = db.Column(db.String(255), unique=True, nullable=True, index=True)
    status = db.Column(db.String(50), default='pending', nullable=False)
    output_url = db.Column(db.String(2048), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    token_cost = db.Column(db.Integer, nullable=False, default=1)
    user = db.relationship('User', backref=db.backref('predictions', lazy=True, cascade="all, delete-orphan"))

oauth.register(
    name='yandex',
    client_id=app.config['YANDEX_CLIENT_ID'],
    client_secret=app.config['YANDEX_CLIENT_SECRET'],
    access_token_url='https://oauth.yandex.ru/token',
    authorize_url='https://oauth.yandex.ru/authorize',
    api_base_url='https://login.yandex.ru/',
    client_kwargs=None,
    userinfo_endpoint='info?format=json',
)

# --- Загрузчик пользователя и декораторы ---
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(user_id)

# Декоратор для проверки, подтвержден ли email
def check_confirmed(func):
    @wraps(func)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.email_confirmed:
            flash('Пожалуйста, подтвердите свой адрес электронной почты, чтобы получить доступ.', 'warning')
            return redirect(url_for('unconfirmed'))
        return func(*args, **kwargs)
    return decorated_function

# --- НОВЫЕ ФУНКЦИИ-ПОМОЩНИКИ ДЛЯ ОТПРАВКИ EMAIL ---
def send_email(to, subject, template):
    try:
        msg = Message(
            subject,
            recipients=[to],
            html=template,
            sender=app.config['MAIL_DEFAULT_SENDER']
        )
        mail.send(msg)
    except Exception as e:
        print(f"!!! Ошибка отправки email: {e}")
        # В реальном приложении здесь может быть более сложная логика (например, повторная отправка)

# --- НОВЫЕ МАРШРУТЫ АУТЕНТИФИКАЦИИ ---

# Эта функция проверяет, что URL для редиректа безопасен
def is_safe_url(target):
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

@app.route('/login/yandex')
def yandex_login():
    redirect_uri = url_for('yandex_callback', _external=True)
    return oauth.yandex.authorize_redirect(redirect_uri)

@app.route('/login/yandex/callback')
def yandex_callback():
    try:
        token = oauth.yandex.authorize_access_token()
        user_info_resp = oauth.yandex.get('info?format=json')
        user_info_resp.raise_for_status()
        user_info = user_info_resp.json()
    except Exception as e:
        flash(f"Произошла ошибка при аутентификации через Яндекс: {e}", "danger")
        return redirect(url_for('login'))

    yandex_id = user_info.get('id')
    email = user_info.get('default_email')
    
    user = User.query.filter_by(yandex_id=yandex_id).first()
    if not user:
        user = User.query.filter_by(email=email).first()

    if user:
        if not user.yandex_id:
            user.yandex_id = yandex_id
        if not user.email_confirmed:
            user.email_confirmed = True
            user.email_confirmed_at = datetime.utcnow()
        db.session.commit()
    else:
        user = User(
            id=str(uuid.uuid4()),
            yandex_id=yandex_id,
            email=email,
            username=user_info.get('login'),
            password_hash='social_login',
            email_confirmed=True,
            email_confirmed_at=datetime.utcnow()
        )
        db.session.add(user)
        db.session.commit()

    login_user(user)
    flash("Вы успешно вошли через Яндекс!", "success")
    return redirect(url_for('index'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        remember = True if request.form.get('remember') else False

        user = User.query.filter_by(email=email).first()

        if not user or not user.check_password(password):
            flash('Неверный email или пароль.', 'danger')
            return redirect(url_for('login'))

        login_user(user, remember=remember)

        # Безопасный редирект на предыдущую страницу
        next_page = request.args.get('next')
        if not is_safe_url(next_page):
            return redirect(url_for('index'))

        return redirect(next_page or url_for('index'))

    return render_template('login.html') # Будет создан на следующем шаге

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        # Новая проверка на согласие с условиями
        terms_accepted = request.form.get('terms_check')
        if not terms_accepted:
            flash('Вы должны согласиться с Условиями обслуживания и Политикой конфиденциальности.', 'danger')
            return redirect(url_for('register'))

        if User.query.filter_by(email=email).first():
            flash('Этот email уже зарегистрирован.', 'warning')
            return redirect(url_for('register'))
        
        if not password or len(password) < 8:
            flash('Пароль должен содержать не менее 8 символов.', 'danger')
            return redirect(url_for('register'))

        # Новая обработка согласия на маркетинг
        marketing_consent = True if request.form.get('marketing_check') else False

        new_user = User(
            email=email,
            username=email.split('@')[0],
            marketing_consent=marketing_consent
        )
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        # Отправка письма для подтверждения (без изменений)
        subject = "Подтверждение регистрации на Pifly.io"
        token = ts.dumps(new_user.email, salt='email-confirm-salt')
        confirm_url = url_for('confirm_email', token=token, _external=True)
        html = render_template('emails/confirmation_email.html', confirm_url=confirm_url)
        
        send_email(new_user.email, subject, html)

        login_user(new_user)

        flash('Пожалуйста, подтвердите свой email. Письмо было отправлено на указанный вами электронный адрес', 'info')
        return redirect(url_for('unconfirmed'))

    return render_template('register.html')

@app.route('/confirm/<token>')
def confirm_email(token):
    try:
        email = ts.loads(token, salt="email-confirm-salt", max_age=86400) # Токен действителен 24 часа
    except SignatureExpired:
        flash('Ссылка для подтверждения истекла. Пожалуйста, запросите новую.', 'danger')
        return redirect(url_for('resend_confirmation'))
    except BadTimeSignature:
        flash('Неверная ссылка для подтверждения.', 'danger')
        return redirect(url_for('login'))

    user = User.query.filter_by(email=email).first_or_404()

    if user.email_confirmed:
        flash('Аккаунт уже подтвержден. Пожалуйста, войдите.', 'info')
    else:
        user.email_confirmed = True
        user.email_confirmed_at = datetime.utcnow()
        db.session.add(user)
        db.session.commit()
        flash('Ваш email успешно подтвержден! Теперь вы можете зайти в свой личный кабинет.', 'success')

    return redirect(url_for('index'))

@app.route('/unconfirmed')
@login_required
def unconfirmed():
    if current_user.email_confirmed:
        return redirect(url_for('index'))
    return render_template('unconfirmed.html') # Будет создан на следующем шаге

@app.route('/resend_confirmation')
@login_required
def resend_confirmation():
    if current_user.email_confirmed:
        return redirect(url_for('index'))

    subject = "Подтверждение регистрации на Pifly.io"
    token = ts.dumps(current_user.email, salt='email-confirm-salt')
    confirm_url = url_for('confirm_email', token=token, _external=True)
    html = render_template('emails/confirmation_email.html', confirm_url=confirm_url)
    send_email(current_user.email, subject, html)

    flash('Новое письмо с подтверждением отправлено.', 'success')
    return redirect(url_for('unconfirmed'))


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Вы вышли из системы.', 'success')
    return redirect(url_for('login'))


# --- ГЛАВНЫЙ МАРШРУТ И ОСНОВНОЕ ПРИЛОЖЕНИЕ ---
@app.route('/')
@login_required
@check_confirmed # Пользователь должен быть залогинен и подтвердить почту
def index():
    return render_template('index.html')

# --- СТАРЫЕ МАРШРУТЫ FIREBASE И STRIPE УДАЛЕНЫ ИЛИ ЗАКОММЕНТИРОВАНЫ ---
# @app.route('/session-login', methods=['POST']) -> Удален
# @app.route('/create-checkout-session', ...) -> Закомментирован
# @app.route('/stripe-webhook', ...) -> Закомментирован

# --- Маршруты для юридических и вспомогательных страниц (без изменений) ---
@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/marketing-policy')
def marketing_policy():
    return render_template('marketing.html')

# --- Функции-помощники и маршруты для работы с изображениями (без существенных изменений) ---
# Функция для загрузки в S3 хранилище Selectel
# ИСПРАВЛЕННАЯ ВЕРСИЯ ФУНКЦИИ
def upload_file_to_s3(file_to_upload):
    """
    Загружает исходный файл в Amazon S3 и возвращает публичную ссылку.
    """
    bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
    region = os.environ.get('AWS_S3_REGION')
    s3_client = boto3.client(
        's3',
        region_name=region,
        aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY')
    )
    _, f_ext = os.path.splitext(file_to_upload.filename)
    object_name = f"uploads/{uuid.uuid4()}{f_ext}"
    
    file_to_upload.stream.seek(0)
    
    s3_client.upload_fileobj(
        file_to_upload.stream,
        bucket_name,
        object_name,
        ExtraArgs={'ContentType': file_to_upload.content_type, 'ACL': 'public-read'}
    )

    # Формируем стандартную публичную ссылку для Amazon S3
    public_url = f"https://{bucket_name}.s3.{region}.amazonaws.com/{object_name}"
    
    print(f"!!! Изображение загружено на Amazon S3: {public_url}")
    return public_url


def _reupload_and_save_result(prediction, temp_url):
    """
    Скачивает результат с Replicate, загружает в Amazon S3 и обновляет запись в БД.
    """
    try:
        print(f"Начало перезаливки для Prediction ID: {prediction.id} с URL: {temp_url}")
        
        image_response = requests.get(temp_url, stream=True)
        image_response.raise_for_status()
        image_data = io.BytesIO(image_response.content)
        
        bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
        region = os.environ.get('AWS_S3_REGION')
        s3_client = boto3.client(
            's3',
            region_name=region,
            aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY')
        )
        
        file_extension = os.path.splitext(temp_url.split('?')[0])[-1] or '.png'
        object_name = f"generations/{prediction.user_id}/{prediction.id}{file_extension}"
        
        s3_client.upload_fileobj(
            image_data,
            bucket_name,
            object_name,
            ExtraArgs={'ContentType': image_response.headers.get('Content-Type', 'image/png'), 'ACL': 'public-read'}
        )
        
        permanent_s3_url = f"https://{bucket_name}.s3.{region}.amazonaws.com/{object_name}"
        
        prediction.output_url = permanent_s3_url
        prediction.status = 'completed'
        db.session.commit()
        print(f"!!! Изображение для Prediction {prediction.id} успешно сохранено в Amazon S3: {permanent_s3_url}")

    except Exception as e:
        app.logger.error(f"!!! Ошибка при перезаливке изображения '{prediction.id}': {e}", exc_info=True)
        prediction.status = 'failed'
        user = User.query.get(prediction.user_id)
        if user:
            user.token_balance += prediction.token_cost
        db.session.commit()


# ===============================================================
# ДОБАВЬТЕ ЭТОТ НОВЫЙ МАРШРУТ В КОНЕЦ ВАШЕГО ФАЙЛА, ПЕРЕД `if __name__ == '__main__':`
# ===============================================================

@app.route('/media/<path:object_name>')
@login_required
def serve_media_file(object_name):
    """
    Прокси-маршрут для безопасной отдачи файлов из приватного S3-хранилища,
    с детальным логгированием для отладки.
    """
    # Записываем в лог сам факт вызова маршрута
    app.logger.info(f"Запрос на проксирование файла: {object_name}")
    try:
        if not object_name.startswith('uploads/') and not object_name.startswith('generations/'):
            app.logger.warning(f"Попытка доступа к запрещенному пути: {object_name}")
            return "Not Found", 404

        if object_name.startswith('generations/'):
            parts = object_name.split('/')
            if len(parts) > 2 and parts[1] != current_user.id:
                app.logger.warning(f"Пользователь {current_user.id} попытался получить доступ к чужому файлу: {object_name}")
                return "Access Denied", 403

        app.logger.info(f"Создание S3 клиента для объекта: {object_name}")
        s3_client = boto3.client(
            's3',
            endpoint_url=os.environ.get('AWS_S3_ENDPOINT_URL'),
            region_name=os.environ.get('AWS_S3_REGION'),
            aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY')
        )

        bucket_name = os.environ.get('AWS_S3_BUCKET_NAME')
        app.logger.info(f"Загрузка объекта '{object_name}' из бакета '{bucket_name}'")
        
        s3_response = s3_client.get_object(Bucket=bucket_name, Key=object_name)

        app.logger.info(f"Отдача файла '{object_name}' пользователю.")
        return Response(
            s3_response['Body'].read(),
            mimetype=s3_response['ContentType'],
            headers={"Content-Disposition": f"inline; filename={os.path.basename(object_name)}"}
        )

    except Exception as e:
        # Эта часть запишет в лог любую возникшую ошибку с полной трассировкой
        app.logger.error(f"!!! Ошибка при отдаче файла через прокси '{object_name}': {e}", exc_info=True)
        return "Not Found", 404

@app.route('/process-image', methods=['POST'])
@login_required
@check_confirmed
def process_image():
    mode = request.form.get('mode')
    token_cost = 0
    if mode == 'edit':
        token_cost = 65
    elif mode == 'upscale':
        scale_factor = int(request.form.get('scale_factor', '2'))
        if scale_factor <= 2: token_cost = 17
        elif scale_factor <= 4: token_cost = 65
        else: token_cost = 150
    
    if current_user.token_balance < token_cost:
        return jsonify({'error': 'Недостаточно токенов'}), 403
    if 'image' not in request.files:
        return jsonify({'error': 'Изображение отсутствует'}), 400
    
    uploaded_file = request.files['image']
    image_bytes = uploaded_file.read()
    uploaded_file.seek(0) # Возвращаем указатель для других операций

    try:
        # --- ОБЩАЯ ЛОГИКА ДЛЯ BASE64, ТАК КАК ОНА НУЖНА ОБОИМ РЕЖИМАМ ---
        base64_image_str = base64.b64encode(image_bytes).decode('utf-8')
        image_mime_type = uploaded_file.content_type or mimetypes.guess_type(uploaded_file.filename)[0] or 'image/png'
        base64_data_url = f"data:{image_mime_type};base64,{base64_image_str}"

        replicate_input = {}
        model_version_id = ""

        if mode == 'edit':
            prompt = request.form.get('prompt', '')
            edit_mode = request.form.get('edit_mode')
            model_version_id = "black-forest-labs/flux-kontext-max:0b9c317b23e79a9a0d8b9602ff4d04030d433055927fb7c4b91c44234a6818c4"
            
            # Логика получения промпта от OpenAI через прокси
            proxy_url = "https://pifly-proxy.onrender.com/proxy/openai"
            proxy_secret_key = os.environ.get('PROXY_SECRET_KEY')
            headers = {"Authorization": f"Bearer {proxy_secret_key}", "Content-Type": "application/json"}
            
            system_prompt_text = ""
            if edit_mode == 'autofix':
                system_prompt_text = "You are an expert image analyst. You will be given an image with potential visual flaws. Your task is to generate a descriptive prompt in English that describes the ideal, corrected version of the image. Focus on technical correction. For example: 'A photorealistic hand with five fingers, correct anatomy, soft lighting'. For artifacts, describe the clean area: 'a clear blue sky'. Output only the prompt."
                messages = [{"role": "system", "content": system_prompt_text}, {"role": "user", "content": [{"type": "image_url", "image_url": {"url": base64_data_url}}]}]
            else:
                system_prompt_text = "You are a helpful assistant. A user will provide a request in any language to modify an image. Your only task is to accurately translate this request into a concise English command. Do not add any conversational fluff, explanations, or extra descriptions. Output only the direct translation."
                messages = [{"role": "system", "content": system_prompt_text}, {"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": base64_data_url}}]}]

            openai_payload = {"model": "gpt-4o", "messages": messages, "max_tokens": 150}
            proxy_response = requests.post(proxy_url, json=openai_payload, headers=headers, timeout=30)
            proxy_response.raise_for_status()
            openai_response_data = proxy_response.json()
            final_prompt = openai_response_data['choices'][0]['message']['content'].strip()
            
            replicate_input = {"input_image": base64_data_url, "prompt": final_prompt}

        elif mode == 'upscale':
            model_version_id = "dfad41707589d68ecdccd1dfa600d55a208f9310748e44bfe35b4a6291453d5e"
            replicate_input = {
                "image": base64_data_url,
                "scale_factor": float(request.form.get('scale_factor', '2')),
                "creativity": round(float(request.form.get('creativity', '30'))/100.0, 4),
                "resemblance": round(float(request.form.get('resemblance', '20'))/100.0*3.0, 4),
                "dynamic": round(float(request.form.get('dynamic', '10'))/100.0*50.0, 4)
            }

        if not model_version_id or not replicate_input:
            raise Exception("Неверный режим или отсутствуют входные данные.")

        current_user.token_balance -= token_cost
        new_prediction = Prediction(user_id=current_user.id, token_cost=token_cost)
        db.session.add(new_prediction)
        db.session.commit()

        headers = {"Authorization": f"Bearer {os.environ.get('REPLICATE_API_TOKEN')}", "Content-Type": "application/json"}
        post_payload = {"version": model_version_id, "input": replicate_input, "webhook": url_for('replicate_webhook', _external=True), "webhook_events_filter": ["completed"]}
        
        start_response = requests.post("https://api.replicate.com/v1/predictions", json=post_payload, headers=headers)
        start_response.raise_for_status()
        prediction_data = start_response.json()

        new_prediction.replicate_id = prediction_data.get('id')
        db.session.commit()
        return jsonify({'prediction_id': new_prediction.id, 'new_token_balance': current_user.token_balance})

    except Exception as e:
        print(f"!!! ОБЩАЯ ОШИБКА в process_image: {e}")
        db.session.rollback()
        return jsonify({'error': f'Произошла внутренняя ошибка сервера: {e}'}), 500



# Замените существующий маршрут @app.route('/get-result/...')
@app.route('/get-result/<string:prediction_id>', methods=['GET'])
@login_required
def get_result(prediction_id):
    prediction = Prediction.query.get(prediction_id)
    if not prediction or prediction.user_id != current_user.id:
        return jsonify({'error': 'Prediction not found or access denied'}), 404

    if prediction.status == 'completed':
        return jsonify({'status': 'completed', 'output_url': prediction.output_url, 'new_token_balance': current_user.token_balance})
    
    if prediction.status == 'failed':
        user = User.query.get(current_user.id)
        return jsonify({'status': 'failed', 'error': 'Generation failed. Your tokens have been refunded.', 'new_token_balance': user.token_balance})

    if prediction.status == 'pending' and prediction.replicate_id:
        try:
            headers = {"Authorization": f"Bearer {REPLICATE_API_TOKEN}", "Content-Type": "application/json"}
            poll_url = f"https://api.replicate.com/v1/predictions/{prediction.replicate_id}"
            get_response = requests.get(poll_url, headers=headers)
            get_response.raise_for_status()
            status_data = get_response.json()

            if status_data.get("status") == "succeeded":
                temp_url = status_data.get('output')
                if temp_url and isinstance(temp_url, list): temp_url = temp_url[0]
                
                # ВЫЗЫВАЕМ ХЕЛПЕР ДЛЯ СОХРАНЕНИЯ
                _reupload_and_save_result(prediction, temp_url)
                
                return jsonify({'status': 'completed', 'output_url': prediction.output_url, 'new_token_balance': current_user.token_balance})

            elif status_data.get("status") == "failed":
                prediction.status = 'failed'
                user = User.query.get(prediction.user_id)
                if user: user.token_balance += prediction.token_cost
                db.session.commit()
                return jsonify({'status': 'failed', 'error': f"Generation failed: {status_data.get('error', 'Unknown error')}. Your tokens have been refunded.", 'new_token_balance': user.token_balance})
        except requests.exceptions.RequestException as e:
            print(f"!!! Ошибка при опросе статуса Replicate для {prediction.id}: {e}")
    
    return jsonify({'status': 'pending'})


# Замените существующий маршрут @app.route('/replicate-webhook', ...)
@app.route('/replicate-webhook', methods=['POST'])
def replicate_webhook():
    data = request.json
    replicate_id = data.get('id')
    status = data.get('status')

    if not replicate_id: return 'Invalid payload, missing ID', 400

    with app.app_context():
        prediction = Prediction.query.filter_by(replicate_id=replicate_id).first()
        if not prediction:
            print(f"!!! Вебхук получен для неизвестного Replicate ID: {replicate_id}")
            return 'Prediction not found', 404

        if status == 'succeeded':
            temp_url = data.get('output')
            if temp_url and isinstance(temp_url, list): temp_url = temp_url[0]
            
            if temp_url:
                # ВЫЗЫВАЕМ ХЕЛПЕР ДЛЯ СОХРАНЕНИЯ
                _reupload_and_save_result(prediction, temp_url)
            else:
                prediction.status = 'failed'
                user = User.query.get(prediction.user_id)
                if user: user.token_balance += prediction.token_cost
                db.session.commit()
        
        elif status == 'failed':
            prediction.status = 'failed'
            user = User.query.get(prediction.user_id)
            if user: user.token_balance += prediction.token_cost
            db.session.commit()
            
    return 'Webhook received', 200
# --- Маршруты для биллинга и архива ---
@app.route('/archive')
@login_required
@check_confirmed
def archive():
    predictions = Prediction.query.filter_by(user_id=current_user.id, status='completed').order_by(Prediction.created_at.desc()).all()
    return render_template('archive.html', predictions=predictions)

@app.route('/billing')
@login_required
@check_confirmed
def billing():
    # TODO: Этот шаблон нужно будет переделать под российскую платежную систему
    flash("Страница биллинга находится в разработке.", "info")
    return render_template('billing.html') # Пока будет использоваться старый шаблон


# --- Final app setup ---
with app.app_context():
    db.create_all()

def _generate_tinkoff_token(data):
    """Генерирует токен подписи для запроса к API Тинькофф."""
    secret_key = app.config['TINKOFF_SECRET_KEY']
    # Отфильтровываем все None значения и добавляем пароль
    filtered_data = {k: v for k, v in data.items() if v is not None}
    filtered_data['Password'] = secret_key
    
    # Сортируем по ключу и конкатенируем значения
    sorted_data = sorted(filtered_data.items())
    values_str = "".join(str(v) for _, v in sorted_data)
    
    # Хэшируем и возвращаем
    return hashlib.sha256(values_str.encode('utf-8')).hexdigest()

@app.route('/create-payment', methods=['POST'])
@login_required
def create_payment():
    """Создает платеж и перенаправляет пользователя на страницу оплаты."""
    # В будущем здесь будет логика выбора плана и определения суммы
    amount_kopecks = 999 * 100  # Например, 999 рублей в копейках
    order_id = str(uuid.uuid4()) # Уникальный ID заказа

    payload = {
        "TerminalKey": app.config['TINKOFF_TERMINAL_KEY'],
        "Amount": amount_kopecks,
        "OrderId": order_id,
        "Description": "Покупка кредитов Pifly.io",
        "DATA": {
            "Email": current_user.email,
            "UserId": current_user.id
        },
        "Receipt": {
            "Email": current_user.email,
            "Taxation": "usn_income",
            "Items": [
                {
                    "Name": "Кредиты Pifly.io",
                    "Price": amount_kopecks,
                    "Quantity": 1.00,
                    "Amount": amount_kopecks,
                    "Tax": "none"
                }
            ]
        }
    }
    
    # Генерируем токен и добавляем его в payload
    payload['Token'] = _generate_tinkoff_token(payload)

    try:
        response = requests.post('https://securepay.tinkoff.ru/v2/Init', json=payload)
        response.raise_for_status()
        result = response.json()

        if result.get("Success"):
            # TODO: Сохранить `order_id` и `result.get('PaymentId')` в нашей БД
            # чтобы связать их с пользователем и проверить при уведомлении.
            return redirect(result.get('PaymentURL'))
        else:
            error_message = result.get('Message', 'Неизвестная ошибка')
            flash(f"Ошибка при создании платежа: {error_message}", "danger")
            return redirect(url_for('billing'))
            
    except Exception as e:
        flash(f"Не удалось связаться с платежным шлюзом: {e}", "danger")
        return redirect(url_for('billing'))


@app.route('/tinkoff-notification', methods=['POST'])
def tinkoff_notification():
    """Обрабатывает уведомления о статусе платежа от Т-Банка."""
    data = request.json
    
    # Проверяем токен, чтобы убедиться, что это запрос от Т-Банка
    received_token = data.pop('Token', None)
    expected_token = _generate_tinkoff_token(data)

    if received_token != expected_token:
        # Если токен не совпадает, игнорируем запрос
        return "error: invalid token", 400

    # Если статус платежа CONFIRMED, начисляем кредиты
    if data.get('Status') == 'CONFIRMED':
        user_id = data.get('DATA', {}).get('UserId')
        if user_id:
            user = User.query.get(user_id)
            if user:
                # TODO: Определять количество кредитов на основе суммы
                user.token_balance += 1500 
                db.session.commit()
                print(f"Пользователю {user_id} успешно начислено 1500 токенов.")

    # Отвечаем банку, что все получили
    return "OK", 200

@app.route('/delete-account', methods=['POST'])
@login_required
def delete_account():
    """Удаляет пользователя и все его данные."""
    try:
        user_id = current_user.id

        # Сначала удаляем связанные записи (генерации), чтобы избежать ошибок
        Prediction.query.filter_by(user_id=user_id).delete()

        # Теперь находим самого пользователя
        user_to_delete = User.query.get(user_id)
        if not user_to_delete:
            flash("Не удалось найти пользователя для удаления.", "error")
            return redirect(url_for('billing'))

        # Выходим из системы ПЕРЕД удалением, чтобы избежать ошибок сессии
        logout_user()

        # Удаляем пользователя из нашей базы данных
        db.session.delete(user_to_delete)
        db.session.commit()

        # TODO: В будущем здесь нужно будет добавить логику удаления пользователя
        # из социальной сети (Яндекс), если он был создан через нее.

        flash('Ваш аккаунт и все связанные данные были успешно удалены.', 'success')
        return redirect(url_for('login'))

    except Exception as e:
        db.session.rollback()
        print(f"!!! Ошибка при удалении аккаунта: {e}")
        flash('Произошла ошибка при удалении вашего аккаунта.', 'error')
        return redirect(url_for('billing'))

if __name__ == '__main__':
    # Для локального запуска, debug=True. На сервере будет False.
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get("PORT", 5001)))
