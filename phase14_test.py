"""Тесты Фазы 14: безопасность.

Фаза проверяет не «наличие файла с настройками», а то, что защита
действительно срабатывает:

  * 14.1 — приложение не поднимается в production с ключом из исходников
    или с коротким ключом, но поднимается в разработке.
  * 14.2 — отладочный сервер не поднимается наружу: ни в коде, ни в
    контейнере, и production с отладкой на внешнем интерфейсе падает.
  * 14.3 — POST без CSRF-токена отклоняется, и данные не меняются.
    Проверяется и по факту (объект остался/не появился), и статически
    (каждый POST-маршрут и каждая POST-форма имеют проверку и токен):
    одна забытая форма иначе осталась бы незамеченной.

Запуск: python phase14_test.py
"""

import os
import re
import shutil
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import config
from app.init_ import create_app
from app.models import (User, Group, Student, Grade, Subject, ScheduleItem,
                        AttendanceRecord, AuditLog)
from app.utils import get_log_file_path

import phase13_test as base

# Снимок/восстановление базы берём из Фазы 13: там уже отлажено удаление
# собственных записей журнала, здесь оно нужно не меньше — тест удаляет
# группы и предметы.
snapshot = base.snapshot
restore = base.restore
check = base.check
page_text = base.page_text
csrf = base.csrf
login = base.login
ADMIN_PASSWORD = base.ADMIN_PASSWORD

app = base.app
app.config['TESTING'] = True

ROOT = os.path.dirname(os.path.abspath(__file__))

TEST_SUBJECT = 'Предмет Ф14'
TEST_GROUP = 'ГР-Ф14'
TEST_STUDENT = 'Ф14-01'


def drop_previous():
    """Убрать следы прошлого прогона.

    Тест проверяет, что данные не меняются, поэтому мусор от упавшего
    прогона сначала убираем: иначе он сам станет причиной падения
    следующего (UNIQUE на группе), и проверка молча потеряет смысл.
    """
    with app.app_context():
        student = Student.query.filter_by(student_id=TEST_STUDENT).first()
        if student is not None:
            Grade.query.filter_by(student_id=student.id).delete(
                synchronize_session=False)
            AttendanceRecord.query.filter_by(student_id=student.id).delete(
                synchronize_session=False)
            base.db.session.delete(student)
        group = Group.query.filter_by(name=TEST_GROUP).first()
        if group is not None:
            ScheduleItem.query.filter_by(group_id=group.id).delete(
                synchronize_session=False)
            Grade.query.filter(Grade.student_id.is_(None)).delete(
                synchronize_session=False)
            base.db.session.delete(group)
        subject = Subject.query.filter_by(name=TEST_SUBJECT).first()
        if subject is not None:
            ScheduleItem.query.filter_by(subject_id=subject.id).delete(
                synchronize_session=False)
            Grade.query.filter_by(subject_id=subject.id).delete(
                synchronize_session=False)
            base.db.session.delete(subject)
        base.db.session.commit()


def section(title):
    print(f'\n--- {title} ---')


# ===================== 14.1 SECRET_KEY =====================
def config_class(name, **values):
    """Временный класс конфигурации с нужными значениями.

    SECRET_KEY, DEBUG и HOST в config.Config читаются из окружения один раз
    при импорте модуля, поэтому подменить их через os.environ в тесте
    нельзя: модуль-то уже загружен. Класс-наследник задаёт значения
    явно, а is_production() по-прежнему смотрит в окружение — так проверяется
    и запрет по ключу, и запрет по отладке.
    """
    return type(name, (config.Config,), values)


