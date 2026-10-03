"""Изоляция тестов от рабочей базы.

Ключевая идея: pytest импортирует тестовые модули раньше, чем что-либо
сработает, поэтому переменные окружения выставляются здесь, на верхнем
уровне, до первого `import config`. Иначе тесты молча писали бы в
college.db разработчика — то есть «зелёный» прогон означал бы, что данные
целы, ровно наоборот.

Каталоги тоже временные: иначе тесты засоряли бы рабочие logs/, backups/
и exports/, а проверки содержимого этих файлов ловили бы чужие данные.
"""

import os
import re
import shutil
import sys
import tempfile
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix='college_pytest_')
TEMPLATE_DB = os.path.join(TMP, 'template.db')
WORK_DB = os.path.join(TMP, 'test.db')

# До импорта config. load_dotenv не перезаписывает уже заданные переменные,
# поэтому .env машины разработчика этими значениями не перебивается.
os.environ['DATABASE_URL'] = 'sqlite:///' + WORK_DB
os.environ['SECRET_KEY'] = 'pytest-secret-key-not-a-real-one-0123456789'
os.environ['APP_ENV'] = 'development'
os.environ.pop('FLASK_DEBUG', None)
os.environ.pop('WAITRESS', None)

# Каталоги тоже задаются через окружение: и config.py, и utils.py читают
# эти имена. Иначе бэкапы и выгрузки в тестах попадали бы в рабочие
# backups/ и exports/ рядом с исходниками.
for _name, _folder in (('UPLOAD_FOLDER', 'uploads'), ('BACKUP_FOLDER', 'backups'),
                       ('EXPORT_FOLDER', 'exports'), ('LOG_FOLDER', 'logs')):
    _path = os.path.join(TMP, _folder)
    os.makedirs(_path, exist_ok=True)
    os.environ[_name] = _path

import pytest  # noqa: E402

import config  # noqa: E402
from app.init_ import create_app, db  # noqa: E402
from app.models import (AcademicPeriod, AttendanceRecord, Grade,  # noqa: E402
                        Group, LessonDate, ScheduleItem, Student,
                        Subject, SystemSettings, User)
from migrations import apply_schema  # noqa: E402

ADMIN_LOGIN = 'admin'
ADMIN_PASSWORD = 'AdminParol16'
TEACHER_LOGIN = 'teacher16'
TEACHER_PASSWORD = 'UchitelParol16'
STUDENT_LOGIN = 'ST0016'
STUDENT_PASSWORD = 'StudentParol16'
OTHER_STUDENT_LOGIN = 'ST0017'
OTHER_TEACHER_LOGIN = 'other16'
OTHER_TEACHER_PASSWORD = 'OtherParol16'
LESSON_DATE = '2026-09-28'
LESSON_DATE_HISTORY = '2026-09-30'


class TestConfig(config.Config):
    """Настройки тестов: те же, что у приложения, но с временными путями.

    Каталоги приходят из окружения, выставленного выше: иначе utils.py
    продолжил бы писать бэкапы и выгрузки в рабочие backups/ и exports/.
    """

    TESTING = True
    SQLALCHEMY_DATABASE_URI = os.environ['DATABASE_URL']
    WTF_CSRF_ENABLED = True


class TemplateConfig(TestConfig):
    """То же, но база — эталонная, из которой тесты делают копии."""

    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + TEMPLATE_DB


@pytest.fixture(scope='session')
def template_app():
    """Приложение на эталонной базе: она только наполняется."""
    application = create_app(TemplateConfig)
    with application.app_context():
        apply_schema()
        db.create_all()
        seed(application)
        db.session.remove()
        # Соединение закрываем, иначе файл останется занят на все тесты
        db.engine.dispose()
    return application


