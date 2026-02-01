import os
from datetime import timedelta

basedir = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-123'
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(basedir, 'college.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Настройки сессии
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    
    # Настройки загрузки файлов
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB
    UPLOAD_FOLDER = os.path.join(basedir, 'uploads')
    
    # Настройки резервного копирования
    BACKUP_FOLDER = os.path.join(basedir, 'backups')
    
    @staticmethod
    def init_app(app):
        # Создаем необходимые директории
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        os.makedirs(app.config['BACKUP_FOLDER'], exist_ok=True)
        os.makedirs(os.path.join(basedir, 'exports'), exist_ok=True)
        os.makedirs(os.path.join(basedir, 'logs'), exist_ok=True)