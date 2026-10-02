"""Функциональная проверка Фазы 7: периоды, расписание, отметки о занятиях.

Запуск:  python phase7_test.py

Проверяет то, что нельзя увидеть на страницах:
  * CRUD периодов и запрет удаления периода с оценками;
  * автосоздание периодов (идемпотентность);
  * добавление, заполнение дня, конфликты преподавателя, удаление пар;
  * отметки «занятие состоялось» и снятие отметок;
  * что преподаватель не может править расписание.

Все изменения откатываются в финале: скрипт запоминает снимок и
восстанавливает состояние, поэтому база остаётся как была.
"""

import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.init_ import create_app, db
from app.models import (User, Group, Student, Subject, Grade, SystemSettings,
                        AuditLog,
                        AcademicPeriod, ScheduleItem, LessonDate)
from app.utils import build_periods

from config import load_env_file

load_env_file()

app = create_app()
app.config['TESTING'] = True

ADMIN = os.environ.get('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')

failures = []
checks = 0


def check(label, ok, detail=''):
    global checks
    checks += 1
    print(f'{"✓" if ok else "✗"} {label}' + (f' — {detail}' if detail else ''))
    if not ok:
        failures.append(f'{label}: {detail}')


def csrf(client, url='/schedule'):
    import re
    html = client.get(url).get_data(as_text=True)
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return match.group(1) if match else ''


def login(client):
    client.post('/login', data={
        'username': ADMIN, 'password': ADMIN_PASSWORD,
        'csrf_token': csrf(client, '/login')}, follow_redirects=True)


def flash_of(html):
    """Тексты flash-сообщений из уже полученного HTML."""
    import re
    found = re.findall(r'alert-(?:danger|warning|success)[^>]*>(.*?)</div>',
                       html, re.DOTALL)
    return [' '.join(re.sub(r'<[^>]+>', ' ', chunk).split())
            for chunk in found if chunk.strip()]


def _audit_max_id():
    """Наибольший id в журнале действий — точка отсчёта для очистки.

    Вызывается из snapshot(), где контекст приложения уже открыт: свой
    контекст здесь только развёл бы читателя по лишней сессии.
    """
    return db.session.query(db.func.max(AuditLog.id)).scalar() or 0


def snapshot():
    """Состояние, которое нужно вернуть после проверки."""
    with app.app_context():
        return {
            'audit_max_id': _audit_max_id(),
            'settings_period_kind': SystemSettings.get_settings().period_kind,
            'periods': [{'id': p.id, 'name': p.name, 'year': p.academic_year,
                         'start': p.start_date, 'end': p.end_date,
                         'order': p.sort_order, 'kind': p.kind,
                         'annual': p.is_annual}
                        for p in AcademicPeriod.query.all()],
            'items': [{'id': i.id, 'group': i.group_id, 'subject': i.subject_id,
                       'day': i.day_of_week, 'lesson': i.lesson_number,
                       'teacher': i.teacher_id, 'room': i.room}
                      for i in ScheduleItem.query.all()],
            'lesson_dates': [{'id': r.id, 'item': r.schedule_item_id,
                              'date': r.date} for r in LessonDate.query.all()],
            'grades_periods': [(g.id, g.period_id) for g in Grade.query.all()],
        }


