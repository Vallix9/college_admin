"""Дымовой тест: открывает все страницы приложения авторизованным админом.

Запуск:  python smoke_test.py
Ищет ошибки рендеринга (в том числе url_for на несуществующие эндпоинты)
и проверяет ответы health-эндпоинта.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.init_ import create_app, db
from app.models import User, Student, Group, Subject, Grade

from config import load_env_file

load_env_file()

app = create_app()
app.config['TESTING'] = True

# Страницы, требующие параметров id — подставляем реальные из базы
with app.app_context():
    student_id = Student.query.first().id if Student.query.first() else 1
    group_id = Group.query.first().id if Group.query.first() else 1
    subject_id = Subject.query.first().id if Subject.query.first() else 1
    grade_id = Grade.query.first().id if Grade.query.first() else 1
    admin_pw = os.environ.get('ADMIN_PASSWORD', 'admin123')
    admin_name = os.environ.get('ADMIN_USERNAME', 'admin')

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
    ('/reports', 'отчёты'),
    ('/reports/students', 'экспорт студентов в Excel'),
    (f'/reports/group/{group_id}', 'экспорт группы в Excel'),
    ('/settings', 'настройки'),
    ('/settings/backup', 'резервные копии'),
    ('/settings/import', 'импорт данных'),
    ('/health', 'health-check'),
]


def main():
    failures = []

    with app.test_client() as client:
        with app.app_context():
            admin = User.query.filter_by(username=admin_name).first()

        # Вход в систему
        response = client.post('/login', data={
            'username': admin_name,
            'password': admin_pw,
        }, follow_redirects=True)
        if response.status_code != 200:
            failures.append(('ВХОД', response.status_code))
            print('✗ Не удалось войти в систему')
            return finish(failures)

        print('✓ Вход выполнен\n')

        for url, title in PAGES:
            try:
                resp = client.get(url, follow_redirects=True)
            except Exception as e:
                failures.append((url, f'исключение: {type(e).__name__}: {e}'))
                print(f'✗ {url:42} {title:32} ИСКЛЮЧЕНИЕ: {e}')
                continue

            status = resp.status_code
            body = resp.get_data(as_text=True)

            # Страница отрендерилась, но внутри — ошибка Jinja
            if status != 200:
                failures.append((url, status))
                print(f'✗ {url:42} {title:32} HTTP {status}')
            elif 'BuildError' in body or 'jinja2.exceptions' in body:
                failures.append((url, 'BuildError в теле ответа'))
                print(f'✗ {url:42} {title:32} BuildError')
            elif 'Internal Server Error' in body:
                failures.append((url, '500 внутри'))
                print(f'✗ {url:42} {title:32} внутренняя ошибка')
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
    print(f'✅ Все {len(PAGES)} страниц открылись без ошибок')
    return 0


if __name__ == '__main__':
    sys.exit(main())
