"""Вход, выход и ограничение попыток (16.1).

Проверяется поведение, а не наличие формы: неверный пароль должен
оставлять человека на странице входа, а пять неудач подряд — закрывать
вход даже с верным паролем.
"""

from conftest import (ADMIN_LOGIN, STUDENT_LOGIN, STUDENT_PASSWORD,
                      TEACHER_LOGIN, TEACHER_PASSWORD, csrf, login_admin,
                      login_student, login_teacher, logout, page_text)

from app.init_ import db
from app.models import User


def test_login_page_has_form_and_token(client):
    response = client.get('/login')
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'csrf_token' in html
    assert 'password' in html


def test_admin_login_sees_admin_menu(client):
    login_admin(client)
    text = page_text(client.get('/dashboard').get_data(as_text=True))
    assert 'Учётные записи' in text
    assert 'Журнал действий' in text


def test_teacher_login_has_no_accounts_section(client):
    login_teacher(client)
    text = page_text(client.get('/dashboard').get_data(as_text=True))
    assert 'Учётные записи' not in text
    assert 'Преподаватель' in text or 'Петров' in text


def test_student_login_goes_to_portal(client):
    response = login_student(client)
    assert '/portal' in response.request.path or 'Кабинет' in page_text(
        response.get_data(as_text=True))


def test_wrong_password_stays_on_login(client, ctx):
    response = client.post('/login', data={
        'csrf_token': csrf(client, '/login'),
        'username': ADMIN_LOGIN, 'password': 'не тот пароль'},
        follow_redirects=True)
    text = page_text(response.get_data(as_text=True))
    assert 'Невер' in text or 'невер' in text
    assert 'Учётные записи' not in text
    user = User.query.filter_by(username=ADMIN_LOGIN).first()
    assert user.failed_login_count >= 1
    user.clear_failed_logins()
    db.session.commit()


def test_five_failures_lock_the_account(client, ctx):
    """Пять неудач подряд закрывают вход даже с верным паролем.

    Иначе ограничение было бы украшением: пароль подбирают, пока не
    угадают, а не пять раз.
    """
    try:
        for _ in range(5):
            client.post('/login', data={
                'csrf_token': csrf(client, '/login'),
                'username': TEACHER_LOGIN, 'password': 'ошибка'},
                follow_redirects=True)
        user = User.query.filter_by(username=TEACHER_LOGIN).first()
        assert user.login_locked, 'После пяти неудач вход не закрыт'

        response = client.post('/login', data={
            'csrf_token': csrf(client, '/login'),
            'username': TEACHER_LOGIN, 'password': TEACHER_PASSWORD},
            follow_redirects=True)
        text = page_text(response.get_data(as_text=True))
        assert 'Слишком много неудачных попыток' in text
        assert 'Петров' not in text
    finally:
        user = User.query.filter_by(username=TEACHER_LOGIN).first()
        user.clear_failed_logins()
        db.session.commit()


def test_disabled_account_cannot_log_in(client, ctx):
    user = User.query.filter_by(username=STUDENT_LOGIN).first()
    user.is_active = False
    db.session.commit()
    try:
        response = client.post('/login', data={
            'csrf_token': csrf(client, '/login'),
            'username': STUDENT_LOGIN, 'password': STUDENT_PASSWORD},
            follow_redirects=True)
        assert 'отключ' in page_text(response.get_data(as_text=True)).lower()
    finally:
        user.is_active = True
        db.session.commit()


def test_logout_closes_session(client):
    login_admin(client)
    response = logout(client)
    assert response.status_code == 302
    after = client.get('/students', follow_redirects=False)
    assert after.status_code == 302
    assert '/login' in after.headers['Location']


def test_anonymous_is_redirected_to_login(client):
    response = client.get('/students', follow_redirects=False)
    assert response.status_code == 302
    assert '/login' in response.headers['Location']