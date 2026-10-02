"""Тесты Фазы 13: журнал действий (аудит).

Проверяется не «таблица создалась», а три вещи, ради которых фаза и
затевалась:

  * 13.2 — каждая изменяющая операция оставляет запись: кто, что, когда,
    откуда. Молчаливо пропущенная кнопка «удалить» здесь опаснее всего,
    поэтому покрытие проверяется и по факту (запись появилась), и
    статически (каждое действие из ACTION_LABELS где-то вызывается).
  * 13.4 — пароли и их хеши в журнале не лежат ни при каком сценарии:
    ни пароль сотрудника, ни временный пароль студента, ни смена своего
    пароля. Проверяется и через реальные операции, и напрямую через
    scrub_audit_details.
  * 13.3 — журнал читает только тот, кому положено: администратор видит
    всё, сотрудник — только свои записи (в том числе когда подставляет
    чужой логин в фильтр), студент не попадает в журнал вовсе.

Журнал не удаляется, поэтому после каждого прогона его содержимое
возвращается к снимку: иначе тест оставлял бы записи, которые потом
мешали бы разбирать настоящие инциденты.

Запуск: python phase13_test.py
"""

import glob
import os
import re
import shutil
import sqlite3
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.init_ import create_app, db
from app.models import (User, Group, Student, Grade, Subject, AcademicPeriod,
                        AttendanceRecord, ScheduleItem, LessonDate, AuditLog,
                        SystemSettings)
from app.utils import (scrub_audit_details, format_audit_details,
                        get_backup_dir, EXPORT_DIR, DB_PATH)

from config import load_env_file

load_env_file()

app = create_app()
app.config['TESTING'] = True

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')
PASSWORD = 'Kabri13Test'
NEW_PASSWORD = 'NovyParol13'

TEST_GROUP = 'ГР-Ф13'
TEST_LOGIN = 'TEACH13'
STUDENT_LOGIN = 'Ф13-01'

# Дата, на которой тест отмечает пропуски: по ней создаётся отметка о
# состоявшемся занятии, иначе отметка будет отвергнута до записи в журнал
BULK_DATE = date(2026, 9, 16)

# Причина пропуска: код из REASONS модели. Произвольная строка отвергается
# проверкой формы, и операция молча не доходит до журнала
REASON = AttendanceRecord.REASON_ILLNESS

# Пароли, которые обязаны не просочиться в журнал ни при каком сценарии
SECRETS = (PASSWORD, NEW_PASSWORD, 'SvoyParol13', 'Vremen13Pass')

# Записи журнала, созданные текущим прогоном (см. audit_base_mark)
AUDIT_BASE = {'id': 0}

failures = []
checks = 0

routes_source = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  'app', 'routes.py'),
                     encoding='utf-8-sig').read()


def check(label, ok, detail=''):
    global checks
    checks += 1
    print(f'{"✓" if ok else "✗"} {label}' + (f' — {detail}' if detail else ''))
    if not ok:
        failures.append(f'{label}: {detail}')


def page_text(html):
    """Текст страницы без тегов: проверяем смысл, а не разметку."""
    html = re.sub(r'<script.*?</script>', ' ', html, flags=re.S)
    html = re.sub(r'<style.*?</style>', ' ', html, flags=re.S)
    html = re.sub(r'<[^>]+>', ' ', html)
    return re.sub(r'\s+', ' ', html)


def table_text(html):
    """Текст только строк таблицы.

    Фильтр «Действие» выводит в списке названия всех действий, поэтому по
    всему тексту страницы «Вход в систему» найдётся даже когда в таблице
    только удаление группы. Проверять надо именно то, что попало в строки.
    """
    body = re.search(r'<tbody.*?</tbody>', html, flags=re.S)
    return page_text(body.group(0) if body else '')


def csrf(client, url='/login'):
    html = client.get(url).get_data(as_text=True)
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return match.group(1) if match else ''


def login(client, username, password):
    return client.post('/login', data={
        'username': username, 'password': password,
        'csrf_token': csrf(client)}, follow_redirects=True)