def test_secret_key():
    section('14.1 SECRET_KEY')
    good = '0123456789abcdef0123456789abcdef0123456789abcdef'

    check('ключ из исходников отклоняется',
          config.secret_key_error(config.DEV_SECRET_KEY) is not None)
    check('пустой ключ отклоняется',
          config.secret_key_error('') is not None)
    check('короткий ключ отклоняется',
          config.secret_key_error('a' * 31) is not None)
    check('ключ в 32 символа принимается',
          config.secret_key_error('a' * 32) is None)
    check('случайный ключ принимается',
          config.secret_key_error(good) is None)

    saved = {name: os.environ.get(name) for name in ('APP_ENV', 'WAITRESS')}
    try:
        for name in ('APP_ENV', 'WAITRESS'):
            os.environ.pop(name, None)
        check('в разработке запуск не считается production',
              config.is_production() is False)

        os.environ['WAITRESS'] = '1'
        check('WAITRESS=1 считается production', config.is_production() is True)
        os.environ.pop('WAITRESS')

        os.environ['APP_ENV'] = 'production'
        check('APP_ENV=production считается production',
              config.is_production() is True)
        os.environ['APP_ENV'] = 'development'
        check('APP_ENV=development — это разработка',
              config.is_production() is False)

        # Реальная проверка: приложение должно падать, а не предупреждать
        os.environ['APP_ENV'] = 'production'

        setattr(config, 'Ф14DevKey', config_class('Ф14DevKey',
                                                  SECRET_KEY=config.DEV_SECRET_KEY))
        try:
            create_app('config.Ф14DevKey')
            check('в production ключ из исходников не проходит', False,
                  'приложение создалось')
        except RuntimeError as error:
            check('в production ключ из исходников не проходит', True,
                  str(error)[:60])

        setattr(config, 'Ф14ShortKey', config_class('Ф14ShortKey',
                                                    SECRET_KEY='короткий'))
        try:
            create_app('config.Ф14ShortKey')
            check('в production короткий ключ не проходит', False,
                  'приложение создалось')
        except RuntimeError:
            check('в production короткий ключ не проходит', True)

        setattr(config, 'Ф14GoodKey', config_class('Ф14GoodKey',
                                                   SECRET_KEY=good))
        try:
            create_app('config.Ф14GoodKey')
            check('в production с нормальным ключом приложение поднимается',
                  True)
        except RuntimeError as error:
            check('в production с нормальным ключом приложение поднимается',
                  False, str(error)[:60])

        # В разработке запасной ключ из исходников допустим: иначе не
        # запустится ничего, даже чтобы посмотреть страницу
        os.environ['APP_ENV'] = 'development'
        try:
            create_app('config.Ф14DevKey')
            check('в разработке запасной ключ допустим', True)
        except RuntimeError as error:
            check('в разработке запасной ключ допустим', False,
                  str(error)[:60])
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


# ===================== 14.2 ЗАПУСК =====================
def test_startup():
    section('14.2 Запуск без отладочного сервера в сети')

    run_py = open(os.path.join(ROOT, 'run.py'), encoding='utf-8-sig').read()
    compose = open(os.path.join(ROOT, 'docker-compose.yml'),
                   encoding='utf-8-sig').read()

    check('в run.py нет жёсткого debug=True',
          not re.search(r'\bdebug\s*=\s*True', run_py))
    check('в run.py нет жёсткого host=0.0.0.0',
          not re.search(r"host\s*=\s*['\"]0\.0\.0\.0['\"]", run_py))
    check('отладка и хост берутся из настроек',
          "app.config['DEBUG']" in run_py and "app.config['HOST']" in run_py)
    check('waitress поднимается при выключенной отладке',
          re.search(r'if not debug', run_py) is not None
          and 'from waitress import serve' in run_py)
    check('в контейнере отладка выключена',
          re.search(r'FLASK_DEBUG:\s*"0"', compose) is not None)

    good = '0123456789abcdef' * 4
    saved = os.environ.get('APP_ENV')
    try:
        os.environ['APP_ENV'] = 'production'
        setattr(config, 'Ф14NetDebug', config_class(
            'Ф14NetDebug', SECRET_KEY=good, DEBUG=True, HOST='0.0.0.0'))
        try:
            create_app('config.Ф14NetDebug')
            check('отладка на внешнем интерфейсе не проходит', False,
                  'приложение создалось')
        except RuntimeError as error:
            check('отладка на внешнем интерфейсе не проходит', True,
                  str(error)[:60])

        setattr(config, 'Ф14LocalDebug', config_class(
            'Ф14LocalDebug', SECRET_KEY=good, DEBUG=True, HOST='127.0.0.1'))
        try:
            create_app('config.Ф14LocalDebug')
            check('отладка на 127.0.0.1 допустима', True)
        except RuntimeError as error:
            check('отладка на 127.0.0.1 допустима', False, str(error)[:60])

        # FLASK_DEV_SERVER=1 в production — тоже отказ: документ обещает
        # запрет, а значит он должен быть настоящим
        os.environ['FLASK_DEV_SERVER'] = '1'
        try:
            setattr(config, 'Ф14DevServer', config_class(
                'Ф14DevServer', SECRET_KEY=good, DEBUG=False, HOST='0.0.0.0'))
            try:
                create_app('config.Ф14DevServer')
                check('отладочный сервер в production не проходит', False,
                      'приложение создалось')
            except RuntimeError as error:
                check('отладочный сервер в production не проходит', True,
                      str(error)[:60])
        finally:
            os.environ.pop('FLASK_DEV_SERVER', None)
    finally:
        if saved is None:
            os.environ.pop('APP_ENV', None)
        else:
            os.environ['APP_ENV'] = saved