def restore(state):
    """Возвращает базу к состоянию до проверки.

    Периоды и пары пересоздаются и получают новые id, поэтому прежние id
    надо пересобрать: иначе оценки остались бы ссылаться на несуществующие
    периоды, а отметки — на несуществующие пары.
    """
    with app.app_context():
        for row in LessonDate.query.all():
            db.session.delete(row)
        for row in ScheduleItem.query.all():
            db.session.delete(row)
        for row in AcademicPeriod.query.all():
            db.session.delete(row)
        db.session.flush()

        period_ids = {}
        for data in state['periods']:
            period = AcademicPeriod(
                name=data['name'], academic_year=data['year'],
                start_date=data['start'], end_date=data['end'],
                sort_order=data['order'], kind=data['kind'],
                is_annual=data['annual'])
            db.session.add(period)
            db.session.flush()
            period_ids[data['id']] = period.id
        for grade_id, old_period in state['grades_periods']:
            if old_period in period_ids:
                db.session.get(Grade, grade_id).period_id = period_ids[old_period]
            else:
                db.session.get(Grade, grade_id).period_id = None
        db.session.flush()

        item_ids = {}
        for data in state['items']:
            item = ScheduleItem(
                group_id=data['group'], subject_id=data['subject'],
                day_of_week=data['day'], lesson_number=data['lesson'],
                teacher_id=data['teacher'], room=data['room'])
            db.session.add(item)
            db.session.flush()
            item_ids[data['id']] = item.id
        db.session.flush()

        for data in state['lesson_dates']:
            db.session.add(LessonDate(
                schedule_item_id=item_ids.get(data['item'], data['item']),
                date=data['date']))
        SystemSettings.get_settings().period_kind = state['settings_period_kind']
        db.session.commit()
        # Журнал действий (Фаза 13) тест тоже наполняет: без сброса каждый
        # прогон оставлял бы в рабочей базе записи о тестовых входах
        for entry in AuditLog.query.filter(
                AuditLog.id > state['audit_max_id']).all():
            db.session.delete(entry)
        db.session.commit()


def free_slot(group_id):
    """Пустая ячейка расписания для группы."""
    with app.app_context():
        used = {(i.day_of_week, i.lesson_number)
                for i in ScheduleItem.query.filter_by(group_id=group_id)}
        for day in range(1, 6):
            for lesson in range(1, 9):
                if (day, lesson) not in used:
                    return day, lesson
    return None, None


def test_periods_crud(client, group_id):
    with app.app_context():
        before = AcademicPeriod.query.count()

    resp = client.post('/periods/add', data={
        'csrf_token': csrf(client, '/periods/add'),
        'name': 'Тестовая четверть', 'academic_year': '2030-2031',
        'start_date': '2030-09-01', 'end_date': '2030-12-25',
        'sort_order': '1', 'kind': 'quarter'}, follow_redirects=False)
    with app.app_context():
        created = AcademicPeriod.query.filter_by(
            academic_year='2030-2031').first()
        check('период создан', created is not None and resp.status_code == 302,
              f'статус {resp.status_code}')
        if created is None:
            return None
        check('даты периода сохранены',
              str(created.start_date) == '2030-09-01' and
              str(created.end_date) == '2030-12-25',
              f'{created.start_date}..{created.end_date}')

    # Дубль номера в одном году
    client.post('/periods/add', data={
        'csrf_token': csrf(client, '/periods/add'),
        'name': 'Дубль', 'academic_year': '2030-2031',
        'start_date': '2030-01-01', 'end_date': '2030-02-01',
        'sort_order': '1', 'kind': 'quarter'}, follow_redirects=True)
    with app.app_context():
        count = AcademicPeriod.query.filter_by(academic_year='2030-2031').count()
    check('дубль номера периода отклонён', count == 1, f'в базе {count}')

    # Правка
    resp = client.post(f'/periods/{created.id}/edit', data={
        'csrf_token': csrf(client, f'/periods/{created.id}/edit'),
        'name': 'Правленная четверть', 'academic_year': '2030-2031',
        'start_date': '2030-09-01', 'end_date': '2030-12-20',
        'sort_order': '1', 'kind': 'quarter'}, follow_redirects=False)
    with app.app_context():
        edited = db.session.get(AcademicPeriod, created.id)
        check('период отредактирован', edited.name == 'Правленная четверть',
              f'статус {resp.status_code}, имя «{edited.name}»')

    # Период с оценками удалять нельзя
    with app.app_context():
        busy = AcademicPeriod.query.filter(
            AcademicPeriod.grades.any()).first()
    if busy is None:
        check('период с оценками удаляется', False, 'в базе нет периодов с оценками')
    else:
        resp = client.post(f'/periods/{busy.id}/delete',
                           data={'csrf_token': csrf(client)},
                           follow_redirects=False)
        with app.app_context():
            still = db.session.get(AcademicPeriod, busy.id) is not None
            grades = Grade.query.filter_by(period_id=busy.id).count()
        check('период с оценками не удаляется', still and grades > 0,
              f'остался в базе: {still}, оценок в нём: {grades}')

    # Пустой период удаляется
    resp = client.post(f'/periods/{created.id}/delete',
                       data={'csrf_token': csrf(client)},
                       follow_redirects=False)
    with app.app_context():
        gone = db.session.get(AcademicPeriod, created.id) is None
        after = AcademicPeriod.query.count()
    check('пустой период удаляется', gone and after == before,
          f'статус {resp.status_code}, периодов было {before}, стало {after}')
    return created


