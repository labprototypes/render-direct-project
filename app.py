# FIX: Explicitly apply gevent monkey-patching at the very top of the file.
from gevent import monkey
monkey.patch_all()

import os
import boto3
import uuid
import requests
import time
import openai
import stripe
from flask import Flask, request, jsonify, render_template, url_for, redirect, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, BooleanField
from wtforms.validators import DataRequired, Email, EqualTo, Length, ValidationError
from functools import wraps

# --- Настройки ---
AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
AWS_S3_BUCKET_NAME = os.environ.get('AWS_S3_BUCKET_NAME')
AWS_S3_REGION = os.environ.get('AWS_S3_REGION')

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'a-very-secret-key-for-flask-login')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///site.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 280,
}

# --- Stripe Configuration ---
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET')

# VVVVVV   ВАЖНО: УБЕДИТЕСЬ, ЧТО ЭТИ ID СООТВЕТСТВУЮТ ЦЕНАМ В STRIPE   VVVVVV
PLAN_PRICES = {
    'taste': 'price_1RYA1GEAARFPkzEzyWSV75UE', # ID для €9/mo
    'best':  'price_1RYA2eEAARFPkzEzvWRFgeSm',  # ID для €19/mo
    'pro':   'price_1RYA3HEAARFPkzEzLQEmRz8Q',   # ID для €35/mo
}
TOKEN_PRICE_ID = 'price_1RYA4BEAARFPkzEzw98ohUMH' # ID для разовой покупки токенов
# ^^^^^^   ВАЖНО: УБЕДИТЕСЬ, ЧТО ЭТИ ID СООТВЕТСТВУЮТ ЦЕНАМ В STRIPE   ^^^^^^


db = SQLAlchemy(app)

# --- Настройка Flask-Login ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = "Please log in to access this page."
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
    subscription_status = db.Column(db.String(50), default='trial', nullable=False)
    stripe_customer_id = db.Column(db.String(255), nullable=True, unique=True)
    stripe_subscription_id = db.Column(db.String(255), nullable=True, unique=True)
    current_plan = db.Column(db.String(50), nullable=True, default='trial')

    @property
    def is_active(self):
        return True

