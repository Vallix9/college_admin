"""Создание или обновление учётной записи администратора.

Запуск:  python create_admin.py
Логин и пароль берутся из переменных ADMIN_USERNAME / ADMIN_PASSWORD в файле .env
(см. .env.example). Если переменные не заданы, используются admin / admin123.
"""

import os
import sys
import getpass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from werkzeug.security import generate_password_hash

from app.init_ import db, create_app
from app.models import User
from config import load_env_file

load_env_file()

app = create_app()

DEFAULT_USERNAME = 'admin'
DEFAULT_PASSWORD = 'admin123'


def get_credentials():
    username = os.environ.get('ADMIN_USERNAME') or DEFAULT_USERNAME
    password = os.environ.get('ADMIN_PASSWORD')
    if not password:
        password = getpass.getpass(
            f'Пароль для «{username}» (Enter — {DEFAULT_PASSWORD}): '
        ) or DEFAULT_PASSWORD
    return username, password


def set_password(user, password):
    if hasattr(user, 'set_password'):
        user.set_password(password)
    else:
        user.password_hash = generate_password_hash(password)


with app.app_context():
    username, password = get_credentials()
    admin = User.query.filter_by(username=username).first()

    if admin:
        action = 'обновлён' if os.environ.get('ADMIN_PASSWORD') else 'уже существует'
        print(f'ℹ️ Администратор «{username}» {action}')
        if os.environ.get('RESET_ADMIN_PASSWORD'):
            set_password(admin, password)
            db.session.commit()
            print('🔑 Пароль сброшен на значение из ADMIN_PASSWORD')
    else:
        admin = User(username=username, role='admin')
        set_password(admin, password)
        db.session.add(admin)
        db.session.commit()
        print(f'✅ Администратор создан: {username}')
        print('🔑 Пароль взят из ADMIN_PASSWORD в .env — смените его после первого входа')

    print(f'👥 Всего пользователей в базе: {User.query.count()}')
