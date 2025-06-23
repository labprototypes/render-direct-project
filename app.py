# FIX: Explicitly apply gevent monkey-patching at the very top of the file.
from gevent import monkey
monkey.patch_all()

# Standard library imports
import os
import uuid
import time
import io
import json 
from datetime import datetime, timezone
from functools import wraps

# Third-party imports
import boto3
import openai
import requests
import stripe
from flask import Flask, request, jsonify, render_template, url_for, redirect, flash, session, get_flashed_messages
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from PIL import Image
from werkzeug.datastructures import FileStorage

# --- Настройки приложения ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'a-very-secret-key-for-flask-login')
app.config['GOOGLE_CLIENT_ID'] = os.environ.get('GOOGLE_CLIENT_ID')
app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///site.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 280,
}
app.static_folder = 'static'

# --- Конфигурация внешних сервисов ---
AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
AWS_S3_BUCKET_NAME = os.environ.get('AWS_S3_BUCKET_NAME')
AWS_S3_REGION = os.environ.get('AWS_S3_REGION')

stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET')
REPLICATE_API_TOKEN = os.environ.get('REPLICATE_API_TOKEN')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
REDIS_URL = os.environ.get('REDIS_URL')

import redis # <--- ДОБАВЬТЕ ЭТУ СТРОКУ
if REDIS_URL: # <--- ДОБАВЬТЕ ЭТИ 4 СТРОКИ
    redis_client = redis.from_url(REDIS_URL)
else:
    redis_client = None
    print("!!! ВНИМАНИЕ: REDIS_URL не найден. Отправка задач в воркер не будет работать.")

PLAN_PRICES = {
    'taste': 'price_1RYA1GEAARFPkzEzyWSV75UE',
    'best':  'price_1RYA2eEAARFPkzEzvWRFgeSm',
    'pro':   'price_1RYA3HEAARFPkzEzLQEmRz8Q',
}
TOKEN_PRICE_ID = 'price_1RYA4BEAARFPkzEzw98ohUMH'

# --- Инициализация расширений ---
db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "info"

if OPENAI_API_KEY:
    openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
else:
    openai_client = None
    print("!!! ВНИМАНИЕ: OPENAI_API_KEY не найден. Улучшение промптов и Autofix не будут работать.")

import firebase_admin
from firebase_admin import credentials, auth

# Путь к секретному файлу на Render
cred_path = '/etc/secrets/firebase_credentials.json'
if os.path.exists(cred_path):
    cred = credentials.Certificate(cred_path)
else:
    # Для локальной разработки, положите файл в корень проекта
    if os.path.exists('firebase_credentials.json'):
        cred = credentials.Certificate('firebase_credentials.json')
    else:
        cred = None
        print("!!! ВНИМАНИЕ: Не найден файл firebase_credentials.json. Аутентификация Firebase не будет работать.")

if cred and not firebase_admin._apps:
    firebase_admin.initialize_app(cred)

