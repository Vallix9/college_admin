#!/usr/bin/env python3
"""
Скрипт для инициализации базы данных
"""

from app.init_ import create_app, db
from app.models import User, SystemSettings, Group, Student, Subject, Grade

def init_database():
    app = create_app()
    
    with app.app_context():
        print("🔄 Создание таблиц базы данных...")
        
        # Создание всех таблиц
        db.create_all()
        print("✅ Таблицы созданы")
        
        # Создание администратора по умолчанию
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', role='admin')
            admin.set_password('admin123')
            db.session.add(admin)
            print("✅ Администратор создан")
        else:
            print("ℹ️ Администратор уже существует")
        
        # Создание настроек по умолчанию
        if not SystemSettings.query.first():
            settings = SystemSettings()
            db.session.add(settings)
            print("✅ Настройки системы созданы")
        else:
            print("ℹ️ Настройки системы уже существуют")
        
        db.session.commit()
        print("\n✅ База данных инициализирована!")
        print("📝 Данные для входа:")
        print("   Логин: admin")
        print("   Пароль: admin123")
        
        # Проверка созданных таблиц
        print("\n📊 Проверка таблиц:")
        print(f"   Пользователи: {User.query.count()}")
        print(f"   Настройки: {SystemSettings.query.count()}")
        print(f"   Группы: {Group.query.count()}")
        print(f"   Студенты: {Student.query.count()}")
        print(f"   Предметы: {Subject.query.count()}")
        print(f"   Оценки: {Grade.query.count()}")

if __name__ == '__main__':
    init_database()