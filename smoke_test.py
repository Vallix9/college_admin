"""Дымовой тест: открывает все страницы приложения авторизованным админом.

Запуск:  python smoke_test.py
Ищет ошибки рендеринга (в том числе url_for на несуществующие эндпоинты)
и проверяет ответы health-эндпоинта.

Важно: страница входа тоже отдаёт HTTP 200, поэтому одного кода ответа
недостаточно. Тест отдельно убеждается, что вход состоялся, и что
защищённая страница не перенаправила обратно на /login — иначе все
проверки проходили бы, не проверив ничего.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.init_ import create_app, db
from app.models import (User, Student, Group, Subject, Grade,
                        AcademicPeriod, ScheduleItem)

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
    ('/settings/export-template/students', 'шаблон импорта: студенты'),
    ('/settings/export-template/grades', 'шаблон импорта: оценки'),
    ('/settings/export-template/groups', 'шаблон импорта: группы'),
    ('/settings/export-template/settings', 'шаблон импорта: настройки'),
    ('/api/system/download-logs', 'скачивание журнала'),
    ('/health', 'health-check'),
]


def main():
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

        for url, title in PAGES:
            try:
                resp = client.get(url, follow_redirects=True)
            except Exception as e:
                failures.append((url, f'исключение: {type(e).__name__}: {e}'))
                print(f'✗ {url:42} {title:32} ИСКЛЮЧЕНИЕ: {e}')
                continue

            status = resp.status_code
            content_type = resp.headers.get('Content-Type', '')

            # Выгрузки (xlsx) — это zip-архив, его нельзя декодировать
            # как текст. Такие ответы проверяем только по коду.
            is_binary = not content_type.startswith('text/')
            body = '' if is_binary else resp.get_data(as_text=True)

            # Страница отрендерилась, но внутри — ошибка Jinja
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

    return finish(failures)


def finish(failures):
    print()
    if failures:
        print(f'❌ Провалено проверок: {len(failures)} из {len(PAGES)}')
        for url, err in failures:
            print(f'   - {url}: {err}')
        return 1
    print(f'✅ Все {len(PAGES)} страниц открылись без ошибок (вход подтверждён)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