class Prediction(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    replicate_id = db.Column(db.String(255), unique=True, nullable=True, index=True)
    status = db.Column(db.String(50), default='pending', nullable=False)
    output_url = db.Column(db.String(2048), nullable=True)
    created_at = db.Column(db.DateTime, default=db.func.now())
    token_cost = db.Column(db.Integer, nullable=False, default=1)

    user = db.relationship('User', backref=db.backref('predictions', lazy=True))

# --- Формы ---
class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember = BooleanField('Remember me')
    submit = SubmitField('Login')

class RegisterForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    # FIX: Сделали поле username обязательным и добавили валидаторы
    username = StringField('Username', validators=[
        DataRequired(message="Please enter a username."),
        Length(min=3, max=30, message="Username must be between 3 and 30 characters.")
    ])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    password_confirm = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password', message='Passwords must match')])
    accept_tos = BooleanField('I accept the Terms of Service and have read the Privacy Policy', validators=[DataRequired(message="You must accept the Terms and Privacy Policy.")])
    marketing_consent = BooleanField('I agree to receive marketing communications as described in the Marketing Policy', default=True)
    submit = SubmitField('Sign Up')

    # FIX: Добавили метод для проверки уникальности имени пользователя
    def validate_username(self, username):
        user = User.query.filter_by(username=username.data).first()
        if user:
            raise ValidationError('This username is already taken. Please choose another.')

class ChangePasswordForm(FlaskForm):
    old_password = PasswordField('Current Password', validators=[DataRequired()])
    new_password = PasswordField('New Password', validators=[DataRequired(), Length(min=6)])
    new_password_confirm = PasswordField('Confirm New Password', validators=[DataRequired(), EqualTo('new_password', message='Passwords must match')])
    submit = SubmitField('Change Password')

app.static_folder = 'static'
REPLICATE_API_TOKEN = os.environ.get('REPLICATE_API_TOKEN')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

if OPENAI_API_KEY:
    openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
else:
    openai_client = None
    print("!!! ВНИМАНИЕ: OPENAI_API_KEY не найден. Улучшение промптов и Autofix не будут работать.")

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
            flash('Invalid email or password.', 'error')
    return render_template('custom_login_user.html', form=form)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = RegisterForm()
    if form.validate_on_submit():
        existing_user = User.query.filter_by(email=form.email.data).first()
        if existing_user:
            flash('A user with this email already exists.', 'error')
            return redirect(url_for('register'))

        hashed_password = generate_password_hash(form.password.data, method='pbkdf2:sha256')
        
        try:
            stripe_customer = stripe.Customer.create(email=form.email.data)
        except Exception as e:
            flash(f'Error creating customer in Stripe: {e}', 'error')
            return render_template('custom_register_user.html', form=form)
            
        new_user = User(
            email=form.email.data,
            username=form.username.data,
            password=hashed_password,
            marketing_consent=form.marketing_consent.data,
            stripe_customer_id=stripe_customer.id
        )
        db.session.add(new_user)
        db.session.commit()
        login_user(new_user)
        return redirect(url_for('choose_plan'))
    return render_template('custom_register_user.html', form=form)

@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not check_password_hash(current_user.password, form.old_password.data):
            flash('Incorrect current password.', 'error')
            return redirect(url_for('change_password'))
        current_user.password = generate_password_hash(form.new_password.data, method='pbkdf2:sha256')
        db.session.commit()
        flash('Your password has been changed successfully!', 'success')
        return redirect(url_for('index'))
    return render_template('custom_change_password.html', form=form)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/marketing-policy')
def marketing_policy():
    return render_template('marketing.html')


# --- Функции-помощники ---
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
        # FIX: Updated credit amounts
        if price_id == PLAN_PRICES.get('taste'):
            user.token_balance += 1500
        elif price_id == PLAN_PRICES.get('best'):
            user.token_balance += 4500
        elif price_id == PLAN_PRICES.get('pro'):
            user.token_balance += 15000
        db.session.commit()

# --- Маршруты для биллинга, Stripe и новых страниц ---
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
            # Возвращаем токены пользователю, если генерация не удалась
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
    token_cost = 5 if mode == 'upscale' else 1
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
                    "You are an advanced AI image analysis and correction expert. Your PRIMARY goal is to meticulously examine the provided image and identify any significant VISUAL ARTIFACTS, ANOMALIES, or UNINTENDED OBJECTS that detract from the image quality or do not logically belong."
                    "\n\nFollow these strict steps:"
                    "\n1. **DETAILED ANALYSIS:** Scrutinize the image for any unusual or unexpected elements. These could include (but are not limited to):"
                    "\n   - Distorted or malformed body parts (faces, hands, limbs)."
                    "\n   - Glitches, unnatural lines, or color splotches."
                    "\n   - Objects that appear to be floating, misaligned, or out of context."
                    "\n   - Textures that look artificial or inconsistent with the rest of the image."
                    "\n   - Any element that looks like a mistake or an unwanted byproduct of the image generation process (e.g., extra limbs, merged objects, etc.)."
                    "\n2. **PRIORITIZE ARTIFACT REMOVAL:** If you identify one or more clear artifacts, your priority is to generate a precise ENGLISH prompt for the 'Kontext' image editing model to REMOVE or CORRECT the MOST OBVIOUS artifact."
                    "\n   - The prompt MUST clearly specify the artifact and the desired outcome (e.g., 'remove the strange silver object from the person's left shoulder', 'correct the distorted fingers on the right hand')."
                    "\n   - Ensure the prompt includes instructions to PRESERVE the rest of the image accurately and maintain the original style and composition."
                    "\n3. **NO ARTIFACTS FOUND:** If, after careful examination, you find NO clear visual artifacts, ONLY THEN should you generate a prompt for subtle, general quality enhancement. This prompt should be something like: 'Slightly enhance the image quality, improving sharpness and detail while fully preserving the original content and style.' Be very cautious not to make drastic changes."
                    "\n\nYour output MUST be ONLY the final English prompt. Do not add any conversational text or explanations."
                )
                messages = [
                    {"role": "system", "content": autofix_system_prompt},
                    {"role": "user", "content": [{"type": "image_url", "image_url": {"url": s3_url}}]}
                ]
            else: # Standard 'edit' mode
                print("!!! Запрос к OpenAI Vision API для Edit (с картинкой)...")
                edit_system_prompt = (
                    "You are an AI image analysis and editing expert. Your task is to combine the visual context of an image with a user's text request to generate a precise, technical, English-language prompt for the 'Kontext' image editing model."
                    "\n\nFollow these rules:"
                    "\n1. **Analyze Both Inputs:** First, analyze the image to understand its contents (e.g., 'a photo of glasses levitating in a forest'). Then, apply the user's text request to this specific visual context."
                    "\n2. **Contextual Application:** If the image shows levitating glasses and the user says 'remove the glasses', the prompt must be about removing levitating glasses from a forest, NOT from a person's face. Be literal to the image content."
                    "\n3. **Preserve Everything Else:** The generated prompt must implicitly or explicitly preserve all other elements of the image that were not mentioned in the user's request. Maintain the original style, composition, and lighting unless asked otherwise."
                    "\n4. **Language:** The user's request can be in any language. Your output prompt MUST be in English."
                    "\n\nYour output MUST be ONLY the final English prompt. Do not add any conversational text, greetings, or explanations."
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

# --- Final app setup ---
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get("PORT", 5001)))