# ===================== 14.3 CSRF =====================
def post_routes():
    """POST-маршруты из routes.py вместе с их телом."""
    lines = base.routes_source.splitlines()
    routes = []
    for index, line in enumerate(lines):
        match = re.match(r'@main\.route\((.+)\)\s*$', line.strip())
        if match:
            routes.append({'spec': match.group(1), 'body': []})
        if routes:
            routes[-1]['body'].append(line)
    return [route for route in routes if 'POST' in route['spec']]


def test_csrf_static():
    section('14.3 CSRF: покрытие статически')

    unprotected = []
    for route in post_routes():
        text = '\n'.join(route['body'])
        if not any(mark in text for mark in
                   ('check_csrf_or_none', 'validate_on_submit',
                    'form.validate()', 'csrf_denied')):
            unprotected.append(route['spec'])
    check('все POST-маршруты проверяют токен',
          not unprotected, f'без проверки: {unprotected}')

    import glob
    bad_forms = []
    total = 0
    for path in sorted(glob.glob(os.path.join(ROOT, 'app', 'templates', '*.html'))):
        text = open(path, encoding='utf-8-sig').read()
        for match in re.finditer(r'<form\b[^>]*?method\s*=\s*["\']?POST',
                                 text, re.I | re.S):
            total += 1
            end = text.find('>', match.end())
            window = text[end:end + 300]
            if 'csrf_token' not in window and 'hidden_tag' not in window:
                bad_forms.append(os.path.basename(path))
    check('во всех POST-формах есть токен', not bad_forms,
          f'без токена: {sorted(set(bad_forms))}')
    check('POST-форм найдено больше нуля', total > 0, f'найдено {total}')

    check('токен доступен шаблонам без передачи из маршрута',
          'csrf_processor' in open(os.path.join(ROOT, 'app', 'init_.py'),
                                   encoding='utf-8-sig').read())


