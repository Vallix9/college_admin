#!/usr/bin/env python3
"""
Скрипт для инициализации базы данных
"""

from app.init_ import create_app, db
from app.models import User

def init_database():
    app = create_app()
    
    with app.app_context():
        # Создание всех таблиц
        db.create_all()
        
        # Создание администратора по умолчанию
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', role='admin')
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
            print("✅ База данных инициализирована!")
            print("📝 Данные для входа:")
            print("   Логин: admin")
            print("   Пароль: admin123")
        else:
            print("✅ База данных уже инициализирована")

if __name__ == '__main__':
    init_database()