# --- Модели Базы Данных ---
class User(db.Model, UserMixin):
    id = db.Column(db.String(128), primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    username = db.Column(db.String(255), nullable=True)
    token_balance = db.Column(db.Integer, default=100, nullable=False)
    marketing_consent = db.Column(db.Boolean, nullable=False, default=True)
    subscription_status = db.Column(db.String(50), default='free', nullable=False)
    stripe_customer_id = db.Column(db.String(255), nullable=True, unique=True)
    stripe_subscription_id = db.Column(db.String(255), nullable=True, unique=True)
    current_plan = db.Column(db.String(50), nullable=True, default='free')
    trial_used = db.Column(db.Boolean, default=False, nullable=False)
    subscription_ends_at = db.Column(db.DateTime, nullable=True)

    @property
    def is_active(self):
        return True

class Prediction(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.String(128), db.ForeignKey('user.id'), nullable=False)
    replicate_id = db.Column(db.String(255), unique=True, nullable=True, index=True)
    status = db.Column(db.String(50), default='pending', nullable=False)
    output_url = db.Column(db.String(2048), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    token_cost = db.Column(db.Integer, nullable=False, default=1)
    user = db.relationship('User', backref=db.backref('predictions', lazy=True, cascade="all, delete-orphan"))

class UsedTrialEmail(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)


# --- Декораторы и Загрузчик пользователя ---
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(user_id)

def subscription_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('login'))
        if current_user.subscription_status not in ['active', 'trial', 'canceled']:
            flash('Your plan does not allow access to this feature. Please subscribe.', 'warning')
            return redirect(url_for('billing'))
        return f(*args, **kwargs)
    return decorated_function

# --- МАРШРУТЫ АУТЕНТИФИКАЦИИ ---
@app.route('/login')
def login():
    return render_template('login.html')

@app.route('/session-login', methods=['POST'])
def session_login():
    data = request.get_json()
    id_token = data.get('idToken')
    if not id_token:
        return jsonify({"status": "error", "message": "ID token is missing."}), 400

    try:
        decoded_token = auth.verify_id_token(id_token)
        uid = decoded_token['uid']
        email = decoded_token.get('email')
        name = decoded_token.get('name', email)
        
        user = User.query.get(uid)
        
        if not user:
            terms_accepted = data.get('termsAccepted')
            if not terms_accepted:
                return jsonify({"status": "error", "message": "You must accept the Terms of Service and Privacy Policy."}), 400
            get_flashed_messages()
            trial_used_record = UsedTrialEmail.query.filter_by(email=email).first()
            initial_tokens = 0
            marketing_consent = data.get('marketingConsent', True)

            user = User(
                id=uid, 
                email=email, 
                username=name,
                token_balance=initial_tokens,
                marketing_consent=marketing_consent,
                trial_used=bool(trial_used_record)
            )
            db.session.add(user)
            db.session.commit()
            
            login_user(user)
            return jsonify({"status": "success", "action": "redirect", "url": url_for('billing')})

        login_user(user)
        return jsonify({"status": "success", "action": "redirect", "url": url_for('index')})

    except Exception as e:
        print(f"Error in session_login: {e}")
        return jsonify({"status": "error", "message": str(e)}), 401

# --- Маршруты для юридических и вспомогательных страниц ---
@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/marketing-policy')
def marketing_policy():
    return render_template('marketing.html')

# --- Функции-помощники для Stripe и S3 ---
def upload_file_to_s3(file_to_upload):
    if not all([AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_S3_BUCKET_NAME, AWS_S3_REGION]):
        raise Exception("Server configuration error for image uploads.")
    s3_client = boto3.client('s3', region_name=AWS_S3_REGION, aws_access_key_id=AWS_ACCESS_KEY_ID, aws_secret_access_key=AWS_SECRET_ACCESS_KEY)
    _, f_ext = os.path.splitext(file_to_upload.filename)
    object_name = f"uploads/{uuid.uuid4()}{f_ext}"
    file_to_upload.stream.seek(0)
    s3_client.upload_fileobj(file_to_upload.stream, AWS_S3_BUCKET_NAME, object_name, ExtraArgs={'ContentType': file_to_upload.content_type})
    hosted_image_url = f"https://{AWS_S3_BUCKET_NAME}.s3.{AWS_S3_REGION}.amazonaws.com/{object_name}"
    print(f"!!! Изображение загружено на Amazon S3: {hosted_image_url}")
    return hosted_image_url

def resize_image_for_openai(file_storage):
    """Сжимает изображение, если оно слишком большое для OpenAI Vision."""
    MAX_SIZE_MB = 20
    MAX_DIMENSION = 2048 

    img = Image.open(file_storage.stream)
    
    file_storage.stream.seek(0, os.SEEK_END)
    file_size_mb = file_storage.stream.tell() / (1024 * 1024)
    file_storage.stream.seek(0)

    if file_size_mb < MAX_SIZE_MB and max(img.size) <= MAX_DIMENSION:
        return file_storage
    
    img.thumbnail((MAX_DIMENSION, MAX_DIMENSION))
    
    byte_arr = io.BytesIO()
    img_format = img.format if img.format in ['JPEG', 'PNG'] else 'JPEG'
    
    if img_format == 'JPEG':
        img.save(byte_arr, format=img_format, quality=85)
    else:
        img.save(byte_arr, format=img_format)

    byte_arr.seek(0)
    
    compressed_file = FileStorage(
        stream=byte_arr,
        filename=file_storage.filename,
        content_type=f'image/{img_format.lower()}'
    )
    
    return compressed_file

# В файле app.py

def handle_checkout_session(session_data):
    customer_id = session_data.get('customer')
    user_id_from_metadata = session_data.get('metadata', {}).get('user_id')

    with app.app_context():
        user = User.query.get(user_id_from_metadata) if user_id_from_metadata else None
        if not user and customer_id:
            user = User.query.filter_by(stripe_customer_id=customer_id).first()
        
        if not user:
            print(f"Webhook Error: Could not find user for checkout session {session_data.get('id')}")
            return

        if not user.stripe_customer_id and customer_id:
            user.stripe_customer_id = customer_id
        
        if session_data.get('subscription'):
            subscription_id = session_data.get('subscription')
            subscription = stripe.Subscription.retrieve(subscription_id)
            user.stripe_subscription_id = subscription_id
            
            # --- ИСПРАВЛЕННАЯ ЛОГИКА СТАТУСОВ ---
            stripe_status = subscription.status
            if stripe_status == 'trialing':
                user.subscription_status = 'trial'
                user.current_plan = 'taste' # Триал всегда для плана 'taste'
                user.subscription_ends_at = datetime.fromtimestamp(subscription.trial_end, tz=timezone.utc)
                if not user.trial_used:
                    user.trial_used = True
                    user.token_balance += 1500
                    print(f"User {user.id} started a free trial. Added 1500 tokens.")
            elif stripe_status == 'active':
                user.subscription_status = 'active'
                user.subscription_ends_at = datetime.fromtimestamp(subscription.current_period_end, tz=timezone.utc)
                price_id = subscription.items.data[0].price.id
                user.current_plan = next((name for name, id in PLAN_PRICES.items() if id == price_id), 'unknown')
            else: # incomplete, past_due, etc.
                user.subscription_status = stripe_status
            
            if session_data.get('payment_status') == 'paid':
                handle_successful_payment(subscription=subscription)

        elif session_data.get('payment_intent'):
            user.token_balance += 1000

        db.session.commit()

def handle_subscription_change(subscription):
    with app.app_context():
        user = User.query.filter_by(stripe_subscription_id=subscription.id).first()
        if not user: 
            print(f"Webhook: Could not find user for subscription change: {subscription.id}")
            return

        # ИСПРАВЛЕНИЕ: Сначала проверяем, существует ли поле, перед тем как его использовать
        if subscription.get('current_period_end'):
            user.subscription_ends_at = datetime.fromtimestamp(subscription.current_period_end, tz=timezone.utc)

        # Обновляем статус на основе флагов и статуса из Stripe
        if subscription.cancel_at_period_end:
            user.subscription_status = 'canceled'
        elif subscription.status == 'trialing':
            user.subscription_status = 'trial'
        elif subscription.status == 'active':
            user.subscription_status = 'active'
            price_id = subscription.items.data[0].price.id
            user.current_plan = next((name for name, id in PLAN_PRICES.items() if id == price_id), 'unknown')
        else:
            user.subscription_status = subscription.status # e.g., 'past_due' or final 'canceled'
            if user.subscription_status == 'canceled':
                user.current_plan = 'free' # Если подписка окончательно отменена

        db.session.commit()

def handle_successful_payment(invoice=None, subscription=None):
    subscription_id = invoice.get('subscription') if invoice else (subscription.id if subscription else None)
    if not subscription_id: return
    
    with app.app_context():
        user = User.query.filter_by(stripe_subscription_id=subscription_id).first()
        if not user: return
        
        if not subscription:
            subscription = stripe.Subscription.retrieve(subscription_id)
        
        price_id = subscription.items.data[0].price.id
        token_map = {'taste': 1500, 'best': 4500, 'pro': 15000}
        plan_name = next((name for name, id in PLAN_PRICES.items() if id == price_id), None)
        
        # Начисляем токены только если это не триал (чтобы не дублировать)
        if plan_name in token_map and not subscription.trial_end:
            user.token_balance += token_map[plan_name]
        
        db.session.commit()

# --- Маршруты для биллинга и Stripe Webhook ---
@app.route('/choose-plan')
@login_required
def choose_plan():
    if current_user.subscription_status == 'active':
        return redirect(url_for('billing'))
    return render_template('choose_plan.html', PLAN_PRICES=PLAN_PRICES)

@app.route('/billing')
@login_required
def billing():
    return render_template('billing.html', PLAN_PRICES=PLAN_PRICES, TOKEN_PRICE_ID=TOKEN_PRICE_ID)
    
@app.route('/archive')
@login_required
@subscription_required
def archive():
    predictions = Prediction.query.filter_by(user_id=current_user.id, status='completed').order_by(Prediction.created_at.desc()).all()
    return render_template('archive.html', predictions=predictions)

@app.route('/create-checkout-session', methods=['POST'])
@login_required
def create_checkout_session():
    price_id = request.form.get('price_id')
    is_trial = request.form.get('trial') == 'true'

    if is_trial and (current_user.trial_used or current_user.subscription_status == 'active'):
        flash('Free trial can only be used once.', 'warning')
        return redirect(url_for('billing'))

    mode = 'subscription' if price_id in PLAN_PRICES.values() else 'payment'
    try:
        checkout_params = {
            'payment_method_types': ['card'], 'line_items': [{'price': price_id, 'quantity': 1}],
            'mode': mode, 'automatic_tax': {'enabled': True},
            'success_url': url_for('billing', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            'cancel_url': url_for('billing', _external=True),
        }
        if is_trial and mode == 'subscription':
            checkout_params['subscription_data'] = {'trial_period_days': 3}

        if current_user.stripe_customer_id:
            checkout_params['customer'] = current_user.stripe_customer_id
            checkout_params['customer_update'] = {'address': 'auto'}
        else:
            checkout_params['customer_email'] = current_user.email
            checkout_params['metadata'] = {'user_id': current_user.id}

        checkout_session = stripe.checkout.Session.create(**checkout_params)
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        flash(f'Stripe error: {str(e)}', 'error')
        return redirect(url_for('billing'))

@app.route('/create-portal-session', methods=['POST'])
@login_required
def create_portal_session():
    if not current_user.stripe_customer_id:
        flash('Stripe customer not found.', 'error')
        return redirect(url_for('billing'))
    portal_session = stripe.billing_portal.Session.create(
        customer=current_user.stripe_customer_id,
        return_url=url_for('billing', _external=True),
    )
    return redirect(portal_session.url, code=303)

@app.route('/cancel-subscription', methods=['POST'])
@login_required
def cancel_subscription():
    if not current_user.stripe_subscription_id:
        flash('No active subscription to cancel.', 'error')
        return redirect(url_for('billing'))
    try:
        # Отправляем команду в Stripe
        stripe.Subscription.modify(
            current_user.stripe_subscription_id,
            cancel_at_period_end=True
        )

        # ИСПРАВЛЕНИЕ: Немедленно обновляем статус в НАШЕЙ базе данных
        current_user.subscription_status = 'canceled'
        db.session.commit()

        flash('Your subscription will be canceled at the end of the current period.', 'success')
    except Exception as e:
        flash(f'Error cancelling subscription: {str(e)}', 'error')

    return redirect(url_for('billing'))

@app.route('/delete-account', methods=['POST'])
@login_required
def delete_account():
    try:
        user_email = current_user.email
        user_id = current_user.id
        stripe_subscription_id = current_user.stripe_subscription_id

        if stripe_subscription_id:
            try:
                stripe.Subscription.cancel(stripe_subscription_id)
            except stripe.error.InvalidRequestError as e:
                print(f"Subscription {stripe_subscription_id} might already be canceled or invalid: {e}")

        # Удаляем связанные генерации (cascade должен сработать, но для надежности)
        Prediction.query.filter_by(user_id=user_id).delete()
        
        if not UsedTrialEmail.query.filter_by(email=user_email).first():
            db.session.add(UsedTrialEmail(email=user_email))
        
        user_to_delete = User.query.get(user_id)
        logout_user() # Важно выйти из системы перед удалением
        db.session.delete(user_to_delete)
        
        # Удаляем пользователя из Firebase
        auth.delete_user(user_id)
        
        db.session.commit()
        
        flash('Your account and all associated data have been successfully deleted.', 'success')
        return redirect(url_for('login'))
    except Exception as e:
        db.session.rollback()
        flash(f'An error occurred while deleting your account: {str(e)}', 'error')
        return redirect(url_for('billing'))

@app.route('/stripe-webhook', methods=['POST'])
def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        return 'Invalid payload or signature', 400
    
    event_map = {
        'checkout.session.completed': handle_checkout_session,
        'customer.subscription.updated': handle_subscription_change,
        'customer.subscription.deleted': handle_subscription_change,
        'invoice.payment_succeeded': handle_successful_payment,
    }
    handler = event_map.get(event['type'])
    if handler:
        handler(event['data']['object'])

    return 'OK', 200

# --- Маршруты API и обработки изображений ---
@app.route('/get-result/<string:prediction_id>', methods=['GET'])
@login_required
def get_result(prediction_id):
    prediction = Prediction.query.get(prediction_id)
    if not prediction or prediction.user_id != current_user.id:
        return jsonify({'error': 'Prediction not found or access denied'}), 404
    if prediction.status == 'completed':
        return jsonify({'status': 'completed', 'output_url': prediction.output_url, 'new_token_balance': current_user.token_balance})
    if prediction.status == 'failed':
        return jsonify({'status': 'failed', 'error': 'Generation failed. Your tokens have been refunded.', 'new_token_balance': User.query.get(current_user.id).token_balance})
    if prediction.status == 'pending' and prediction.replicate_id:
        try:
            headers = {"Authorization": f"Bearer {REPLICATE_API_TOKEN}", "Content-Type": "application/json"}
            poll_url = f"https://api.replicate.com/v1/predictions/{prediction.replicate_id}"
            get_response = requests.get(poll_url, headers=headers)
            get_response.raise_for_status()
            status_data = get_response.json()
            if status_data.get("status") == "failed":
                print(f"!!! Polling detected failed prediction {prediction.id}. Refunding tokens.")
                prediction.status = 'failed'
                user = User.query.get(prediction.user_id)
                if user:
                    user.token_balance += prediction.token_cost
                db.session.commit()
                return jsonify({'status': 'failed', 'error': f"Generation failed: {status_data.get('error', 'Unknown error')}. Your tokens have been refunded.", 'new_token_balance': user.token_balance if user else None})
        except requests.exceptions.RequestException as e:
            print(f"!!! Error polling Replicate status for {prediction.id}: {e}")
    return jsonify({'status': 'pending'})

@app.route('/process-image', methods=['POST'])
@login_required
@subscription_required
def process_image():
    if 'image' not in request.files:
        return jsonify({'error': 'Image is missing'}), 400
    
    mode = request.form.get('mode')
    token_cost = 0

    try:
        # Проверяем баланс и списываем токены
        edit_mode = request.form.get('edit_mode') # 'edit' или 'autofix' (для Basic)
        if mode == 'edit':
            if edit_mode == 'autofix': # Это наша новая логика "Basic"
                token_cost = 100
            else: # Это старая логика "Edit"
                token_cost = 65
        elif mode == 'upscale':
            scale_factor = int(request.form.get('scale_factor', '2'))
            if scale_factor <= 2: token_cost = 17
            elif scale_factor <= 4: token_cost = 65
            else: token_cost = 150

        if token_cost == 0:
            return jsonify({'error': 'Invalid processing mode'}), 400
        if current_user.token_balance < token_cost:
            return jsonify({'error': f'Insufficient tokens. Need {token_cost}, you have {current_user.token_balance}.'}), 403

        # --- НАЧАЛО ОБРАБОТКИ ---
        
        if mode == 'edit':
            if edit_mode == 'autofix':
                # --- ЛОГИКА ДЛЯ НОВОГО РЕЖИМА "BASIC" ---
                print("!!! РЕЖИМ: Basic (через Autofix)")
                if not redis_client:
                    raise Exception("Redis is not configured on the server.")

                uploaded_file = request.files['image']
                prompt = request.form.get('prompt', '')
                
                # Сохраняем файл в памяти для многократного чтения
                image_data = uploaded_file.read()
                img_for_size_check = Image.open(io.BytesIO(image_data))
                original_width, original_height = img_for_size_check.size

                # Загружаем оригинал в S3
                original_stream = io.BytesIO(image_data)
                original_for_upload = FileStorage(stream=original_stream, filename=uploaded_file.filename, content_type=uploaded_file.content_type)
                original_s3_url = upload_file_to_s3(original_for_upload)
                
                # Готовим и загружаем копию для анализа OpenAI
                analysis_stream = io.BytesIO(image_data)
                analysis_for_upload = FileStorage(stream=analysis_stream, filename=uploaded_file.filename, content_type=uploaded_file.content_type)
                image_for_openai = resize_image_for_openai(analysis_for_upload)
                s3_url_for_openai = upload_file_to_s3(image_for_openai)

                # Этап 1: Создаем описательный промпт для генерации
                generation_system_prompt = (
                    "You are an expert prompt engineer for an image editing AI. A user will provide a request, possibly in any language, to modify an existing uploaded image. "
                    "Your tasks are: 1. Understand the user's core intent for image modification. 2. Translate the request to concise and clear English if it's not already. "
                    "3. Rephrase it into a concise, command-based instruction in English. After the command, you MUST append the exact phrase: ', do not change anything else, keep the original style'. Example: 'Add a frog on the leaf, do not change anything else, keep the original style' "
                    "The output should be only the refined prompt, no explanations or conversational fluff."
                )
                messages_for_generation = [{"role": "system", "content": generation_system_prompt}, {"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": s3_url_for_openai}}]}]
                
                generation_response = openai_client.chat.completions.create(model="gpt-4o", messages=messages_for_generation, max_tokens=250, temperature=0.2)
                generation_prompt = generation_response.choices[0].message.content.strip()

                # Этап 2: Определяем намерение и объект для маски
                intent_system_prompt = (
                    "You are a classification AI. Analyze the user's original request. Your task is to generate a JSON object with two keys: "
                    "1. \"intent\": Classify the user's intent as one of three possible string values: 'ADD', 'REMOVE', or 'REPLACE'. "
                    "2. \"mask_prompt\": Extract a very short (2-5 words) English name for the object being acted upon. "
                    "You MUST only output the raw JSON object."
                )
                messages_for_intent = [{"role": "system", "content": intent_system_prompt}, {"role": "user", "content": prompt}]
                
                intent_response = openai_client.chat.completions.create(model="gpt-4o", messages=messages_for_intent, max_tokens=100, response_format={"type": "json_object"}, temperature=0.0)
                intent_data = json.loads(intent_response.choices[0].message.content)
                intent = intent_data.get("intent")
                mask_prompt = intent_data.get("mask_prompt")

                # Списываем токены и отправляем задачу в воркер
                current_user.token_balance -= token_cost
                new_prediction = Prediction(user_id=current_user.id, token_cost=token_cost, status='pending')
                db.session.add(new_prediction)
                db.session.commit()

                job_data = {
                    "prediction_id": new_prediction.id, "original_s3_url": original_s3_url,
                    "intent": intent, "generation_prompt": generation_prompt,
                    "mask_prompt": mask_prompt, "token_cost": token_cost, "user_id": current_user.id,
                    "original_width": original_width, "original_height": original_height
                }
                redis_client.lpush('pifly_edit_jobs', json.dumps(job_data))
                return jsonify({'prediction_id': new_prediction.id, 'new_token_balance': current_user.token_balance})

            else: # Старая логика edit_mode == 'edit'
                # --- ВАША СТАРАЯ ЛОГИКА "EDIT", ОСТАЛАСЬ БЕЗ ИЗМЕНЕНИЙ ---
                print("!!! РЕЖИМ: Edit (старый)")
                s3_url = upload_file_to_s3(request.files['image'])
                prompt = request.form.get('prompt', '')
                if not openai_client: raise Exception("OpenAI client not configured.")
                
                edit_system_prompt = (
                    "You are an expert prompt engineer for an image editing AI. A user will provide a request, possibly in any language, to modify an existing uploaded image. Your tasks are: 1. Understand the user's core intent for image modification. 2. Translate the request to concise and clear English if it's not already. 3. Rephrase it into a descriptive prompt focusing on visual attributes of the desired *final state* of the image. This prompt will be given to an AI that modifies the uploaded image based on this prompt. Be specific. For example, instead of 'make it better', describe *how* to make it better visually. The output should be only the refined prompt, no explanations or conversational fluff."
                )
                messages = [
                    {"role": "system", "content": edit_system_prompt},
                    {"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": s3_url}}]}
                ]
                response = openai_client.chat.completions.create(model="gpt-4o", messages=messages, max_tokens=150)
                final_prompt = response.choices[0].message.content.strip().replace('\\n', ' ').replace('\\r', ' ').strip()
                
                replicate_input = {"input_image": s3_url, "prompt": final_prompt}
                model_version_id = "black-forest-labs/flux-kontext-max:0b9c317b23e79a9a0d8b9602ff4d04030d433055927fb7c4b91c44234a6818c4"
        
        elif mode == 'upscale':
            # --- ВАША ЛОГИКА "UPSCALE", ОСТАЛАСЬ БЕЗ ИЗМЕНЕНИЙ ---
            print("!!! РЕЖИМ: Upscale")
            s3_url = upload_file_to_s3(request.files['image'])
            model_version_id = "dfad41707589d68ecdccd1dfa600d55a208f9310748e44bfe35b4a6291453d5e"
            scale_factor = float(request.form.get('scale_factor', '2'))
            creativity = round(float(request.form.get('creativity', '30')) / 100.0, 4)
            resemblance = round(float(request.form.get('resemblance', '20')) / 100.0 * 3.0, 4)
            dynamic = round(float(request.form.get('dynamic', '10')) / 100.0 * 50.0, 4)
            replicate_input = {"image": s3_url, "scale_factor": scale_factor, "creativity": creativity, "resemblance": resemblance, "dynamic": dynamic}

        # Общий код для Replicate (используется для старого "Edit" и "Upscale")
        if mode != 'edit' or edit_mode != 'autofix':
            if not REPLICATE_API_TOKEN: raise Exception("Replicate API token is not configured.")
            
            current_user.token_balance -= token_cost
            new_prediction = Prediction(user_id=current_user.id, token_cost=token_cost)
            db.session.add(new_prediction)
            db.session.commit()

            headers = {"Authorization": f"Bearer {REPLICATE_API_TOKEN}", "Content-Type": "application/json"}
            post_payload = {"version": model_version_id, "input": replicate_input, "webhook": url_for('replicate_webhook', _external=True), "webhook_events_filter": ["completed", "failed"]}
            
            start_response = requests.post("https://api.replicate.com/v1/predictions", json=post_payload, headers=headers)
            start_response.raise_for_status()
            prediction_data = start_response.json()

            new_prediction.replicate_id = prediction_data.get('id')
            db.session.commit()
            return jsonify({'prediction_id': new_prediction.id, 'new_token_balance': current_user.token_balance})

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        # Безопасный возврат токенов
        if token_cost > 0:
             user_in_db = db.session.get(User, current_user.id)
             if user_in_db:
                 user_in_db.token_balance += token_cost
                 db.session.commit()
        return jsonify({'error': f'An internal server error occurred. Your tokens have been refunded. Error: {str(e)}'}), 500