def test_csrf_functional():
    section('14.3 CSRF: запрос без токена не меняет данные')
    drop_previous()

    with app.app_context():
        group = Group(name=TEST_GROUP, specialty='Тест', year=2026)
        db_add(group)
        subject = Subject(name=TEST_SUBJECT, hours=2)
        db_add(subject)
        student = Student(student_id=TEST_STUDENT, last_name='Тестов',
                          first_name='Студент', gender='M',
                          group_id=group.id, status='active')
        db_add(student)
        slot = ScheduleItem(group_id=group.id, subject_id=subject.id,
                            day_of_week=1, lesson_number=1)
        db_add(slot)
        grade = Grade(student_id=student.id, subject_id=subject.id,
                      grade_value='5', grade_type='exam', date=date(2026, 9, 15))
        db_add(grade)
        ids = {'group': group.id, 'subject': subject.id,
               'student': student.id, 'slot': slot.id, 'grade': grade.id}

    client = base.admin_client()
    with app.app_context():
        students_before = Student.query.count()
        subjects_before = Subject.query.count()
        groups_before = Group.query.count()
        accounts_before = User.query.filter_by(role=User.ROLE_STUDENT).count()

    # Ни токена, ни его вида в теле: ровно то, что прислал бы чужой сайт
    probes = [
        ('/subjects/add', {'name': 'Взломан', 'hours': '10'}, 'предмет не создан',
         lambda: Subject.query.filter_by(name='Взломан').first() is None),
        (f'/students/{ids["student"]}/grades/add',
         {'subject_id': ids['subject'], 'grade_value': '5'},
         'оценка не добавлена',
         lambda: Grade.query.filter_by(student_id=ids['student'],
                                       date=date.today()).first() is None),
        ('/accounts/issue', {'group_id': ids['group']},
         'учётные записи не выданы',
         lambda: User.query.filter_by(role=User.ROLE_STUDENT).count()
                 == accounts_before),
        (f'/students/{ids["student"]}/delete', {},
         'студент не удалён', lambda: db_get(Student, ids['student']) is not None),
        (f'/groups/{ids["group"]}/delete', {},
         'группа не удалена', lambda: db_get(Group, ids['group']) is not None),
        (f'/subjects/{ids["subject"]}/delete', {},
         'предмет не удалён', lambda: db_get(Subject, ids['subject']) is not None),
        (f'/grades/{ids["grade"]}/delete', {},
         'оценка не удалена', lambda: db_get(Grade, ids['grade']) is not None),
        (f'/schedule/{ids["slot"]}/delete', {},
         'пара не удалена', lambda: db_get(ScheduleItem, ids['slot']) is not None),
        (f'/journal/grade/{ids["grade"]}/delete', {},
         'оценка в журнале не удалена',
         lambda: db_get(Grade, ids['grade']) is not None),
        ('/settings/backup/нет-такой-копии/delete', {},
         'резервная копия не удалена', lambda: True),
    ]

    for url, data, label, probe in probes:
        response = client.post(url, data=data, follow_redirects=False)
        check(f'{label}: POST без токена отклонён',
              response.status_code == 400, f'{url} → {response.status_code}')
        with app.app_context():
            check(f'{label}: данные не изменились', probe())

    with app.app_context():
        check('число студентов не изменилось',
              Student.query.count() == students_before)
        check('число предметов не изменилось',
              Subject.query.count() == subjects_before)
        check('число групп не изменилось', Group.query.count() == groups_before)

    section('14.3 CSRF: с токеном запрос проходит')
    token = csrf(client, '/subjects')
    response = client.post('/subjects/add',
                           data={'name': 'С токеном', 'hours': '10',
                                 'csrf_token': token},
                           follow_redirects=False)
    check('добавление предмета с токеном работает',
          response.status_code in (200, 302), f'→ {response.status_code}')
    with app.app_context():
        check('предмет действительно создан',
              Subject.query.filter_by(name='С токеном').first() is not None)
        base.db.session.query(Subject).filter_by(name='С токеном').delete()
        base.db.session.commit()

    check('удалённый отчёт больше не принимает POST',
          client.post('/reports/generate',
                      data={'report_type': 'students'},
                      follow_redirects=False).status_code in (404, 405))


def db_add(obj):
    db = base.db
    db.session.add(obj)
    db.session.commit()
    return obj


def db_get(model, obj_id):
    return base.db.session.get(model, obj_id)


# ===================== 14.4 ОГРАНИЧЕНИЕ ПОПЫТОК ВХОДА =====================
def reset_login_counters():
    """Обнулить счётчики подбора у всех, кого тест помучил."""
    with app.app_context():
        for user in User.query.all():
            user.failed_login_count = 0
            user.last_failed_login_at = None
        base.db.session.commit()


def attempt(client, username, password):
    response = client.post('/login', data={
        'username': username, 'password': password,
        'csrf_token': csrf(client)}, follow_redirects=True)
    return page_text(response.get_data(as_text=True))


