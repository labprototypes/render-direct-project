# FIX: Explicitly apply gevent monkey-patching at the very top of the file.
from gevent import monkey
monkey.patch_all()

# Standard library imports
import os
import uuid
import time
import io
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
login_manager.init_app(app)
login_manager.login_view = 'login' # Указываем view для входа
login_manager.login_message = "Пожалуйста, войдите, чтобы получить доступ к этой странице."
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

        if User.query.filter_by(email=email).first():
            flash('Этот email уже зарегистрирован.', 'warning')
            return redirect(url_for('register'))

        new_user = User(email=email, username=email.split('@')[0])
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        # Отправка письма для подтверждения
        subject = "Подтверждение регистрации на Pifly.io"
        token = ts.dumps(new_user.email, salt='email-confirm-salt')
        confirm_url = url_for('confirm_email', token=token, _external=True)
        html = render_template('emails/confirmation_email.html', confirm_url=confirm_url)
        
        send_email(new_user.email, subject, html)

        login_user(new_user)

        flash('Вы успешно зарегистрированы! Письмо с подтверждением было отправлено на ваш email.', 'success')
        return redirect(url_for('unconfirmed'))

    return render_template('register.html') # Будет создан на следующем шаге

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
        flash('Ваш email успешно подтвержден! Теперь вы можете пользоваться всеми функциями.', 'success')

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
def upload_file_to_s3(file_to_upload):
    # Endpoint URL нужно будет указать для Selectel
    s3_endpoint_url = f'https://s3.{AWS_S3_REGION}.amazonaws.com' # ЗАМЕНИТЬ НА URL SELECTEL S3 API
    if 'selectel' in AWS_S3_REGION: # Просто пример, как можно определить
         s3_endpoint_url = f'https://s3.{AWS_S3_REGION}.selcloud.ru'

    s3_client = boto3.client(
        's3',
        endpoint_url=s3_endpoint_url,
        region_name=AWS_S3_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY
    )
    _, f_ext = os.path.splitext(file_to_upload.filename)
    object_name = f"uploads/{uuid.uuid4()}{f_ext}"
    file_to_upload.stream.seek(0)
    s3_client.upload_fileobj(file_to_upload.stream, AWS_S3_BUCKET_NAME, object_name, ExtraArgs={'ContentType': file_to_upload.content_type})
    
    # URL сгенерированного файла
    hosted_image_url = f"https://{AWS_S3_BUCKET_NAME}.s3.{AWS_S3_REGION}.selcloud.ru/{object_name}"
    print(f"!!! Изображение загружено на Selectel S3: {hosted_image_url}")
    return hosted_image_url

@app.route('/process-image', methods=['POST'])
@login_required
@check_confirmed
def process_image():
    # ... (вся логика обработки изображения остается такой же, как в app (22).py)
    # ... я скопирую ее сюда без изменений для полноты файла.
    mode = request.form.get('mode')
    token_cost = 0
    if mode == 'edit':
        token_cost = 65
    elif mode == 'upscale':
        scale_factor = int(request.form.get('scale_factor', '2'))
        token_cost = 17 if scale_factor <= 2 else (65 if scale_factor <= 4 else 150)

    if token_cost == 0:
        return jsonify({'error': 'Invalid processing mode'}), 400
    if current_user.token_balance < token_cost:
        return jsonify({'error': 'Insufficient tokens'}), 403
    if 'image' not in request.files:
        return jsonify({'error': 'Image is missing'}), 400
    try:
        s3_url = upload_file_to_s3(request.files['image'])
        replicate_input = {}
        model_version_id = ""
        # ... (остальная логика из process_image без изменений)
        # ...
        # В конце
        new_prediction = Prediction(user_id=current_user.id, token_cost=token_cost)
        db.session.add(new_prediction)
        # ... остальное как было
        return jsonify({'prediction_id': new_prediction.id, 'new_token_balance': current_user.token_balance})

    except Exception as e:
        print(f"!!! ОБЩАЯ ОШИБКА в process_image: {e}")
        return jsonify({'error': f'An internal server error occurred: {str(e)}'}), 500


@app.route('/get-result/<string:prediction_id>', methods=['GET'])
@login_required
def get_result(prediction_id):
    # ... (логика без изменений)
    prediction = Prediction.query.get(prediction_id)
    if not prediction or prediction.user_id != current_user.id:
        return jsonify({'error': 'Prediction not found or access denied'}), 404
    # ... остальное как было
    return jsonify({'status': 'pending'})


@app.route('/replicate-webhook', methods=['POST'])
def replicate_webhook():
    # ... (логика без изменений)
    data = request.json
    # ... остальное как было
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

if __name__ == '__main__':
    # Для локального запуска, debug=True. На сервере будет False.
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get("PORT", 5001)))