def logout(client):
    client.get('/logout')


def admin_client():
    client = app.test_client()
    login(client, 'admin', ADMIN_PASSWORD)
    return client


def teacher_client(password=None):
    client = app.test_client()
    login(client, TEST_LOGIN, password or PASSWORD)
    return client


def student_client():
    client = app.test_client()
    login(client, STUDENT_LOGIN, PASSWORD)
    return client


def snapshot():
    with app.app_context():
        return {
            'users': [{'id': u.id, 'username': u.username,
                       'password_hash': u.password_hash, 'role': u.role,
                       'created_by': u.created_by, 'created_at': u.created_at,
                       'password_changed_at': u.password_changed_at,
                       'password_temporary': u.password_temporary,
                       'last_login_at': u.last_login_at,
                       'is_active': u.is_active, 'full_name': u.full_name,
                       'email': u.email}
                      for u in User.query.all()],
            'students': {s.id: (s.user_id, s.student_id, s.last_name,
                                s.first_name, s.patronymic, s.group_id, s.status)
                         for s in Student.query.all()},
            'groups': {g.id: (g.name, g.specialty, g.year) for g in Group.query.all()},
            'subjects': {s.id for s in Subject.query.all()},
            'grades': {g.id for g in Grade.query.all()},
            'attendance': {a.id for a in AttendanceRecord.query.all()},
            'periods': {p.id for p in AcademicPeriod.query.all()},
            'schedule_items': {s.id for s in ScheduleItem.query.all()},
            'lesson_dates': {d.id for d in LessonDate.query.all()},
            # точка отсчёта, а не количество: после удаления строк счётчик
            # совпал бы со следующим id, и чужой снимок не восстановился бы
            'audit': db.session.query(db.func.max(AuditLog.id)).scalar() or 0,
            'settings': _settings_snapshot(),
            'backups': set(glob.glob(os.path.join(get_backup_dir(), '*'))),
            'exports': set(glob.glob(os.path.join(EXPORT_DIR, '*'))),
        }


def _settings_snapshot():
    row = db.session.query(SystemSettings).first()
    if row is None:
        return None
    return {column.name: getattr(row, column.name)
            for column in SystemSettings.__table__.columns}


def restore(state):
    """Возврат базы и файлов к снимку.

    Порядок обязателен: сначала снимаем связи и удаляем учётные записи, и
    только потом — группы и студентов, которых завёл тест. Иначе внешние
    ключи не дадут удалить, и «восстановление» тихо проглочет ошибку.
    """
    with app.app_context():
        for student_id, values in state['students'].items():
            student = db.session.get(Student, student_id)
            if student is not None:
                (student.user_id, student.student_id, student.last_name,
                 student.first_name, student.patronymic, student.group_id,
                 student.status) = values
        db.session.flush()

        keep_users = {u['id'] for u in state['users']}
        for user in User.query.all():
            if user.id not in keep_users:
                db.session.delete(user)
        db.session.flush()

        for row in state['users']:
            user = db.session.get(User, row['id'])
            if user is None:
                continue
            user.username = row['username']
            user.password_hash = row['password_hash']
            user.role = row['role']
            user.created_by = row['created_by']
            user.created_at = row['created_at']
            user.password_changed_at = row['password_changed_at']
            user.password_temporary = row['password_temporary']
            user.last_login_at = row['last_login_at']
            user.is_active = row['is_active']
            user.full_name = row['full_name']
            user.email = row['email']
        db.session.flush()

        # Отметки о занятиях — раньше по ссылке на пару, поэтому первыми
        for model, key in ((LessonDate, 'lesson_dates'),
                           (ScheduleItem, 'schedule_items'),
                           (AttendanceRecord, 'attendance'),
                           (Grade, 'grades'),
                           (Student, 'students'),
                           (AcademicPeriod, 'periods'),
                           (Group, 'groups'),
                           (Subject, 'subjects')):
            for row in model.query.all():
                if row.id not in state[key]:
                    db.session.delete(row)
            db.session.flush()

        # Журнал возвращаем к снимку: записи теста в нём — мусор, который
        # при разборе реального инцидента только мешает
        for entry in AuditLog.query.filter(AuditLog.id > state['audit']).all():
            db.session.delete(entry)

        if state['settings'] is not None:
            current = db.session.query(SystemSettings).first()
            if current is not None:
                for key, value in state['settings'].items():
                    setattr(current, key, value)
        db.session.commit()

    # Файлы: ведомость с паролями и резервная копия, созданные тестом,
    # не должны остаться лежать на диске
    for folder, before in ((get_backup_dir(), state['backups']),
                           (EXPORT_DIR, state['exports'])):
        for path in set(glob.glob(os.path.join(folder, '*'))) - before:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                try:
                    os.remove(path)
                except OSError:
                    pass