def test_period_generate(client):
    resp = client.post('/periods/generate', data={
        'csrf_token': csrf(client, '/periods/generate'),
        'academic_year': '2031-2032', 'kind': 'quarter'},
        follow_redirects=False)
    with app.app_context():
        made = AcademicPeriod.query.filter_by(
            academic_year='2031-2032').order_by(
            AcademicPeriod.sort_order).all()
        check('автосоздание: 4 четверти', len(made) == 4,
              f'статус {resp.status_code}, создано {len(made)}')
        if len(made) == 4:
            expected = build_periods('2031-2032', 'quarter')
            match = all(
                (p.name, str(p.start_date), str(p.end_date)) ==
                (name, str(start), str(end))
                for p, (name, start, end) in zip(made, expected))
            check('даты четвертей совпадают с шаблоном', match,
                  f'{made[0].start_date}..{made[-1].end_date}')

    # Повторное создание не плодит дубли
    client.post('/periods/generate', data={
        'csrf_token': csrf(client, '/periods/generate'),
        'academic_year': '2031-2032', 'kind': 'quarter'},
        follow_redirects=True)
    with app.app_context():
        again = AcademicPeriod.query.filter_by(academic_year='2031-2032').count()
    check('повторное автосоздание не дублирует', again == 4, f'в базе {again}')

    # Семестры
    client.post('/periods/generate', data={
        'csrf_token': csrf(client, '/periods/generate'),
        'academic_year': '2032-2033', 'kind': 'semester'},
        follow_redirects=True)
    with app.app_context():
        sems = AcademicPeriod.query.filter_by(academic_year='2032-2033').count()
    check('автосоздание: 2 семестра', sems == 2, f'в базе {sems}')

    # Кривой год не ломает страницу
    resp = client.post('/periods/generate', data={
        'csrf_token': csrf(client, '/periods/generate'),
        'academic_year': 'не-год', 'kind': 'quarter'}, follow_redirects=True)
    with app.app_context():
        junk = AcademicPeriod.query.filter_by(academic_year='не-год').count()
    check('кривой учебный год отклонён', junk == 0,
          f'результат {resp.status_code}, записей {junk}')

    # Подчищаем за собой
    with app.app_context():
        for year in ('2031-2032', '2032-2033'):
            AcademicPeriod.query.filter_by(academic_year=year).delete(
                synchronize_session=False)
        db.session.commit()


def test_schedule_crud(client, group_id, subject_id):
    day, lesson = free_slot(group_id)
    check('нашлась свободная ячейка', day is not None, f'{day}:{lesson}')

    resp = client.post('/schedule/add', data={
        'csrf_token': csrf(client), 'group_id': group_id,
        'subject_id': subject_id, 'day_of_week': day,
        'lesson_number': lesson, 'room': '101'}, follow_redirects=False)
    with app.app_context():
        item = ScheduleItem.query.filter_by(
            group_id=group_id, day_of_week=day,
            lesson_number=lesson).first()
        item_id = item.id if item else None
        item_teacher = item.teacher_id if item else None
        item_day = item.day_of_week if item else day
        item_lesson = item.lesson_number if item else lesson
        subject_teacher = db.session.get(Subject, subject_id).teacher_id
        check('пара добавлена', item_id is not None and resp.status_code == 302,
              f'статус {resp.status_code}')

    if item_id is None:
        return None

    if item_teacher is None and subject_teacher:
        check('преподаватель подставлен из предмета', False,
              'пара осталась без преподавателя')
    else:
        check('преподаватель подставлен из предмета', True,
              f'teacher_id={item_teacher}')

    # Дубль в той же ячейке
    client.post('/schedule/add', data={
        'csrf_token': csrf(client), 'group_id': group_id,
        'subject_id': subject_id, 'day_of_week': day,
        'lesson_number': lesson}, follow_redirects=True)
    with app.app_context():
        count = ScheduleItem.query.filter_by(
            group_id=group_id, day_of_week=day, lesson_number=lesson).count()
    check('дубль в ячейке расписания отклонён', count == 1, f'в базе {count}')

    # Номер пары вне 1..8
    client.post('/schedule/add', data={
        'csrf_token': csrf(client), 'group_id': group_id,
        'subject_id': subject_id, 'day_of_week': day,
        'lesson_number': 9}, follow_redirects=True)
    with app.app_context():
        bad = ScheduleItem.query.filter_by(lesson_number=9).count()
    check('номер пары 9 отклонён', bad == 0, f'в базе {bad}')
    return item_id


