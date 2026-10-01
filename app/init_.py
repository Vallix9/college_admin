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
    
    # Хелперы журнала логов и расчёта оценок для шаблонов берём из utils —
    # там же настроено само логирование
    @app.context_processor
    def utility_processor():
        from app.utils import (get_log_level, extract_timestamp, extract_message,
                               count_logs_by_level, get_log_file_size,
                               get_log_file_mtime, grade_to_points, average_grade)
        return {
            'get_log_level': get_log_level,
            'extract_timestamp': extract_timestamp,
            'extract_message': extract_message,
            'count_logs_by_level': count_logs_by_level,
            'get_log_file_size': get_log_file_size,
            'get_log_file_mtime': get_log_file_mtime,
            'grade_to_points': grade_to_points,
            'average_grade': average_grade
        }

    # home_url() живёт в routes, но нужен почти каждому шаблону: и меню,
    # и страница 403, и редирект после входа
    @app.context_processor
    def navigation_processor():
        from app.routes import home_url
        return {'home_url': home_url}

    # Список пунктов меню с учётом роли. Собирается здесь, а не в шаблоне,
    # чтобы условия видимости были в одном месте и к ним легко было вернуться
    @app.context_processor
    def menu_processor():
        from flask_login import current_user
        return {'nav_items': nav_items_for(current_user),
                'system_items': system_items_for(current_user),
                'can_manage_data': current_user.is_authenticated
                and current_user.can_manage_data}


    return app


def nav_items_for(user):
    """Пункты бокового меню, доступные пользователю с текущей ролью.

    Студент видит только свой кабинет, преподаватель — учебные разделы без
    обслуживания системы, администратор — всё.
    """
    if not user.is_authenticated:
        return []

    is_admin = user.is_admin
    is_student = user.is_student

    if is_student:
        return [{'endpoint': 'main.my_account', 'icon': 'bi-person-badge',
                 'label': 'Мой кабинет'}]

    items = [
        {'endpoint': 'main.dashboard', 'icon': 'bi-speedometer2',
         'label': 'Дашборд'},
        {'endpoint': 'main.students', 'icon': 'bi-people', 'label': 'Студенты'},
        {'endpoint': 'main.groups', 'icon': 'bi-collection', 'label': 'Группы'},
        {'endpoint': 'main.subjects', 'icon': 'bi-book', 'label': 'Предметы'},
        {'endpoint': 'main.journal', 'icon': 'bi-table', 'label': 'Журнал'},
        {'endpoint': 'main.attendance', 'icon': 'bi-person-x', 'label': 'Пропуски'},
        {'endpoint': 'main.grades', 'icon': 'bi-journal-check', 'label': 'Оценки'},
        {'endpoint': 'main.periods', 'icon': 'bi-calendar-range',
         'label': 'Периоды'},
        {'endpoint': 'main.schedule', 'icon': 'bi-calendar-week',
         'label': 'Расписание'},
        {'endpoint': 'main.reports', 'icon': 'bi-file-earmark-bar-graph',
         'label': 'Отчёты'},
    ]

    if is_admin:
        items.append({'endpoint': 'main.staff', 'icon': 'bi-person-gear',
                      'label': 'Сотрудники'})

    return items


def system_items_for(user):
    """Пункты блока обслуживания системы — только для администратора."""
    if not user.is_authenticated or not user.is_admin:
        return []
    return [
        {'endpoint': 'main.settings', 'icon': 'bi-gear', 'label': 'Настройки'},
        {'endpoint': 'main.settings_backup', 'icon': 'bi-hdd',
         'label': 'Резервные копии'},
        {'endpoint': 'main.settings_import', 'icon': 'bi-file-earmark-arrow-up',
         'label': 'Импорт данных'},
        {'endpoint': 'main.view_logs', 'icon': 'bi-journal-text',
         'label': 'Журнал событий'},
    ]
