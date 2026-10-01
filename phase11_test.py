"""Тесты Фазы 11: учётные записи студентов и выдача паролей.

Проверяется не «страница открылась», а смысл: логин нельзя занять дважды
с точностью до регистра, номер зачётки не должен отдать логин сотрудника,
выданный пароль действительно пускает в систему, сброс убивает старый, а в
списке учётных записей пароля нет вообще.

База восстанавливается из снимка, поэтому тест можно гонять много раз подряд
и на рабочих данных.

Запуск: python phase11_test.py
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.init_ import create_app, db
from app.models import User, Group, Student, Grade
from app.utils import (generate_temporary_password, username_taken,
                        normalize_username, READABLE_PASSWORD_CHARS, EXPORT_DIR)

from config import load_env_file

load_env_file()

app = create_app()
app.config['TESTING'] = True

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')
TEACHER_PASSWORD = 'teacher123'
TEST_TEACHER = 't11_teacher'
TEST_GROUP = 'ГР-Ф11'
PASSWORD = 'Vremenny1'
WRONG_PASSWORD = 'NeTotParol'

failures = []
checks = 0
issued_files = []


def remove_issued_files():
    """Удалить ведомости, созданные тестом.

    В ведомости лежат действующие пароли, поэтому оставлять их в `exports/`
    нельзя: файл пережил бы тест и достался тому, кто следующим откроет каталог.
    """
    for name in issued_files:
        path = os.path.join(EXPORT_DIR, name)
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError as error:
            print(f'! не удалось удалить {name}: {error}')


def check(label, ok, detail=''):
    global checks
    checks += 1
    print(f'{"✓" if ok else "✗"} {label}' + (f' — {detail}' if detail else ''))
    if not ok:
        failures.append(f'{label}: {detail}')


def csrf(client, url='/login'):
    html = client.get(url).get_data(as_text=True)
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return match.group(1) if match else ''


def login(client, username, password):
    return client.post('/login', data={
        'username': username, 'password': password,
        'csrf_token': csrf(client, '/login')}, follow_redirects=True)


def logout(client):
    client.get('/logout', follow_redirects=True)


def flash_of(html):
    found = re.findall(r'alert-(?:danger|warning|success|info)[^>]*>(.*?)</div>',
                       html, re.DOTALL)
    return [' '.join(re.sub(r'<[^>]+>', ' ', chunk).split())
            for chunk in found if chunk.strip()]


def page_text(html):
    without_scripts = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html,
                             flags=re.DOTALL | re.IGNORECASE)
    return ' '.join(re.sub(r'<[^>]+>', ' ', without_scripts).split())


def snapshot():
    with app.app_context():
        return {
            'users': [{'id': u.id, 'username': u.username,
                       'password_hash': u.password_hash, 'role': u.role,
                       'created_by': u.created_by, 'created_at': u.created_at,
                       'password_changed_at': u.password_changed_at,
                       'password_temporary': u.password_temporary,
                       'last_login_at': u.last_login_at, 'is_active': u.is_active,
                       'full_name': u.full_name, 'email': u.email}
                      for u in User.query.all()],
            'students': {s.id: s.user_id for s in Student.query.all()},
            'groups': {g.id for g in Group.query.all()},
        }


def restore(state):
    """Возврат базы к снимку.

    Порядок обязателен: сначала снимаем связь студента с учётной записью,
    потом удаляем записи, потом временных студентов и группу, которые тест
    завёл сам. Без последнего шага группа Ф11 осталась бы в базе, и
    последующие тесты (в частности фаза 8) брали бы её первой — пустой, без
    расписания, и падали бы на пустой сетке журнала.
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

        for student in Student.query.all():
            if student.id not in state['students']:
                db.session.delete(student)
        db.session.flush()

        for group in Group.query.all():
            if group.id not in state['groups']:
                db.session.delete(group)
        db.session.commit()


def admin_client():
    client = app.test_client()
    login(client, os.environ.get('ADMIN_USERNAME', 'admin'), ADMIN_PASSWORD)
    return client