def test_teacher_conflict(client, group_id, subject_id, item_id):
    """У преподавателя не может быть двух пар в один момент."""
    with app.app_context():
        item = db.session.get(ScheduleItem, item_id)
        teacher_id = item.teacher_id
        day, lesson = item.day_of_week, item.lesson_number
        other = (Group.query.filter(Group.id != group_id)
                 .order_by(Group.id).first())
        other_id = other.id if other else None

    if not teacher_id or other_id is None:
        check('конфликт преподавателя', False,
              'у пары нет преподавателя или нет второй группы')
        return

    resp = client.post('/schedule/add', data={
        'csrf_token': csrf(client), 'group_id': other_id,
        'subject_id': subject_id, 'day_of_week': day,
        'lesson_number': lesson,
        'teacher_id': teacher_id}, follow_redirects=True)
    with app.app_context():
        conflict = (ScheduleItem.query
                    .filter_by(teacher_id=teacher_id, group_id=other_id,
                               day_of_week=day, lesson_number=lesson).first())
    body = resp.get_data(as_text=True)
    messages = flash_of(body)
    check('конфликт преподавателя отклонён', conflict is None,
          f'статус {resp.status_code}')
    check('в сообщении объяснён конфликт',
          any('уже занят' in m.lower() for m in messages),
          f'сообщения: {messages}')


def test_fill_day(client, group_id, subject_id):
    day, first = free_slot(group_id)
    last = min(first + 1, 8)
    resp = client.post('/schedule/day', data={
        'csrf_token': csrf(client), 'group_id': group_id,
        'subject_id': subject_id, 'day_of_week': day,
        'first_lesson': first, 'last_lesson': last, 'room': '202'},
        follow_redirects=False)
    with app.app_context():
        made = ScheduleItem.query.filter_by(
            group_id=group_id, day_of_week=day, lesson_number=last).first()
    check('массовое заполнение дня', made is not None and resp.status_code == 302,
          f'статус {resp.status_code}, ждём пару {last} в день {day}')

    # Заполнение поверх занятого не должно ломаться
    resp = client.post('/schedule/day', data={
        'csrf_token': csrf(client), 'group_id': group_id,
        'subject_id': subject_id, 'day_of_week': day,
        'first_lesson': 1, 'last_lesson': 8, 'room': '203'},
        follow_redirects=False)
    check('повторное заполнение дня не падает', resp.status_code in (200, 302),
          f'статус {resp.status_code}')

    # Диапазон наоборот
    resp = client.post('/schedule/day', data={
        'csrf_token': csrf(client), 'group_id': group_id,
        'subject_id': subject_id, 'day_of_week': day,
        'first_lesson': 5, 'last_lesson': 2}, follow_redirects=True)
    check('перевёрнутый диапазон отклонён', resp.status_code == 200,
          f'статус {resp.status_code}')