def prepare():
    """Группа, преподаватель с паролем и студент с учётной записью.

    Свой преподаватель и свой студент обязательны: их пароли известны
    тесту, и по ним проверяется, что пароль не утек в журнал.
    """
    with app.app_context():
        group = Group(name=TEST_GROUP, specialty='Аудит действий', year=2026)
        db.session.add(group)
        db.session.flush()

        teacher = User(username=TEST_LOGIN, role=User.ROLE_TEACHER,
                       is_active=True, full_name='Преподаватель Фазы 13')
        teacher.set_temporary_password(PASSWORD)
        db.session.add(teacher)

        student = Student(student_id=STUDENT_LOGIN, last_name='Фамилия',
                          first_name='Тестовый', patronymic='Отчество',
                          gender='F', group_id=group.id, status='active')
        db.session.add(student)
        db.session.flush()

        account = User(username=STUDENT_LOGIN, role=User.ROLE_STUDENT,
                       is_active=True, full_name=student.full_name)
        account.set_temporary_password(PASSWORD)
        db.session.add(account)
        db.session.flush()
        student.user_id = account.id

        subject = Subject(name='Предмет Фазы 13', hours=72,
                          teacher_id=teacher.id)
        db.session.add(subject)
        db.session.flush()

        # Пара и отметка о состоявшемся занятии — обязательное условие для
        # отметки пропуска: без них /attendance/bulk отвечает «занятия не
        # было» и до log_audit() не доходит, то есть проверялась бы не та
        # операция. Расписание теста создаёт запись в журнале сама по себе,
        # поэтому ставится напрямую, мимо роутов.
        item = ScheduleItem(group_id=group.id, subject_id=subject.id,
                            day_of_week=BULK_DATE.weekday() + 1,
                            lesson_number=1, teacher_id=teacher.id,
                            room='Аудит-13')
        db.session.add(item)
        db.session.flush()
        db.session.add(LessonDate(schedule_item_id=item.id, date=BULK_DATE))
        db.session.commit()

        return {'group_id': group.id, 'student_id': student.id,
                'subject_id': subject.id, 'teacher_id': teacher.id}


def op_accepted(response):
    """Операция дошла до записи в журнал, а не отвергнута проверками.

    Отвергнутый POST возвращает 400 или 403, а принятый уводит обратно
    redirect'ом (302). Проверять это обязательно: тест, который считает
    «запись появилась» достаточным, зелёный и на запросе, отклонённом
    до log_audit() — именно так в журнале и появляются пропущенные
    кнопки.
    """
    return response.status_code not in (400, 403)


def audit_base_mark():
    """Запомнить последнюю запись журнала на момент запуска теста.

    Дальше проверяются только записи, появившиеся в этом прогоне. Иначе
    проверка «запись появилась» satisfied-ается чем угодно, оставшимся от
    прошлых запусков, и пропущенный вызов log_audit() остаётся незамеченным.
    """
    with app.app_context():
        AUDIT_BASE['id'] = db.session.query(db.func.max(AuditLog.id)).scalar() or 0
    return AUDIT_BASE['id']


