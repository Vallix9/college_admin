from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
import os
import re
from datetime import datetime

db = SQLAlchemy()
login_manager = LoginManager()

def create_app(config_class='config.Config'):
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'main.login'
    
    # Импортируем и регистрируем Blueprint
    from app.routes import main
    app.register_blueprint(main)
    
    # Добавляем контекстные процессоры для шаблонов
    @app.context_processor
    def utility_processor():
        def get_log_level(log_text):
            """Определяет уровень лога из текста"""
            if 'ERROR' in log_text or 'CRITICAL' in log_text:
                return 'ERROR'
            elif 'WARNING' in log_text:
                return 'WARNING'
            elif 'DEBUG' in log_text:
                return 'DEBUG'
            else:
                return 'INFO'
        
        def extract_timestamp(log_text):
            """Извлекает временную метку из лога"""
            match = re.search(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', log_text)
            return match.group() if match else 'Неизвестно'
        
        def extract_message(log_text):
            """Извлекает сообщение из лога"""
            # Удаляем временную метку и уровень
            cleaned = re.sub(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}.*?\[(INFO|WARNING|ERROR|DEBUG|CRITICAL)\]\s*', '', log_text)
            return cleaned.strip()
        
        def count_logs_by_level(level, logs=None):
            """Считает логи определенного уровня"""
            if not logs:
                return 0
            return sum(1 for log in logs if get_log_level(log) == level)
        
        def get_log_file_size():
            """Получает размер файла логов"""
            log_file = 'app.log'
            if os.path.exists(log_file):
                return round(os.path.getsize(log_file) / 1024, 2)
            return 0
        
        def get_log_file_mtime():
            """Получает время последнего изменения файла логов"""
            log_file = 'app.log'
            if os.path.exists(log_file):
                mtime = os.path.getmtime(log_file)
                return datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
            return 'Неизвестно'
        
        return {
            'get_log_level': get_log_level,
            'extract_timestamp': extract_timestamp,
            'extract_message': extract_message,
            'count_logs_by_level': count_logs_by_level,
            'get_log_file_size': get_log_file_size,
            'get_log_file_mtime': get_log_file_mtime
        }
    
    return app