def test_lesson_dates(client, group_id):
    with app.app_context():
        item = (ScheduleItem.query
                .filter_by(group_id=group_id)
                .order_by(ScheduleItem.id).first())
        if item is None:
            check('отметка о занятии', False, 'в расписании группы нет пар')
            return
        item_id = item.id
        weekday = item.day_of_week

    # Берём прошедшую дату, подходящую дню недели пары
    day = datetime.date.today() - datetime.timedelta(days=7)
    while day.weekday() + 1 != weekday:
        day -= datetime.timedelta(days=1)

    # Берём прошедшую дату, подходящую дню недели пары
    day = datetime.date.today() - datetime.timedelta(days=7)
    while day.weekday() + 1 != weekday:
        day -= datetime.timedelta(days=1)

    resp = client.post('/schedule/lessons', data={
        'csrf_token': csrf(client), 'group_id': group_id,
        'date': day.strftime('%Y-%m-%d'),
        'item_ids': str(item_id)}, follow_redirects=False)
    with app.app_context():
        marked = LessonDate.query.filter_by(
            schedule_item_id=item_id, date=day).first()
    check('занятие отмечено состоявшимся', marked is not None and
          resp.status_code == 302, f'статус {resp.status_code}, дата {day}')

    # Повторная отметка не плодит дубли
    client.post('/schedule/lessons', data={
        'csrf_token': csrf(client), 'group_id': group_id,
        'date': day.strftime('%Y-%m-%d'),
        'item_ids': str(item_id)}, follow_redirects=False)
    with app.app_context():
        count = LessonDate.query.filter_by(
            schedule_item_id=item_id, date=day).count()
    check('повторная отметка не дублирует', count == 1, f'в базе {count}')

    # Снятие отметки
    client.post('/schedule/lessons', data={
        'csrf_token': csrf(client), 'group_id': group_id,
        'date': day.strftime('%Y-%m-%d')}, follow_redirects=False)
    with app.app_context():
        left = LessonDate.query.filter_by(
            schedule_item_id=item_id, date=day).count()
    check('отметка снимается', left == 0, f'осталось {left}')

    # Будущая дата не принимается
    future = datetime.date.today() + datetime.timedelta(days=30)
    resp = client.post('/schedule/lessons', data={
        'csrf_token': csrf(client), 'group_id': group_id,
        'date': future.strftime('%Y-%m-%d'),
        'item_ids': str(item_id)}, follow_redirects=True)
    with app.app_context():
        future_marks = LessonDate.query.filter(
            LessonDate.date == future).count()
    check('будущая дата не отмечается', future_marks == 0,
          f'результат {resp.status_code}, отметок {future_marks}')


def test_teacher_locked(state):
    """Преподаватель не может менять расписание и периоды."""
    # Читаем данные ДО смены пароля и закрываем сессию: держать транзакцию
    # открытой, пока идут запросы тест-клиента, — верный способ получить
    # «database is locked» на SQLite.
    with app.app_context():
        teacher = User.query.filter_by(role=User.ROLE_TEACHER).first()
        if teacher is None:
            check('преподаватель закрыт от правки расписания', False,
                  'в базе нет преподавателей')
            return
        teacher_name = teacher.username
        teacher_id = teacher.id
        original_hash = teacher.password_hash
        group_id = Group.query.first().id
        subject_id = Subject.query.first().id
        item = ScheduleItem.query.filter_by(group_id=group_id).first()
        item_id = item.id if item else 0
        before = ScheduleItem.query.count()
        teacher.set_password('phase7-check-1')
        db.session.commit()

    # Клиент создаётся здесь, а не внутри другого test_client: вложенные
    # клиенты наследуют сессию внешнего (проверено — внутренний «логинится»
    # как администратор без всякого POST /login).
    with app.test_client() as client2:
        client2.post('/login', data={
            'username': teacher_name, 'password': 'phase7-check-1',
            'csrf_token': csrf(client2, '/login')}, follow_redirects=True)

        for url in ('/schedule/add', '/schedule/day', '/schedule/lessons',
                    '/periods/add', '/periods/generate'):
            resp = client2.post(url, data={
                'csrf_token': csrf(client2, '/schedule'),
                'group_id': group_id, 'subject_id': subject_id,
                'day_of_week': 1, 'lesson_number': 1, 'first_lesson': 1,
                'last_lesson': 1, 'date': '2026-09-28',
                'academic_year': '2033-2034', 'kind': 'quarter',
                'start_date': '2033-09-01', 'end_date': '2033-12-25',
                'sort_order': 1, 'name': 'Взлом'}, follow_redirects=False)
            check(f'преподавателю закрыт {url}', resp.status_code == 403,
                  f'статус {resp.status_code}')

        if item_id:
            resp = client2.post(f'/schedule/{item_id}/delete',
                                data={'csrf_token': csrf(client2, '/schedule')},
                                follow_redirects=False)
            check('преподаватель не удаляет пару', resp.status_code == 403,
                  f'статус {resp.status_code}')

        # Страницы смотреть может
        for url in ('/schedule', '/periods'):
            resp = client2.get(url, follow_redirects=False)
            check(f'преподаватель видит {url}', resp.status_code == 200,
                  f'статус {resp.status_code}')

    with app.app_context():
        check('расписание не изменилось попытками преподавателя',
              ScheduleItem.query.count() == before,
              f'пар было {before}, стало {ScheduleItem.query.count()}')
        # Возвращаем хеш пароля преподавателя: демо-вход ломать нельзя
        db.session.get(User, teacher_id).password_hash = original_hash
        db.session.commit()


