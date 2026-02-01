# create_admin.py в корневой папке (рядом с run.py)
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import db
from app.models import User
from werkzeug.security import generate_password_hash

# Импортируем приложение Flask правильно
from app import create_app

app = create_app()

with app.app_context():
    # Проверяем, существует ли пользователь admin
    admin = User.query.filter_by(username='admin').first()
    
    if not admin:
        admin = User(username='admin')
        # Если в вашей модели есть метод set_password
        if hasattr(admin, 'set_password'):
            admin.set_password('admin123')
        else:
            # Если нет, устанавливаем напрямую
            admin.password_hash = generate_password_hash('admin123')
        
        db.session.add(admin)
        db.session.commit()
        print("✅ Администратор создан: admin / admin123")
    else:
        print("ℹ️ Администратор уже существует")
    
    # Проверка количества пользователей
    user_count = User.query.count()
    print(f"👥 Всего пользователей в базе: {user_count}")