@pytest.fixture()
def app(template_app):
    """Свежая копия эталонной базы на каждый тест.

    Один общий файл базы на весь прогон — источник тихих, но обидных
    ошибок: тест, который переименовал студента, ломал следующий тест, и
    падало всё, что зависело от данных, а не от кода. Одна копия на тест
    стоит копейки: файл пустой, строк в нём десятки.
    """
    shutil.copyfile(TEMPLATE_DB, WORK_DB)
    for stale in (WORK_DB + '-journal', WORK_DB + '-wal', WORK_DB + '-shm'):
        if os.path.exists(stale):
            os.remove(stale)
    for folder in ('backups', 'exports', 'uploads'):
        _clear(os.path.join(TMP, folder))
    application = create_app(TestConfig)
    yield application
    with application.app_context():
        db.session.remove()
        db.engine.dispose()
    if os.path.exists(WORK_DB):
        os.remove(WORK_DB)


@pytest.fixture()
def ctx(app):
    """Контекст приложения на время теста."""
    with app.app_context():
        yield app
        db.session.rollback()
        db.session.remove()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(scope='session', autouse=True)
def cleanup():
    yield
    shutil.rmtree(TMP, ignore_errors=True)


def _clear(folder):
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        shutil.rmtree(path, ignore_errors=True) if os.path.isdir(path) \
            else os.remove(path)


# ---------------------------------------------------------------- фикстуры

def seed(application):
    """Минимальные данные, на которых проверяется поведение, а не наличие.

    Полный демонстрационный набор init_db.py для тестов не нужен: он
    добавляет сотни оценок и делает прогоны медленными, а проверяемое здесь
    — правила доступа и расчёты на известных значениях.
    """
    if User.query.first():
        return

    admin = User(username=ADMIN_LOGIN, full_name='Администратор Тестов',
                 role=User.ROLE_ADMIN, is_active=True)
    admin.set_password(ADMIN_PASSWORD)
    teacher = User(username=TEACHER_LOGIN, full_name='Петров Пётр Петрович',
                   role=User.ROLE_TEACHER, is_active=True)
    teacher.set_password(TEACHER_PASSWORD)
    # Второй преподаватель нужен, чтобы проверять границы прав: чужой
    # предмет не должен выставляться чужим преподавателем
    other = User(username=OTHER_TEACHER_LOGIN, full_name='Сидоров С. С.',
                 role=User.ROLE_TEACHER, is_active=True)
    other.set_password(OTHER_TEACHER_PASSWORD)
    db.session.add_all([admin, teacher, other])
    db.session.flush()

    group = Group(name='ПС-16', specialty='Программные системы', year=2026)
    other_group = Group(name='ВТ-16', specialty='Вечернее отделение', year=2026)
    db.session.add_all([group, other_group])
    db.session.flush()

    maths = Subject(name='Математика', hours=120, teacher_id=teacher.id)
    history = Subject(name='История', hours=90, teacher_id=teacher.id)
    geography = Subject(name='География', hours=60, teacher_id=other.id)
    db.session.add_all([maths, history, geography])
    db.session.flush()

    first = Student(student_id='ST0016', last_name='Иванов', first_name='Иван',
                    patronymic='Иванович', gender='M', group_id=group.id,
                    status='active')
    second = Student(student_id='ST0017', last_name='Петрова', first_name='Мария',
                     patronymic='Петровна', gender='F', group_id=group.id,
                     status='active')
    db.session.add_all([first, second])
    db.session.flush()

    first_account = User(username=STUDENT_LOGIN, full_name='Иванов Иван Иванович',
                         role=User.ROLE_STUDENT, is_active=True)
    first_account.set_password(STUDENT_PASSWORD)
    second_account = User(username=OTHER_STUDENT_LOGIN, full_name='Петрова Мария Петровна',
                          role=User.ROLE_STUDENT, is_active=True)
    second_account.set_password(STUDENT_PASSWORD)
    db.session.add_all([first_account, second_account])
    db.session.flush()
    first.user_id = first_account.id
    second.user_id = second_account.id

    # Период охватывает всю тестовую дату: журнал и итоги считают по нему
    period = AcademicPeriod(name='I четверть', academic_year='2026-2027',
                            start_date=date(2026, 9, 1), end_date=date(2026, 12, 31),
                            sort_order=1, kind=AcademicPeriod.KIND_QUARTER)
    db.session.add(period)
    db.session.flush()

    # Пара в понедельник на первой паре: сетка журнала и расписание
    maths_slot = ScheduleItem(group_id=group.id, subject_id=maths.id,
                              day_of_week=1, lesson_number=1,
                              teacher_id=teacher.id, room='101')
    history_slot = ScheduleItem(group_id=group.id, subject_id=history.id,
                                day_of_week=3, lesson_number=2,
                                teacher_id=teacher.id, room='102')
    db.session.add_all([maths_slot, history_slot])
    db.session.flush()

    # Занятия «состоялись»: без отметок сетка журнала пуста, а пропуск
    # невозможно поставить — сервер сверяется с реальными занятиями
    for slot, day in ((maths_slot, date(2026, 9, 28)),
                      (history_slot, date(2026, 9, 30))):
        db.session.add(LessonDate(schedule_item_id=slot.id, date=day,
                                  created_by=admin.id))

    # Известные значения для проверки среднего балла: (5 + 4 + 3) / 3 = 4.0.
    # Дата — день реального занятия, иначе оценки не попадут в сетку
    for value, kind in (('5', 'exam'), ('4', 'test'), ('3', 'homework')):
        db.session.add(Grade(student_id=first.id, subject_id=maths.id,
                             grade_value=value, grade_type=kind,
                             date=date(2026, 9, 28), period_id=period.id))
    db.session.add(Grade(student_id=second.id, subject_id=maths.id,
                         grade_value='2', grade_type='exam',
                         date=date(2026, 9, 28), period_id=period.id))

    db.session.add(AttendanceRecord(student_id=first.id, date=date(2026, 9, 30),
                                    reason=AttendanceRecord.REASON_ILLNESS,
                                    subject_id=history.id))

    db.session.add(SystemSettings(academic_year='2026-2027'))
    db.session.commit()


