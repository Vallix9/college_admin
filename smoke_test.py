"""Дымовой тест: открывает все страницы приложения авторизованным админом.

Запуск:  python smoke_test.py
Ищет ошибки рендеринга (в том числе url_for на несуществующие эндпоинты)
и проверяет ответы health-эндпоинта.

Важно: страница входа тоже отдаёт HTTP 200, поэтому одного кода ответа
недостаточно. Тест отдельно убеждается, что вход состоялся, и что
защищённая страница не перенаправила обратно на /login — иначе все
проверки проходили бы, не проверив ничего.
"""

import atexit
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.init_ import create_app, db
from app.models import (User, Student, Group, Subject, Grade,
                        AcademicPeriod, ScheduleItem, AuditLog)

from config import load_env_file

load_env_file()

app = create_app()
app.config['TESTING'] = True

# Страницы, требующие параметров id — подставляем реальные из базы
with app.app_context():
    student_id = Student.query.first().id if Student.query.first() else 1
    group_id = Group.query.first().id if Group.query.first() else 1
    subject_id = Subject.query.first().id if Subject.query.first() else 1
    period_id = (AcademicPeriod.query.first().id
                 if AcademicPeriod.query.first() else 1)
    grade_id = Grade.query.first().id if Grade.query.first() else 1
    admin_pw = os.environ.get('ADMIN_PASSWORD', 'admin123')
    admin_name = os.environ.get('ADMIN_USERNAME', 'admin')

CSRF_RE = re.compile(r'name="csrf_token"[^>]*value="([^"]+)"')


def is_login_page(resp):
    """Открылась ли вместо запрошенной страницы форма входа.

    Сверяем конечный адрес: неудачный вход тоже оставляет нас на /login,
    а защищённый раздел перенаправляет туда же. Признаки в теле шаблона
    для этой проверки не годились — на странице сотрудников есть и логин,
    и пароль, и токен, и она принималась за форму входа.
    """
    return resp.request.path.rstrip('/') == '/login'


def extract_csrf(html):
    match = CSRF_RE.search(html)
    return match.group(1) if match else None

PAGES = [
    ('/', 'дашборд'),
    ('/students', 'список студентов'),
    (f'/student/{student_id}', 'карточка студента'),
    (f'/students/{student_id}/edit', 'редактирование студента'),
    ('/students/add', 'добавление студента'),
    ('/groups', 'список групп'),
    ('/groups/add', 'добавление группы'),
    (f'/groups/{group_id}/edit', 'редактирование группы'),
    ('/subjects', 'список предметов'),
    ('/subjects/add', 'добавление предмета'),
    (f'/subjects/{subject_id}/edit', 'редактирование предмета'),
    ('/grades', 'журнал оценок (старый)'),
    ('/grades/add', 'добавление оценки'),
    ('/periods', 'учебные периоды'),
    ('/periods/add', 'добавление периода'),
    ('/periods/generate', 'автосоздание периодов'),
    (f'/periods/{period_id}/edit', 'редактирование периода'),
    ('/schedule', 'расписание'),
    (f'/schedule?group_id={group_id}', 'расписание группы из параметра'),
    ('/attendance', 'журнал пропусков'),
    (f'/attendance?group_id={group_id}', 'пропуски группы из параметра'),
    ('/results', 'итоги и свод'),
    (f'/results?group_id={group_id}', 'итоги группы из параметра'),
    ('/accounts', 'учётные записи студентов'),
    ('/reports', 'отчёты'),
    ('/reports/students', 'отчёт по студентам в Excel'),
    (f'/reports/group/{group_id}', 'отчёт по группе в Excel'),
    ('/staff', 'сотрудники'),
    ('/staff/add', 'создание сотрудника'),
    ('/my/password', 'смена собственного пароля'),
    ('/settings', 'настройки'),

    ('/settings/backup', 'резервные копии'),
    ('/settings/import', 'импорт данных'),
    ('/settings/logs', 'журнал событий'),
    ('/settings/logs?level=ERROR', 'журнал: только ошибки'),
    ('/settings/logs?q=%D0%98%D0%B2%D0%B0%D0%BD', 'журнал: поиск'),
    # Фаза 13: журнал действий в базе. Он пуст на чистой базе, поэтому
    # проверяется отрисовка страницы и фильтров, а не содержимое
    ('/audit', 'журнал действий'),
    ('/audit?action=login', 'журнал действий: фильтр по действию'),
    ('/audit?user=admin&q=&date_from=&date_to=', 'журнал действий: фильтры'),
    ('/audit?page=2', 'журнал действий: вторая страница'),
    ('/settings/export-template/students', 'шаблон импорта: студенты'),
    ('/settings/export-template/grades', 'шаблон импорта: оценки'),
    ('/settings/export-template/groups', 'шаблон импорта: группы'),
    ('/settings/export-template/settings', 'шаблон импорта: настройки'),
    ('/api/system/download-logs', 'скачивание журнала'),
    ('/health', 'health-check'),
]