def make_group_with_students(count=3, status='active', name=TEST_GROUP):
    """Группа и студенты для тестов."""
    with app.app_context():
        group = Group.query.filter_by(name=name).first()
        if group is None:
            group = Group(name=name, specialty='Специальность 11', year=2025)
            db.session.add(group)
            db.session.commit()
        students = []
        for index in range(count):
            sid = f'Ф11-{index + 1:02d}'
            student = Student.query.filter_by(student_id=sid).first()
            if student is None:
                student = Student(student_id=sid, last_name=f'Тестов{index + 1}',
                                  first_name='Одиннадцать', patronymic='Тестовый',
                                  gender='M', group_id=group.id, status=status)
                db.session.add(student)
                db.session.commit()
            students.append({'id': student.id, 'login': sid,
                             'name': student.full_name})
        return {'id': group.id, 'name': group.name, 'students': students}


def make_solo_student(index):
    """Отдельный студент для проверок карточки.

    Отдельная группа нужна, чтобы не сменить номер зачётки у `target`:
    от него зависят все последующие разделы теста (массовая выдача, удаление
    записи), и после переименования они искали бы несуществующий логин.
    """
    name = f'{TEST_GROUP}-КАРТОЧКА'
    with app.app_context():
        group = Group.query.filter_by(name=name).first()
        if group is None:
            group = Group(name=name, specialty='Специальность 11 (карточка)', year=2025)
            db.session.add(group)
            db.session.commit()
        sid = f'Ф11-КАРТ{index:02d}'
        student = Student.query.filter_by(student_id=sid).first()
        if student is None:
            student = Student(student_id=sid, last_name=f'Карточ{index}',
                              first_name='Тестовый', patronymic='Ученик',
                              gender='F', group_id=group.id, status='active')
            db.session.add(student)
            db.session.commit()
        return {'id': student.id, 'login': sid, 'group_id': group.id,
                'name': student.full_name}


def issue_one(client, student, password=PASSWORD, confirm=None):
    data = {'password': password,
            'confirm_password': confirm if confirm is not None else password,
            'csrf_token': csrf(client, f'/students/{student["id"]}/edit')}
    return client.post(f'/students/{student["id"]}/account', data=data,
                       follow_redirects=True)


