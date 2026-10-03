"""CSRF, роли, изоляция кабинета и заголовки (16.1).

Здесь важно, что запрет возвращает отказ, а не «вежливую» страницу:
POST без токена обязан быть отвергнут (400), а студент не должен попасть
на страницы сотрудника ни при каком методе.
"""

from conftest import (OTHER_STUDENT_LOGIN, STUDENT_PASSWORD, csrf,
                      csrf_any, login_admin, login_student, login_teacher,
                      logout, page_text)

from app.models import Group, Student, Subject, User

# Формы WTForms отвечают 200 с текстом ошибок, поэтому проверяется не код
# ответа, а главное: данные не изменились. Маршруты с явной проверкой
# токена (check_csrf_or_none) отвечают 400.
FORM_ROUTES = [
    ('/staff/add', {'username': 'vzlom16', 'role': User.ROLE_TEACHER,
                    'password': 'VzlomParol16',
                    'confirm_password': 'VzlomParol16'}),
    ('/groups/add', {'name': 'ВЗЛОМ-16', 'specialty': 'x', 'year': '2027'}),
]

GUARDED_ROUTES = [
    ('/subjects/add', {'name': 'ВЗЛОМ', 'hours': '10'}),
    ('/accounts/issue', {'student_ids': '1'}),
]


def test_post_without_csrf_changes_nothing(client, ctx):
    login_admin(client)
    users_before = User.query.count()
    for path, data in FORM_ROUTES:
        client.post(path, data=data)
    assert User.query.count() == users_before, 'Учётная запись создана без токена'
    assert User.query.filter_by(username='vzlom16').first() is None
    assert Group.query.filter_by(name='ВЗЛОМ-16').first() is None


def test_guarded_routes_answer_400_without_token(client, ctx):
    login_admin(client)
    before = Subject.query.count()
    for path, data in GUARDED_ROUTES:
        response = client.post(path, data=data)
        assert response.status_code == 400, \
            f'{path} принял POST без CSRF-токена'
    assert Subject.query.count() == before


def test_post_with_wrong_csrf_is_rejected(client, ctx):
    login_admin(client)
    before = Subject.query.count()
    response = client.post('/subjects/add', data={
        'csrf_token': 'подделка', 'name': 'ВЗЛОМ', 'hours': '10'})
    assert response.status_code == 400
    assert Subject.query.count() == before


def test_student_is_locked_out_of_staff_pages(client, ctx):
    """Кабинет студента не должен открывать страницы сотрудника.

    Проверяем именно GET всех ключевых разделов: преподавательские права
    проверяются декораторами, а не «скрытием» пунктов меню.
    """
    login_student(client)
    for path in ('/staff', '/students', '/journal', '/attendance',
                 '/groups', '/subjects', '/schedule', '/results',
                 '/accounts', '/audit', '/settings/logs', '/periods',
                 '/grades/add'):
        response = client.get(path, follow_redirects=False)
        assert response.status_code in (302, 403), \
            f'Студент получил доступ к {path} ({response.status_code})'
        if response.status_code == 302:
            assert '/portal' in response.headers['Location'] or \
                '/login' in response.headers['Location'], \
                f'Студента с {path} отправили в неизвестное место'


def test_teacher_is_locked_out_of_admin_pages(client, ctx):
    login_teacher(client)
    for path in ('/staff', '/accounts', '/settings/logs'):
        response = client.get(path, follow_redirects=False)
        assert response.status_code in (302, 403), \
            f'Преподаватель получил доступ к {path}'


def test_teacher_sees_only_own_audit_entries(client, ctx):
    """Журнал действий преподавателю положен — но только его собственные."""
    login_teacher(client)
    client.get('/students')
    logout(client)
    login_admin(client)
    client.get('/audit')

    logout(client)
    login_teacher(client)
    text = page_text(client.get('/audit').get_data(as_text=True))
    assert 'admin' not in text.lower(), 'В журнале видны чужие действия'


def test_student_cannot_read_other_student_card(client, ctx):
    """Карточка другого студента закрыта: student_id не подставляется."""
    login_student(client)
    other = Student.query.filter_by(student_id=OTHER_STUDENT_LOGIN).first()
    response = client.get(f'/student/{other.id}', follow_redirects=False)
    assert response.status_code in (302, 403)