def test_login_throttling():
    section('14.4 Ограничение попыток входа')
    check('порог подбора задан', User.MAX_FAILED_LOGINS >= 3,
          f'{User.MAX_FAILED_LOGINS} попыток')
    check('блокировка не бесконечная', 0 < User.LOCKOUT_MINUTES <= 60,
          f'{User.LOCKOUT_MINUTES} мин')
    check('счётчик и время неудачи хранятся в модели, а не в памяти',
          hasattr(User, 'failed_login_count')
          and hasattr(User, 'last_failed_login_at'))

    reset_login_counters()
    client = base.app.test_client()

    texts = [attempt(client, 'admin', f'неверный-{i}')
             for i in range(User.MAX_FAILED_LOGINS - 1)]
    check('до порога вход не закрывается',
          not any('Слишком много неудачных попыток' in text for text in texts))
    with app.app_context():
        user = db_get(User, 1) or User.query.filter_by(username='admin').first()
        check('счётчик накапливается',
              user.failed_login_count == User.MAX_FAILED_LOGINS - 1,
              f'{user.failed_login_count}')
        check('остаток попыток считается',
              user.login_attempts_left == 1, f'{user.login_attempts_left}')

    locked_text = attempt(client, 'admin', 'ещё-один-неверный')
    check('на пороге вход закрывается',
          'Слишком много неудачных попыток' in locked_text)
    with app.app_context():
        user = User.query.filter_by(username='admin').first()
        check('счётчик достиг порога',
              user.failed_login_count == User.MAX_FAILED_LOGINS,
              f'{user.failed_login_count}')
        check('учётная запись помечена закрытой', user.login_locked)

    with base.app.test_client() as fresh:
        check('закрытый вход не пускает даже с верным паролем',
              'Слишком много неудачных попыток' in
              attempt(fresh, 'admin', ADMIN_PASSWORD))

    # Время блокировки истекло — человек снова может войти
    with app.app_context():
        user = User.query.filter_by(username='admin').first()
        user.last_failed_login_at = datetime.now() - timedelta(
            minutes=User.LOCKOUT_MINUTES + 1)
        base.db.session.commit()
        check('после истечения блокировки вход открывается',
              not user.login_locked)
    with base.app.test_client() as after:
        check('с верным паролем вход снова работает',
              'Добро пожаловать' in attempt(after, 'admin', ADMIN_PASSWORD))
    with app.app_context():
        user = User.query.filter_by(username='admin').first()
        check('успешный вход обнуляет счётчик',
              user.failed_login_count == 0 and not user.login_locked)

    # Счётчик относится к последнему получасу, а не ко всей истории
    with app.app_context():
        user = User.query.filter_by(username='admin').first()
        user.failed_login_count = User.MAX_FAILED_LOGINS - 1
        user.last_failed_login_at = datetime.now() - timedelta(hours=2)
        base.db.session.commit()
        user.register_failed_login()
        base.db.session.commit()
        check('после перерыва серия неудач начинается заново',
              user.failed_login_count == 1, f'{user.failed_login_count}')

    # Смена пароля администратором открывает заблокированный вход
    with app.app_context():
        user = User.query.filter_by(username='admin').first()
        user.failed_login_count = User.MAX_FAILED_LOGINS
        user.last_failed_login_at = datetime.now()
        base.db.session.commit()
        check('до смены пароля вход закрыт', user.login_locked)
        user.set_password(ADMIN_PASSWORD)
        base.db.session.commit()
        check('смена пароля снимает блокировку', not user.login_locked)

    # Ограничение общее для сотрудников и студентов
    with app.app_context():
        student = (Student.query
                   .join(User, User.id == Student.user_id)
                   .filter(User.role == User.ROLE_STUDENT)
                   .first())
        if student is None:
            check('нашлась учётная запись студента для проверки', False)
        else:
            student_login = student.student_id
    if student is not None:
        with base.app.test_client() as student_client:
            for _ in range(User.MAX_FAILED_LOGINS):
                attempt(student_client, student_login, 'мимо')
            check('ограничение действует и на студента',
                  'Слишком много неудачных попыток' in
                  attempt(student_client, student_login, 'ещё-мимо'))
            with app.app_context():
                user = User.query.filter_by(username=student_login).first()
                check('счётчик студента вырос', user.login_locked)

    reset_login_counters()


