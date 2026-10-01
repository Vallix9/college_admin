"""Считает SQL-запросы при рендере страниц — для проверки отсутствия N+1.

Запуск:  python query_count.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from sqlalchemy import event

from app.init_ import create_app
from config import load_env_file

load_env_file()

app = create_app()
app.config['TESTING'] = True

with app.app_context():
    engine = app.extensions['sqlalchemy'].engine

PAGES = ['/', '/students', '/groups', '/subjects', '/grades', '/reports', '/settings',
         '/periods', '/schedule', '/journal',
         '/journal?mode=subjects', '/attendance', '/results', '/accounts']

with app.test_client() as client:
    html = client.get('/login').get_data(as_text=True)
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
    client.post('/login', data={
        'username': os.environ.get('ADMIN_USERNAME', 'admin'),
        'password': os.environ.get('ADMIN_PASSWORD', 'admin123'),
        'csrf_token': token,
    }, follow_redirects=True)

    total = 0
    for url in PAGES:
        statements = []

        def before_cursor(conn, cursor, stmts, ctx, many, exec_ctx=None):
            # В SQLAlchemy 2.0 сюда приходит сама строка SQL, а не список
            statements.append(str(stmts).strip())

        event.listen(engine, 'before_cursor_execute', before_cursor)
        resp = client.get(url)
        event.remove(engine, 'before_cursor_execute', before_cursor)

        selects = [s for s in statements if s.upper().startswith('SELECT')]
        total += len(selects)
        print(f'{url:16} {resp.status_code}  SELECT={len(selects)}  ВСЕГО={len(statements)}')
        for s in statements:
            print('    >', ' '.join(str(s).split())[:80])

    print(f'\nвсего SELECT за один проход: {total}')

# Кабинет студента (Фаза 12) считаем отдельно: админский вход его не видит,
# а страницы должны обходиться без N+1 — по запросу на каждого одногруппника
# таблица сводной вырождалась бы в отдельный SELECT на студента.
with app.app_context():
    from app.models import Student
    pupil = (Student.query
             .filter(Student.user_id.isnot(None))
             .order_by(Student.id).first())
    pupil_user_id = pupil.user_id if pupil else None

if pupil_user_id is None:
    print('\n(студентов с учётной записью нет — кабинет не считаем)')
else:
    with app.test_client() as client:
        with client.session_transaction() as session:
            session['_user_id'] = str(pupil_user_id)
            session['_fresh'] = True

        total = 0
        for url in ['/portal', '/portal/grades', '/portal/grades?view=group',
                    '/portal/attendance']:
            statements = []

            def before_cursor(conn, cursor, stmts, ctx, many, exec_ctx=None):
                statements.append(str(stmts).strip())

            event.listen(engine, 'before_cursor_execute', before_cursor)
            resp = client.get(url)
            event.remove(engine, 'before_cursor_execute', before_cursor)

            selects = [s for s in statements if s.upper().startswith('SELECT')]
            total += len(selects)
            print(f'\n{url:26} {resp.status_code}  SELECT={len(selects)}  '
                  f'всего={len(statements)}')
            for s in statements:
                print('    >', ' '.join(str(s).split())[:80])

        print(f'\nвсего SELECT по кабинету: {total}')
