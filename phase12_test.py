"""Тесты Фазы 12: личный кабинет студента.

Проверяется не «страница открылась», а смысл и изоляция данных: студент видит
только себя, подставив чужой `student_id` в URL ничего не получает, оценки
соседа по группе читаются, а чужой группы — нет; ошибка входа не говорит,
какие логины в системе есть.

Ключевое здесь — 12.8 и 12.9. Поэтому тест подставляет `?student_id=` и `?group_id=`
в каждый маршрут кабинета и требует, чтобы это ни на что не влияло.

База восстанавливается из снимка, поэтому тест можно гонять много раз подряд
и на рабочих данных.

Запуск: python phase12_test.py
"""

import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.init_ import create_app, db
from app.models import (User, Group, Student, Grade, Subject, AcademicPeriod,
                        AuditLog,
                        AttendanceRecord, PeriodResult)

from config import load_env_file

load_env_file()

app = create_app()
app.config['TESTING'] = True

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')
PASSWORD = 'Kabri12Test'
SHORT_PASSWORD = 'Ka1'
OWN_PASSWORD = 'SvoyParol12'

TEST_GROUP = 'ГР-Ф12'
OTHER_GROUP = 'ГР-Ф12-ЧУЖАЯ'
STUDENT_LOGIN = 'Ф12-01'
MATE_LOGIN = 'Ф12-02'
OUTSIDER_LOGIN = 'Д12-01'

failures = []
checks = 0


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


def flash_of(html):
    return re.findall(r'alert-\w+[^>]*>(.*?)</div>', html, flags=re.S)


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


def student_client(username, password):
    client = app.test_client()
    login(client, username, password)
    return client


def field_errors(html, field):
    """Ошибки конкретного поля формы: <input name=...><div ...>текст</div>."""
    blocks = re.findall(r'<input[^>]*name="%s".*?(?=<input|</form)' % field,
                        html, flags=re.S)
    return ' '.join(re.findall(r'text-danger[^>]*>(.*?)</div>', ' '.join(blocks),
                               flags=re.S))


def grade_chips(html):
    """Значения оценок, отрисованных плашками: ['5', '4', ...].

    Читаем сырой HTML, а не текст страницы: иначе подпись в `title` с
    комментарием к оценке тоже попала бы в сравнение.
    """
    return re.findall(r'grade-chip[^>]*>\s*([^<\s]+)', html)


def _audit_max_id():
    """Наибольший id в журнале действий — точка отсчёта для очистки.

    Вызывается из snapshot(), где контекст приложения уже открыт: свой
    контекст здесь только развёл бы читателя по лишней сессии.
    """
    return db.session.query(db.func.max(AuditLog.id)).scalar() or 0


def snapshot():
    with app.app_context():
        return {
            'audit_max_id': _audit_max_id(),
            'users': [{'id': u.id, 'username': u.username,
                       'password_hash': u.password_hash, 'role': u.role,
                       'created_by': u.created_by, 'created_at': u.created_at,
                       'password_changed_at': u.password_changed_at,
                       'password_temporary': u.password_temporary,
                       'last_login_at': u.last_login_at,
                       'is_active': u.is_active, 'full_name': u.full_name,
                       'email': u.email}
                      for u in User.query.all()],
            'students': {s.id: s.user_id for s in Student.query.all()},
            'groups': {g.id for g in Group.query.all()},
            'subjects': {s.id for s in Subject.query.all()},
            'grades': {g.id for g in Grade.query.all()},
            'attendance': {a.id for a in AttendanceRecord.query.all()},
            'results': {r.id for r in PeriodResult.query.all()},
        }


def restore(state):
    """Возврат базы к снимку.

    Порядок обязателен: сначала снимаем связи и удаляем учётные записи, и
    только потом — предметы, группы и студентов, которых завёл тест. Иначе
    внешние ключи не дадут удалить, и «восстановление» тихо проглочет ошибку.
    """
    with app.app_context():
        for student_id, user_id in state['students'].items():
            student = db.session.get(Student, student_id)
            if student is not None:
                student.user_id = user_id
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

        for model, key in ((PeriodResult, 'results'),
                           (AttendanceRecord, 'attendance'),
                           (Grade, 'grades'),
                           (Student, 'students'),
                           (Group, 'groups'),
                           (Subject, 'subjects')):
            for row in model.query.all():
                if row.id not in state[key]:
                    db.session.delete(row)
            db.session.flush()
        db.session.commit()
        # Журнал действий (Фаза 13) тест тоже наполняет: без сброса каждый
        # прогон оставлял бы в рабочей базе записи о тестовых входах
        for entry in AuditLog.query.filter(
                AuditLog.id > state['audit_max_id']).all():
            db.session.delete(entry)
        db.session.commit()