# Кабинет студента (Фаза 12) — отдельный проход: страницы закрыты для
# сотрудника, а пароль студента у теста неизвестен. Сессия подставляется
# напрямую — smoke проверяет отрисовку, а не права (это roles_test).
STUDENT_PAGES = [
    ('/portal', 'кабинет студента'),
    ('/portal/grades', 'мои оценки'),
    ('/portal/grades?view=group', 'сводная по своей группе'),
    ('/portal/attendance', 'мои пропуска'),
    ('/my/password', 'смена пароля студентом'),
    ('/my/account', 'старый адрес кабинета'),
]


def student_session():
    """Клиент, вошедший как студент с учётной записью."""
    with app.app_context():
        student = (Student.query
                   .filter(Student.user_id.isnot(None))
                   .order_by(Student.id).first())
        if student is None:
            return None, None
        return student, student.user_id


def check_pages(client, pages, failures):
    for url, title in pages:
        try:
            resp = client.get(url, follow_redirects=True)
        except Exception as e:
            failures.append((url, f'исключение: {type(e).__name__}: {e}'))
            print(f'✗ {url:42} {title:32} ИСКЛЮЧЕНИЕ: {e}')
            continue

        status = resp.status_code
        content_type = resp.headers.get('Content-Type', '')
        is_binary = not content_type.startswith('text/')
        body = '' if is_binary else resp.get_data(as_text=True)

        if status != 200:
            failures.append((url, status))
            print(f'✗ {url:42} {title:32} HTTP {status}')
        elif is_binary:
            print(f'✓ {url:42} {title:32} {status} (файл, {len(resp.get_data())} байт)')
        elif 'BuildError' in body or 'jinja2.exceptions' in body:
            failures.append((url, 'BuildError в теле ответа'))
            print(f'✗ {url:42} {title:32} BuildError')
        elif 'Internal Server Error' in body:
            failures.append((url, '500 внутри'))
            print(f'✗ {url:42} {title:32} внутренняя ошибка')
        elif is_login_page(resp):
            failures.append((url, 'редирект на страницу входа'))
            print(f'✗ {url:42} {title:32} открывает форму входа вместо страницы')
        else:
            print(f'✓ {url:42} {title:32} {status}')


def audit_max_id():
    """Наибольший id в журнале действий на момент вызова."""
    with app.app_context():
        return db.session.query(db.func.max(AuditLog.id)).scalar() or 0


def clean_audit_log(audit_base):
    """Убрать записи журнала, созданные прогоном.

    Smoke ничего не меняет, но входы и скачивание файлов пишутся в журнал
    действий. Оставленные записи об обходе всех страниц выглядели бы при
    разборе инцидента как след настоящей работы. Регистрируется в atexit,
    чтобы убираться и при раннем выходе, и при падении: неудачный вход не
    должен оставлять в журнале запись о попытке, которой не было.
    """
    with app.app_context():
        for entry in AuditLog.query.filter(AuditLog.id > audit_base).all():
            db.session.delete(entry)
        db.session.commit()


def main():
    audit_base = audit_max_id()
    atexit.register(clean_audit_log, audit_base)
    failures = []

    with app.test_client() as client:
        with app.app_context():
            admin = User.query.filter_by(username=admin_name).first()

        # Вход в систему: сначала получаем csrf_token со страницы входа
        login_page = client.get('/login')
        token = extract_csrf(login_page.get_data(as_text=True))
        if not token:
            print('✗ Не удалось получить csrf_token со страницы входа')
            return 1

        response = client.post('/login', data={
            'username': admin_name,
            'password': admin_pw,
            'csrf_token': token,
        }, follow_redirects=True)
        body = response.get_data(as_text=True)

        if response.status_code != 200:
            failures.append(('ВХОД', response.status_code))
            print('✗ Не удалось войти в систему')
            return finish(failures)
        if is_login_page(response):
            print('✗ Вход не состоялся: сервер вернул форму входа (проверьте пароль ADMIN_PASSWORD)')
            return 1

        # Контрольная проверка: защищённая страница не должна перенаправлять на /login
        probe = client.get('/students', follow_redirects=False)
        if probe.status_code in (301, 302):
            failures.append(('АВТОРИЗАЦИЯ', f'/students → {probe.headers.get("Location")}'))
            print('✗ Сессия не закрепилась: защищённая страница перенаправляет на вход')
            return finish(failures)

        print('✓ Вход выполнен, сессия активна\n')

        check_pages(client, PAGES, failures)

    # Проход по кабинету студента
    _, student_user_id = student_session()
    if student_user_id is None:
        print('  (студентов с учётной записью нет — кабинет не проверяем)')
    else:
        print()
        with app.test_client() as pupil:
            with pupil.session_transaction() as session:
                session['_user_id'] = str(student_user_id)
                session['_fresh'] = True
            check_pages(pupil, STUDENT_PAGES, failures)

    return finish(failures)


def finish(failures):
    total = len(PAGES) + len(STUDENT_PAGES)
    print()
    if failures:
        print(f'❌ Провалено проверок: {len(failures)} из {total}')
        for url, err in failures:
            print(f'   - {url}: {err}')
        return 1
    print(f'✅ Все {total} страниц открылись без ошибок (вход подтверждён)')
    return 0



if __name__ == '__main__':
    sys.exit(main())
