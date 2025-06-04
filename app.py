import os
import boto3
import uuid
import requests
import time
import openai
from flask import Flask, request, jsonify, render_template_string, url_for, redirect
from flask_sqlalchemy import SQLAlchemy
from flask_security import Security, SQLAlchemyUserDatastore, UserMixin, RoleMixin, login_required, current_user
from flask_security.utils import hash_password
from flask_mail import Mail
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import relationship
from flask_security import AsaList


# --- Настройки для подключения к Amazon S3 ---
AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
AWS_S3_BUCKET_NAME = os.environ.get('AWS_S3_BUCKET_NAME')
AWS_S3_REGION = os.environ.get('AWS_S3_REGION')

# Инициализируем Flask приложение
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'YOUR_VERY_SECRET_KEY_HERE_CHANGE_ME_IN_PROD')
app.config['SECURITY_PASSWORD_SALT'] = os.environ.get('FLASK_SECURITY_PASSWORD_SALT', 'YOUR_VERY_SECRET_SALT_HERE_CHANGE_ME_IN_PROD')

app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///site.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

app.config['SECURITY_REGISTERABLE'] = True
app.config['SECURITY_SEND_REGISTER_EMAIL'] = False
app.config['SECURITY_RECOVERABLE'] = True
app.config['SECURITY_CHANGEABLE'] = True
app.config['SECURITY_CONFIRMABLE'] = False
app.config['SECURITY_USERNAME_ENABLE'] = True
app.config['SECURITY_USERNAME_REQUIRED'] = False
app.config['SECURITY_EMAIL_VALIDATOR_ARGS'] = {"check_deliverability": False}
app.config['SECURITY_POST_LOGIN_VIEW'] = '/'
app.config['SECURITY_POST_LOGOUT_VIEW'] = '/'
app.config['SECURITY_POST_REGISTER_VIEW'] = '/'
app.config['SECURITY_LOGIN_URL'] = '/login'
app.config['SECURITY_LOGOUT_URL'] = '/logout'
app.config['SECURITY_REGISTER_URL'] = '/register'
app.config['SECURITY_CHANGE_URL'] = '/change-password'
app.config['SECURITY_RESET_URL'] = '/reset-password'

app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.googlemail.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', 'true').lower() == 'true'
app.config['MAIL_USE_SSL'] = os.environ.get('MAIL_USE_SSL', 'false').lower() == 'true'
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER', ("Changer AI", 'noreply@example.com'))
app.config['SECURITY_EMAIL_SENDER'] = app.config['MAIL_DEFAULT_SENDER']

db = SQLAlchemy(app)
mail = Mail(app)

roles_users = db.Table(
    "roles_users",
    db.Column("user_id", db.Integer(), db.ForeignKey("user.id")),
    db.Column("role_id", db.Integer(), db.ForeignKey("role.id")),
)

class Role(db.Model, RoleMixin):
    id = db.Column(db.Integer(), primary_key=True)
    name = db.Column(db.String(80), unique=True)
    description = db.Column(db.String(255))
    permissions = db.Column(MutableList.as_mutable(AsaList()), nullable=True)

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    username = db.Column(db.String(255), unique=True, nullable=True)
    password = db.Column(db.String(255), nullable=False)
    active = db.Column(db.Boolean(), default=True)
    fs_uniquifier = db.Column(db.String(255), unique=True, nullable=False)
    confirmed_at = db.Column(db.DateTime())
    roles = db.relationship(
        "Role", secondary=roles_users, backref=db.backref("users", lazy="dynamic")
    )
    token_balance = db.Column(db.Integer, default=10, nullable=False)

user_datastore = SQLAlchemyUserDatastore(db, User, Role)
security = Security(app, user_datastore)

app.static_folder = 'static'

REPLICATE_API_TOKEN = os.environ.get('REPLICATE_API_TOKEN')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

if OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY
else:
    print("!!! ВНИМАНИЕ: OPENAI_API_KEY не найден. Улучшение промптов не будет работать.")

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
            --controls-bg-color: #F8F8F8;
            --controls-bg-color-transparent: rgba(248, 248, 248, 0.8);
            --blur-intensity: 8px;
            --mob-spacing-unit: 5px;
            --desktop-spacing-unit: 8px;
            --download-icon-size: 28px; 
            
            --header-text-color-on-light-bg: #333333;
            --header-border-radius: 22px; 
            --coin-color: #D9F47A; 
            --header-vertical-padding: 15px; 
            --header-logo-height-mob: 30px;
            --header-logo-height-desk: 35px;
            
            --footer-height-mob: 70px; 
            --action-buttons-height-mob: 50px; 
            --footer-height-desk: 80px;
            --action-buttons-height-desk: 60px; 
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
            overflow-x: hidden;
        }
        
        .app-container-wrapper {
            position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background-size: cover; background-position: center center; background-repeat: no-repeat;
            z-index: -1; transition: filter 0.4s ease-in-out;
            background-image: url("{{ url_for('static', filename='images/MOB_BACK.png') }}"); 
        }
        .app-container-wrapper.bg-blur { filter: blur(var(--blur-intensity)); }

        .app-container {
            width: 100%; max-width: 1200px; margin: 0 auto; 
            padding-left: var(--mob-spacing-unit);
            padding-right: var(--mob-spacing-unit);
            padding-top: calc(var(--header-logo-height-mob) + var(--header-vertical-padding) * 2 + var(--mob-spacing-unit)); 
            padding-bottom: calc(var(--footer-height-mob) + var(--action-buttons-height-mob) + var(--mob-spacing-unit) * 2); 
            display: flex; flex-direction: column; align-items: center;
            flex-grow: 1; position: relative; z-index: 1;
        }

        /* --- Новый Хедер --- */
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
            padding: var(--header-vertical-padding) 0; 
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .app-logo-link { display: inline-block; line-height: 0; } 
        .logo { height: var(--header-logo-height-mob); cursor: pointer; display: block;}

        .top-right-nav { position: relative; display: flex; align-items: center; }

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
            background-color: rgba(248, 248, 248, 0.9); 
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
            margin-bottom: 10px; padding-bottom: 8px; border-bottom: 1px solid rgba(0,0,0,0.08);
        }
        .dropdown-user-email { 
            color: #333; font-size: 0.9rem; font-weight: bold;
            overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
            flex-grow: 1; 
        }
        .close-menu-btn { background: none; border: none; padding: 0; cursor: pointer; display: flex; align-items: center; justify-content: center; width:20px; height:20px;}
        .close-menu-btn svg { stroke: #555; stroke-width:10; stroke-linecap:round; }
        .dropdown-menu ul { list-style: none; padding: 0; margin: 0; }
        .dropdown-menu li a {
            display: block; padding: 8px 0; color: #333; text-decoration: none;
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

        .app-main {
            width: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: flex-start; 
            flex-grow: 1;
            padding-top: var(--mob-spacing-unit);
            gap: var(--mob-spacing-unit); 
        }
        
        .initial-top-group { 
            display: flex; flex-direction: column; align-items: center;
            gap: var(--mob-spacing-unit); 
            width: 100%;
            margin-top: 30px; 
        }
        .desktop-main-text-img { 
            display: none; 
            max-width: 1200px;
            width: auto;
            max-height: 85vh;
            object-fit: contain;
            margin-top: 12vh; 
        }
        .mobile-main-text-img { 
            display: block; 
            max-height: 20vh; 
            max-width: 90%; object-fit: contain; 
        }

        .image-drop-area-mobile {
            width: 80%; max-width: 280px; height: 165px; background-color: transparent; 
            border-radius: 25px; display: flex; justify-content: center; align-items: center;
            cursor: pointer; position: relative; overflow: hidden; 
            border: 2px dashed rgba(248, 248, 248, 0.3); 
            margin-top: 40px; 
        }
        .image-drop-area-mobile.dragover { border-color: var(--text-accent-color); background-color: rgba(217, 244, 122, 0.1); }
        .image-drop-area-mobile .mob-drop-placeholder-img { 
            width: auto; max-width: 80%; max-height: 40%; height: auto; object-fit: contain; 
        }
        .image-drop-area-mobile::before { 
            content: ""; position: absolute; top: 0; left: 0; right: 0; bottom: 0;
            background-color: rgba(248, 248, 248, 0.1); 
            backdrop-filter: blur(4px); -webkit-backdrop-filter: blur(4px);
            z-index: -1; border-radius: inherit;
        }
        .image-drop-area-mobile .image-preview-mobile-img {
            display: none; width: 100%; height: 100%; object-fit: cover;
            border-radius: inherit; position: relative; z-index: 1;
        }
        
        .action-buttons { 
            display: flex; justify-content: center; align-items: center;
            gap: 10px; 
            flex-wrap: nowrap; 
            width: calc(100% - calc(2 * var(--mob-spacing-unit))); 
            max-width: 320px; 
            padding: 5px 0; 
            position: fixed; 
            bottom: calc(5vh + var(--footer-height-mob) - 10px); 
            left: 50%; transform: translateX(-50%);
            z-index: 99; 
        }
        .action-btn img { 
            height: 22px; width: auto; max-width: 70px; 
            object-fit: contain; cursor: pointer; transition: transform 0.2s ease;
            display: block; visibility: visible; 
        }
        .action-btn img:hover { transform: scale(1.05); }

        .result-image-wrapper {
             justify-content: center; flex-grow: 1; display: inline-flex; 
             align-items: center; width: auto; max-width: 100%; 
             position: relative; 
             margin-bottom: calc(var(--download-icon-size) + 5px + 5px); /* ИЗМЕНЕНИЕ: Уменьшен отступ для кнопки скачать */
        }
        #result-image {
            max-width: 90vw; max-height: 60vh; object-fit: contain;
            border-radius: 12px; box-shadow: 0 6px 20px rgba(0,0,0,0.25); 
            display: block; 
        }

        .download-action-link {
            display: none; position: absolute;
            bottom: calc(-1 * (var(--download-icon-size) + 5px)); /* ИЗМЕНЕНИЕ: Уменьшен отступ кнопки скачать */
            right: 0; z-index: 10; cursor: pointer;
            padding: 5px; line-height: 0; 
        }
        .download-button-icon { 
            height: var(--download-icon-size); width: var(--download-icon-size); display: block;
        }

        .loader-container {
            justify-content: center; align-items: center; min-height: 200px; 
            z-index: 101; flex-grow: 1; display: flex; 
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

        .app-footer { 
            width: calc(100% - calc(2 * var(--mob-spacing-unit))); 
            max-width: 500px; padding: 0; position: fixed;
            bottom: 5vh; 
            left: 50%;
            transform: translateX(-50%); z-index: 100;
        }
        .input-area { 
            display: flex; align-items: center;
            background-color: var(--controls-bg-color-transparent); 
            backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
            border-radius: 50px; padding: 6px 8px; width: 100%;
            box-shadow: 0 4px 15px rgba(0,0,0,0.1);
            border: 2px dashed transparent; 
        }
        .input-area.dragover { border-color: var(--text-accent-color); }
        #image-file-common { display: none; } 
        .file-upload-label-desktop { display: none; }
        #prompt {
            flex-grow: 1; border: none; padding: 12px 10px; font-size: 0.9rem; 
            background-color: transparent; outline: none; color: #333333; 
            font-family: 'ChangerFont', sans-serif; line-height: 1.3;
        }
        #prompt::placeholder { color: #888888; opacity: 1; }
        .submit-button-element { 
            background-color: transparent; border: none; cursor: pointer; padding: 0;
            margin-left: 8px; display: flex; align-items: center; justify-content: center;
            flex-shrink: 0; 
        }
        .submit-button-icon-img { height: 40px; width: 40px; }
        .submit-button-text-content { display: none; }

        .error-message {
            display: none; margin-top: 10px; font-size: 0.9rem; color: var(--text-accent-color); 
            background-color: rgba(0,0,0,0.65); backdrop-filter: blur(5px);
            padding: 10px 15px; border-radius: 8px; position: fixed;
            bottom: calc(var(--footer-height-mob) + var(--action-buttons-height-mob) + var(--mob-spacing-unit) * 2); 
            left: 50%; transform: translateX(-50%);
            width: calc(100% - calc(4 * var(--mob-spacing-unit)));
            max-width: 480px; z-index: 105; text-align: center;
        }

        /* --- Desktop Styles --- */
        @media (min-width: 769px) {
            .app-container-wrapper { background-image: url("{{ url_for('static', filename='images/DESK_BACK.png') }}"); }
            .app-container { 
                padding-left: var(--desktop-spacing-unit);
                padding-right: var(--desktop-spacing-unit);
                padding-top: calc(var(--header-logo-height-desk) + var(--header-vertical-padding) * 2 + var(--desktop-spacing-unit)); 
                padding-bottom: calc(var(--footer-height-desk) + var(--action-buttons-height-desk) + var(--desktop-spacing-unit) * 2); 
            }
            .page-header-inner {
                padding: var(--header-vertical-padding) 0; 
            }
            .logo { height: var(--header-logo-height-desk); }
            .app-main { 
                gap: var(--desktop-spacing-unit);
                 padding-bottom: calc(var(--footer-height-desk) + var(--action-buttons-height-desk) + var(--desktop-spacing-unit)); 
            }
            
            .initial-top-group { 
                gap: var(--desktop-spacing-unit); 
                margin-top: 40px; 
            }
            .mobile-main-text-img { display: none; }
            .desktop-main-text-img { display: block; }
            .image-drop-area-mobile { display: none; } 
            
            .action-buttons { 
                gap: 25px; 
                max-width: 700px; 
                bottom: calc(5vh + var(--footer-height-desk) - 15px); 
            }
            .action-btn img { height: calc(48px / 2); max-width: 120px; }
            
            .download-action-link { }
            #result-image { max-height: 60vh; }
            .app-footer { 
                max-width: 700px;
                bottom: 5vh; 
            }
            .input-area { padding: 10px 12px; border-radius: 30px; }
            .file-upload-label-desktop { 
                display: flex; cursor: pointer; padding: 0; margin-right: 12px;
                align-items: center; justify-content: center; position: relative;
                width: calc(56px / 1.5); height: calc(56px / 1.5); 
                background-color: transparent; border-radius: 12px; 
                flex-shrink: 0; overflow: hidden;
            }
            .upload-icon-desktop-img { height: 100%; width: 100%; object-fit: contain;}
            .image-preview-desktop-img { display: none; width: 100%; height: 100%; object-fit: cover; border-radius: inherit;}
            #prompt { padding: 15px 15px; font-size: 1rem;}
            .submit-button-icon-img { height: 48px; width: 48px;}

            .user-controls-loggedin { gap: 15px; padding: 10px 10px 10px 20px; }
            .token-display { font-size: 1rem; } 
            .token-coin { width: 20px; height: 20px; }
            .burger-menu-btn { width: 42px; height: 42px; }
            .user-controls-loggedout { padding: 12px 25px; }
            .user-controls-loggedout .auth-button { font-size: 1rem; }
            .error-message { bottom: calc(var(--footer-height-desk) + var(--action-buttons-height-desk) + var(--desktop-spacing-unit) * 2); }
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
                {% if current_user.is_authenticated %}
                    <div class="user-controls-loggedin">
                        <span class="token-display">
                            <span id="token-balance-display">{{ current_user.token_balance }}</span>
                            <span class="token-coin"></span>
                        </span>
                        <button class="burger-menu-btn" id="burger-menu-toggle" aria-label="Меню пользователя" aria-expanded="false">
                            <svg class="burger-icon" viewBox="0 0 100 80"> 
                                <rect class="line line1" x="0" y="0" width="100" height="12" rx="6"></rect>
                                <rect class="line line2" x="0" y="34" width="100" height="12" rx="6"></rect>
                                <rect class="line line3" x="0" y="68" width="100" height="12" rx="6"></rect>
                            </svg>
                             <svg class="close-icon" viewBox="0 0 80 80"> 
                                <line class="line" x1="20" y1="20" x2="60" y2="60"/>
                                <line class="line" x1="60" y1="20" x2="20" y2="60"/>
                            </svg>
                        </button>
                    </div>
                    <div class="dropdown-menu" id="dropdown-menu">
                        <div class="dropdown-header">
                             <span class="dropdown-user-email">{{ current_user.email or current_user.username }}</span>
                            <button class="close-menu-btn" id="close-menu-btn-inner" aria-label="Закрыть меню">
                                 <svg viewBox="0 0 100 100" width="18" height="18">
                                    <line x1="10" y1="10" x2="90" y2="90"/>
                                    <line x1="10" y1="90" x2="90" y2="10"/>
                                </svg>
                            </button>
                        </div>
                        <ul>
                            <li><a href="{{ url_for('buy_tokens_page') }}">пополнить баланс</a></li>
                            <li><a href="{{ url_for_security('change_password') }}">Сменить пароль</a></li>
                            <li><a href="{{ url_for_security('logout') }}">Выйти</a></li>
                        </ul>
                    </div>
                {% else %}
                    <div class="user-controls-loggedout">
                        <a href="{{ url_for_security('login') }}" class="auth-button">Логин</a>
                        <span class="auth-separator">|</span>
                        <a href="{{ url_for_security('register') }}" class="auth-button">Регистрация</a>
                    </div>
                {% endif %}
            </div>
        </div>
    </div>

    <div class="app-container">
        <main class="app-main auth-required-content">
            <div class="initial-top-group">
                <img src="{{ url_for('static', filename='images/DESK_MAIN.png') }}" alt="Change Everything" class="main-text-display-img desktop-main-text-img">
                <img src="{{ url_for('static', filename='images/MOB_MAIN.svg') }}" alt="Change Everything" class="main-text-display-img mobile-main-text-img">

                <label for="image-file-common" class="image-drop-area-mobile">
                    <img src="{{ url_for('static', filename='images/JDTI.png') }}" alt="Just drop the image" class="mob-drop-placeholder-img">
                    <img id="image-preview-mobile" src="#" alt="Preview" class="image-preview-mobile-img">
                </label>
            </div>

            <div class="result-image-wrapper">
                <img id="result-image" src="" alt="Generated Image">
                <a href="#" id="download-action" class="download-action-link" download="generated_image.png" target="_blank" rel="noopener noreferrer">
                    <img src="{{ url_for('static', filename='images/Download.png') }}" alt="Скачать" class="download-button-icon">
                </a>
            </div>
            
            <div id="loader" class="loader-container">
                <div class="pulsating-dot"></div>
            </div>

            <div class="action-buttons">
                <div class="action-btn" data-action="create"><img src="{{ url_for('static', filename='images/Create.png') }}" alt="Create"></div>
                <div class="action-btn" data-action="relight"><img src="{{ url_for('static', filename='images/relight.png') }}" alt="Relight"></div>
                <div class="action-btn" data-action="remove"><img src="{{ url_for('static', filename='images/remove.png') }}" alt="Remove"></div>
                <div class="action-btn" data-action="change"><img src="{{ url_for('static', filename='images/change.png') }}" alt="Change"></div>
            </div>
        </main>

        <footer class="app-footer auth-required-content">
            <form id="edit-form" class="input-area">
                <label for="image-file-common" class="file-upload-label-desktop">
                    <img src="{{ url_for('static', filename='images/DESK_UPLOAD.png') }}" alt="Upload Icon" class="upload-icon-desktop-img">
                    <img id="image-preview-desktop" src="#" alt="Preview" class="image-preview-desktop-img">
                </label>
                <input type="file" id="image-file-common" name="image" accept="image/*">
                
                <input type="text" id="prompt" name="prompt" placeholder="TYPE WHAT YOU WANT TO CHANGE">
                
                <button type="submit" id="submit-button" class="submit-button-element">
                    <img src="{{ url_for('static', filename='images/MAGIC_GREEN.png') }}" alt="Generate" id="magic-button-icon-img" class="submit-button-icon-img">
                    <span id="submit-button-text-content" class="submit-button-text-content">Start over</span>
                </button>
            </form>
        </footer>
        <div id="error-box" class="error-message"></div>
    </div>

    <script>
    // --- DOM Elements ---
    const tokenBalanceDisplaySpan = document.getElementById('token-balance-display'); 
    const burgerMenuToggle = document.getElementById('burger-menu-toggle');
    const dropdownMenu = document.getElementById('dropdown-menu');
    const closeMenuBtnInner = document.getElementById('close-menu-btn-inner');
    const burgerIconSvg = burgerMenuToggle ? burgerMenuToggle.querySelector('.burger-icon') : null;
    const closeIconSvg = burgerMenuToggle ? burgerMenuToggle.querySelector('.close-icon') : null;


    function updateTokenBalanceDisplay(newBalance) {
        if (tokenBalanceDisplaySpan) {
            tokenBalanceDisplaySpan.textContent = newBalance;
        }
    }

    if (burgerMenuToggle && dropdownMenu) {
        burgerMenuToggle.addEventListener('click', (e) => {
            e.stopPropagation(); 
            const isOpen = burgerMenuToggle.classList.contains('open');
            burgerMenuToggle.setAttribute('aria-expanded', String(!isOpen));
            dropdownMenu.classList.toggle('open');
            burgerMenuToggle.classList.toggle('open'); 
        });
    }
    if (closeMenuBtnInner && dropdownMenu && burgerMenuToggle) {
         closeMenuBtnInner.addEventListener('click', (e) => {
            e.stopPropagation();
            burgerMenuToggle.setAttribute('aria-expanded', 'false');
            dropdownMenu.classList.remove('open');
            burgerMenuToggle.classList.remove('open');
        });
    }
    
    document.addEventListener('click', function(event) {
        if (dropdownMenu && burgerMenuToggle && dropdownMenu.classList.contains('open')) {
            const isClickInsideMenu = dropdownMenu.contains(event.target);
            const isClickOnBurger = burgerMenuToggle.contains(event.target) || (burgerMenuToggle.querySelector('svg') && burgerMenuToggle.querySelector('svg').contains(event.target));
            if (!isClickInsideMenu && !isClickOnBurger) {
                burgerMenuToggle.setAttribute('aria-expanded', 'false');
                dropdownMenu.classList.remove('open');
                burgerMenuToggle.classList.remove('open');
            }
        }
    });

    // --- Остальной JavaScript код ---
    const appBgWrapper = document.getElementById('app-bg-wrapper');
    const imageFileInput = document.getElementById('image-file-common');
    
    const mobileDropArea = document.querySelector('.image-drop-area-mobile');
    const mobileDropPlaceholderImg = document.querySelector('.mob-drop-placeholder-img');
    const mobileImagePreviewImg = document.getElementById('image-preview-mobile');
    
    const desktopUploadLabel = document.querySelector('.file-upload-label-desktop');
    const desktopUploadIconImg = document.querySelector('.upload-icon-desktop-img');
    const desktopImagePreviewImg = document.getElementById('image-preview-desktop');

    const editForm = document.getElementById('edit-form');
    const promptInput = document.getElementById('prompt');
    const inputArea = document.querySelector('.input-area'); 
    const submitButton = document.getElementById('submit-button');
    const magicButtonIconImg = document.getElementById('magic-button-icon-img');
    const submitButtonTextContent = document.getElementById('submit-button-text-content');

    const initialTopGroup = document.querySelector('.initial-top-group'); 
    const resultImageWrapper = document.querySelector('.result-image-wrapper');
    const resultImage = document.getElementById('result-image');
    const downloadLink = document.getElementById('download-action'); 
    const loaderContainer = document.getElementById('loader');
    const actionButtonsContainer = document.querySelector('.action-buttons');

    const mobileMainTextImg = document.querySelector('.mobile-main-text-img');
    const desktopMainTextImg = document.querySelector('.desktop-main-text-img');
    const errorBox = document.getElementById('error-box');

    let currentView = 'initial'; 

    function isDesktopView() {
        return window.innerWidth > 768;
    }

    function showError(message) {
        if(errorBox) {
            errorBox.textContent = message;
            errorBox.style.display = 'block';
            setTimeout(() => { errorBox.style.display = 'none'; }, 4000);
        } else {
            console.error("Error box not found for message:", message);
        }
    }

    function updateView(viewName) {
        currentView = viewName;
        
        const desktopUploadIcon = inputArea.querySelector('.file-upload-label-desktop');

        if(appBgWrapper) appBgWrapper.classList.remove('bg-blur');
        
        if (initialTopGroup) initialTopGroup.style.display = 'none';
        if (resultImageWrapper) resultImageWrapper.style.display = 'none';
        if (loaderContainer) loaderContainer.style.display = 'none';
        if (downloadLink) downloadLink.style.display = 'none'; 
        
        if (actionButtonsContainer) { 
            if (viewName === 'loading') {
                actionButtonsContainer.style.display = 'none';
            } else {
                actionButtonsContainer.style.display = 'flex';
            }
        }

        if (mobileMainTextImg) mobileMainTextImg.style.display = 'none'; 
        if (desktopMainTextImg) desktopMainTextImg.style.display = 'none'; 
        if (mobileDropArea) mobileDropArea.style.display = 'none'; 

        // Reset common elements for input area before specific view logic
        if (promptInput) promptInput.style.display = 'block'; // Default to visible
        if (submitButton) {
            submitButton.style.flexGrow = '0'; // Default to no grow
            submitButton.style.justifyContent = 'center'; // Default justification
        }
        if(desktopUploadIcon) desktopUploadIcon.style.display = isDesktopView() ? 'flex' : 'none';


        if (viewName === 'initial') {
            if (initialTopGroup) initialTopGroup.style.display = 'flex';
            if (isDesktopView()) {
                if (desktopMainTextImg) desktopMainTextImg.style.display = 'block';
                if(desktopUploadIcon) desktopUploadIcon.style.display = 'flex';
            } else { 
                if (mobileMainTextImg) mobileMainTextImg.style.display = 'block';
                if (mobileDropArea) mobileDropArea.style.display = 'flex';
                if(desktopUploadIcon) desktopUploadIcon.style.display = 'none';
            }
            if (magicButtonIconImg) magicButtonIconImg.style.display = 'block';
            if (submitButtonTextContent) {
                submitButtonTextContent.style.display = 'none';
                submitButtonTextContent.textContent = 'Start over'; // Reset text
            }
            if (submitButton) submitButton.dataset.action = "generate";
            if (promptInput) {
                 promptInput.value = ''; 
                 promptInput.placeholder = "TYPE WHAT YOU WANT TO CHANGE";
            }
            resetImagePreviews();
        } else if (viewName === 'loading') {
            if (loaderContainer) loaderContainer.style.display = 'flex';
            if (appBgWrapper) appBgWrapper.classList.add('bg-blur'); 
            if (initialTopGroup) initialTopGroup.style.display = 'none';
            if (resultImageWrapper) resultImageWrapper.style.display = 'none';
        } else if (viewName === 'result') {
            if (resultImageWrapper) resultImageWrapper.style.display = 'inline-flex'; 
            if (downloadLink) downloadLink.style.display = 'block'; 
            if (appBgWrapper) appBgWrapper.classList.add('bg-blur'); 

            if (!isDesktopView()) { // Mobile specific changes for result view
                if (magicButtonIconImg) magicButtonIconImg.style.display = 'none';
                if (submitButtonTextContent) {
                    submitButtonTextContent.style.display = 'block'; 
                    submitButtonTextContent.textContent = 'Начать заново'; 
                }
                if (submitButton) {
                    submitButton.dataset.action = "startover";
                    submitButton.style.flexGrow = '1'; 
                    submitButton.style.justifyContent = 'center'; 
                }
                if (promptInput) {
                    promptInput.value = ''; 
                    promptInput.style.display = 'none'; 
                }
                if(desktopUploadIcon) desktopUploadIcon.style.display = 'none';
            } else { // Desktop specific changes for result view
                if (magicButtonIconImg) magicButtonIconImg.style.display = 'block';
                if (submitButtonTextContent) submitButtonTextContent.style.display = 'none';
                if (submitButton) {
                    submitButton.dataset.action = "generate"; 
                    // flexGrow and justifyContent reset to default by common reset code above
                }
                if (promptInput) {
                    // promptInput.value = ''; // Optionally clear prompt on desktop
                    promptInput.style.display = 'block';
                }
                if(desktopUploadIcon) desktopUploadIcon.style.display = 'flex';
            }
        }
        adjustLayoutForResultImage();
    }
    
    function adjustLayoutForResultImage() {
        if (currentView === 'result' && resultImage && resultImage.src && resultImage.src !== window.location.href + "#") {
            const img = new Image();
            img.onload = () => {
                resultImage.classList.remove('result-aspect-portrait', 'result-aspect-landscape');
                if (img.naturalWidth < img.naturalHeight) { 
                    resultImage.classList.add('result-aspect-portrait');
                } else { 
                    resultImage.classList.add('result-aspect-landscape');
                }
            }
            img.src = resultImage.src;
        }
    }

    window.addEventListener('resize', () => {
        // Reset elements in inputArea before calling updateView to handle responsive changes correctly
        const desktopUploadIcon = inputArea.querySelector('.file-upload-label-desktop');
        if (promptInput) promptInput.style.display = 'block'; 
        if (submitButton) {
            submitButton.style.flexGrow = '0'; 
            submitButton.style.justifyContent = 'center'; 
        }
        if (magicButtonIconImg) magicButtonIconImg.style.display = 'block';
        if (submitButtonTextContent) submitButtonTextContent.style.display = 'none';
        if(desktopUploadIcon) desktopUploadIcon.style.display = isDesktopView() ? 'flex' : 'none';
        
        updateView(currentView); 
    });
    
    function handleFileSelect(file) {
        if (file && imageFileInput) { 
            const dataTransfer = new DataTransfer();
            dataTransfer.items.add(file);
            imageFileInput.files = dataTransfer.files;
            
            const event = new Event('change', { bubbles: true });
            imageFileInput.dispatchEvent(event);
        }
    }

    function setupDragAndDrop(dropZoneElement, isPromptArea = false) {
        if (!dropZoneElement) return;

        dropZoneElement.addEventListener('dragover', (event) => {
            event.preventDefault();
            event.stopPropagation();
            dropZoneElement.classList.add('dragover');
        });

        dropZoneElement.addEventListener('dragleave', (event) => {
            event.preventDefault();
            event.stopPropagation();
            dropZoneElement.classList.remove('dragover');
        });

        dropZoneElement.addEventListener('drop', (event) => {
            event.preventDefault();
            event.stopPropagation();
            dropZoneElement.classList.remove('dragover');
            
            if (event.dataTransfer.files && event.dataTransfer.files[0]) {
                handleFileSelect(event.dataTransfer.files[0]);
                if (isPromptArea && isDesktopView()) {
                    if (desktopImagePreviewImg && desktopUploadIconImg) {
                        const reader = new FileReader();
                        reader.onload = function(e_preview) {
                            desktopImagePreviewImg.src = e_preview.target.result;
                            desktopImagePreviewImg.style.display = 'block';
                            desktopUploadIconImg.style.display = 'none';
                        }
                        reader.readAsDataURL(event.dataTransfer.files[0]);
                    }
                }
            }
        });
    }

    if (mobileDropArea) setupDragAndDrop(mobileDropArea);
    if (desktopUploadLabel) setupDragAndDrop(desktopUploadLabel);
    if (inputArea) setupDragAndDrop(inputArea, true); 


    if (imageFileInput) {
        imageFileInput.addEventListener('change', function() {
            if (this.files && this.files[0]) {
                const reader = new FileReader();
                reader.onload = function(e) {
                    if (isDesktopView()) {
                        if (desktopImagePreviewImg) {
                            desktopImagePreviewImg.src = e.target.result;
                            desktopImagePreviewImg.style.display = 'block';
                        }
                        if (desktopUploadIconImg) desktopUploadIconImg.style.display = 'none';
                    } else { 
                        if (mobileImagePreviewImg) {
                            mobileImagePreviewImg.src = e.target.result;
                            mobileImagePreviewImg.style.display = 'block';
                            if(mobileDropPlaceholderImg) mobileDropPlaceholderImg.style.display = 'none';
                        }
                    }
                }
                reader.readAsDataURL(this.files[0]);
            } else {
                resetImagePreviews();
            }
        });
    }


    function resetImagePreviews() {
        if (mobileImagePreviewImg && mobileDropPlaceholderImg) {
            mobileImagePreviewImg.src = '#';
            mobileImagePreviewImg.style.display = 'none';
            mobileDropPlaceholderImg.style.display = 'block'; 
        }
        if (desktopImagePreviewImg && desktopUploadIconImg) {
            desktopImagePreviewImg.src = '#';
            desktopImagePreviewImg.style.display = 'none';
            desktopUploadIconImg.style.display = 'block';
        }
        if (imageFileInput) imageFileInput.value = ''; 
    }

    if (editForm) {
        editForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            
            if (submitButton.dataset.action === "startover") {
                updateView('initial');
                return;
            }

            if (!imageFileInput || !imageFileInput.files || imageFileInput.files.length === 0) {
                showError("Пожалуйста, выберите файл для загрузки.");
                return;
            }
            if (!promptInput || !promptInput.value.trim()) {
                showError("Пожалуйста, введите текстовый промпт.");
                return;
            }

            if(submitButton) submitButton.disabled = true;
            if (errorBox) errorBox.style.display = 'none';
            updateView('loading');

            const formData = new FormData();
            formData.append('image', imageFileInput.files[0]);
            formData.append('prompt', promptInput.value);
            
            try {
                const response = await fetch("{{ url_for('process_image') }}", {
                    method: 'POST',
                    body: formData
                });
                const data = await response.json();
                
                if (!response.ok) {
                    let errorDetail = data.error || data.detail || 'Неизвестная ошибка сервера';
                    if (response.status === 403 && (data.error === 'Недостаточно токенов' || data.detail === 'Недостаточно токенов')) { 
                         errorDetail = 'У вас недостаточно токенов для генерации. Пожалуйста, пополните баланс.';
                    }
                    throw new Error(errorDetail);
                }


                if(resultImage) resultImage.src = data.output_url;
                if(downloadLink) downloadLink.href = data.output_url;
                if (data.new_token_balance !== undefined) { 
                    updateTokenBalanceDisplay(data.new_token_balance);
                }
                
                const tempImg = new Image();
                tempImg.onload = () => {
                    updateView('result');
                };
                tempImg.onerror = () => { 
                    showError("Не удалось загрузить сгенерированное изображение.");
                    updateView('initial');
                };
                tempImg.src = data.output_url;
                
            } catch (error) {
                console.error('Ошибка при отправке/обработке:', error);
                showError("Произошла ошибка: " + error.message);
                updateView('initial'); 
            } finally {
                if(submitButton) submitButton.disabled = false;
            }
        });
    }

    document.querySelectorAll('.action-btn').forEach(button => {
        button.addEventListener('click', (e) => {
            const action = e.currentTarget.dataset.action;
            let prefillText = "";
            if (action === "create") {
                prefillText = "Based on a [object] from the image create [describe the scene]";
            } else if (action === "relight") {
                prefillText = "Relight [object or scene] and make it [describe the light]";
            } else if (action === "remove") {
                prefillText = "Remove [object] from the image";
            } else if (action === "change") {
                prefillText = "Change the [object you want to be changed] to the [object you want to be added]";
            }
            
            if(promptInput) {
                promptInput.value = prefillText;
                promptInput.focus();
            }
        });
    });
    
    const logoElement = document.querySelector('.logo');
    if (logoElement) {
        logoElement.addEventListener('click', () => {
            if (currentView !== 'loading') { 
                 updateView('initial');
            }
        });
    }
    
    updateView('initial');
    </script>
</body>
</html>
"""

# Маршрут для главной страницы
@app.route('/')
def index():
    return render_template_string(INDEX_HTML)

@app.route('/buy-tokens')
@login_required
def buy_tokens_page():
    return """
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <title>Покупка токенов</title>
            <style>
                body { font-family: sans-serif; margin: 20px; background-color: #f4f4f4; color: #333; }
                .container { background-color: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }
                h1 { color: #333; }
                a { color: #007bff; text-decoration: none; }
                a:hover { text-decoration: underline; }
                .button { display: inline-block; padding: 10px 20px; background-color: #D9F47A; color: #333; border-radius: 5px; text-decoration: none; margin-top:15px;}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Купить токены</h1>
                <p>Привет, {{ current_user.email or current_user.username }}!</p>
                <p>Ваш текущий баланс: <strong>{{ current_user.token_balance }}</strong> токенов.</p>
                <p>Здесь будет информация о пакетах токенов и кнопка для перехода к оплате (например, на Tilda).</p>
                <p><a href="{{ url_for('index') }}">Вернуться на главную</a></p>
            </div>
        </body>
        </html>
    """

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

if __name__ == '__main__':  
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get("PORT", 5001)))
