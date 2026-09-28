from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
import os
import importlib

from app.utils import setup_logging

db = SQLAlchemy()
login_manager = LoginManager()

def resolve_config(config_class):
    """Превращает 'config.Config' в сам класс Config."""
    module_name, _, class_name = config_class.rpartition('.')
    if not module_name:
        return config_class
    return getattr(importlib.import_module(module_name), class_name)

def create_app(config_class='config.Config'):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Создаём каталоги uploads/, backups/, exports/, logs/ — без этого
    # экспорт отчётов и резервное копирование падают с FileNotFoundError
    config_cls = resolve_config(config_class)
    if hasattr(config_cls, 'init_app'):
        config_cls.init_app(app)

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'main.login'
    
    app_logger = setup_logging(app.config['LOG_FOLDER'])
    app_logger.info('Приложение запущено, отладочный режим: %s',
                    'включён' if app.config['DEBUG'] else 'выключен')
    
    # Импортируем и регистрируем Blueprint
    from app.routes import main
    app.register_blueprint(main)
    
    # Хелперы журнала логов для шаблонов берём из utils — там же
    # настроено само логирование
    @app.context_processor
    def utility_processor():
        from app.utils import (get_log_level, extract_timestamp, extract_message,
                               count_logs_by_level, get_log_file_size,
                               get_log_file_mtime)
        return {
            'get_log_level': get_log_level,
            'extract_timestamp': extract_timestamp,
            'extract_message': extract_message,
            'count_logs_by_level': count_logs_by_level,
            'get_log_file_size': get_log_file_size,
            'get_log_file_mtime': get_log_file_mtime
        }
    
    return app
