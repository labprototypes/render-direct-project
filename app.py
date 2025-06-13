# FIX: Explicitly apply gevent monkey-patching at the very top of the file.
from gevent import monkey
monkey.patch_all()

# Standard library imports
import os
import uuid
import time
from datetime import datetime
from functools import wraps

# Third-party imports
import boto3
import openai
import requests
import stripe
from flask import Flask, request, jsonify, render_template, url_for, redirect, flash, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user

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
    cred = credentials.Certificate('firebase_credentials.json')

firebase_admin.initialize_app(cred)
# --- Модели Базы Данных ---
# В app.py

class User(db.Model, UserMixin):
    id = db.Column(db.String(128), primary_key=True) # ID теперь строка для Firebase UID
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    username = db.Column(db.String(255), nullable=True) # Имя пользователя от Google
    token_balance = db.Column(db.Integer, default=100, nullable=False)
    marketing_consent = db.Column(db.Boolean, nullable=False, default=True)
    subscription_status = db.Column(db.String(50), default='trial', nullable=False)
    stripe_customer_id = db.Column(db.String(255), nullable=True, unique=True)
    stripe_subscription_id = db.Column(db.String(255), nullable=True, unique=True)
    current_plan = db.Column(db.String(50), nullable=True, default='trial')

    # Поле password больше не нужно

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
    user = db.relationship('User', backref=db.backref('predictions', lazy=True))

class UsedTrialEmail(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)


# --- Декораторы и Загрузчик пользователя ---
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(user_id) # <--- Убрали int()

def subscription_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('login'))
        if current_user.subscription_status not in ['active', 'trial']:
            flash('Your plan does not allow access to this feature. Please subscribe.', 'warning')
            return redirect(url_for('billing'))
        return f(*args, **kwargs)
    return decorated_function

# --- МАРШРУТЫ АУТЕНТИФИКАЦИИ ---
# --- НОВЫЙ МАРШРУТ ДЛЯ АУТЕНТИФИКАЦИИ ЧЕРЕЗ FIREBASE ---
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

        # Ищем пользователя или создаем нового
        user = User.query.get(uid)
        if not user:
            # Логика для новых пользователей
            trial_used = UsedTrialEmail.query.filter_by(email=email).first()
            initial_tokens = 0 if trial_used else 100
            
            # Получаем согласие на маркетинг из запроса
            marketing_consent = data.get('marketingConsent', True)

            user = User(
                id=uid, 
                email=email, 
                username=name,
                token_balance=initial_tokens,
                marketing_consent=marketing_consent
            )
            db.session.add(user)
            db.session.commit()

        login_user(user)
        return jsonify({"status": "success"})

    except Exception as e:
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

def handle_checkout_session(session):
    customer_id = session.get('customer')
    with app.app_context():
        user = User.query.filter_by(stripe_customer_id=customer_id).first()
        if not user: return
        if session.get('subscription'):
            subscription_id = session.get('subscription')
            subscription = stripe.Subscription.retrieve(subscription_id)
            user.stripe_subscription_id = subscription_id
            user.subscription_status = subscription.status
            price_id = subscription.items.data[0].price.id
            user.current_plan = next((name for name, id in PLAN_PRICES.items() if id == price_id), 'unknown')
            handle_successful_payment(invoice=None, subscription=subscription)
        elif session.get('payment_intent'):
            user.token_balance += 1000
        db.session.commit()

def handle_subscription_change(subscription):
    customer_id = subscription.get('customer')
    with app.app_context():
        user = User.query.filter_by(stripe_customer_id=customer_id).first()
        if not user: return
        user.subscription_status = subscription.status
        if subscription.status != 'active':
            user.current_plan = 'inactive'
        db.session.commit()