# ===================== 14.5 ЭКРАНИРОВАНИЕ =====================
XSS = '<script>alert(1)</script>'
XSS_ATTR = '"><img src=x onerror=alert(2)>'


def test_escaping():
    section('14.5 Экранирование пользовательского вывода')

    import glob
    unsafe = []
    for path in sorted(glob.glob(os.path.join(ROOT, 'app', 'templates', '*.html'))):
        for number, line in enumerate(open(path, encoding='utf-8-sig'), 1):
            if '|safe' in line or 'Markup(' in line:
                unsafe.append(f'{os.path.basename(path)}:{number}')
    check('в шаблонах нет |safe и Markup', not unsafe, f'найдено: {unsafe}')

    drop_previous()
    with app.app_context():
        group = Group(name=TEST_GROUP, specialty='Тест', year=2026)
        db_add(group)
        student = Student(student_id=TEST_STUDENT,
                          last_name='Скрипт' + XSS,
                          first_name='Тест' + XSS_ATTR,
                          patronymic='<b>жирный</b>',
                          gender='M', group_id=group.id, status='active',
                          email=f'"{XSS}"@example.com')
        db_add(student)
        student_id = student.id

    client = base.admin_client()
    response = client.get(f'/student/{student_id}')
    html = response.get_data(as_text=True)
    check('карточка студента открылась', response.status_code == 200,
          f'→ {response.status_code}')
    check('тег script из ФИО не выполнится', XSS not in html)
    check('тег script экранирован', '&lt;script&gt;' in html)
    check('инъекция в атрибут не выполнится', XSS_ATTR not in html)
    check('обращение в ФИО видно как текст',
          page_text(html).find('Скрипт') != -1)

    # Журнал событий: строка из файла попадает на страницу как есть
    log_path = get_log_file_path()
    size_before = os.path.getsize(log_path) if os.path.isfile(log_path) else 0
    try:
        with open(log_path, 'a', encoding='utf-8') as handle:
            handle.write(f'2026-10-02 00:00:00 [WARNING] подбор {XSS}\n')
        response = client.get('/settings/logs')
        html = response.get_data(as_text=True)
        check('журнал событий открылся', response.status_code == 200,
              f'→ {response.status_code}')
        check('тег script из строки журнала не выполнится', XSS not in html)
        check('строка журнала экранирована', '&lt;script&gt;' in html)
    finally:
        if os.path.isfile(log_path):
            with open(log_path, 'r+', encoding='utf-8') as handle:
                handle.truncate(size_before)

    with app.app_context():
        db_get(Student, student_id).last_name = 'Скрипт'
        base.db.session.commit()
    drop_previous()