# ---------------------------------------------------------------- хелперы

def page_text(html):
    """Текст страницы без разметки — иначе проверки ищут тег целиком."""
    text = re.sub(r'(?is)<(script|style).*?</\1>', ' ', html)
    text = re.sub(r'(?s)<[^>]+>', ' ', text)
    return re.sub(r'\s+', ' ', text)


def csrf(client, path='/'):
    """Токен со страницы, где есть форма.

    С редиректами: '/' у администратора отвечает 302, и на пустом ответе
    токена нет — тест падал бы не из-за приложения, а из-за маршрута.
    """
    html = client.get(path, follow_redirects=True).get_data(as_text=True)
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    assert match, f'На странице {path} нет CSRF-токена'
    return match.group(1)


def csrf_any(client, *paths):
    """Токен с первой страницы, где форма нашлась.

    Нужно там, где проверяется запрет: у роли нет доступа к запрещённой
    странице, а без токена POST не проверить — тест упал бы на отсутствии
    токена, а не на проверке прав.
    """
    for path in paths:
        html = client.get(path, follow_redirects=True).get_data(as_text=True)
        match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
        if match:
            return match.group(1)
    raise AssertionError(f'Ни на одной из страниц {paths} нет CSRF-токена')


def login(client, username, password):
    """Вход по-настоящему: через POST с токеном, а волшебной сессией."""
    response = client.post('/login', data={
        'csrf_token': csrf(client, '/login'),
        'username': username, 'password': password},
        follow_redirects=True)
    assert 'Добро пожаловать' in page_text(response.get_data(as_text=True)), \
        f'Вход «{username}» не удался'
    return response


def login_admin(client):
    return login(client, ADMIN_LOGIN, ADMIN_PASSWORD)


def login_teacher(client):
    return login(client, TEACHER_LOGIN, TEACHER_PASSWORD)


def login_student(client, username=STUDENT_LOGIN):
    return login(client, username, STUDENT_PASSWORD)


def logout(client):
    # Выход в приложении — ссылка GET, а не форма с токеном: выход не
    # меняет данные, а токен на каждой странице только мешал бы.
    return client.get('/logout', follow_redirects=False)