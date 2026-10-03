from flask import Flask, request
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
import importlib

from app.utils import setup_logging

db = SQLAlchemy()
login_manager = LoginManager()

def resolve_config(config_class):
    """Превращает 'config.Config' в сам класс Config.

    Принимается и готовый класс: тестам нужен свой (с временной базой и
    каталогами), а класс, объявленный прямо в conftest.py, нельзя назвать
    строкой 'conftest.TestConfig' так, чтобы импорт не зависел от того,
    откуда запущен pytest.
    """
    if not isinstance(config_class, str):
        return config_class
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

    @app.after_request
    def add_security_headers(response):
        """Заголовки, защищающие браузер от неоговорённых сценариев.

        Без них форма, вставленная в чужую страницу, спокойно утаскивала
        данные учётной записи, а приложение могло быть встроено в iframe
        на стороннем сайте (clickjacking). Strict-Transport-Security
        добавляется только по HTTPS: по HTTP он бессмысленен и вводит в
        заблуждение при проверке из локальной сети.
        """
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        response.headers.setdefault('Referrer-Policy', 'same-origin')
        # Только frame-ancestors: ограничение script-src сломало бы страницы,
        # где скрипты инлайновые, а это почти все экраны журнала и оценок
        response.headers.setdefault('Content-Security-Policy',
                                     "frame-ancestors 'self'")
        if request.is_secure:
            response.headers.setdefault(
                'Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
        return response
    
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

    # Меню кабинета студента. Отдельное от nav_items_for(): там состав
    # зависит от роли и пополняется админскими разделами, а здесь три
    # фиксированных пункта, одинаковых для всех студентов.
    @app.context_processor
    def portal_menu_processor():
        from flask_login import current_user
        return {'portal_nav_items': portal_items_for(current_user)}

    # Токен для обычных, не-WTForms <form>. Глобального CSRFProtect в
    # проекте нет, поэтому каждый такой POST обязан сам положить токен в
    # тело и проверить его на сервере. Чтобы шаблон не изобретал способ
    # получения токена, отдаём его одним контекстом.
    @app.context_processor
    def csrf_processor():
        from app.forms import DeleteTokenForm
        return {'csrf_token': DeleteTokenForm().csrf_token}

    return app


def portal_items_for(user):
    """Пункты верхнего меню личного кабинета."""
    if not user.is_authenticated or not user.is_student:
        return []
    return [
        {'endpoint': 'main.portal', 'icon': 'bi-speedometer2', 'label': 'Кабинет'},
        {'endpoint': 'main.portal_grades', 'icon': 'bi-journal-check', 'label': 'Оценки'},
        {'endpoint': 'main.portal_attendance', 'icon': 'bi-person-x', 'label': 'Пропуска'},
    ]


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
        return [{'endpoint': 'main.portal', 'icon': 'bi-person-badge',
                 'label': 'Кабинет'},
                {'endpoint': 'main.portal_grades', 'icon': 'bi-journal-check',
                 'label': 'Мои оценки'},
                {'endpoint': 'main.portal_attendance', 'icon': 'bi-person-x',
                 'label': 'Мои пропуска'}]

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

    # Преподавателю журнал действий нужен, чтобы проверить свои записи
    # (кто и когда поставил оценку), но чужи записи role_required и фильтр
    # в самом маршруте ему не показывают
    if not is_admin:
        items.append({'endpoint': 'main.audit_logs', 'icon': 'bi-shield-check',
                      'label': 'Мои действия'})

    if is_admin:
        items.append({'endpoint': 'main.staff', 'icon': 'bi-person-gear',
                      'label': 'Сотрудники'})
        items.append({'endpoint': 'main.accounts', 'icon': 'bi-person-badge',
                      'label': 'Учётные записи'})

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
        {'endpoint': 'main.audit_logs', 'icon': 'bi-shield-check',
         'label': 'Журнал действий'},
    ]