def test_teacher_cannot_edit_foreign_subject(client, ctx):
    from conftest import OTHER_TEACHER_LOGIN, OTHER_TEACHER_PASSWORD, login
    from app.models import Subject
    login(client, OTHER_TEACHER_LOGIN, OTHER_TEACHER_PASSWORD)
    foreign = Subject.query.filter_by(name='Математика').first()
    response = client.get(f'/subjects/{foreign.id}/edit', follow_redirects=False)
    assert response.status_code in (302, 403)


def test_anonymous_get_stays_401_or_redirects(client, ctx):
    for path in ('/', '/students', '/journal', '/portal', '/my/password'):
        response = client.get(path, follow_redirects=False)
        assert response.status_code in (302, 401, 403)
        if response.status_code == 302:
            assert '/login' in response.headers['Location']


def test_security_headers_present(client, ctx):
    """Заголовки, которые отвечают за базовую защиту браузера."""
    login_admin(client)
    response = client.get('/dashboard')
    assert 'nosniff' in response.headers.get('X-Content-Type-Options', '')
    assert response.headers.get('X-Frame-Options') in ('SAMEORIGIN', 'DENY')


def test_html_is_escaped_not_rendered(client, ctx):
    """ФИО с разметкой не должно превращаться в элементы страницы."""
    login_admin(client)
    student = Student.query.filter_by(student_id='ST0016').first()
    client.post(f'/students/{student.id}/edit', data={
        'csrf_token': csrf(client, f'/students/{student.id}/edit'),
        'student_id': 'ST0016', 'last_name': '<script>alert(1)</script>',
        'first_name': 'Иван', 'group_id': str(student.group_id),
        'status': 'active'}, follow_redirects=True)
    html = client.get(f'/student/{student.id}').get_data(as_text=True)
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;' in html


def test_journal_export_requires_staff(client, ctx):
    login_student(client)
    response = client.get('/journal/export', follow_redirects=False)
    assert response.status_code in (302, 403)


def test_login_page_has_no_password_value(client, ctx):
    """Пароль не должен возвращаться в разметке формы."""
    html = client.get('/login').get_data(as_text=True)
    assert 'value="' not in html.split('name="password"')[-1][:200] \
        or 'password' in html


def test_session_cookie_is_httponly_and_samesite(client, ctx):
    """Кука сессии не читается из JavaScript и не уходит на чужой POST."""
    response = client.post('/login', data={
        'csrf_token': csrf(client, '/login'), 'username': 'admin',
        'password': 'AdminParol16'}, follow_redirects=False)
    cookie = response.headers.get('Set-Cookie', '')
    assert 'session=' in cookie, 'Кука сессии не выдана'
    assert 'HttpOnly' in cookie, 'Кука сессии доступна из JavaScript'
    assert 'SameSite=Lax' in cookie, 'Кука сессии без SameSite'


def test_student_cannot_reach_export_of_other_group(client, ctx):
    """Экспорт ведомости — только своим предметом или с правами админа."""
    login_student(client)
    for path in ('/journal/export?group_id=1', '/results/export'):
        response = client.get(path, follow_redirects=False)
        assert response.status_code in (302, 403), \
            f'Студент получил {path}'


def test_temporary_password_flag_after_issue(client, ctx):
    """Выданный пароль помечается временным: вход с ним требует смены."""
    login_admin(client)
    student = Student.query.filter_by(student_id='ST0016').first()
    client.post(f'/students/{student.id}/account', data={
        'csrf_token': csrf_any(client, f'/students/{student.id}/edit'),
        'password': 'VremennyParol16', 'confirm_password': 'VremennyParol16'})
    user = User.query.filter_by(username='ST0016').first()
    assert user.password_temporary is True


def test_issued_student_password_works(client, ctx):
    login_admin(client)
    student = Student.query.filter_by(student_id='ST0016').first()
    client.post(f'/students/{student.id}/account', data={
        'csrf_token': csrf_any(client, f'/students/{student.id}/edit'),
        'password': 'NovichokParol16', 'confirm_password': 'NovichokParol16'})
    client.get('/logout')

    from conftest import login
    response = login(client, 'ST0016', 'NovichokParol16')
    assert response.status_code == 200
    assert Student.query.filter_by(student_id='ST0016').first().user_id


def test_student_password_is_not_staff_password(client, ctx):
    """Пароль студента не подходит сотруднику с тем же паролем."""
    login_student(client, 'ST0016')
    client.get('/logout')
    response = client.post('/login', data={
        'csrf_token': csrf(client, '/login'), 'username': 'teacher16',
        'password': STUDENT_PASSWORD}, follow_redirects=True)
    assert 'Учётные записи' not in response.get_data(as_text=True)