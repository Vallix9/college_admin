"""Сквозная проверка маршрутов: ни одна страница не должна падать с 5xx.

Маршруты берутся из карты URL самого приложения, поэтому новый маршрут
автоматически попадает в проверку. Значения параметров подставляются из
seed-данных, а для POST-маршрутов проверяется, что GET на них даёт 405, а не
500: это сразу показывает, что маршрут случайно не стал доступен на чтение.
"""

import pytest

from conftest import login_admin, login_student, login_teacher

from app.models import Group, Student, User
from app.utils import create_backup

# Значения для параметров URL. Числовые берём из seed-данных, чтобы
# страница открывалась по-настоящему, а не отдавала 404.
IDS = {}
STRING_VALUES = {
    'import_type': 'students',
}


@pytest.fixture()
def urls(app):
    """Готовые адреса всех GET-маршрутов приложения."""
    backup = create_backup(description='для проверки маршрутов')
    STRING_VALUES['filename'] = backup

    adapter = app.url_map.bind('localhost')
    result = {'get': [], 'post_only': []}
    for rule in app.url_map.iter_rules():
        if rule.endpoint == 'static':
            continue
        # /logout — GET, который завершает сессию: в обходе он сломал бы
        # авторизацию для всех следующих адресов. Проверен в test_auth.
        if rule.endpoint == 'main.logout':
            continue
        args = {name: _value(name) for name in sorted(rule.arguments)}
        method = 'GET' if 'GET' in rule.methods else 'POST'
        address = adapter.build(rule.endpoint, args, method=method)
        if 'GET' in rule.methods:
            result['get'].append((rule.endpoint, address))
        else:
            result['post_only'].append((rule.endpoint, address))
    result['get'].sort()
    result['post_only'].sort()
    return result


def _value(name):
    if name in STRING_VALUES:
        return STRING_VALUES[name]
    if name in IDS:
        return IDS[name]
    return 1  # любой int-параметр: несуществующий id даст 404, не 500


@pytest.fixture(autouse=True)
def _collect_ids(app):
    with app.app_context():
        IDS['student_id'] = Student.query.first().id
        IDS['group_id'] = Group.query.first().id
        IDS['id'] = Student.query.first().id
        IDS['user_id'] = User.query.filter_by(role='teacher').first().id
    yield
    IDS.clear()
    STRING_VALUES.pop('filename', None)


@pytest.mark.parametrize('role', ['admin', 'teacher', 'student', 'anonymous'])
def test_no_page_raises_server_error(client, urls, role):
    """Каждая GET-страница отдаётся без 5xx при любой роли."""
    if role == 'admin':
        login_admin(client)
    elif role == 'teacher':
        login_teacher(client)
    elif role == 'student':
        login_student(client)

    broken = []
    for endpoint, address in urls['get']:
        response = client.get(address, follow_redirects=True)
        if response.status_code >= 500:
            broken.append(f'{endpoint} ({address}) -> {response.status_code}')

    assert not broken, 'Страницы с ошибкой сервера:\n' + '\n'.join(broken)


def test_admin_really_opens_pages(client, urls):
    """Проверка не должна проходить за счёт 404 на каждом маршруте."""
    login_admin(client)

    key_pages = ['/dashboard', '/students', '/students/add', '/groups',
                 '/groups/add', '/subjects', '/subjects/add', '/schedule',
                 '/journal', '/grades', '/grades/add', '/attendance',
                 '/reports', '/reports/students', '/results', '/periods',
                 '/periods/add', '/staff', '/accounts', '/settings',
                 '/settings/backup', '/settings/import', '/settings/logs',
                 '/audit', '/my/password', '/health']

    broken = []
    for address in key_pages:
        status = client.get(address).status_code
        if status != 200:
            broken.append(f'{address} -> {status}')
    assert not broken, 'Ключевые страницы не открылись:\n' + '\n'.join(broken)

    # Ожидаемые особые случаи: / и /my/account ведут администратора дальше,
    # кабинет студента администратору закрыт по правам.
    assert client.get('/').status_code in (200, 302)
    assert client.get('/my/account').status_code in (200, 302)
    for address in ('/portal', '/portal/grades', '/portal/attendance'):
        assert client.get(address).status_code == 403, \
            f'{address} не закрыт от администратора'


def test_get_on_post_only_routes_is_rejected(client, urls):
    """POST-маршруты не должны отвечать на GET."""
    login_admin(client)
    wrong = []
    for endpoint, address in urls['post_only']:
        status = client.get(address).status_code
        if status != 405:
            wrong.append(f'{endpoint} ({address}) -> {status}')
    assert not wrong, 'GET на POST-маршрутах:\n' + '\n'.join(wrong)
    assert len(urls['post_only']) >= 25, 'Список POST-маршрутов подозрительно мал'


def test_health_is_available_without_login(client):
    """Проверка живости не должна требовать авторизации."""
    response = client.get('/health')
    assert response.status_code == 200
    assert 'status' in response.get_data(as_text=True).lower()


def test_audit_page_requires_login(client):
    response = client.get('/audit')
    assert response.status_code == 302
    assert '/login' in response.headers['Location']