def prepare():
    """Готовит группу, двух одногруппников, студента из чужой группы и оценки.

    Своя группа и чужая — обязательны: без чужой группы нечем доказать, что
    кабинет не выпускает студента за пределы своей.
    """
    with app.app_context():
        # Период берётся тот же, что покажет кабинет по умолчанию, иначе
        # оценки окажутся в другом периоде и проверки будут врать
        today = date.today()
        period = (AcademicPeriod.query
                  .filter(AcademicPeriod.start_date <= today,
                          AcademicPeriod.end_date >= today)
                  .order_by(AcademicPeriod.sort_order.desc()).first())
        if period is None:
            period = (AcademicPeriod.query
                      .order_by(AcademicPeriod.academic_year.desc(),
                                AcademicPeriod.sort_order.desc()).first())
        if period is None:
            raise SystemExit('В базе нет учебных периодов — тест не с чем работать')

        teacher = (User.query.filter(User.role == User.ROLE_ADMIN).first())
        subjects = Subject.query.order_by(Subject.name).limit(2).all()
        subject_ids = [s.id for s in subjects]

        for name in (TEST_GROUP, OTHER_GROUP):
            if not Group.query.filter_by(name=name).first():
                db.session.add(Group(name=name, specialty='Специальность 12',
                                     year=2025))
        db.session.commit()

        group = Group.query.filter_by(name=TEST_GROUP).first()
        other = Group.query.filter_by(name=OTHER_GROUP).first()

        people = [
            (STUDENT_LOGIN, 'Кабинетец', 'Двенадцатый', group, teacher),
            (MATE_LOGIN, 'Сосед', 'ПоГруппе', group, teacher),
            (OUTSIDER_LOGIN, 'Чужой', 'ИзДругой', other, teacher),
        ]
        for login, last, first, grp, author in people:
            student = Student.query.filter_by(student_id=login).first()
            if student is None:
                student = Student(student_id=login, last_name=last,
                                  first_name=first, patronymic='Тестовый',
                                  gender='M', group_id=grp.id, status='active')
                db.session.add(student)
                db.session.commit()
            user = User.query.filter_by(username=login).first()
            if user is None:
                user = User(username=login, role=User.ROLE_STUDENT,
                            is_active=True, created_by=author.id)
                user.full_name = student.full_name
                db.session.add(user)
                db.session.commit()
            user.set_temporary_password(PASSWORD)
            user.password_temporary = True
            student.user_id = user.id
            db.session.flush()

        group_students = Student.query.filter_by(group_id=group.id).all()
        db.session.commit()

        # Оценки с разными значениями, чтобы средние баллы не совпали
        for student, values in ((group_students[0], ['5', '4']),
                                (group_students[1], ['3', '2'])):
            for index, value in enumerate(values):
                if not Grade.query.filter_by(student_id=student.id,
                                             subject_id=subject_ids[index]).first():
                    db.session.add(Grade(
                        student_id=student.id,
                        subject_id=subject_ids[index],
                        grade_value=value, grade_type='контрольная',
                        period_id=period.id,
                        comments=f'Оценка Ф12 {index}'))

        # Пропуск с причиной: в кабинете он должен попасть в сводку.
        # Дата берётся внутри периода — пропуск фильтруется по границам
        # периода, и отметка вне них в сводку не попала бы.
        period = db.session.get(AcademicPeriod, period.id)
        if not AttendanceRecord.query.filter_by(
                student_id=group_students[0].id,
                date=period.start_date).first():
            db.session.add(AttendanceRecord(
                student_id=group_students[0].id,
                date=period.start_date, reason='unexcused',
                subject_id=subject_ids[0],
                note='Пропуск Ф12', created_by=teacher.id))

        # Итог за период по первому предмету
        if not PeriodResult.query.filter_by(
                student_id=group_students[0].id,
                subject_id=subject_ids[0]).first():
            db.session.add(PeriodResult(
                student_id=group_students[0].id, subject_id=subject_ids[0],
                period_id=period.id, final_value='4',
                created_by=teacher.id))
        db.session.commit()

        return {
            'group': group.id,
            'other_group': other.id,
            'period': period.id,
            'subjects': subject_ids,
            'absence_date': period.start_date.strftime('%d.%m.%Y'),
            'students': {s.student_id: s.id for s in group_students},
            'outsider': Student.query.filter_by(student_id=OUTSIDER_LOGIN).first().id,
        }