# ===================== 14.6 ПОЛИТИКА ПАРОЛЕЙ =====================
def test_password_policy():
    section('14.6 Политика паролей')

    from app.forms import MIN_PASSWORD_LENGTH

    check('минимальная длина пароля — не меньше 8',
          MIN_PASSWORD_LENGTH >= 8, f'{MIN_PASSWORD_LENGTH} символов')

    forms_source = open(os.path.join(ROOT, 'app', 'forms.py'),
                        encoding='utf-8-sig').read()
    short_rules = []
    for match in re.finditer(r'PasswordField\((?:[^()]|\([^()]*\))*\)',
                             forms_source, re.S):
        if re.search(r'min\s*=\s*([0-9]+)', match.group(0)):
            short_rules.append(match.group(0)[:60])
    check('в формах паролей нет собственного короткого минимума',
          not short_rules, f'{short_rules}')

    for name in ('StaffForm', 'StaffPasswordResetForm', 'AccountPasswordForm',
                 'StudentAccountForm'):
        start = forms_source.find(f'class {name}(')
        end = forms_source.find('\nclass ', start + 1)
        body = forms_source[start:end if end != -1 else None]
        check(f'{name} пользуется общим правилом длины',
              'password_length(' in body)

    from app.models import User
    # Короткий пароль через форму администратора не принимается
    drop_previous()
    client = base.admin_client()
    response = client.post('/staff/add', data={
        'username': 'ф14пароль', 'full_name': 'Тест Пароля',
        'role': User.ROLE_TEACHER, 'password': 'коротк',
        'confirm_password': 'коротк', 'is_active': 'y',
        'csrf_token': csrf(client, '/staff/add')},
        follow_redirects=True)
    html = response.get_data(as_text=True)
    check('короткий пароль не создаёт сотрудника',
          'не короче' in page_text(html).lower()
          or '8 символов' in page_text(html),
          'сообщение о длине пароля')
    with app.app_context():
        check('сотрудника с коротким паролем в базе нет',
              User.query.filter_by(username='ф14пароль').first() is None)

    # Нормальный пароль проходит
    good = 'DlinnyjParol14'
    response = client.post('/staff/add', data={
        'username': 'ф14пароль', 'full_name': 'Тест Пароля',
        'role': User.ROLE_TEACHER, 'password': good,
        'confirm_password': good, 'is_active': 'y',
        'csrf_token': csrf(client, '/staff/add')},
        follow_redirects=True)
    with app.app_context():
        created = User.query.filter_by(username='ф14пароль').first()
        check('пароль из 8+ символов принимается', created is not None)
        if created is not None:
            check('пароль проверен и сохранён', created.check_password(good))
            check('новый пароль помечен временным', created.password_temporary)
            check('выданный пароль не длиннее минимума',
                  len(good) >= MIN_PASSWORD_LENGTH)
            base.db.session.delete(created)
            base.db.session.commit()
    drop_previous()

    # Временный пароль подсказывает смену при входе
    with app.app_context():
        student = (Student.query
                   .join(User, User.id == Student.user_id)
                   .filter(User.role == User.ROLE_STUDENT)
                   .first())
        if student is None:
            check('нашлась учётная запись студента', False)
        else:
            login_name = student.student_id
            temporary = 'Vremennyj14'
            account = User.query.filter_by(username=login_name).first()
            account.set_temporary_password(temporary)
            base.db.session.commit()
    with base.app.test_client() as student_client:
        text = attempt(student_client, login_name, temporary)
        check('при входе с временным паролем просят его сменить',
              'временным паролем' in text)


