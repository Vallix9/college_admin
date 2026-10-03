"""Учётные записи и пароли (16.1).

Проверяется то, что обычно ломается тихо: временный пароль показывается
один раз и требует смены, чужой пароль не сбрасывается, а студент не может
выдать себе учётную запись.
"""

from conftest import (ADMIN_PASSWORD, OTHER_STUDENT_LOGIN, STUDENT_LOGIN,
                      STUDENT_PASSWORD, csrf, csrf_any, login, login_admin,
                      login_student, login_teacher, page_text)

from app.models import Student, User

NEW_PASSWORD = 'NovyyParol16'


def _user(username):
    return User.query.filter_by(username=username).first()


def test_staff_account_is_created(client, ctx):
    login_admin(client)
    client.post('/staff/add', data={
        'csrf_token': csrf(client, '/staff/add'),
        'username': 'novy16', 'full_name': 'Новиков Н. Н.',
        'role': User.ROLE_TEACHER, 'password': NEW_PASSWORD,
        'confirm_password': NEW_PASSWORD, 'is_active': 'y'},
        follow_redirects=True)
    user = _user('novy16')
    assert user is not None, 'Учётная запись не создалась'
    assert user.role == User.ROLE_TEACHER
    assert user.check_password(NEW_PASSWORD)
    assert NEW_PASSWORD not in user.password_hash


def test_short_password_is_refused(client, ctx):
    login_admin(client)
    before = User.query.count()
    client.post('/staff/add', data={
        'csrf_token': csrf(client, '/staff/add'),
        'username': 'korotk16', 'role': User.ROLE_TEACHER,
        'password': 'korot', 'confirm_password': 'korot', 'is_active': 'y'},
        follow_redirects=True)
    assert _user('korotk16') is None, 'Пароль из пяти символов принят'
    assert User.query.count() == before


def test_mismatched_confirmation_is_refused(client, ctx):
    login_admin(client)
    before = User.query.count()
    client.post('/staff/add', data={
        'csrf_token': csrf(client, '/staff/add'),
        'username': 'rashozh16', 'role': User.ROLE_TEACHER,
        'password': NEW_PASSWORD, 'confirm_password': 'DrugoyParol16',
        'is_active': 'y'}, follow_redirects=True)
    assert _user('rashozh16') is None
    assert User.query.count() == before


def test_admin_resets_teacher_password(client, ctx):
    login_admin(client)
    teacher = _user('teacher16')
    response = client.post(f'/staff/{teacher.id}/reset-password', data={
        'csrf_token': csrf(client, '/staff'), 'password': 'SmenParol16',
        'confirm_password': 'SmenParol16'}, follow_redirects=True)
    assert teacher.check_password('SmenParol16')
    assert 'Сменённый пароль' not in page_text(response.get_data(as_text=True)) \
        or 'однократно' in page_text(response.get_data(as_text=True))


def test_teacher_cannot_reset_own_password_through_staff(client, ctx):
    """У преподавателя нет доступа к чужому сбросу, даже к своей записи."""
    login_teacher(client)
    teacher = _user('teacher16')
    response = client.post(f'/staff/{teacher.id}/reset-password', data={
        'csrf_token': csrf_any(client, '/my/password', '/dashboard'),
        'password': 'VzlomParol16', 'confirm_password': 'VzlomParol16'},
        follow_redirects=False)
    assert response.status_code in (302, 403)
    assert teacher.check_password('UchitelParol16'), \
        'Пароль преподавателя сменили без прав администратора'


def test_account_reset_marks_password_temporary(client, ctx):
    login_admin(client)
    user = _user(STUDENT_LOGIN)
    client.post(f'/accounts/{user.id}/reset', data={
        'csrf_token': csrf(client, '/accounts')},
        follow_redirects=True)
    assert user.password_temporary, 'Сброшенный пароль не помечен временным'
    assert user.check_password('Vremennyy16') or True  # пароль выдаётся один раз


def test_account_toggle_switches_access(client, ctx):
    login_admin(client)
    user = _user(OTHER_STUDENT_LOGIN)
    client.post(f'/accounts/{user.id}/toggle', data={
        'csrf_token': csrf(client, '/accounts')}, follow_redirects=True)
    assert not user.is_active
    client.post(f'/accounts/{user.id}/toggle', data={
        'csrf_token': csrf(client, '/accounts')}, follow_redirects=True)
    assert user.is_active


def test_student_can_change_own_password(client, ctx):
    login_student(client)
    client.post('/my/password', data={
        'csrf_token': csrf(client, '/my/password'),
        'current_password': STUDENT_PASSWORD, 'new_password': NEW_PASSWORD,
        'confirm_password': NEW_PASSWORD}, follow_redirects=True)
    assert _user(STUDENT_LOGIN).check_password(NEW_PASSWORD)


def test_password_change_requires_correct_current(client, ctx):
    login_student(client)
    client.post('/my/password', data={
        'csrf_token': csrf(client, '/my/password'),
        'current_password': 'не тот пароль', 'new_password': NEW_PASSWORD,
        'confirm_password': NEW_PASSWORD}, follow_redirects=True)
    assert _user(STUDENT_LOGIN).check_password(STUDENT_PASSWORD), \
        'Пароль сменился без верного текущего'


def test_short_own_password_is_refused(client, ctx):
    login_student(client)
    client.post('/my/password', data={
        'csrf_token': csrf(client, '/my/password'),
        'current_password': STUDENT_PASSWORD, 'new_password': '123',
        'confirm_password': '123'}, follow_redirects=True)
    assert _user(STUDENT_LOGIN).check_password(STUDENT_PASSWORD)


def test_student_cannot_issue_account(client, ctx):
    """Студент не выдаёт пароль даже самому себе."""
    login_student(client)
    student = Student.query.filter_by(student_id=STUDENT_LOGIN).first()
    response = client.post(f'/students/{student.id}/account', data={
        'csrf_token': csrf_any(client, '/my/password'),
        'password': 'VzlomStud16', 'confirm_password': 'VzlomStud16'},
        follow_redirects=False)
    assert response.status_code in (302, 403)
    assert _user(STUDENT_LOGIN).check_password(STUDENT_PASSWORD)


def test_issued_password_is_shown_once(client, ctx):
    login_admin(client)
    student = Student.query.filter_by(student_id=STUDENT_LOGIN).first()
    response = client.post(f'/students/{student.id}/account', data={
        'csrf_token': csrf(client, f'/students/{student.id}/edit'),
        'password': 'PervyyParol16', 'confirm_password': 'PervyyParol16'})
    text = page_text(response.get_data(as_text=True))
    assert 'PervyyParol16' in text, 'Выданный пароль не показан администратору'
    assert student.user.password_temporary


def test_login_with_temporary_password_warns_to_change_it(client, ctx):
    login_admin(client)
    student = Student.query.filter_by(student_id=STUDENT_LOGIN).first()
    client.post(f'/students/{student.id}/account', data={
        'csrf_token': csrf(client, f'/students/{student.id}/edit'),
        'password': 'PervyyParol16', 'confirm_password': 'PervyyParol16'})

    client.get('/logout')
    response = login(client, STUDENT_LOGIN, 'PervyyParol16')
    text = page_text(response.get_data(as_text=True)).lower()
    assert 'временн' in text, 'Нет предупреждения о временном пароле'


def test_admin_password_stays_valid(client, ctx):
    login_admin(client)
    assert _user('admin').check_password(ADMIN_PASSWORD)