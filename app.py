import os
import replicate
from flask import Flask, request, jsonify, render_template_string

# Инициализируем Flask приложение
app = Flask(__name__)

# Получаем API токен из переменных окружения
REPLICATE_API_TOKEN = os.environ.get('REPLICATE_API_TOKEN')

# HTML-шаблон для нашей главной страницы. Весь визуал находится здесь.
INDEX_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