# ===================== 14.7 IDOR =====================
def test_idor():
    section('14.7 Чужие объекты по прямой ссылке')

    drop_previous()
    with app.app_context():
        group = Group(name=TEST_GROUP, specialty='Тест', year=2026)
        db_add(group)
        own_subject = Subject(name=TEST_SUBJECT, hours=2)
        foreign_subject = Subject(name=TEST_SUBJECT + ' (чужой)', hours=2)
        db_add(own_subject)
        db_add(foreign_subject)
        own = Student(student_id=TEST_STUDENT, last_name='Свой',
                      first_name='Ученик', gender='M', group_id=group.id,
                      status='active')
        db_add(own)
        other = Student(student_id='Ф14-99', last_name='Чужой',
                        first_name='Ученик', gender='M', group_id=group.id,
                        status='active')
        db_add(other)
        grade = Grade(student_id=other.id, subject_id=foreign_subject.id,
                      grade_value='5', grade_type='exam', date=date(2026, 9, 15))
        db_add(grade)
        ids = {'own_subject': own_subject.id,
               'foreign_subject': foreign_subject.id,
               'own': own.id, 'other': other.id, 'grade': grade.id}

    client = base.admin_client()
    # Администратору всё видно — иначе отбор помешал бы работать
    check('администратору доступна чужая карточка',
          client.get(f'/student/{ids["other"]}').status_code == 200)

    # Преподаватель: свой предмет открывается, чужой — нет
    with app.app_context():
        teacher = User(username='Ф14-УЧИТ', full_name='Учитель Ф14',
                       role=User.ROLE_TEACHER, is_active=True)
        teacher.set_password('UchitelParol14')
        other_teacher = User(username='Ф14-ЧУЖОЙ', full_name='Чужой Ф14',
                             role=User.ROLE_TEACHER, is_active=True)
        other_teacher.set_password('ChuzhoyParol14')
        base.db.session.add(teacher)
        base.db.session.add(other_teacher)
        base.db.session.commit()
        db_get(Subject, ids['own_subject']).teacher_id = teacher.id
        db_get(Subject, ids['foreign_subject']).teacher_id = other_teacher.id
        base.db.session.commit()
    with base.app.test_client() as teacher_client:
        login(teacher_client, 'Ф14-УЧИТ', 'UchitelParol14')
        token = csrf(teacher_client, '/students')
        check('свой предмет преподавателю доступен',
              teacher_client.get(f'/subjects/{ids["own_subject"]}/edit'
                                 ).status_code == 200)
        checks = (
            (teacher_client.get(f'/subjects/{ids["foreign_subject"]}/edit'),
             'правка чужого предмета'),
            (teacher_client.post(f'/journal/grade/{ids["grade"]}/edit',
                                 data={'csrf_token': token, 'value': '2'},
                                 follow_redirects=False),
             'правка чужой оценки в журнале'),
            (teacher_client.post(f'/journal/grade/{ids["grade"]}/delete',
                                 data={'csrf_token': token},
                                 follow_redirects=False),
             'удаление чужой оценки из журнала'),
            (teacher_client.post(f'/grades/{ids["grade"]}/delete',
                                 data={'csrf_token': token},
                                 follow_redirects=False),
             'удаление чужой оценки'),
        )
        for response, label in checks:
            check(f'преподавателю закрыто: {label}',
                  response.status_code == 403,
                  f'→ {response.status_code}')

    with app.app_context():
        grade_left = db_get(Grade, ids['grade'])
        check('чужая оценка на месте', grade_left is not None)
        check('чужая оценка не изменилась',
              grade_left is not None and grade_left.grade_value == '5',
              f'{grade_left.grade_value if grade_left else "удалена"}')
        check('чужой предмет на месте', db_get(Subject, ids['foreign_subject']) is not None)
        for user in (User.query.filter(User.username.in_(['Ф14-УЧИТ', 'Ф14-ЧУЖОЙ'])).all()):
            base.db.session.delete(user)
        base.db.session.commit()

    # Студент не достаёт чужое
    with app.app_context():
        own_user = User(username='Ф14-СТУД', full_name='Студент Ф14',
                        role=User.ROLE_STUDENT, is_active=True)
        own_user.set_password('StudentParol14')
        base.db.session.add(own_user)
        base.db.session.commit()
        own_user_id = own_user.id
        own = db_get(Student, ids['own'])
        own.user_id = own_user_id
        base.db.session.commit()
    with base.app.test_client() as student_client:
        login(student_client, 'Ф14-СТУД', 'StudentParol14')
        for url, label in ((f'/student/{ids["other"]}', 'карточка чужого студента'),
                           (f'/student/{ids["own"]}', 'карточка соседа по тесту'),
                           ('/journal', 'журнал оценок'),
                           ('/accounts', 'учётные записи'),
                           ('/audit', 'журнал действий')):
            response = student_client.get(url)
            check(f'студенту закрыт {label}', response.status_code in (302, 403),
                  f'{url} → {response.status_code}')

    with app.app_context():
        portal = None
        own_user = db_get(User, own_user_id)
        base.db.session.delete(own_user)
        base.db.session.commit()
    drop_previous()


def main():
    drop_previous()
    state = snapshot()
    try:
        test_secret_key()
        test_startup()
        test_csrf_static()
        test_csrf_functional()
        test_login_throttling()
        test_escaping()
        test_password_policy()
        test_idor()
    finally:
        reset_login_counters()
        drop_previous()
        restore(state)
        print('\nБаза восстановлена из снимка.')

    print()
    # Счётчики ведёт общий check() из Фазы 13, а не свой: иначе сводка
    # в конце всегда показывала бы «0/0» и прогон выглядел бы успешным
    # независимо от результата
    if base.failures:
        print(f'ПРОВАЛЕНО {len(base.failures)} из {base.checks}:')
        for item in base.failures:
            print(' -', item)
        return 1
    print(f'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ ({base.checks}/{base.checks})')
    return 0


if __name__ == '__main__':
    sys.exit(main())