@app.route('/replicate-webhook', methods=['POST'])
def replicate_webhook():
    data = request.json
    replicate_id = data.get('id')
    status = data.get('status')

    if not replicate_id:
        return 'Invalid payload, missing ID', 400

    with app.app_context():
        prediction = Prediction.query.filter_by(replicate_id=replicate_id).first()
        if not prediction:
            print(f"!!! Вебхук получен для неизвестного Replicate ID: {replicate_id}")
            return 'Prediction not found', 404

        if status == 'succeeded':
            temp_url = data.get('output')
            if temp_url and isinstance(temp_url, list): temp_url = temp_url[0]
            
            if temp_url:
                try:
                    image_response = requests.get(temp_url, stream=True)
                    image_response.raise_for_status()
                    image_data = io.BytesIO(image_response.content)
                    s3_client = boto3.client('s3', region_name=AWS_S3_REGION, aws_access_key_id=AWS_ACCESS_KEY_ID, aws_secret_access_key=AWS_SECRET_ACCESS_KEY)
                    file_extension = os.path.splitext(temp_url.split('?')[0])[-1] or '.png'
                    object_name = f"generations/{prediction.user_id}/{prediction.id}{file_extension}"
                    
                    s3_client.upload_fileobj(
                        image_data, AWS_S3_BUCKET_NAME, object_name,
                        ExtraArgs={'ContentType': image_response.headers.get('Content-Type', 'image/png')}
                    )
                    
                    permanent_s3_url = f"https://{AWS_S3_BUCKET_NAME}.s3.{AWS_S3_REGION}.amazonaws.com/{object_name}"
                    prediction.output_url = permanent_s3_url
                    prediction.status = 'completed'

                except Exception as e:
                    print(f"!!! Ошибка при скачивании/перезагрузке изображения из Replicate: {e}")
                    prediction.status = 'failed'
                    user = User.query.get(prediction.user_id)
                    if user: user.token_balance += prediction.token_cost
            else:
                prediction.status = 'failed'

        elif status == 'failed':
            prediction.status = 'failed'
            user = User.query.get(prediction.user_id)
            if user:
                user.token_balance += prediction.token_cost
            print(f"!!! Вебхук обработан для Prediction {prediction.id}. Статус: failed. Токены возвращены.")
            
        db.session.commit()
        
    return 'Webhook received', 200


# --- Главный маршрут и выход ---
@app.route('/')
@login_required
@subscription_required
def index():
    return render_template('index.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))

# --- Конец блока для вставки ---

# --- Final app setup ---
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get("PORT", 5001)))