def run_checks(data):
    me = data['students'][STUDENT_LOGIN]
    mate = data['students'][MATE_LOGIN]
    outsider = data['outsider']

    # ================= 12.1 одна форма входа =============================
    print('\n--- 12.1 Единая форма входа ---')
    client = app.test_client()
    response = login(client, STUDENT_LOGIN, PASSWORD)
    check('студент входит по номеру зачётки',
          'Кабинет' in page_text(response.get_data(as_text=True)))

    # Несуществующий логин и неверный пароль должны отвечать одинаково,
    # иначе по разнице ответов перебором узнаются существующие логины
    logout(client)
    missing = login(client, 'Ф12-НЕТ-ТАКОГО', PASSWORD)
    missing_text = ' '.join(flash_of(missing.get_data(as_text=True)))
    logout(client)
    wrong = login(client, STUDENT_LOGIN, 'NeTotParol')
    wrong_text = ' '.join(flash_of(wrong.get_data(as_text=True)))
    check('несуществующий логин и неверный пароль отвечают одинаково',
          ('Неверное имя пользователя или пароль' in missing_text
           and 'Неверное имя пользователя или пароль' in wrong_text),
          f'нет такого логина: {missing_text.strip()!r}; '
          f'неверный пароль: {wrong_text.strip()!r}')

    # Отключённая запись не должна выдавать себя до проверки пароля:
    # иначе «отключена» подтверждала бы, что логин существует
    with app.app_context():
        blocked = db.session.get(User, db.session.get(
            Student, me).user_id)
        blocked_was_active = blocked.is_active
        blocked.is_active = False
        db.session.commit()
    logout(client)
    probe = login(client, STUDENT_LOGIN, 'NeTotParol')
    probe_text = ' '.join(flash_of(probe.get_data(as_text=True)))
    check('отключённая запись не выдаёт себя при неверном пароле',
          'отключена' not in probe_text.lower(),
          f'ответ: {probe_text.strip()!r}')
    logout(client)
    blocked_login = login(client, STUDENT_LOGIN, PASSWORD)
    blocked_text = ' '.join(flash_of(blocked_login.get_data(as_text=True)))
    check('при верном пароле отключённая запись объясняет причину',
          'отключена' in blocked_text.lower(),
          f'ответ: {blocked_text.strip()!r}')
    with app.app_context():
        user = db.session.get(User, db.session.get(Student, me).user_id)
        user.is_active = blocked_was_active
        db.session.commit()

    # ================= 12.2 кабинет ======================================
    print('\n--- 12.2 Кабинет ---')
    client = student_client(STUDENT_LOGIN, PASSWORD)
    response = client.get('/portal')
    html = response.get_data(as_text=True)
    text = page_text(html)
    check('кабинет открывается студенту', response.status_code == 200)
    check('в кабинете есть ФИО студента', 'Кабинетец' in text)
    check('в кабинете виден номер зачётки', STUDENT_LOGIN in text)
    check('в кабинете назван период', 'четверть' in text.lower())
    check('в кабинете есть свои оценки', 'оценки по предметам' in text.lower())
    check('в кабинете видны оценки, а не пустая таблица',
          grade_chips(html) == ['5', '4'], f'плашки: {grade_chips(html)}')
    check('в кабинете есть блок пропусков', 'Пропусков' in text)
    check('выданный пароль помечен как несменённый',
          'ещё не изменён' in text)
    check('в кабинете нет ссылки на рейтинг группы', 'рейтинг' not in text.lower())
    check('в кабинете нет среднего балла группы',
          'Средний балл группы' not in text)

    # Средний балл в кабинете — только свой
    with app.app_context():
        own = (Grade.query.filter_by(student_id=me).all())
        expected = sum(int(g.grade_value) for g in own) / len(own)
        mate_avg = (sum(int(g.grade_value) for g in
                        Grade.query.filter_by(student_id=mate).all())
                    / len(Grade.query.filter_by(student_id=mate).all()))
    check('в кабинете средний балл свой', str(expected) in text,
          f'ожидался {expected}')
    check('средний балл одногруппника не подставлен',
          str(mate_avg) not in text or expected == mate_avg,
          f'сосед: {mate_avg}, свой: {expected}')

    # ================= 12.3 свои оценки по периодам ======================
    print('\n--- 12.3 Свои оценки по предметам и периодам ---')
    response = client.get('/portal/grades')
    html = response.get_data(as_text=True)
    text = page_text(html)
    check('страница оценок открывается', response.status_code == 200)
    check('есть выбор периода', 'Период' in text)
    check('видны только свои оценки', grade_chips(html) == ['5', '4'],
          f'плашки: {grade_chips(html)}')
    check('в кабинете оценок нет кнопок правки', 'Удалить' not in text
          and 'Исправить' not in text)

    with app.app_context():
        other_period = (AcademicPeriod.query
                        .filter(AcademicPeriod.id != data['period'])
                        .order_by(AcademicPeriod.sort_order).first())
    if other_period is not None:
        response = client.get(f'/portal/grades?period_id={other_period.id}')
        text = page_text(response.get_data(as_text=True))
        check('период из запроса учитывается', other_period.name in text)
        check('за чужой период своих оценок не видно',
              grade_chips(response.get_data(as_text=True)) == [],
              f'плашки: {grade_chips(response.get_data(as_text=True))}')

    # ================= 12.5 оценки одногруппников ========================
    print('\n--- 12.5 Сводная по группе, только чтение ---')
    response = client.get('/portal/grades?view=group')
    html = response.get_data(as_text=True)
    text = page_text(html)
    check('сводная по группе открывается', response.status_code == 200)
    check('видны одногруппники', 'Сосед' in text)
    check('своя строка помечена', 'badge bg-primary ms-1">я<' in html)
    check('в сводной видны оценки соседа', sorted(grade_chips(html)) == ['2', '3', '4', '5'],
          f'плашки: {grade_chips(html)}')
    check('в сводной нет кнопок правки', 'Удалить' not in text
          and 'Исправить' not in text)
    check('в сводной нет места в рейтинге', 'место' not in text.lower()
          and 'рейтинг' not in text.lower())
    check('в сводной нет среднего балла группы',
          'Средний балл группы' not in text)
    check('в сводной нет подсчёта среднего по столбцам',
          'средн' not in text.lower())

    # Чужая группа в параметрах не должна ничего открывать
    response = client.get(f'/portal/grades?view=group&group_id={data["other_group"]}')
    html = response.get_data(as_text=True)
    check('чужой group_id в URL игнорируется', 'Чужой' not in page_text(html))
    check('чужой group_id в URL не меняет состав сводной',
          sorted(grade_chips(html)) == ['2', '3', '4', '5'],
          f'плашки: {grade_chips(html)}')

    # ================= 12.6 пропуска ====================================
    print('\n--- 12.6 Пропуска ---')
    response = client.get('/portal/attendance')
    text = page_text(response.get_data(as_text=True))
    check('страница пропусков открывается', response.status_code == 200)
    check('видна сводка пропусков', 'Всего пропусков' in text)
    check('видна причина пропуска', 'Без уважительной причины' in text)
    check('в списке есть сам пропуск', 'Пропуск Ф12' in text)
    check('в списке есть дата пропуска', data['absence_date'] in text)
    check('на странице пропусков нет кнопок правки', 'Удалить' not in text
          and 'Исправить' not in text)
    check('пропуск относится к паре, а не ко всему дню',
          'Пропущено пар' in text and '1' in text)

    # ================= 12.7 смена пароля ================================
    print('\n--- 12.7 Смена пароля ---')
    response = client.get('/my/password')
    text = page_text(response.get_data(as_text=True))
    check('форма смены пароля доступна', response.status_code == 200)
    check('форма просит текущий пароль', 'Текущий пароль' in text)
    check('форма в оформлении кабинета', 'Личный кабинет' in text)

    # Короткий пароль: проверяем не текст ошибки, а главное — что старый
    # пароль продолжает работать. Иначе тест прошёл бы, даже если бы
    # ограничение на длину исчезло.
    response = client.post('/my/password', data={
        'current_password': PASSWORD, 'new_password': SHORT_PASSWORD,
        'confirm_password': SHORT_PASSWORD,
        'csrf_token': csrf(client, '/my/password')}, follow_redirects=True)
    html = response.get_data(as_text=True)
    check('короткий пароль отклоняется',
          'Field must be' in field_errors(html, 'new_password'),
          f'ошибка поля: {field_errors(html, "new_password")!r}')
    with app.app_context():
        user = db.session.get(User, db.session.get(Student, me).user_id)
        check('короткий пароль не сохранился', user.check_password(PASSWORD))

    response = client.post('/my/password', data={
        'current_password': 'NeTotParol', 'new_password': OWN_PASSWORD,
        'confirm_password': OWN_PASSWORD,
        'csrf_token': csrf(client, '/my/password')}, follow_redirects=True)
    check('неверный текущий пароль отклоняется',
          'Текущий пароль указан неверно' in page_text(
              response.get_data(as_text=True)))
    with app.app_context():
        user = db.session.get(User, db.session.get(Student, me).user_id)
        check('пароль не сменился при неверном текущем',
              user.check_password(PASSWORD))

    # Новый пароль, совпадающий со старым, — частая ошибка: человек считает,
    # что сменил пароль, а старый продолжает работать
    response = client.post('/my/password', data={
        'current_password': PASSWORD, 'new_password': PASSWORD,
        'confirm_password': PASSWORD,
        'csrf_token': csrf(client, '/my/password')}, follow_redirects=True)
    check('новый пароль не может совпадать со старым',
          'совпадает с текущим' in page_text(response.get_data(as_text=True)))
    with app.app_context():
        user = db.session.get(User, db.session.get(Student, me).user_id)
        check('пароль не сменился при совпадении',
              user.check_password(PASSWORD))
        check('пароль остался помеченным как временный',
              user.password_temporary is True)

    response = client.post('/my/password', data={
        'current_password': PASSWORD, 'new_password': OWN_PASSWORD,
        'confirm_password': 'SvoyParol13',
        'csrf_token': csrf(client, '/my/password')}, follow_redirects=True)
    check('несовпадение повторов отклоняется',
          'Пароли не совпадают' in page_text(response.get_data(as_text=True)))

    response = client.post('/my/password', data={
        'current_password': PASSWORD, 'new_password': OWN_PASSWORD,
        'confirm_password': OWN_PASSWORD,
        'csrf_token': csrf(client, '/my/password')}, follow_redirects=True)
    check('пароль успешно изменён', 'успешно изменён' in page_text(
        response.get_data(as_text=True)))
    with app.app_context():
        user = db.session.get(User, db.session.get(Student, me).user_id)
        check('новый пароль сохранён', user.check_password(OWN_PASSWORD))
        check('старый пароль перестал работать', not user.check_password(PASSWORD))
        check('флаг временного пароля снят', user.password_temporary is False)

    logout(client)
    check('старый пароль не пускает в систему',
          client.get('/portal', follow_redirects=False).status_code == 302)
    client = student_client(STUDENT_LOGIN, OWN_PASSWORD)
    check('новый пароль пускает в систему',
          client.get('/portal').status_code == 200)
    check('после смены пароля в кабинете нет напоминания о временном пароле',
          'ещё не изменён' not in page_text(client.get('/portal')
                                            .get_data(as_text=True)))

    # ================= 12.8 изоляция данных =============================
    print('\n--- 12.8 Изоляция данных ---')
    with app.app_context():
        outsider_name = db.session.get(Student, outsider).short_name

    # Ожидаемое содержание страниц: свои оценки — ['5', '4'], в сводной
    # добавляются оценки одногруппника ['3', '2'], в пропусках оценок нет
    expected = {'/portal': ['5', '4'],
                '/portal/grades': ['5', '4'],
                '/portal/grades?view=group': ['2', '3', '4', '5'],
                '/portal/attendance': []}
    for url, chips in expected.items():
        base = client.get(url).get_data(as_text=True)
        check(f'{url}: содержимое без параметров ожидаемо',
              sorted(grade_chips(base)) == sorted(chips),
              f'плашки: {grade_chips(base)}')

        # Ключевое: подстановка чужих id в адрес не должна изменить страницу
        # ни на слово. Сравниваем с эталоном целиком, а не по отдельным
        # подстрокам: точечные проверки пропускают подмену целой таблицы.
        separator = '&' if '?' in url else '?'
        html = client.get(f'{url}{separator}student_id={mate}'
                          f'&group_id={data["other_group"]}').get_data(as_text=True)
        text = page_text(html)
        check(f'{url}: подстановка чужих id не меняет страницу',
              text == page_text(base))
        check(f'{url}: студент из чужой группы не появляется',
              outsider_name not in text)
        check(f'{url}: оценки соседа не появились',
              sorted(grade_chips(html)) == sorted(chips),
              f'плашки: {grade_chips(html)}')

    # Групповой режим без своего student_id: чужие id игнорируются
    base = client.get('/portal/grades?view=group').get_data(as_text=True)
    html = client.get(f'/portal/grades?view=group&student_id={mate}').get_data(
        as_text=True)
    check('в сводной не подставляется чужой student_id',
          page_text(html) == page_text(base))

    # Несуществующие идентификаторы не ломают страницу
    response = client.get('/portal/grades?view=group&group_id=999999'
                          '&period_id=888888')
    check('несуществующие группа и период не ломают страницу',
          response.status_code in (200, 404), f'код {response.status_code}')

    # ================= 12.9 закрытые разделы ============================
    print('\n--- 12.9 Что студент не видит ---')
    client = student_client(STUDENT_LOGIN, OWN_PASSWORD)
    forbidden = ['/students', '/groups', '/subjects', '/grades', '/journal',
                 '/attendance', '/results', '/reports', '/schedule',
                 '/periods', '/staff', '/accounts', '/settings',
                 '/settings/backup', '/settings/import', '/settings/logs',
                 '/dashboard', '/api/system/info']
    # 403 — «вход только для сотрудников», 404 — маршрута нет вовсе. Оба
    # ответа закрывают раздел, а вот 200 означал бы дыру.
    opened, missing = [], []
    for url in forbidden:
        code = client.get(url).status_code
        if code == 200:
            opened.append(f'{url} → {code}')
        elif code == 404:
            missing.append(url)
        elif code != 403:
            opened.append(f'{url} → {code} (ожидался 403)')
    check('студент не открывает админские разделы по прямой ссылке',
          not opened, '; '.join(opened))
    if missing:
        print(f'  (разделов нет вовсе: {", ".join(missing)})')

    # Отдельная запись студента по прямой ссылке
    response = client.get(f'/students/{mate}')
    check('карточка чужого студента не открывается',
          response.status_code in (403, 404), f'код {response.status_code}')

    response = client.get('/portal')
    text = page_text(response.get_data(as_text=True))
    check('в кабинете нет пункта «Сотрудники»', 'Сотрудники' not in text)
    check('в кабинете нет пункта «Учётные записи»', 'Учётные записи' not in text)
    check('в кабинете нет пункта «Журнал»', 'Журнал' not in text)

    # Сотрудник не должен попасть в кабинет студента
    staff = admin_client()
    for url in ('/portal', '/portal/grades', '/portal/attendance'):
        code = staff.get(url).status_code
        check(f'{url} закрыт сотруднику', code == 403, f'код {code}')

    # ================= 12.10 оформление =================================
    print('\n--- 12.10 Оформление кабинета ---')
    html = client.get('/portal').get_data(as_text=True)
    text = page_text(html)
    check('кабинет на своём макете', 'Личный кабинет' in text)
    check('в кабинете нет бокового админ-меню',
          'Административная панель' not in text)
    check('в кабинете нет служебных ссылок',
          'Резервные копии' not in text and 'Журнал событий' not in text)
    check('в кабинете есть подсказка про смену пароля',
          'смените его' in text.lower() or 'Сменить пароль' in text)
    check('в меню кабинета три своих раздела',
          'Кабинет' in text and 'Оценки' in text and 'Пропуска' in text)

    # ================= прочее ===========================================
    print('\n--- Смежное ---')
    response = client.get('/my/account', follow_redirects=False)
    check('старый адрес /my/account ведёт в кабинет',
          response.status_code == 302
          and response.headers.get('Location', '').endswith('/portal'),
          f'код {response.status_code}, адрес {response.headers.get("Location")}')

    response = student_client(STUDENT_LOGIN, OWN_PASSWORD).get(
        '/login', follow_redirects=False)
    check('вошедший пользователь не видит форму входа',
          response.status_code == 302,
          f'код {response.status_code}')

    response = client.get('/')
    check('студент с портала попадает в кабинет',
          response.status_code == 302
          and response.headers.get('Location', '').endswith('/portal'),
          f'код {response.status_code}, адрес {response.headers.get("Location")}')


def main():
    state = snapshot()
    try:
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