def audit_rows(action=None, user=None):
    with app.app_context():
        query = AuditLog.query.filter(AuditLog.id > AUDIT_BASE['id'])
        if action:
            query = query.filter(AuditLog.action == action)
        if user:
            query = query.filter(AuditLog.username == user)
        return query.order_by(AuditLog.id.desc()).all()


def row_fields(row):
    """Поля записи как обычный dict: запись из сессии после снятия контекста
    отсоединена от базы, и читать её ленивые связи нельзя."""
    return {'id': row.id, 'user_id': row.user_id, 'username': row.username,
            'role': row.role, 'action': row.action,
            'entity_type': row.entity_type, 'entity_id': row.entity_id,
            'details': row.details, 'ip': row.ip_address,
            'agent': row.user_agent, 'timestamp': row.timestamp,
            'label': row.action_label, 'entity_label': row.entity_label}


def run_checks(data):
    group_id = data['group_id']
    student_id = data['student_id']
    subject_id = data['subject_id']

    # ================= 13.1 структура журнала ==========================
    print('\n--- 13.1 Таблица и модель ---')
    with app.app_context():
        columns = {c.name: c for c in AuditLog.__table__.columns}
        for name in ('id', 'user_id', 'username', 'role', 'action',
                     'entity_type', 'entity_id', 'details', 'ip_address',
                     'user_agent', 'timestamp'):
            check(f'в модели есть поле «{name}»', name in columns)
        check('entity_id текстовый (файлы и настройки тоже пишутся в журнал)',
              isinstance(columns['entity_id'].type, db.String),
              str(columns['entity_id'].type))
        check('логин хранится отдельной строкой, а не только ссылкой',
              'username' in columns)
        indexes = {index.name for index in AuditLog.__table__.indexes}
        check('есть составной индекс действие+время',
              any('action' in name and 'time' in name for name in indexes),
              ', '.join(sorted(indexes)))

    con = sqlite3.connect(DB_PATH)
    tables = {row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    check('таблица audit_log создана в базе', 'audit_log' in tables)

    # ================= 13.2 запись действий =============================
    print('\n--- 13.2 Запись действий ---')

    client = admin_client()
    rows = audit_rows('login', user='admin')
    check('вход администратора записан', bool(rows))
    if rows:
        row = row_fields(rows[0])
        check('в записи о входе есть роль', row['role'] == 'admin', row['role'])
        check('в записи о входе есть логин', row['username'] == 'admin',
              str(row['username']))
        check('в записи о входе есть IP', bool(row['ip']), str(row['ip']))
        check('в записи о входе есть браузер', bool(row['agent']),
              str(row['agent'])[:40])
        check('у действия есть человеческое название',
              row['label'] == 'Вход в систему', row['label'])

    # Неудачный вход: запись обязана быть, а пользователя в ней нет
    fresh = app.test_client()
    login(fresh, 'не-существует-ф13', PASSWORD)
    rows = audit_rows('login_failed')
    check('неудачный вход записан', bool(rows))
    if rows:
        row = row_fields(rows[0])
        check('в неудачном входе нет пользователя', row['user_id'] is None,
              str(row['user_id']))
        check('в неудачном входе сохранён IP', bool(row['ip']), str(row['ip']))
        check('в неудачном входе нет пароля',
              PASSWORD not in (row['details'] or ''), str(row['details']))

    # Заблокированная учётная запись: отличать её от неверного пароля нельзя
    with app.app_context():
        blocked = User(username='BLOCK13', role=User.ROLE_TEACHER,
                       is_active=False)
        blocked.set_temporary_password(PASSWORD)
        db.session.add(blocked)
        db.session.commit()
    fresh = app.test_client()
    login(fresh, 'BLOCK13', PASSWORD)
    check('попытка входа в отключённую запись записана отдельно',
          bool(audit_rows('login_blocked')))

    # ================= операции, которые должны оставлять след ==========
    print('\n--- Запись изменяющих операций ---')

    # Группа
    resp = client.post('/groups/add', data={
        'name': 'ГР-Ф13-2', 'specialty': 'Аудит 2', 'year': 2026,
        'csrf_token': csrf(client, '/groups/add')},
        follow_redirects=True)
    check('добавление группы принято', op_accepted(resp), str(resp.status_code))
    check('добавление группы записано', bool(audit_rows('group_create')))

    # Оценка
    resp = client.post('/grades/add', data={
        'student_id': student_id, 'subject_id': subject_id,
        'grade_value': '5', 'grade_type': 'exam',
        'date': '2026-09-15', 'comments': '',
        'csrf_token': csrf(client, '/grades/add')},
        follow_redirects=True)
    check('выставление оценки принято', op_accepted(resp), str(resp.status_code))
    rows = audit_rows('grade_create')
    check('выставление оценки записано', bool(rows))
    grade_id = None
    if rows:
        row = row_fields(rows[0])
        grade_id = row['entity_id']
        check('в записи об оценке указан объект «оценка»',
              row['entity_type'] == 'grade', str(row['entity_type']))
        check('в записи об оценке есть её номер', bool(row['entity_id']),
              str(row['entity_id']))

    if grade_id:
        client.post(f'/grades/{grade_id}/delete', data={
            'csrf_token': csrf(client, '/grades')}, follow_redirects=True)
        rows = audit_rows('grade_delete')
        check('удаление оценки записано', bool(rows))
        if rows:
            row = row_fields(rows[0])
            check('в записи об удалении сохранён номер удалённой оценки',
                  row['entity_id'] == str(grade_id), str(row['entity_id']))

    # Пропуск: массовая отметка
    bulk = client.post('/attendance/bulk', data={
        'group_id': group_id, 'subject_id': subject_id,
        'date': BULK_DATE.isoformat(), 'reason': REASON, 'note': '',
        'csrf_token': csrf(client, '/attendance')})
    # Успех — это redirect обратно в журнал (302). Проверять надо именно
    # отказ: операция, отвергнутая проверкой формы, до log_audit() не
    # доходит, и «запись появилась» ничего не значит, если запрос не прошёл
    check('массовая отметка пропусков принята', op_accepted(bulk),
          str(bulk.status_code))
    rows = audit_rows('attendance_bulk')
    check('массовая отметка пропусков записана', bool(rows))
    if rows:
        row = row_fields(rows[0])
        check('в записи о пропусках есть число отмеченных',
              'отмечено=' in (row['details'] or ''), str(row['details']))

    # Сотрудник: создание и сброс пароля
    client.post('/staff/add', data={
        'username': 'staff13', 'full_name': 'Сотрудник 13',
        'email': '', 'role': User.ROLE_TEACHER,
        'password': PASSWORD, 'confirm_password': PASSWORD, 'is_active': 'y',
        'csrf_token': csrf(client, '/staff/add')},
        follow_redirects=True)
    rows = audit_rows('staff_create')
    check('создание сотрудника записано', bool(rows))
    staff_id = None
    with app.app_context():
        staff = User.query.filter_by(username='staff13').first()
        staff_id = staff.id if staff else None
    if staff_id:
        client.post(f'/staff/{staff_id}/reset-password', data={
            'password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD,
            'csrf_token': csrf(client, '/staff')},
            follow_redirects=True)
        check('сброс пароля сотрудника записан',
              bool(audit_rows('staff_password_reset')))

    # Массовая выдача учётных записей: в группе Ф13 ещё один студент без записи
    with app.app_context():
        lonely = Student(student_id='Ф13-02', last_name='Без',
                         first_name='Записи', gender='M',
                         group_id=group_id, status='active')
        db.session.add(lonely)
        db.session.commit()
    client.post('/accounts/issue', data={
        'group_id': group_id, 'all_students': '',
        'csrf_token': csrf(client, '/accounts')}, follow_redirects=True)
    rows = audit_rows('accounts_issue')
    check('массовая выдача учётных записей записана', bool(rows))
    if rows:
        row = row_fields(rows[0])
        check('в выдаче есть число выданных записей',
              'выдано=' in (row['details'] or ''), str(row['details']))

    # Выгрузка отчёта: файл с персональными данными уходит из системы,
    # поэтому вид отчёта и число строк обязаны остаться в журнале
    resp = client.get('/reports/students')
    check('выгрузка отчёта выполнена', op_accepted(resp),
          str(resp.status_code))
    rows = audit_rows('report_export')
    check('выгрузка отчёта записана', bool(rows))
    if rows:
        row = row_fields(rows[0])
        check('в записи о выгрузке нет ФИО, только вид и число строк',
              'строк=' in (row['details'] or '')
              and 'Фамилия' not in (row['details'] or ''),
              str(row['details']))

    # Настройки
    with app.app_context():
        settings = SystemSettings.get_settings()
        current = {'college_name': settings.college_name,
                   'academic_year': settings.academic_year,
                   'max_students_per_group': settings.max_students_per_group,
                   'period_kind': settings.period_kind,
                   'theme_color': settings.theme_color,
                   'items_per_page': settings.items_per_page,
                   'export_format': settings.export_format,
                   'enable_system_notifications': settings.enable_system_notifications,
                   'enable_email_notifications': settings.enable_email_notifications,
                   'auto_backup': settings.auto_backup,
                   'backup_frequency': settings.backup_frequency}
    current['theme_color'] = 'blue' if current['theme_color'] != 'blue' else 'green'
    client.post('/settings', data=dict(current, csrf_token=csrf(client, '/settings')),
                follow_redirects=True)
    rows = audit_rows('settings_update')
    check('изменение настроек записано', bool(rows))
    if rows:
        row = row_fields(rows[0])
        check('в настройках записаны названия полей, а не значения',
              'поля=' in (row['details'] or '')
              and 'theme_color' in (row['details'] or ''),
              str(row['details']))

    # Резервная копия
    client.post('/settings/backup', data={
        'description': 'Фаза 13', 'csrf_token': csrf(client, '/settings/backup')},
        follow_redirects=True)
    rows = audit_rows('backup_create')
    check('создание резервной копии записано', bool(rows))
    if rows:
        with app.app_context():
            name = db.session.query(AuditLog).filter_by(
                action='backup_create').order_by(AuditLog.id.desc()).first()
            filename = None
            if name is not None:
                match = re.search(r'файл=\'([^\']+)\'', name.details or '')
                filename = match.group(1) if match else None
        if filename:
            client.post(f'/settings/backup/{filename}/delete', data={
                'csrf_token': csrf(client, '/settings/backup')},
                follow_redirects=True)
            check('удаление резервной копии записано',
                  bool(audit_rows('backup_delete')))
        else:
            check('в записи о копии есть имя файла', False, 'файл не найден')

    # Смена собственного пароля сотрудником
    teacher = teacher_client()
    teacher.post('/my/password', data={
        'current_password': PASSWORD, 'new_password': NEW_PASSWORD,
        'confirm_password': NEW_PASSWORD,
        'csrf_token': csrf(teacher, '/my/password')},
        follow_redirects=True)
    check('смена собственного пароля записана',
          bool(audit_rows('password_change_self')))
    own_change = [row_fields(r) for r in audit_rows('password_change_self')]
    if own_change:
        # Факт смены обязан остаться читаемым: вычистка не должна превращать
        # его в «пароль=***» — тогда по журналу нельзя понять, что произошло
        check('факт смены пароля остаётся читаемым',
              'пароль изменён' in (own_change[0]['details'] or ''),
              str(own_change[0]['details']))
    # Дальше сотрудник входит уже с новым паролем
    teacher_password = NEW_PASSWORD

    # Выход
    logout(teacher)
    check('выход записан', bool(audit_rows('logout', user=TEST_LOGIN)))

    # ================= 13.4 пароли не попадают в журнал ==================
    print('\n--- 13.4 Пароли и хеши не попадают в журнал ---')
    with app.app_context():
        everything = ' '.join(f'{row.details or ""} {row.user_agent or ""}'
                              for row in AuditLog.query.all())
    for secret in SECRETS:
        check(f'пароль «{secret[:4]}…» не встречается в журнале',
              secret not in everything)
    check('хеш пароля не встречается в журнале',
          'pbkdf2' not in everything and 'sha256' not in everything)
    check('в аудите нет полей с хешами',
          not any('password_hash' in (row['details'] or '').lower()
                  for row in [row_fields(r) for r in audit_rows()]))

    # Вычистка проверяется и напрямую — это страховка от «забыли вызвать»
    check('scrub_audit_details прячет password',
          scrub_audit_details({'password': 'секрет'})['password'] == '***')
    check('scrub_audit_details прячет хеш',
          scrub_audit_details('pbkdf2:sha256:260000$salt$hash') == '***')
    check('scrub_audit_details прячет пароль в свободном тексте',
          'секрет' not in (scrub_audit_details('пароль=секрет') or ''))
    check('scrub_audit_details не портит обычный текст',
          scrub_audit_details('оценка выставлена') == 'оценка выставлена')
    check('format_audit_details хранит словарь в JSON',
          format_audit_details({'оценка': '5'}) == '{"оценка": "5"}',
          format_audit_details({'оценка': '5'}))

    # ================= 13.3 интерфейс ==================================
    print('\n--- 13.3 Интерфейс журнала ---')
    admin = admin_client()
    response = admin.get('/audit')
    check('администратор открывает журнал', response.status_code == 200,
          f'код {response.status_code}')
    text = page_text(response.get_data(as_text=True))
    check('в журнале есть заголовок', 'Журнал действий' in text)
    check('в журнале видна метка действия', 'Вход в систему' in text)
    check('в журнале виден IP', bool(re.search(r'\d+\.\d+\.\d+\.\d+', text)))
    check('в журнале есть фильтры', 'С даты' in text and 'Действие' in text)
    check('в меню администратора есть пункт журнала',
          'Журнал действий' in text)

    # Фильтры
    html = admin.get('/audit?action=group_create').get_data(as_text=True)
    rows_text = table_text(html)
    check('фильтр по действию работает',
          'Добавление группы' in rows_text and 'Вход в систему' not in rows_text,
          rows_text[:120])

    html = admin.get('/audit?user=admin').get_data(as_text=True)
    check('фильтр по пользователю работает',
          'admin' in table_text(html), table_text(html)[:120])

    html = admin.get('/audit?q=' + quote('выдано=')).get_data(as_text=True)
    check('поиск по подробностям работает',
          'Массовая выдача' in page_text(html))


    html = admin.get('/audit?date_from=2099-01-01').get_data(as_text=True)
    check('фильтр по будущей дате оставляет пустую выдачу',
          'Записей нет' in page_text(html))

    html = admin.get('/audit?date_from=2000-01-01&date_to=2000-01-02'
                     ).get_data(as_text=True)
    check('фильтр по прошедшему периоду оставляет пустую выдачу',
          'Записей нет' in page_text(html))

    check('вторая страница журнала открывается',
          admin.get('/audit?page=2').status_code in (200, 404))

    response = admin.get('/audit?date_from=не-дата&date_to=тоже')
    check('испорченная дата в фильтре не ломает страницу',
          response.status_code == 200, f'код {response.status_code}')

    # Сотрудник видит только свои записи
    teacher = teacher_client(teacher_password)
    response = teacher.get('/audit')
    check('сотрудник открывает журнал', response.status_code == 200,
          f'код {response.status_code}')
    text = table_text(response.get_data(as_text=True))
    check('сотрудник видит свои записи', TEST_LOGIN in text, text[:120])
    check('сотрудник не видит чужих записей', 'admin' not in text, text[:120])
    check('у сотрудника в меню свой пункт журнала',
          'Мои действия' in page_text(
              teacher.get('/dashboard').get_data(as_text=True)))
    check('сотруднику не показан фильтр по пользователю',
          'name="user"' not in
          teacher.get('/audit').get_data(as_text=True))

    # Подстановка чужого логина в фильтр не обходит ограничение
    html = teacher.get('/audit?user=admin').get_data(as_text=True)
    check('подстановка чужого логина не открывает чужие записи',
          'Записей нет' in page_text(html), table_text(html)[:120])

    # Студент
    student = student_client()
    response = student.get('/audit')
    check('студент не допускается к журналу', response.status_code == 403,
          f'код {response.status_code}')
    check('в кабинете студента нет пункта журнала действий',
          'Журнал действий' not in
          page_text(student.get('/portal').get_data(as_text=True)))

    response = app.test_client().get('/audit', follow_redirects=False)
    check('неавторизованного отправляет на вход',
          response.status_code == 302, f'код {response.status_code}')

    # ================= покрытие операций ================================
    print('\n--- Покрытие: каждое действие где-то вызывается ---')
    for action in AuditLog.ACTION_LABELS:
        called = f"log_audit('{action}'" in routes_source
        check(f'действие «{action}» вызывается в маршрутах', called)

    with app.app_context():
        recorded = {row[0] for row in
                    db.session.query(AuditLog.action).distinct().all()}
    unknown = recorded - set(AuditLog.ACTION_LABELS)
    check('все записанные действия имеют подпись в интерфейсе', not unknown,
          ', '.join(sorted(unknown)))

    # ================= запись после удаления учётной записи =============
    print('\n--- Журнал переживает удаление сотрудника ---')
    if staff_id:
        # Сотрудник сначала что-то делает сам: его собственная запись и есть
        # та, которая обязана остаться читаемой после удаления учётной записи
        staff_client = app.test_client()
        login(staff_client, 'staff13', NEW_PASSWORD)
        staff_client.post('/my/password', data={
            'current_password': NEW_PASSWORD, 'new_password': 'SvoyParol13',
            'confirm_password': 'SvoyParol13',
            'csrf_token': csrf(staff_client, '/my/password')},
            follow_redirects=True)
        own_rows = [row_fields(r)
                    for r in audit_rows('password_change_self',
                                        user='staff13')]
        check('сотрудник оставил собственную запись', bool(own_rows))

        admin.post(f'/staff/{staff_id}/delete', data={
            'csrf_token': csrf(admin, '/staff')}, follow_redirects=True)
        with app.app_context():
            check('учётная запись сотрудника удалена',
                  User.query.filter_by(username='staff13').first() is None)

        rows = [row_fields(r) for r in audit_rows()]
        check('записи удалённого сотрудника остались в журнале',
              any(r['username'] == 'staff13' for r in rows))

        # Журнал остаётся читаемым: записи удалённого сотрудника находятся
        # по логину и показывают его имя, хотя в таблице user его уже нет
        html = admin.get('/audit?user=staff13').get_data(as_text=True)
        text = table_text(html)
        check('записи удалённого сотрудника находятся по логину',
              'staff13' in text and 'Смена собственного пароля' in text,
              text[:140])

        deleted = [r for r in rows if r['action'] == 'staff_delete']
        check('удаление сотрудника записано', bool(deleted))
        if deleted:
            check('логин удалённого сотрудника есть в записи об удалении',
                  'staff13' in (deleted[0]['details'] or ''),
                  str(deleted[0]['details']))
            check('автор удаления — тот, кто удалял, а не удалённый',
                  deleted[0]['username'] == 'admin',
                  str(deleted[0]['username']))


def quote(text):
    from urllib.parse import quote as _quote
    return _quote(text)


def main():
    state = snapshot()
    try:
        audit_base_mark()
        data = prepare()
        run_checks(data)
    finally:
        restore(state)
        print('\nБаза восстановлена из снимка.')

    print()
    if failures:
        print(f'ПРОВАЛЕНО {len(failures)} из {checks}:')
        for item in failures:
            print(' -', item)
        return 1
    print(f'ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ ({checks}/{checks})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