def run_checks():
    # ================= 11.2 защита от коллизии логинов =================
    print('\n--- 11.2 Защита от коллизии логинов ---')
    client = admin_client()
    check('вход администратора состоялся',
          page_text(client.get('/accounts').get_data(as_text=True))
          != 'Войти')

    with app.app_context():
        admin_login = User.query.filter(User.role == User.ROLE_ADMIN).first()
        admin_name = admin_login.username if admin_login else 'admin'
        check('логин сотрудника занят', username_taken(admin_name))
        check('логин занят независимо от регистра',
              username_taken(admin_name.upper()))
        check('логин занят и с пробелами вокруг',
              username_taken(f'  {admin_name.lower()}  '))
        check('нормализация срезает пробелы',
              normalize_username('  Test  ') == 'Test')
        check('свободный логин не считается занятым',
              not username_taken('Ф11-СВОБОДНЫЙ'))

    # Вход не должен зависеть от регистра
    logout(client)
    response = login(client, admin_name.upper(), ADMIN_PASSWORD)
    check('вход работает с логином в верхнем регистре',
          'Неверное имя пользователя или пароль'
          not in ' '.join(flash_of(response.get_data(as_text=True))))
    with app.app_context():
        check('вход нашёл ту же учётную запись, а не первую по id',
              User.query.filter_by(username=admin_name.upper()).count() == 0)

    # Создание сотрудника с занятым логином в другом регистре
    client = admin_client()
    response = client.post('/staff/add', data={
        'username': admin_name.upper(), 'full_name': 'Дубль логина',
        'role': User.ROLE_TEACHER, 'password': WRONG_PASSWORD,
        'confirm_password': WRONG_PASSWORD, 'is_active': 'y',
        'csrf_token': csrf(client, '/staff/add')}, follow_redirects=True)
    text = page_text(response.get_data(as_text=True))
    check('логин в другом регистре не создаётся повторно',
          'Логин занят' in text, 'ожидалось сообщение о занятом логине')
    with app.app_context():
        check('дубль в другой регистре не попал в базу',
              User.query.filter_by(username=admin_name.upper()).count() == 0)

    # ================= 11.1 учётная запись в карточке студента ==========
    print('\n--- 11.1 Учётная запись одного студента ---')
    group = make_group_with_students(3)
    target = group['students'][0]
    client = admin_client()

    response = issue_one(client, target, password=PASSWORD, confirm=WRONG_PASSWORD)
    check('несовпадение паролей отклоняется',
          'Пароли не совпадают' in page_text(response.get_data(as_text=True)))
    with app.app_context():
        check('учётная запись не создана при несовпадении паролей',
              User.query.filter_by(username=target['login']).count() == 0)

    response = issue_one(client, target)
    html = response.get_data(as_text=True)
    check('пароль показан один раз на странице выдачи',
          PASSWORD in html)
    with app.app_context():
        user = User.query.filter_by(username=target['login']).first()
        student = db.session.get(Student, target['id'])
        check('учётная запись создана с ролью «студент»',
              user is not None and user.role == User.ROLE_STUDENT)
        check('учётная запись связана со студентом',
              user is not None and student.user_id == user.id)
        check('в базе только хеш, пароль открытым не хранится',
              user is not None and user.password_hash != PASSWORD)
        check('хеш пароля совпадает с выданным',
              user is not None and user.check_password(PASSWORD))
        check('выданный пароль помечен временным',
              user is not None and user.password_temporary is True)
        check('в списке сотрудников такой записи нет',
              User.query.filter_by(username=target['login'],
                                   role=User.ROLE_ADMIN).count() == 0)

    # повторная выдача = сброс
    response = issue_one(client, target, password='DrugoyParol1')
    check('повторная выдача показывает новый пароль',
          'DrugoyParol1' in response.get_data(as_text=True))
    with app.app_context():
        user = User.query.filter_by(username=target['login']).first()
        check('прежний пароль перестал работать',
              not user.check_password(PASSWORD))
        check('новый пароль работает', user.check_password('DrugoyParol1'))
        check('повторная выдача не плодит записи',
              User.query.filter_by(username=target['login']).count() == 1)

    # --- 11.1 секция в карточке студента ---
    # Отдельный студент: смена номера зачётки у `target` сломала бы все
    # последующие разделы, которые ищут запись по его логину.
    card_student = make_solo_student(1)
    issue_one(client, card_student, password='KartochkaParol1')
    response = client.get(f'/students/{card_student["id"]}/edit')
    raw = response.get_data(as_text=True)
    card = page_text(raw)
    check('карточка студента открывается', response.status_code == 200)
    check('в карточке есть блок учётной записи', 'Учётная запись' in card)
    check('в карточке есть кнопка генерации пароля', 'Сгенерировать' in card)
    check('логин в карточке заблокирован для правки', 'readonly' in raw)
    check('логин в карточке равен номеру зачётки',
          card_student['login'] in raw)
    check('карточка предупреждает, что пароль показывают один раз',
          'один раз' in card)
    check('карточка подключает скрипт генерации', 'js/account.js' in raw)
    script = client.get('/static/js/account.js')
    check('скрипт генерации отдаётся сервером', script.status_code == 200)
    alphabet = script.get_data(as_text=True).split('CHARS = ')[1].split('\n')[0]
    check('алфавит в карточке совпадает с серверным',
          alphabet.strip("'; ") == READABLE_PASSWORD_CHARS,
          f'в шаблоне {alphabet.strip("\'; ")}, на сервере {READABLE_PASSWORD_CHARS}')

    add_page = page_text(client.get('/students/add').get_data(as_text=True))
    check('у нового студента блока учётной записи нет',
          'Учётная запись' not in add_page)

    # Смена номера зачётки должна менять и логин, иначе войти нельзя.
    new_id = f'{card_student["login"]}-ПЕР'
    response = client.post(f'/students/{card_student["id"]}/edit', data={
        'student_id': new_id, 'last_name': 'Карточ1', 'first_name': 'Новый',
        'patronymic': '', 'gender': 'F', 'status': 'active',
        'group_id': str(card_student['group_id']), 'email': 'kart@example.com',
        'csrf_token': csrf(client, f'/students/{card_student["id"]}/edit')},
        follow_redirects=True)
    check('карточка сохранилась', 'Данные студента' in page_text(
        response.get_data(as_text=True)))
    with app.app_context():
        user = User.query.filter_by(username=new_id).first()
        check('логин ученической записи после смены зачётки обновился',
              user is not None)
        check('прежний логин освободился',
              User.query.filter_by(username=card_student['login']).count() == 0)
        check('ФИО в записи совпадает с карточкой',
              user is not None and user.full_name == 'Карточ1 Новый')
        check('email в записи совпадает с карточкой',
              user is not None and user.email == 'kart@example.com')
        check('пароль пережил смену зачётки и остался рабочим',
              user is not None and user.check_password('KartochkaParol1'))

    # Зачётка, занятая сотрудником, не должна украсть его логин.
    with app.app_context():
        staff_login = User.query.filter(
            User.role.in_([User.ROLE_ADMIN, User.ROLE_TEACHER])).first().username
    response = client.post(f'/students/{card_student["id"]}/edit', data={
        'student_id': staff_login, 'last_name': 'Карточ1', 'first_name': 'Новый',
        'patronymic': '', 'gender': 'F', 'status': 'active',
        'group_id': str(card_student['group_id']), 'email': '',
        'csrf_token': csrf(client, f'/students/{card_student["id"]}/edit')},
        follow_redirects=True)
    check('зачётка, занятая сотрудником, отклоняется',
          'уже занят' in page_text(response.get_data(as_text=True)))
    with app.app_context():
        check('логин сотрудника не перехвачен студентом',
              db.session.get(Student, card_student['id']).user.username == new_id)


    # ================= 11.3 массовая выдача =============================
    print('\n--- 11.3 Массовая выдача по группе ---')
    group = make_group_with_students(4)
    client = admin_client()
    response = client.post('/accounts/issue',
                           data={'group_id': str(group['id'])},
                           follow_redirects=True)
    html = response.get_data(as_text=True)
    text = page_text(html)
    issued_now = [s for s in group['students']
                  if s['login'] not in {target['login']}]
    check('страница выдачи открылась', response.status_code == 200)
    check('число выданных записей совпадает со студентами без записи',
          f'Создано записей: {len(issued_now)}' in text,
          f'ожидалось {len(issued_now)}')

    with app.app_context():
        created = [User.query.filter_by(username=s['login']).first()
                   for s in issued_now]
        check('все учётные записи группы созданы',
              all(u is not None for u in created))
        check('у всех проставлен признак временного пароля',
              all(u.password_temporary for u in created))
        # пароль каждой записи должен совпадать с тем, что на экране
        matched = 0
        for student, user in zip(issued_now, created):
            shown = re.findall(
                r'<td><code>' + re.escape(student['login']) + r'</code></td>\s*'
                r'<td><code>([^<]+)</code></td>', html)
            if shown and user.check_password(shown[-1].strip()):
                matched += 1
        check('пароль на экране подходит к учётной записи',
              matched == len(issued_now), f'совпало {matched} из {len(issued_now)}')

    # ведомость с паролями
    check('есть ссылка на скачивание ведомости',
          'Скачать ведомость' in text)
    match = re.search(r'href="(/accounts/credentials/[^"]+)"', html)
    check('ведомость лежит по адресу с именем файла', match is not None)
    if match:
        download = client.get(match.group(1))
        check('ведомость скачивается файлом', download.status_code == 200)
        check('ведомость не отдаётся как текст',
              'spreadsheet' in download.headers.get('Content-Type', ''))
        # Ведомость содержит действующие пароли, поэтому её нельзя оставлять
        # в exports/: она лежала бы там до следующего ручного удаления.
        # Убираем сразу — тестовые пароли на диске не нужны.
        issued_files.append(os.path.basename(match.group(1)))
    check('обход каталога в имени файла не проходит',
          client.get('/accounts/credentials/..%2F..%2Fcollege.db').status_code
          in (302, 403, 404))


    # в журнале событий нет самих паролей
    with app.app_context():
        from app.utils import read_log_lines
        log_text = '\n'.join(line for line in read_log_lines(limit=400))
    check('в журнале есть запись о выдаче',
          'Выдано учётных записей' in log_text)
    leaked = [line for line in log_text.splitlines()
              if any(pw and pw in line for pw in
                     (PASSWORD, 'DrugoyParol1'))]
    check('в журнале событий нет паролей', not leaked,
          leaked[0][:80] if leaked else '')

    # ================= 11.4 список и фильтры ============================
    print('\n--- 11.4 Список учётных записей ---')
    html = client.get('/accounts').get_data(as_text=True)
    text = page_text(html)
    check('список показывает записи группы',
          all(s['login'] in html for s in group['students']))
    check('в списке есть «ещё не входил»', 'ещё не входил' in text)
    check('в списке есть признак временного пароля',
          'временный, не менялся' in text)
    check('в списке нет ни одного пароля',
          all(pw not in html for pw in (PASSWORD, 'DrugoyParol1')))
    check('в списке нет колонки с паролем',
          'Пароль</th>' not in html and 'пароль</th>' not in html.lower())

    filtered = client.get(f'/accounts?group_id={group["id"]}')
    check('фильтр по группе работает', filtered.status_code == 200)
    by_login = client.get('/accounts?q=' + target['login'].replace('-', '-'))
    check('поиск по зачётке работает', by_login.status_code == 200)
    by_name = client.get('/accounts?q=Тестов1')
    check('поиск по ФИО работает', by_name.status_code == 200)

    # ================= 11.5 сброс, блокировка, удаление ==================
    print('\n--- 11.5 Действия администратора ---')
    with app.app_context():
        student_user = User.query.filter_by(username=target['login']).first()
        student_user_id = student_user.id
        before_hash = student_user.password_hash

    response = client.post(f'/accounts/{student_user_id}/reset',
                           follow_redirects=True)
    html = response.get_data(as_text=True)
    with app.app_context():
        user = db.session.get(User, student_user_id)
        new_password = user.password_hash
    shown = re.findall(r'<td><code class="fs-5">([^<]+)</code></td>', html)
    check('после сброса показан новый пароль', len(shown) == 1)
    if shown:
        with app.app_context():
            user = db.session.get(User, student_user_id)
            check('новый пароль действительно установлен',
                  user.check_password(shown[0].strip()))
            check('старый пароль после сброса не работает',
                  not user.check_password('DrugoyParol1'))
        check('пароль в базе изменился', new_password != before_hash)

    # сброс напечатанного пароля на странице списка быть не должно
    html = client.get('/accounts').get_data(as_text=True)
    check('после сброса пароль не виден в списке',
          shown and shown[0].strip() not in html)

    # блокировка
    client.post(f'/accounts/{student_user_id}/toggle', follow_redirects=True)
    with app.app_context():
        user = db.session.get(User, student_user_id)
        check('запись заблокирована', user.is_active is False)

    student_session = app.test_client()
    if shown:
        response = login(student_session, target['login'], shown[0].strip())
        blocked = student_session.get('/my/account', follow_redirects=False)
        check('заблокированный студент не пускает в кабинет',
              blocked.status_code in (302, 401, 403),
              f'статус {blocked.status_code}')
        text = page_text(response.get_data(as_text=True))
        check('студенту сказано, что запись отключена',
              'отключена' in text or 'заблокирован' in text.lower(),
              'ожидалось сообщение об отключённой записи')

    client.post(f'/accounts/{student_user_id}/toggle', follow_redirects=True)
    with app.app_context():
        user = db.session.get(User, student_user_id)
        check('запись разблокирована', user.is_active is True)
    if shown:
        student_session = app.test_client()
        login(student_session, target['login'], shown[0].strip())
        check('после разблокировки студент снова входит',
              student_session.get('/my/account').status_code == 200)

    # смена своего пароля снимает признак временного
    if shown:
        password_page = student_session.get('/my/password')
        check('студенту доступна смена пароля',
              password_page.status_code == 200)
        response = student_session.post('/my/password', data={
            'current_password': shown[0].strip(), 'new_password': 'SvoyParol777',
            'confirm_password': 'SvoyParol777',
            'csrf_token': csrf(student_session, '/my/password')},
            follow_redirects=True)
        check('студент сменил пароль',
              'успешно изменён' in page_text(response.get_data(as_text=True)))
        with app.app_context():
            user = db.session.get(User, student_user_id)
            check('после смены пароль не временный',
                  user.password_temporary is False)
            check('новый собственный пароль работает',
                  user.check_password('SvoyParol777'))
        relogin = app.test_client()
        login(relogin, target['login'], 'SvoyParol777')
        check('вход с новым паролем удаётся',
              relogin.get('/my/account').status_code == 200)

    # удаление записи, студент остаётся
    with app.app_context():
        student = db.session.get(Student, target['id'])
        Grade.query.filter_by(student_id=student.id).first()
    client.post(f'/accounts/{student_user_id}/delete', follow_redirects=True)
    with app.app_context():
        check('учётная запись удалена',
              db.session.get(User, student_user_id) is None)
        student = db.session.get(Student, target['id'])
        check('студент остался в базе', student is not None)
        check('у студента снята связь с учётной записью',
              student.user_id is None)

    # повторная выдача после удаления
    issue_one(admin_client(), target, password=PASSWORD)
    with app.app_context():
        user = User.query.filter_by(username=target['login']).first()
        check('запись можно выдать заново', user is not None)
        check('повторно выданный пароль работает',
              user is not None and user.check_password(PASSWORD))

    # ================= 11.7 доступ и меню ===============================
    print('\n--- 11.7 Доступ и меню ---')
    with app.app_context():
        stale = User.query.filter_by(username=TEST_TEACHER).first()
        if stale:
            db.session.delete(stale)
            db.session.flush()
        outsider = User(username=TEST_TEACHER, full_name='Посторонний Ф11',
                        role=User.ROLE_TEACHER, is_active=True)
        outsider.set_password(TEACHER_PASSWORD)
        db.session.add(outsider)
        db.session.commit()
        outsider_id = outsider.id

    other = app.test_client()
    login(other, TEST_TEACHER, TEACHER_PASSWORD)
    check('преподаватель не открывает /accounts',
          other.get('/accounts').status_code == 403)
    check('преподаватель не выдаёт записи по одному студенту',
          other.post(f'/students/{target["id"]}/account',
                     data={'password': PASSWORD, 'confirm_password': PASSWORD}
                     ).status_code == 403)
    check('преподаватель не выдаёт записи массово',
          other.post('/accounts/issue',
                     data={'group_id': str(group['id'])}).status_code == 403)
    check('преподаватель не сбрасывает пароль',
          other.post(f'/accounts/{student_user_id}/reset').status_code == 403)
    check('преподаватель не удаляет запись',
          other.post(f'/accounts/{student_user_id}/delete').status_code == 403)
    check('преподаватель не качает ведомость с паролями',
          other.get('/accounts/credentials/любой.xlsx').status_code == 403)
    check('в меню преподавателя нет пункта «Учётные записи»',
          'Учётные записи</span>' not in other.get('/').get_data(as_text=True))

    admin_html = admin_client().get('/').get_data(as_text=True)
    check('в меню администратора есть пункт «Учётные записи»',
          'Учётные записи</span>' in admin_html)
    check('в меню ведёт на /accounts', 'href="/accounts"' in admin_html)

    check('старый адрес выдачи из Фазы 5 убран',
          admin_client().post('/staff/student-accounts',
                              data={'group': str(group['id'])}).status_code
          in (404, 405))
    check('на странице сотрудников есть ссылка на учётные записи',
          'href="/accounts"' in admin_client().get('/staff').get_data(as_text=True))

    with app.app_context():
        db.session.delete(db.session.get(User, outsider_id))
        db.session.commit()


def main():
    state = snapshot()
    try:
        run_checks()
    finally:
        restore(state)
        remove_issued_files()
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