def handle_successful_payment(invoice=None, subscription=None):
    if invoice:
        subscription_id = invoice.get('subscription')
    elif subscription:
        subscription_id = subscription.id
    else: return
    if not subscription_id: return
    with app.app_context():
        user = User.query.filter_by(stripe_subscription_id=subscription_id).first()
        if not user: return
        if not subscription:
            subscription = stripe.Subscription.retrieve(subscription_id)
        price_id = subscription.items.data[0].price.id
        if price_id == PLAN_PRICES.get('taste'):
            user.token_balance += 1500
        elif price_id == PLAN_PRICES.get('best'):
            user.token_balance += 4500
        elif price_id == PLAN_PRICES.get('pro'):
            user.token_balance += 15000
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
    mode = 'subscription' if price_id in PLAN_PRICES.values() else 'payment'
    try:
        checkout_session = stripe.checkout.Session.create(
            customer=current_user.stripe_customer_id,
            payment_method_types=['card'],
            line_items=[{'price': price_id, 'quantity': 1}],
            mode=mode,
            automatic_tax={'enabled': True},
            customer_update={'address': 'auto'},
            success_url=url_for('billing', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('billing', _external=True),
        )
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

@app.route('/stripe-webhook', methods=['POST'])
def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        return 'Invalid payload or signature', 400
    with app.app_context():
        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            handle_checkout_session(session)
        if event['type'] in ['customer.subscription.updated', 'customer.subscription.deleted']:
            subscription = event['data']['object']
            handle_subscription_change(subscription)
        if event['type'] == 'invoice.payment_succeeded':
            invoice = event['data']['object']
            handle_successful_payment(invoice=invoice)
    return 'OK', 200

@app.route('/replicate-webhook', methods=['POST'])
def replicate_webhook():
    """
    Принимает вебхук от Replicate по завершении генерации.
    """
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
            output_url = data.get('output')
            if output_url and isinstance(output_url, list):
                prediction.output_url = output_url[0]
            else:
                 prediction.output_url = output_url
            prediction.status = 'completed'
            print(f"!!! Вебхук успешно обработан для Prediction {prediction.id}. Статус: completed.")
        elif status == 'failed':
            prediction.status = 'failed'
            user = User.query.get(prediction.user_id)
            if user:
                user.token_balance += prediction.token_cost
            print(f"!!! Вебхук обработан для Prediction {prediction.id}. Статус: failed. Токены возвращены.")
        db.session.commit()
    return 'Webhook received', 200

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
    mode = request.form.get('mode')
    token_cost = 0
    if mode == 'edit':
        token_cost = 65
    elif mode == 'upscale':
        scale_factor = int(request.form.get('scale_factor', '2'))
        if scale_factor <= 2:
            token_cost = 17
        elif scale_factor <= 4:
            token_cost = 65
        else:
            token_cost = 150
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
        if mode == 'edit':
            edit_mode = request.form.get('edit_mode')
            prompt = request.form.get('prompt', '')
            model_version_id = "black-forest-labs/flux-kontext-max:0b9c317b23e79a9a0d8b9602ff4d04030d433055927fb7c4b91c44234a6818c4"
            if not openai_client:
                raise Exception("System error, your tokens have been refunded")
            final_prompt = ""
            if edit_mode == 'autofix':
                print("!!! Запрос к OpenAI Vision API для Autofix...")
                autofix_system_prompt = (
                    "You are an advanced AI image analysis and correction expert..."
                )
                messages = [
                    {"role": "system", "content": autofix_system_prompt},
                    {"role": "user", "content": [{"type": "image_url", "image_url": {"url": s3_url}}]}
                ]
            else:
                print("!!! Запрос к OpenAI Vision API для Edit (с картинкой)...")
                edit_system_prompt = (
                    "You are an AI image analysis and editing expert..."
                )
                messages = [
                    {"role": "system", "content": edit_system_prompt},
                    {"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": s3_url}}
                    ]}
                ]
            response = openai_client.chat.completions.create(model="gpt-4o", messages=messages, max_tokens=150)
            final_prompt = response.choices[0].message.content.strip().replace('\n', ' ').replace('\r', ' ').strip()
            print(f"!!! Улучшенный промпт ({edit_mode}): {final_prompt}")
            replicate_input = {"input_image": s3_url, "prompt": final_prompt}
        elif mode == 'upscale':
            model_version_id = "dfad41707589d68ecdccd1dfa600d55a208f9310748e44bfe35b4a6291453d5e"
            scale_factor = float(request.form.get('scale_factor', '2'))
            creativity = round(float(request.form.get('creativity', '30')) / 100.0, 4)
            resemblance = round(float(request.form.get('resemblance', '20')) / 100.0 * 3.0, 4)
            dynamic = round(float(request.form.get('dynamic', '10')) / 100.0 * 50.0, 4)
            replicate_input = {"image": s3_url, "scale_factor": scale_factor, "creativity": creativity, "resemblance": resemblance, "dynamic": dynamic}
        if not model_version_id or not replicate_input:
            raise Exception(f"Invalid mode or missing inputs. Mode: {mode}. Model ID set: {bool(model_version_id)}. Input set: {bool(replicate_input)}")
        if not REPLICATE_API_TOKEN:
            raise Exception("System error, your tokens have been refunded")
        new_prediction = Prediction(user_id=current_user.id, token_cost=token_cost)
        db.session.add(new_prediction)
        db.session.commit()
        headers = {"Authorization": f"Bearer {REPLICATE_API_TOKEN}", "Content-Type": "application/json"}
        post_payload = {"version": model_version_id, "input": replicate_input, "webhook": url_for('replicate_webhook', _external=True), "webhook_events_filter": ["completed"]}
        print(f"!!! Replicate Payload: {post_payload}")
        start_response = requests.post("https://api.replicate.com/v1/predictions", json=post_payload, headers=headers)
        start_response.raise_for_status()
        prediction_data = start_response.json()
        replicate_prediction_id = prediction_data.get('id')
        new_prediction.replicate_id = replicate_prediction_id
        current_user.token_balance -= token_cost
        db.session.commit()
        return jsonify({'prediction_id': new_prediction.id, 'new_token_balance': current_user.token_balance})
    except requests.exceptions.HTTPError as e:
        error_details = "No details in response."
        if e.response is not None:
            try:
                error_details = e.response.text
            except Exception:
                pass
        print(f"!!! ОБЩАЯ ОШИБКА в process_image (HTTPError): {e}\nReplicate Response: {error_details}")
        return jsonify({'error': 'System error, your tokens have been refunded'}), 500
    except Exception as e:
        print(f"!!! ОБЩАЯ ОШИБКА в process_image (General): {e}")
        return jsonify({'error': f'An internal server error occurred: {str(e)}'}), 500

# --- Главный маршрут ---
@app.route('/')
@login_required
@subscription_required
def index():
    return render_template('index.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'success') # Добавим сообщение для пользователя
    return redirect(url_for('login'))

# --- Final app setup ---
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get("PORT", 5001)))