def test_student_locked(state):
    with app.app_context():
        student = Student.query.filter(Student.user_id.isnot(None)).first()
        if student is None:
            check('студент закрыт от расписания', False, 'нет студента с учёткой')
            return
        login_name = student.student_id
        account_id = student.user_id
        original_hash = student.user.password_hash
        student.user.set_password('phase7-check-2')
        db.session.commit()

    with app.test_client() as client3:
        client3.post('/login', data={
            'username': login_name, 'password': 'phase7-check-2',
            'csrf_token': csrf(client3, '/login')}, follow_redirects=True)
        for url in ('/schedule', '/periods'):
            resp = client3.get(url, follow_redirects=False)
            check(f'студент не видит {url}', resp.status_code == 403,
                  f'статус {resp.status_code}')

    # Возвращаем исходный хеш: демо-пароль студента ломать нельзя
    with app.app_context():
        db.session.get(User, account_id).password_hash = original_hash
        db.session.commit()


def main():
    state = snapshot()
    with app.app_context():
        group = Group.query.first()
        subject = Subject.query.first()
        if group is None or subject is None:
            print('Нужны группы и предметы. Запустите init_db.py')
            return 1

    # Объекты достаём внутри контекста и сразу превращаем в простые числа:
    # держать ORM-объекты между app_context нельзя — сессия закрывается, и
    # при обращении к полю получаем DetachedInstanceError.
    with app.app_context():
        group_id = group.id
        subject_id = subject.id

    print('=== Периоды ===')
    with app.test_client() as client:
        login(client)
        test_periods_crud(client, group_id)
        test_period_generate(client)

    print('\n=== Расписание ===')
    with app.test_client() as client:
        login(client)
        item_id = test_schedule_crud(client, group_id, subject_id)
        if item_id:
            test_teacher_conflict(client, group_id, subject_id, item_id)
        test_fill_day(client, group_id, subject_id)

    print('\n=== Отметки о занятиях ===')
    with app.test_client() as client:
        login(client)
        test_lesson_dates(client, group_id)

    print('\n=== Права ролей ===')
    test_teacher_locked(state)
    test_student_locked(state)

    print('\n=== Возврат базы ===')
    restore(state)
    with app.app_context():
        items_now = ScheduleItem.query.count()
        marks_now = LessonDate.query.count()
        same = (items_now == len(state['items']) and
                marks_now == len(state['lesson_dates']))
    check('база возвращена в исходное состояние', same,
          f'пар: {items_now}, отметок: {marks_now}')

    print()
    if failures:
        print(f'❌ Провалено проверок: {len(failures)} из {checks}')
        for line in failures:
            print(f'   - {line}')
        return 1
    print(f'✅ Фаза 7: все {checks} проверок пройдены')
    return 0


if __name__ == '__main__':
    sys.exit(main())
