from flask import Blueprint, render_template, redirect, url_for, flash, request, send_file, send_from_directory
from flask_login import login_user, logout_user, login_required, current_user
from urllib.parse import urlparse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload
from datetime import datetime, date
from sqlalchemy import or_, func, desc, case
from functools import wraps
import os
from app.init_ import db
from app.models import (User, Student, Group, Subject, Grade, SystemSettings,
                        AcademicPeriod, ScheduleItem, LessonDate,
                        AttendanceRecord, GradeHistory, PeriodResult, AuditLog)
from app.forms import LoginForm, StudentForm, GroupForm, GradeForm, SubjectForm, SettingsForm, BackupForm, ImportForm
from app.forms import ClearLogsForm, StaffForm, StaffPasswordResetForm, AccountPasswordForm
from app.forms import StudentAccountForm
from app.forms import PeriodForm, PeriodGenerateForm, ScheduleItemForm, DeleteTokenForm
from app.forms import ScheduleDayForm, LessonDateForm
from app.forms import JournalGradeForm, JournalEditForm, PeriodResultForm
from app.forms import (AttendanceMarkForm, AttendanceBulkForm,
                       AttendanceEditForm, attendance_reason_choices,
                       ALL_DAY_SUBJECT, ALL_DAY_LABEL)
from app.utils import build_periods, parse_academic_year, teacher_choices
from app.utils import export_to_excel, format_date, create_backup, restore_backup, import_from_file
from app.utils import EXPORT_DIR
import pandas as pd
from app.utils import list_backups, delete_backup, get_backup_dir, sanitize_filename
from app.utils import (get_logger, average_grade, build_import_template,
                        log_audit)
from app.utils import grade_distribution, journal_totals
from app.utils import recommend_final_grade, grade_quality_stats
from app.utils import read_log_lines, get_log_file_path, clear_log_file
from app.utils import generate_password
from app.utils import (generate_temporary_password, username_taken,
                       normalize_username, credentials_sheets,
                       find_user_by_login)

log = get_logger()

main = Blueprint('main', __name__)

# ===================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====================
def flash_msg(type, message):
    icons = {'success': '✅', 'error': '❌', 'warning': '⚠️', 'info': 'ℹ️'}
    flash(f"{icons.get(type, '')} {message}", type if type != 'error' else 'danger')

def get_settings():
    return SystemSettings.get_settings()

def safe_int(value, default=0):
    try:
        return int(value)
    except (ValueError, TypeError):
        return default

def is_safe_redirect(target):
    """Проверяет, что цель редиректа ведёт внутри приложения.

    Отклоняем абсолютные URL, протокол-относительные ('//host', '/\\host') и
    любые цели без ведущего слэша — иначе получаем open redirect.
    """
    if not target:
        return False
    if not target.startswith('/'):
        return False
    if target.startswith('//') or target.startswith('/\\'):
        return False
    return urlparse(target).netloc == '' and not urlparse(target).scheme

def get_pagination_args():
    return {
        'page': request.args.get('page', 1, type=int),
        'per_page': get_settings().items_per_page or 20
    }

def apply_filters(query, model):
    """Применяет фильтры из строки запроса и возвращает их же для шаблона.

    Возвращённый словарь используется формами фильтрации, поэтому в него
    попадает и search — иначе поле поиска теряет введённое значение при
    переходе на следующую страницу.
    """
    filters = {}
    search = (request.args.get('search') or '').strip()
    if search:
        search_term = f'%{search}%'
        if model is Student:
            query = query.filter(or_(
                Student.last_name.ilike(search_term),
                Student.first_name.ilike(search_term),
                Student.patronymic.ilike(search_term),
                Student.student_id.ilike(search_term)
            ))
        elif model is Subject:
            query = query.filter(Subject.name.ilike(search_term))
        filters['search'] = search

    group_id = request.args.get('group')
    if group_id and group_id != 'all':
        query = query.filter_by(group_id=group_id)
        filters['group'] = group_id

    status = request.args.get('status')
    if status and status != 'all':
        query = query.filter_by(status=status)
        filters['status'] = status

    # Фильтр по полу есть в students.html, но бэкенд его не обрабатывал —
    # выбор молча ничего не менял
    gender = request.args.get('gender')
    if gender and gender != 'all':
        query = query.filter_by(gender=gender)
        filters['gender'] = gender

    return query, filters

def group_student_counts(groups_list, status='active'):
    """Число активных студентов по каждой группе одним запросом.

    group.student_count() дёргал отдельный COUNT на каждую групку, а
    шаблоны зовут его по два-три раза на группу.
    """
    if not groups_list:
        return {}

    rows = db.session.query(
        Student.group_id,
        db.func.count(Student.id)
    ).filter(
        Student.group_id.in_([g.id for g in groups_list]),
        Student.status == status
    ).group_by(Student.group_id).all()

    counts = {group_id: count for group_id, count in rows}
    return {g.id: counts.get(g.id, 0) for g in groups_list}

def student_average_grades():
    """Средние баллы всех студентов одним запросом.

    students.html звал student.average_grade() четыре раза на строку, а
    relationship grades ленивый — на каждого студента уходил отдельный
    SELECT. Считаем пачкой.
    """
    rows = db.session.query(Grade.student_id, Grade.grade_value).all()
    buckets = {}
    for student_id, value in rows:
        buckets.setdefault(student_id, []).append(value)
    return {student_id: average_grade(values) for student_id, values in buckets.items()}


def subject_grade_counts():
    """Число оценок по каждому предмету одним запросом (для subjects.html)."""
    rows = db.session.query(
        Grade.subject_id, db.func.count(Grade.id)
    ).group_by(Grade.subject_id).all()
    return dict(rows)


# ===================== РАЗГРАНИЧЕНИЕ ПРАВ ПО РОЛЯМ =====================

def role_required(*roles):
    """Доступ только для перечисленных ролей. Отказ — 403, не редирект.

    Редирект на страницу входа тут был бы неверным: пользователь уже
    авторизован, и молчаливый возврат на главную скрыл бы от него причину.
    """
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in roles:
                log.warning('Доступ запрещён: %s (роль %s) попытался открыть %s',
                            current_user.username, current_user.role, request.path)
                return render_template('error.html', code=403,
                                       message='У вас нет прав для просмотра этой страницы.'), 403
            return view(*args, **kwargs)
        return wrapped
    return decorator


admin_required = role_required(User.ROLE_ADMIN)
staff_required = role_required(User.ROLE_ADMIN, User.ROLE_TEACHER)
student_required = role_required(User.ROLE_STUDENT)


def teacher_owns_subject(subject_id):
    """Преподаватель не должен видеть и править чужие предметы."""
    subject = db.session.get(Subject, subject_id)
    if subject is None:
        return None, (render_template('error.html', code=404,
                                      message='Предмет не найден.'), 404)
    if not current_user.teaches(subject):
        log.warning('Доступ запрещён: %s (роль %s) не ведёт предмет «%s»',
                    current_user.username, current_user.role, subject.name)
        return None, (render_template('error.html', code=403,
                                      message='Этот предмет ведёт другой преподаватель.'), 403)
    return subject, None


def visible_subjects_query():
    """Предметы, доступные текущему пользователю.

    Преподаватель видит только свои, администратор — все, студент — ничего:
    список предметов ему не нужен, у него личный кабинет.
    """
    if current_user.is_admin:
        return Subject.query
    if current_user.is_teacher:
        return Subject.query.filter_by(teacher_id=current_user.id)
    return Subject.query.filter_by(id=-1)


def current_student():
    """Запись Student, связанная с текущим пользователем-студентом."""
    if not current_user.is_student:
        return None
    return Student.query.filter_by(user_id=current_user.id).first()


def home_url():
    """Куда отправлять пользователя после входа и с главной кнопки."""
    if current_user.is_authenticated and current_user.is_student:
        return url_for('main.portal')
    return url_for('main.dashboard')


def selected_date_from(source):
    """Дата из формы или запроса; пустая строка, если её нет."""
    raw = (source.form.get('date') or source.args.get('date') or '').strip()
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date().strftime('%Y-%m-%d')
    except (ValueError, TypeError):
        return ''


def schedule_conflict(group_id, day, lesson, teacher_id, item_id=None):
    """Ищет конфликт занятия. Возвращает текст ошибки или None.

    Две проверки, и обе нужны: у группы не может быть двух пар в одной
    ячейке (это ещё и ловит уникальный индекс), и у преподавателя не может
    быть двух пар в один момент в разных группах — это проверяется только
    здесь, ограничение в базе через внешние таблицы не выразить.
    """
    if teacher_id:
        query = ScheduleItem.query.filter(
            ScheduleItem.teacher_id == teacher_id,
            ScheduleItem.day_of_week == day,
            ScheduleItem.lesson_number == lesson)
        if item_id:
            query = query.filter(ScheduleItem.id != item_id)
        other = query.join(Group).first()
        if other:
            return (f'Преподаватель уже занят в это время: {ScheduleItem.DAY_SHORT.get(day, day)}, '
                    f'пара {lesson}, группа {other.group.name}')

    query = ScheduleItem.query.filter(
        ScheduleItem.group_id == group_id,
        ScheduleItem.day_of_week == day,
        ScheduleItem.lesson_number == lesson)
    if item_id:
        query = query.filter(ScheduleItem.id != item_id)
    own = query.first()
    if own:
        return (f'В расписании группы уже есть пара на этом месте: '
                f'{own.subject.name}')
    return None


# teacher_choices живёт в app.utils: он нужен и формам, а формы не должны
# импортировать маршруты (циклический импорт).

# ===================== АУТЕНТИФИКАЦИЯ =====================
@main.route('/')
@login_required
def index():
    """Корень сайта: сотрудник — сводка, студент — кабинет.

    Раньше `/` был тем же маршрутом, что и `/dashboard`, и студент,
    вписавший адрес вручную, получал 403 вместо своего кабинета.
    """
    return redirect(home_url())


@main.route('/dashboard')
@staff_required
def dashboard():
    try:
        settings = get_settings()
        stats = {
            'total_students': Student.query.count(),
            'active_students': Student.query.filter_by(status='active').count(),
            'total_groups': Group.query.count(),
            'male_students': Student.query.filter_by(gender='M').count(),
            'female_students': Student.query.filter_by(gender='F').count(),
        }
        
        recent_students = Student.query.order_by(desc(Student.created_at)).limit(5).all()
        # joinedload, иначе обращение к grade.student в шаблоне даёт
        # отдельный SELECT на каждую из десяти оценок
        recent_grades = (Grade.query
                         .options(joinedload(Grade.student), joinedload(Grade.subject))
                         .order_by(desc(Grade.created_at)).limit(10).all())

        return render_template('dashboard.html',
                             stats=stats,
                             recent_students=recent_students,
                             recent_grades=recent_grades,
                             attendance_stats=dashboard_attendance_stats(),
                             settings=settings)
    except Exception as e:
        flash_msg('error', f'Ошибка загрузки дашборда: {str(e)}')
        return render_template('dashboard.html', stats={}, recent_students=[], recent_grades=[])

@main.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(home_url())
    form = LoginForm()
    if form.validate_on_submit():
        # Логин ищется без учёта регистра: тот, кто набирает «ADMIN» вместо
        # «admin», тот же человек, а не другой сотрудник. Строгое сравнение
        # давало бы «неверный логин» на верном пароле — и администратор
        # объяснял бы это студенту, который путает заглавные буквы.
        user = find_user_by_login(form.username.data)
        
        # Пароль проверяется всегда, даже у отключённой записи. Иначе
        # «Учётная запись отключена» отвечала бы на любой логин, который
        # существует, и по подбору можно было бы узнать, какие логины в
        # системе есть. С верным паролем сообщение остаётся полезным.
        password_ok = user is not None and user.check_password(form.password.data)

        if password_ok and not user.is_active:
            log.warning('Попытка входа заблокированного пользователя «%s» с %s',
                        form.username.data, request.remote_addr)
            flash_msg('error', 'Учётная запись отключена. Обратитесь к администратору.')
            log_audit('login_blocked', user=user,
                      details=f'login={user.username!r}')
        elif password_ok:
            login_user(user, remember=form.remember_me.data)
            next_page = request.args.get('next')
            
            if not is_safe_redirect(next_page):
                next_page = home_url()
            
            user.last_login_at = datetime.now()
            db.session.commit()
            log.info('Вход выполнен: %s (роль: %s) с %s',
                     user.username, user.role, request.remote_addr)
            log_audit('login', user=user)
            flash_msg('success', f'Добро пожаловать, {user.display_name}!')
            return redirect(next_page)
        
        else:
            # Одно сообщение и на несуществующий логин, и на неверный пароль:
            # разные ответы сказали бы проверяющему, какие логины в системе есть
            log.warning('Неудачная попытка входа: логин «%s» с %s',
                        form.username.data, request.remote_addr)
            flash_msg('error', 'Неверное имя пользователя или пароль')
            # 13.2: попытка входа — тоже действие. Логин пишем (без пароля),
            # чтобы было видно подбор: user=None, потому что запись может
            # не существовать. Попытки неудачного входа — источник для
            # Фазы 14.4 (ограничение попыток).
            log_audit('login_failed', user=None,
                      details=f'login={form.username.data!r}')
    
    return render_template('login.html', form=form)

@main.route('/logout')
@login_required
def logout():
    log.info('Выход: %s', current_user.username)
    log_audit('logout', user=current_user)
    logout_user()
    flash_msg('success', 'Вы успешно вышли из системы')
    return redirect(url_for('main.login'))

# ===================== СТУДЕНТЫ =====================
@main.route('/students')
@staff_required
def students():
    query = Student.query
    query, filters = apply_filters(query, Student)
    page_args = get_pagination_args()
    students_paginated = query.order_by(Student.last_name).paginate(
        page=page_args['page'], per_page=page_args['per_page'], error_out=False)
    
    return render_template('students.html', 
                         students=students_paginated,
                         groups=Group.query.all(),
                         current_filters=filters,
                         averages=student_average_grades())

def changed_fields(form, obj, skip=()):
    """Имена полей формы, значения которых реально поменялись.

    Сравнение идёт с текущими значениями объекта, а не с исходными данными
    формы: форма уже заполнена значениями объекта при создании
    (obj=student), поэтому сравнивать надо с ним. В аудит идёт список
    названий, а не значений — в карточке студента есть email, телефон и
    дата рождения, и дублировать их в журнале действий незачем.
    """
    names = []
    for name in form.data:
        # WTForms отдаёт в form.data ещё служебные csrf-токены
        if name.startswith('csrf') or name in skip:
            continue
        field = getattr(form, name, None)
        if field is None or not hasattr(field, 'data'):
            continue
        before = getattr(obj, name, None)
        after = field.data
        if isinstance(after, str):
            after = after or None
        if after != before:
            names.append(name)
    return names


@main.route('/students/add', methods=['GET', 'POST'])
@admin_required
def add_student():
    form = StudentForm()
    form.group_id.choices = [(0, 'Без группы')] + [(g.id, g.name) for g in Group.query.all()]
    if form.validate_on_submit():
        try:
            student = Student(
                student_id=form.student_id.data,
                last_name=form.last_name.data,
                first_name=form.first_name.data,
                patronymic=form.patronymic.data,
                gender=form.gender.data,
                birth_date=form.birth_date.data,
                email=form.email.data,
                phone=form.phone.data,
                group_id=form.group_id.data if form.group_id.data != 0 else None,
                status=form.status.data,
                enrollment_date=form.enrollment_date.data
            )
            db.session.add(student)
            db.session.commit()
            log.info('Добавлен студент: %s (группа: %s, статус: %s) — %s',
                     student.full_name, student.group.name if student.group else '—',
                     student.status, current_user.username)
            log_audit('student_create', user=current_user, entity_type='student',
                      entity_id=student.id,
                      details=f'студент={student.full_name!r}, '
                              f'номер={student.student_id!r}, '
                              f'группа={student.group_id}, '
                              f'статус={student.status!r}')
            flash_msg('success', f'Студент {student.full_name} успешно добавлен')
            return redirect(url_for('main.students'))
        except IntegrityError:
            db.session.rollback()
            log.error('Дубликат при добавлении студента: %s', form.student_id.data)
            flash_msg('error', 'Запись с такими данными уже существует')
        except Exception as e:
            db.session.rollback()
            log.exception('Ошибка добавления студента: %s', e)
            flash_msg('error', f'Ошибка добавления студента: {str(e)}')
    
    return render_template('student_form.html', form=form, account_form=None,
                           title='Добавить студента', student=None)

@main.route('/students/<int:id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_student(id):
    student = Student.query.get_or_404(id)
    form = StudentForm(obj=student)
    form.group_id.choices = [(0, 'Без группы')] + [(g.id, g.name) for g in Group.query.all()]
    if form.validate_on_submit():
        try:
            # Логин ученической записи равен номеру зачётки, поэтому при его
            # правке старую запись надо переименовать. Пропуск приводил бы к
            # тому, что в ведомости один номер, а войти можно только по старому.
            account = student.user
            old_login = account.username if account else None
            new_login = normalize_username(form.student_id.data)
            if account and old_login != new_login:
                if username_taken(new_login, exclude_id=account.id):
                    form.student_id.errors.append(
                        f'Логин {new_login} уже занят другой учётной записью')
                    return render_template(
                        'student_form.html', form=form,
                        account_form=StudentAccountForm(), title='Редактировать студента',
                        student=student)
                log.warning('Логин ученической записи изменён: %s -> %s — %s',
                            old_login, new_login, current_user.username)
                account.username = new_login

            # Список изменённых полей снимаем до populate_obj: после него
            # в объекте уже новые значения и сравнивать не с чем.
            changed = changed_fields(form, student, skip=('group_id',))
            if old_login != new_login and account is not None:
                changed.append('account_login')

            form.populate_obj(student)
            student.group_id = form.group_id.data if form.group_id.data != 0 else None
            if account:
                # ФИО и email хранятся и в записи, и в карточке. Расхождение
                # выглядело бы как «список сотрудников не совпадает с журналом».
                account.full_name = student.full_name
                account.email = student.email or None
            db.session.commit()
            log.info('Изменён студент: %s — %s', student.student_id, current_user.username)
            # Список изменённых полей, а не их значения: email и телефон
            # относятся к персональным данным и в аудите не нужны
            log_audit('student_edit', user=current_user, entity_type='student',
                      entity_id=student.id,
                      details=f'студент={student.full_name!r}, поля={changed!r}')
            flash_msg('success', f'Данные студента {student.full_name} обновлены')
            return redirect(url_for('main.students'))
        except IntegrityError:
            db.session.rollback()
            log.error('Дубликат при изменении студента: %s', form.student_id.data)
            flash_msg('error', 'Запись с такими данными уже существует')
        except Exception as e:
            db.session.rollback()
            log.exception('Ошибка изменения студента: %s', e)
            flash_msg('error', f'Ошибка изменения студента: {str(e)}')
    
    return render_template('student_form.html', form=form,
                           account_form=StudentAccountForm(), title='Редактировать студента',
                           student=student)

@main.route('/students/<int:id>/delete', methods=['POST'])
@admin_required
def delete_student(id):
    student = Student.query.get_or_404(id)
    full_name = student.full_name
    try:
        db.session.delete(student)
        db.session.commit()
        log.warning('Удалён студент: %s — %s', full_name, current_user.username)
        log_audit('student_delete', user=current_user, entity_type='student',
                  entity_id=id, details=f'студент={full_name!r}')
        flash_msg('success', f'Студент {full_name} удален')
    except Exception as e:
        db.session.rollback()
        log.exception('Ошибка удаления студента %s: %s', full_name, e)
        flash_msg('error', f'Ошибка удаления студента: {str(e)}')
    return redirect(url_for('main.students'))

# ===================== СТРАНИЦА СТУДЕНТА =====================
@main.route('/student/<int:student_id>')
@staff_required
def view_student(student_id):
    """Детальная страница студента с управлением оценками"""
    student = Student.query.get_or_404(student_id)
    # Получаем все оценки студента
    grades = Grade.query.filter_by(student_id=student_id).order_by(Grade.date.desc()).all()
    
    # Группируем оценки по предметам
    grades_by_subject = {}
    for grade in grades:
        subject_id = grade.subject_id
        if subject_id not in grades_by_subject:
            grades_by_subject[subject_id] = {
                'subject': grade.subject,
                'grades': [],
                'average': 0
            }
        grades_by_subject[subject_id]['grades'].append(grade)
    
    # Вычисляем средний балл по каждому предмету
    for data in grades_by_subject.values():
        data['average'] = average_grade(data['grades'])
    
    # Все доступные предметы
    all_subjects = Subject.query.order_by(Subject.name).all()
    
    # Создаем формы для шаблона
    grade_form = GradeForm()
    grade_form.subject_id.choices = [(s.id, s.name) for s in all_subjects]
    subject_form = SubjectForm()
    
    # Текущая дата для шаблона
    today = date.today()

    # Пропуски за текущий период: сводка и список одним проходом по записям
    reasons = attendance_reason_choices()
    attendance_period, failure = current_period_or_default()
    attendance_records = []
    attendance_summary = {'total': 0, 'hours': 0,
                          'reasons': {reason: 0 for reason, _ in reasons}}
    if attendance_period is not None:
        attendance_records = (AttendanceRecord.query
                              .options(joinedload(AttendanceRecord.subject),
                                       joinedload(AttendanceRecord.author))
                              .filter(AttendanceRecord.student_id == student_id,
                                      AttendanceRecord.date >= attendance_period.start_date,
                                      AttendanceRecord.date <= attendance_period.end_date)
                              .order_by(AttendanceRecord.date.desc()).all())
    for record in attendance_records:
        attendance_summary['total'] += 1
        attendance_summary['hours'] += record.hours_count or 0
        if record.reason in attendance_summary['reasons']:
            attendance_summary['reasons'][record.reason] += 1

    return render_template('student_detail.html',
                         student=student,
                         grades_by_subject=grades_by_subject,
                         all_subjects=all_subjects,
                         grade_form=grade_form,
                         subject_form=subject_form,
                         attendance_period=attendance_period,
                         attendance_records=attendance_records,
                         attendance_summary=attendance_summary,
                         attendance_reasons=reasons,
                         today=today)

# ===================== ДОБАВЛЕНИЕ И УДАЛЕНИЕ ОЦЕНОК СТУДЕНТА =====================
@main.route('/students/<int:student_id>/grades/add', methods=['POST'])
@staff_required
def add_grade_to_student(student_id):
    """Добавление оценки конкретному студенту со страницы студента"""
    student = Student.query.get_or_404(student_id)
    
    # Получаем данные из формы
    subject_id = request.form.get('subject_id')
    grade_value = request.form.get('grade_value')
    grade_type = request.form.get('grade_type', 'exam')
    comments = request.form.get('comments', '')
    
    if not subject_id or not grade_value:
        flash_msg('error', 'Пожалуйста, заполните все обязательные поля')
        return redirect(url_for('main.view_student', student_id=student_id))
    
    try:
        # Проверяем, существует ли предмет
        subject = Subject.query.get(subject_id)
        if not subject:
            flash_msg('error', 'Выбранный предмет не найден')
            return redirect(url_for('main.view_student', student_id=student_id))
        
        # Создаем новую оценку
        grade = Grade(
            student_id=student_id,
            subject_id=subject_id,
            grade_value=str(grade_value),
            grade_type=grade_type,
            date=date.today(),
            comments=comments
        )
        db.session.add(grade)
        db.session.commit()
        log.info('Оценка добавлена: студент %s, предмет «%s», значение %s (%s) — %s',
                 student.full_name, subject.name, grade_value, grade_type,
                 current_user.username)
        log_audit('grade_create', user=current_user, entity_type='grade',
                  entity_id=grade.id,
                  details=f'студент={student.full_name!r}, предмет={subject.name!r}, '
                          f'оценка={grade_value!r}')
        flash_msg('success', f'Оценка по предмету "{subject.name}" успешно добавлена')
    except Exception as e:
        db.session.rollback()
        log.exception('Ошибка добавления оценки студенту %s: %s', student_id, e)
        flash_msg('error', f'Ошибка добавления оценки: {str(e)}')
    
    return redirect(url_for('main.view_student', student_id=student_id))

@main.route('/students/<int:student_id>/grades/<int:grade_id>/delete', methods=['POST'])
@staff_required
def delete_student_grade(student_id, grade_id):
    """Удаление оценки студента (основной эндпоинт для шаблона)"""
    try:
        grade = Grade.query.get_or_404(grade_id)
        # Проверяем, что оценка принадлежит студенту
        if grade.student_id != student_id:
            flash_msg('error', 'Оценка не принадлежит данному студенту')
            log.warning('Отклонено удаление чужой оценки: оценка %d, студент %d — %s',
                        grade_id, student_id, current_user.username)
            return redirect(url_for('main.view_student', student_id=student_id))
        
        info = 'предмет %s, значение %s' % (grade.subject.name, grade.grade_value)
        db.session.delete(grade)
        db.session.commit()
        log.warning('Оценка удалена: %s — %s', info, current_user.username)
        log_audit('grade_delete', user=current_user, entity_type='grade',
                  entity_id=grade_id,
                  details=f'студент_id={student_id}, {info!r}')
        flash_msg('success', 'Оценка удалена')
    except Exception as e:
        db.session.rollback()
        log.exception('Ошибка удаления оценки %d: %s', grade_id, e)
        flash_msg('error', f'Ошибка удаления оценки: {str(e)}')
    
    return redirect(url_for('main.view_student', student_id=student_id))

# Альтернативное имя для совместимости
@main.route('/students/<int:student_id>/grades/<int:grade_id>/remove', methods=['POST'])
@staff_required
def delete_grade_from_student(student_id, grade_id):
    """Альтернативный эндпоинт для удаления оценки"""
    return delete_student_grade(student_id, grade_id)

# ===================== ГРУППЫ =====================
@main.route('/groups')
@staff_required
def groups():
    groups_list = Group.query.order_by(Group.name).all()
    # Один запрос вместо student_count() на каждую группу в шаблоне (N+1)
    return render_template('groups.html', groups=groups_list,
                           student_counts=group_student_counts(groups_list))

@main.route('/groups/add', methods=['GET', 'POST'])
@admin_required
def add_group():
    form = GroupForm()
    if form.validate_on_submit():
        try:
            group = Group(
                name=form.name.data,
                specialty=form.specialty.data,
                year=form.year.data
            )
            db.session.add(group)
            db.session.commit()
            log.info('Добавлена группа: %s (%s) — %s',
                     group.name, group.specialty, current_user.username)
            log_audit('group_create', user=current_user, entity_type='group',
                      entity_id=group.id,
                      details=f'название={group.name!r}, год={group.year}')
            flash_msg('success', f'Группа {group.name} успешно добавлена')
            return redirect(url_for('main.groups'))
        except IntegrityError:
            db.session.rollback()
            log.error('Дубликат группы: %s', form.name.data)
            flash_msg('error', 'Группа с таким названием уже существует')
        except Exception as e:
            db.session.rollback()
            log.exception('Ошибка добавления группы: %s', e)
            flash_msg('error', f'Ошибка добавления группы: {str(e)}')
    
    return render_template('group_form.html', form=form, title='Добавить группу', group=None)

@main.route('/groups/<int:id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_group(id):
    group = Group.query.get_or_404(id)
    form = GroupForm(obj=group)
    if form.validate_on_submit():
        try:
            form.populate_obj(group)
            db.session.commit()
            log.info('Изменена группа: %s — %s', group.name, current_user.username)
            log_audit('group_edit', user=current_user, entity_type='group',
                      entity_id=group.id,
                      details=f'название={group.name!r}, специальность={group.specialty!r}, '
                              f'год={group.year}')
            flash_msg('success', f'Группа {group.name} обновлена')
            return redirect(url_for('main.groups'))
        except IntegrityError:
            db.session.rollback()
            log.error('Дубликат при изменении группы: %s', form.name.data)
            flash_msg('error', 'Группа с таким названием уже существует')
        except Exception as e:
            db.session.rollback()
            log.exception('Ошибка изменения группы: %s', e)
            flash_msg('error', f'Ошибка обновления группы: {str(e)}')
    
    return render_template('group_form.html', form=form, title='Редактировать группу', group=group)

@main.route('/groups/<int:id>/delete', methods=['POST'])
@admin_required
def delete_group(id):
    group = Group.query.get_or_404(id)
    name = group.name
    try:
        db.session.delete(group)
        db.session.commit()
        log.warning('Удалена группа: %s — %s', name, current_user.username)
        log_audit('group_delete', user=current_user, entity_type='group',
                  entity_id=id, details=f'название={name!r}')
        flash_msg('success', f'Группа {name} удалена')
    except Exception as e:
        db.session.rollback()
        log.exception('Ошибка удаления группы %s: %s', name, e)
        flash_msg('error', f'Ошибка удаления группы: {str(e)}')
    return redirect(url_for('main.groups'))

# ===================== ПРЕДМЕТЫ =====================
@main.route('/subjects')
@staff_required
def subjects():
    """Список предметов. Преподаватель видит только свои."""
    # teacher подгружается вместе со строкой: в таблице он выводится для
    # каждого предмета, и без joinedload это отдельный SELECT на предмет
    subjects_list = (visible_subjects_query()
                     .options(joinedload(Subject.teacher))
                     .order_by(Subject.name).all())
    return render_template('subjects.html', subjects=subjects_list,
                           grade_counts=subject_grade_counts())

@main.route('/subjects/add', methods=['GET', 'POST'])
@admin_required
def add_subject():
    """Добавление нового предмета (ОДНА ФУНКЦИЯ - НЕТ ДУБЛИРОВАНИЯ)"""
    if request.method == 'POST':
        # Обработка быстрого добавления из текстового поля
        subjects_text = request.form.get('subjects_text')
        if subjects_text:
            lines = subjects_text.strip().split('\n')
            added_count = 0
            for line in lines:
                line = line.strip()
                if line:
                    # Парсим строку: "Название Часы" или просто "Название"
                    parts = line.split()
                    if len(parts) >= 2 and parts[-1].isdigit():
                        name = ' '.join(parts[:-1])
                        hours = int(parts[-1])
                    else:
                        name = line
                        hours = 72
                    
                    # Проверяем, существует ли уже предмет
                    existing = Subject.query.filter_by(name=name).first()
                    if not existing:
                        try:
                            subject = Subject(name=name, hours=hours)
                            db.session.add(subject)
                            added_count += 1
                        except Exception as e:
                            log.error('Не удалось добавить предмет «%s»: %s', name, e)
                            continue
            
            if added_count > 0:
                db.session.commit()
                log.info('Массово добавлено предметов: %d — %s',
                         added_count, current_user.username)
                log_audit('subjects_bulk_create', user=current_user,
                          entity_type='subject', details=f'количество={added_count}')
                flash_msg('success', f'Добавлено {added_count} новых предметов')
            else:
                flash_msg('warning', 'Не удалось добавить ни одного предмета (возможно, они уже существуют)')
            
            return redirect(url_for('main.subjects'))
        else:
            # Обработка обычной формы
            name = request.form.get('name')
            hours = request.form.get('hours', 72, type=int)
            
            if not name:
                flash_msg('error', 'Введите название предмета')
                return redirect(url_for('main.subjects'))
            
            try:
                subject = Subject(
                    name=name,
                    hours=hours
                )
                db.session.add(subject)
                db.session.commit()
                log.info('Добавлен предмет: «%s», %d ч. — %s',
                         subject.name, hours, current_user.username)
                log_audit('subject_create', user=current_user,
                          entity_type='subject', entity_id=subject.id,
                          details=f'название={subject.name!r}, часы={hours}')
                flash_msg('success', f'Предмет "{subject.name}" успешно добавлен')
                return redirect(url_for('main.subjects'))
            except IntegrityError:
                db.session.rollback()
                log.error('Дубликат предмета: %s', name)
                flash_msg('error', 'Предмет с таким названием уже существует')
            except Exception as e:
                db.session.rollback()
                log.exception('Ошибка добавления предмета: %s', e)
                flash_msg('error', f'Ошибка добавления предмета: {str(e)}')
    
    # GET запрос - показываем форму
    form = SubjectForm()
    form.teacher_id.choices = teacher_choices()
    return render_template('subject_form.html', form=form, title='Добавить предмет',
                           teachers=teacher_choices())

@main.route('/subjects/<int:id>/edit', methods=['GET', 'POST'])
@staff_required
def edit_subject(id):
    """Редактирование предмета. Преподаватель — только свой."""
    subject, error = teacher_owns_subject(id)
    if error:
        return error

    form = SubjectForm(obj=subject)
    form.teacher_id.choices = teacher_choices()
    if form.validate_on_submit():
        if current_user.is_admin:
            form.populate_obj(subject)
        else:
            # Преподаватель не может передать предмет другому и не должен
            # подменять его — форма уже заполнена текущим предметом
            subject.name = form.name.data
            subject.hours = form.hours.data
        try:
            db.session.commit()
            log.info('Изменён предмет: «%s» — %s', subject.name, current_user.username)
            log_audit('subject_edit', user=current_user, entity_type='subject',
                      entity_id=subject.id,
                      details=f'название={subject.name!r}, часы={subject.hours}')
            flash_msg('success', f'Предмет "{subject.name}" обновлен')
            return redirect(url_for('main.subjects'))
        except IntegrityError:
            db.session.rollback()
            log.error('Дубликат при изменении предмета: %s', form.name.data)
            flash_msg('error', 'Предмет с таким названием уже существует')
        except Exception as e:
            db.session.rollback()
            log.exception('Ошибка изменения предмета: %s', e)
            flash_msg('error', f'Ошибка обновления предмета: {str(e)}')
    
    return render_template('subject_form.html', form=form,
                           title='Редактировать предмет', subject=subject,
                           teachers=teacher_choices())

@main.route('/subjects/<int:id>/delete', methods=['POST'])
@admin_required
def delete_subject(id):
    """Удаление предмета"""
    subject = Subject.query.get_or_404(id)
    try:
        # Проверяем, есть ли оценки по этому предмету
        grade_count = Grade.query.filter_by(subject_id=id).count()
        if grade_count > 0:
            log.warning('Удаление предмета «%s» отклонено: %d оценок',
                        subject.name, grade_count)
            flash_msg('error', f'Нельзя удалить предмет "{subject.name}", так как по нему уже есть {grade_count} оценок')
            return redirect(url_for('main.subjects'))
        name = subject.name
        db.session.delete(subject)
        db.session.commit()
        log.warning('Удалён предмет: «%s» — %s', name, current_user.username)
        log_audit('subject_delete', user=current_user, entity_type='subject',
                  entity_id=id, details=f'название={name!r}')
        flash_msg('success', f'Предмет "{name}" удален')
    except Exception as e:
        db.session.rollback()
        log.exception('Ошибка удаления предмета: %s', e)
        flash_msg('error', f'Ошибка удаления предмета: {str(e)}')
    
    return redirect(url_for('main.subjects'))

# ===================== ОЦЕНКИ =====================
@main.route('/grades')
@staff_required
def grades():
    """Список оценок. Преподаватель видит оценки только по своим предметам."""
    group_id = request.args.get('group', type=int)
    student_id = request.args.get('student', type=int)
    # options подгружают студента, группу и предмет вместе со строкой:
    # без них обращения в шаблоне дают отдельный SELECT на каждую оценку
    query = Grade.query.join(Student).join(Subject).options(
        joinedload(Grade.student).joinedload(Student.group),
        joinedload(Grade.subject)
    )
    if not current_user.is_admin:
        query = query.filter(Subject.teacher_id == current_user.id)
    
    # Применяем фильтры
    if group_id:
        query = query.filter(Student.group_id == group_id)
    if student_id:
        query = query.filter(Grade.student_id == student_id)
    
    # Пагинация
    page_args = get_pagination_args()
    grades_paginated = query.order_by(desc(Grade.date)).paginate(
        page=page_args['page'], per_page=page_args['per_page'], error_out=False)
    
    # Получаем данные для фильтров
    groups = Group.query.all()
    if current_user.is_admin:
        students = Student.query.order_by(Student.last_name).all()
    else:
        # Преподавателю незачем видеть всех студентов колледжа: в фильтре
        # ему нужны только те, у кого уже есть оценки по его предметам
        subject_ids = [s.id for s in
                       Subject.query.filter_by(teacher_id=current_user.id).all()]
        students = (Student.query.join(Grade)
                    .filter(Grade.subject_id.in_(subject_ids))
                    .distinct().order_by(Student.last_name).all())
    
    # Подготавливаем фильтры для отображения
    current_filters = {}
    if group_id:
        current_filters['group'] = group_id
    if student_id:
        current_filters['student'] = student_id
    
    return render_template('grades.html', 
                         grades=grades_paginated,
                         groups=groups,
                         students=students,
                         current_filters=current_filters)

@main.route('/grades/add', methods=['GET', 'POST'])
@staff_required
def add_grade():
    """Добавление новой оценки"""
    form = GradeForm()
    if form.validate_on_submit():
        try:
            # Проверяем, существует ли студент и предмет
            student = Student.query.get(form.student_id.data)
            subject = Subject.query.get(form.subject_id.data)
            
            if not student:
                flash_msg('error', 'Выбранный студент не найден')
                return redirect(url_for('main.add_grade'))
            
            if not subject:
                flash_msg('error', 'Выбранный предмет не найден')
                return redirect(url_for('main.add_grade'))

            if not current_user.teaches(subject):
                log.warning('Преподаватель %s попытался поставить оценку по чужому предмету «%s»',
                            current_user.username, subject.name)
                return render_template('error.html', code=403,
                                       message='Этот предмет ведёт другой преподаватель.'), 403
            
            grade = Grade(
                student_id=form.student_id.data,
                subject_id=form.subject_id.data,
                grade_value=str(form.grade_value.data),
                grade_type=form.grade_type.data,
                date=form.date.data,
                comments=form.comments.data
            )
            db.session.add(grade)
            db.session.commit()
            log.info('Оценка добавлена: %s, предмет «%s», значение %s (%s) от %s — %s',
                     student.full_name, subject.name, form.grade_value.data,
                     form.grade_type.data, form.date.data, current_user.username)
            log_audit('grade_create', user=current_user, entity_type='grade',
                      entity_id=grade.id,
                      details=f'студент={student.full_name!r}, '
                              f'предмет={subject.name!r}, '
                              f'оценка={form.grade_value.data!r}, '
                              f'тип={form.grade_type.data!r}')
            flash_msg('success', f'Оценка по предмету "{subject.name}" для студента {student.full_name} успешно добавлена')
            return redirect(url_for('main.grades'))
        except Exception as e:
            db.session.rollback()
            log.exception('Ошибка добавления оценки: %s', e)
            flash_msg('error', f'Ошибка добавления оценки: {str(e)}')
    
    form.subject_id.choices = [(0, '— выберите предмет —')] + [
        (s.id, s.name) for s in visible_subjects_query().order_by(Subject.name).all()]
    return render_template('grade_form.html', form=form, title='Добавить оценку')

@main.route('/grades/<int:id>/delete', methods=['POST'])
@staff_required
def delete_grade(id):
    """Удаление оценки"""
    grade = Grade.query.get_or_404(id)
    if not current_user.teaches(grade.subject):
        log.warning('Преподаватель %s попытался удалить оценку по чужому предмету «%s»',
                    current_user.username, grade.subject.name)
        return render_template('error.html', code=403,
                               message='Этот предмет ведёт другой преподаватель.'), 403
    info = 'студент %s, предмет «%s», значение %s' % (
        grade.student.full_name, grade.subject.name, grade.grade_value)
    try:
        db.session.delete(grade)
        db.session.commit()
        log.warning('Оценка удалена: %s — %s', info, current_user.username)
        log_audit('grade_delete', user=current_user, entity_type='grade',
                  entity_id=id, details=info)
        flash_msg('success', 'Оценка удалена')
    except Exception as e:
        db.session.rollback()
        log.exception('Ошибка удаления оценки: %s', e)
        flash_msg('error', f'Ошибка удаления оценки: {str(e)}')
    return redirect(url_for('main.grades'))

# ===================== УЧЕБНЫЕ ПЕРИОДЫ =====================
@main.route('/periods')
@staff_required
def periods():
    """Список учебных периодов, сгруппированный по учебным годам."""
    settings = SystemSettings.get_settings()
    current_year = settings.academic_year
    # Сразу разворачиваем строки в строки: по_year собран по str, и шаблон
    # ищет by_year.get(year) — с объектами Row поиск всегда давал бы None
    years = [row[0] for row in
             (db.session.query(AcademicPeriod.academic_year)
              .distinct()
              .order_by(AcademicPeriod.academic_year.desc()).all())]
    if current_year and current_year not in years:
        years = [current_year] + years

    all_periods = (AcademicPeriod.query
                   .order_by(AcademicPeriod.academic_year.desc(),
                             AcademicPeriod.sort_order).all())
    # Счётчики собираем одним запросом по всем периодам сразу: иначе
    # count() на каждую строку таблицы — это N+1
    grade_counts = dict(
        db.session.query(Grade.period_id, func.count(Grade.id))
        .filter(Grade.period_id.isnot(None))
        .group_by(Grade.period_id).all())
    by_year = {}
    for period in all_periods:
        by_year.setdefault(period.academic_year, []).append(period)

    # Отдельная форма-«пустышка» ради токена: кнопки удаления в таблице
    # не являются FlaskForm, а CSRF-токен должен прийти из формы
    delete_form = DeleteTokenForm()
    return render_template('periods.html', years=years, by_year=by_year,
                           current_year=current_year,
                           grade_counts=grade_counts,
                           delete_token=delete_form.csrf_token,
                           period_kind=settings.period_kind,
                           period_kind_labels=SystemSettings.PERIOD_KIND_LABELS)


@main.route('/periods/add', methods=['GET', 'POST'])
@admin_required
def add_period():
    form = PeriodForm()
    if form.validate_on_submit():
        clash = AcademicPeriod.query.filter_by(
            academic_year=form.academic_year.data,
            sort_order=form.sort_order.data).first()
        if clash:
            flash_msg('error', f'Период с номером {form.sort_order.data} '
                               f'в {form.academic_year.data} уже есть: {clash.name}')
            return render_template('period_form.html', form=form,
                                   title='Добавить период', period=None)

        period = AcademicPeriod(
            name=form.name.data,
            academic_year=form.academic_year.data,
            start_date=form.start_date.data,
            end_date=form.end_date.data,
            sort_order=form.sort_order.data,
            kind=form.kind.data,
            is_annual=form.is_annual.data)
        db.session.add(period)
        try:
            db.session.commit()
            log.info('Добавлен учебный период: %s %s — %s',
                     period.name, period.academic_year, current_user.username)
            log_audit('period_create', user=current_user, entity_type='period',
                      entity_id=period.id,
                      details=f'название={period.name!r}, '
                              f'год={period.academic_year!r}, '
                              f'номер={period.sort_order}')
            flash_msg('success', f'Период «{period.name}» добавлен')
            return redirect(url_for('main.periods'))
        except IntegrityError:
            db.session.rollback()
            flash_msg('error', 'Такой период уже есть')
    return render_template('period_form.html', form=form,
                           title='Добавить период', period=None)


@main.route('/periods/<int:id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_period(id):
    period = AcademicPeriod.query.get_or_404(id)
    form = PeriodForm(obj=period)
    if form.validate_on_submit():
        clash = AcademicPeriod.query.filter(
            AcademicPeriod.academic_year == form.academic_year.data,
            AcademicPeriod.sort_order == form.sort_order.data,
            AcademicPeriod.id != period.id).first()
        if clash:
            flash_msg('error', f'Период с номером {form.sort_order.data} '
                               f'в {form.academic_year.data} уже есть: {clash.name}')
            return render_template('period_form.html', form=form,
                                   title='Редактировать период', period=period)

        form.populate_obj(period)
        try:
            db.session.commit()
            log.info('Изменён учебный период: %s — %s',
                     period.name, current_user.username)
            log_audit('period_edit', user=current_user, entity_type='period',
                      entity_id=period.id,
                      details=f'название={period.name!r}, '
                              f'год={period.academic_year!r}, '
                              f'номер={period.sort_order}')
            flash_msg('success', f'Период «{period.name}» обновлён')
            return redirect(url_for('main.periods'))
        except IntegrityError:
            db.session.rollback()
            flash_msg('error', 'Такой период уже есть')
    return render_template('period_form.html', form=form,
                           title='Редактировать период', period=period)


@main.route('/periods/<int:id>/delete', methods=['POST'])
@admin_required
def delete_period(id):
    period = AcademicPeriod.query.get_or_404(id)
    grades_count = Grade.query.filter_by(period_id=period.id).count()
    if grades_count:
        # Период с оценками удалять нельзя: оценки остались бы висеть
        # на несуществующем периоде, и фильтр журнала потерял бы их
        log.warning('Попытка удалить период %s с оценками: %s — %s',
                    period.name, grades_count, current_user.username)
        flash_msg('error', f'Нельзя удалить период «{period.name}»: '
                           f'в нём {grades_count} оценок')
        return redirect(url_for('main.periods'))

    name, year = period.name, period.academic_year
    db.session.delete(period)
    db.session.commit()
    log.warning('Удалён учебный период: %s %s — %s', name, year, current_user.username)
    log_audit('period_delete', user=current_user, entity_type='period',
              entity_id=id, details=f'название={name!r}, год={year!r}')
    flash_msg('success', f'Период «{name}» удалён')
    return redirect(url_for('main.periods'))


@main.route('/periods/generate', methods=['GET', 'POST'])
@admin_required
def generate_periods():
    """Автосоздание периодов учебного года по шаблону."""
    settings = SystemSettings.get_settings()
    form = PeriodGenerateForm(academic_year=settings.academic_year,
                              kind=settings.period_kind)
    if form.validate_on_submit():
        try:
            template = build_periods(form.academic_year.data, form.kind.data)
        except ValueError as e:
            flash_msg('error', str(e))
            return redirect(url_for('main.generate_periods'))

        # Уже созданные номера пропускаем, а не падаем: повторное нажатие
        # кнопки не должно ломать базу
        existing = {row[0] for row in db.session.query(AcademicPeriod.sort_order)
                    .filter_by(academic_year=form.academic_year.data).all()}
        created, skipped = 0, 0
        for order, (name, start, end) in enumerate(template, start=1):
            if order in existing:
                skipped += 1
                continue
            db.session.add(AcademicPeriod(
                name=name, academic_year=form.academic_year.data,
                start_date=start, end_date=end,
                sort_order=order, kind=form.kind.data, is_annual=False))
            created += 1
        db.session.commit()
        log.info('Созданы учебные периоды: %d за %s (%s) — %s',
                 created, form.academic_year.data, form.kind.data,
                 current_user.username)
        log_audit('periods_generate', user=current_user, entity_type='period',
                  details=f'год={form.academic_year.data!r}, '
                          f'вид={form.kind.data!r}, создано={created}, '
                          f'пропущено={skipped}')
        message = f'Создано периодов: {created}'
        if skipped:
            message += f' (пропущено уже существовавших: {skipped})'
        flash_msg('success', message)
        return redirect(url_for('main.periods'))

    return render_template('periods_generate.html', form=form,
                           settings=settings,
                           period_kind_labels=SystemSettings.PERIOD_KIND_LABELS)


# ===================== РАСПИСАНИЕ =====================
@main.route('/schedule')
@staff_required
def schedule():
    """Сетка расписания: строки — дни недели, столбцы — номера пар."""
    groups_list = Group.query.order_by(Group.name).all()
    if not groups_list:
        flash_msg('warning', 'Сначала создайте хотя бы одну группу')
        return redirect(url_for('main.groups'))

    group_id = request.args.get('group_id', type=int)
    if not group_id or group_id not in [g.id for g in groups_list]:
        group_id = groups_list[0].id
    # Берём группу из уже загруженного списка: отдельный get() — лишний SELECT
    group = next(g for g in groups_list if g.id == group_id)

    lesson_date = request.args.get('date', type=str)
    try:
        selected_date = datetime.strptime(lesson_date, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        selected_date = date.today()

    items = (ScheduleItem.query
             .filter_by(group_id=group.id)
             .options(joinedload(ScheduleItem.subject),
                      joinedload(ScheduleItem.teacher))
             .all())
    grid = {(item.day_of_week, item.lesson_number): item for item in items}
    max_lesson = max([item.lesson_number for item in items] or [1])

    # Отметки «занятие состоялось» — одним запросом на выбранную дату
    marked = {}
    if selected_date:
        rows = (LessonDate.query
                .join(LessonDate.schedule_item)
                .filter(LessonDate.date == selected_date,
                        ScheduleItem.group_id == group.id).all())
        marked = {row.schedule_item_id for row in rows}

    day_form = ScheduleDayForm(group_id=group.id,
                               day_of_week=selected_date.weekday() + 1)
    lesson_form = LessonDateForm(group_id=group.id, date=selected_date)
    lesson_form.item_ids.choices = [
        (item.id, f'Пара {item.lesson_number} — {item.subject.name}')
        for item in sorted(items, key=lambda i: i.lesson_number)]
    for choice_id in marked:
        lesson_form.item_ids.data = list(lesson_form.item_ids.data or []) + [choice_id]

    # Конфликты преподавателей по всей базе: их видно сразу, а не по одному.
    # joinedload обязателен — в шаблоне у конфликтной пары читаются
    # subject/teacher/group, и без прогревки это N+1 по числу конфликтов.
    slots = {}
    for item in (ScheduleItem.query
                 .options(joinedload(ScheduleItem.subject),
                          joinedload(ScheduleItem.teacher),
                          joinedload(ScheduleItem.group))):
        slots.setdefault((item.teacher_id, item.day_of_week, item.lesson_number),
                         []).append(item)

    return render_template(
        'schedule.html', groups=groups_list, group=group, grid=grid,
        max_lesson=max_lesson, selected_date=selected_date, marked=marked,
        day_form=day_form, lesson_form=lesson_form,
        delete_token=DeleteTokenForm().csrf_token,
        lessons_per_day=8, slots=slots,
        subjects=Subject.query.order_by(Subject.name).all(),
        teachers=teacher_choices(),
        week_days=ScheduleItem.DAY_LABELS,
        is_admin=current_user.is_admin)


@main.route('/schedule/add', methods=['POST'])
@admin_required
def add_schedule_item():
    form = ScheduleItemForm()
    if not form.validate_on_submit():
        flash_msg('error', 'Проверьте заполнение полей')
        return redirect(url_for('main.schedule',
                                group_id=request.form.get('group_id')))

    subject = db.session.get(Subject, form.subject_id.data)
    teacher_id = form.teacher_id.data or (subject.teacher_id if subject else None)
    conflict = schedule_conflict(form.group_id.data, form.day_of_week.data,
                                 form.lesson_number.data, teacher_id)
    if conflict:
        flash_msg('error', conflict)
        return redirect(url_for('main.schedule', group_id=form.group_id.data))

    item = ScheduleItem(
        group_id=form.group_id.data, subject_id=form.subject_id.data,
        day_of_week=form.day_of_week.data,
        lesson_number=form.lesson_number.data,
        teacher_id=teacher_id, room=form.room.data or None)
    db.session.add(item)
    try:
        db.session.commit()
        log.info('Добавлено занятие: %s, %s, пара %d — %s',
                 db.session.get(Group, item.group_id).name,
                 ScheduleItem.DAY_LABELS.get(item.day_of_week), item.lesson_number,
                 current_user.username)
        log_audit('schedule_item_create', user=current_user,
                  entity_type='schedule_item', entity_id=item.id,
                  details=f'группа={item.group_id}, '
                          f'день={ScheduleItem.DAY_LABELS.get(item.day_of_week)!r}, '
                          f'пара={item.lesson_number}, '
                          f'предмет_id={item.subject_id}')
        flash_msg('success', 'Занятие добавлено в расписание')
    except IntegrityError:
        db.session.rollback()
        flash_msg('error', 'В этой ячейке расписания уже есть занятие')
    return redirect(url_for('main.schedule', group_id=form.group_id.data))


@main.route('/schedule/day', methods=['POST'])
@admin_required
def fill_schedule_day():
    """Массовое заполнение дня: один предмет на диапазон пар."""
    form = ScheduleDayForm()
    if not form.validate_on_submit():
        flash_msg('error', 'Проверьте заполнение полей')
        return redirect(url_for('main.schedule',
                                group_id=request.form.get('group_id')))

    subject = db.session.get(Subject, form.subject_id.data)
    teacher_id = form.teacher_id.data or (subject.teacher_id if subject else None)
    group = db.session.get(Group, form.group_id.data)

    created, conflicts = 0, []
    for lesson in range(form.first_lesson.data, form.last_lesson.data + 1):
        conflict = schedule_conflict(group.id, form.day_of_week.data,
                                     lesson, teacher_id)
        if conflict:
            conflicts.append(f'пара {lesson} — {conflict}')
            continue
        db.session.add(ScheduleItem(
            group_id=group.id, subject_id=subject.id,
            day_of_week=form.day_of_week.data, lesson_number=lesson,
            teacher_id=teacher_id, room=form.room.data or None))
        created += 1
    db.session.commit()

    log.info('Массовое заполнение расписания: %s, %s, пар %d-%d, создано %d, '
             'конфликтов %d — %s', group.name,
             ScheduleItem.DAY_LABELS.get(form.day_of_week.data),
             form.first_lesson.data, form.last_lesson.data, created,
             len(conflicts), current_user.username)
    log_audit('schedule_day_fill', user=current_user, entity_type='schedule_item',
              details=f'группа={group.id}, '
                      f'день={ScheduleItem.DAY_LABELS.get(form.day_of_week.data)!r}, '
                      f'пары={form.first_lesson.data}-{form.last_lesson.data}, '
                      f'создано={created}, конфликтов={len(conflicts)}')
    if created and not conflicts:
        flash_msg('success', f'Добавлено занятий: {created}')
    elif created and conflicts:
        flash_msg('warning', f'Добавлено занятий: {created}. '
                             f'Пропущено из-за конфликтов: {len(conflicts)} — '
                             + '; '.join(conflicts))
    else:
        flash_msg('error', 'Ничего не добавлено: ' + '; '.join(conflicts))

    return redirect(url_for('main.schedule', group_id=group.id,
                            date=selected_date_from(request)))


@main.route('/schedule/<int:id>/delete', methods=['POST'])
@admin_required
def delete_schedule_item(id):
    item = ScheduleItem.query.get_or_404(id)
    group_id = item.group_id
    # Отметки о состоявшихся занятиях — вместе со слотом
    LessonDate.query.filter_by(schedule_item_id=item.id).delete(
        synchronize_session=False)
    db.session.delete(item)
    db.session.commit()
    log.warning('Удалено занятие из расписания: id %d — %s', id, current_user.username)
    log_audit('schedule_item_delete', user=current_user,
              entity_type='schedule_item', entity_id=id,
              details=f'группа={group_id}')
    flash_msg('success', 'Занятие удалено из расписания')
    return redirect(url_for('main.schedule', group_id=group_id,
                            date=selected_date_from(request)))


@main.route('/schedule/lessons', methods=['POST'])
@admin_required
def mark_lessons():
    """Отметка «занятие состоялось» за конкретную дату."""
    form = LessonDateForm()
    if not form.validate_on_submit():
        flash_msg('error', 'Проверьте дату и группу')
        return redirect(url_for('main.schedule', group_id=request.form.get('group_id')))

    group = db.session.get(Group, form.group_id.data)
    day = form.date.data.weekday() + 1
    slots = (ScheduleItem.query
             .filter_by(group_id=group.id, day_of_week=day).all())

    wanted = set()
    if form.mark_all.data:
        wanted = {item.id for item in slots}
    else:
        # Только слоты этой группы: иначе можно было бы отметить чужое занятие
        picked = set(form.item_ids.data or [])
        wanted = {item.id for item in slots if item.id in picked}

    existing = {row.schedule_item_id for row in
                LessonDate.query.filter(LessonDate.date == form.date.data).all()}
    created = [item for item in wanted if item not in existing]
    for item_id in created:
        db.session.add(LessonDate(schedule_item_id=item_id,
                                  date=form.date.data,
                                  created_by=current_user.id))
    removed = [item for item in existing
               if item in {s.id for s in slots} and item not in wanted]
    if removed:
        LessonDate.query.filter(
            LessonDate.date == form.date.data,
            LessonDate.schedule_item_id.in_(removed)).delete(
                synchronize_session=False)

    db.session.commit()
    log.info('Отметки о занятиях на %s, группа %s: добавлено %d, снято %d — %s',
             form.date.data, group.name, len(created), len(removed),
             current_user.username)
    log_audit('lessons_mark', user=current_user, entity_type='lesson_date',
              details=f'группа={group.id}, '
                      f'дата={form.date.data.strftime("%d.%m.%Y")!r}, '
                      f'отмечено={len(created)}, снято={len(removed)}')
    message = f'Отмечено занятий: {len(created)}'
    if removed:
        message += f', снято отметок: {len(removed)}'
    flash_msg('success', message)

    return redirect(url_for('main.schedule', group_id=group.id,
                            date=form.date.data.strftime('%Y-%m-%d')))


# ===================== ЖУРНАЛ =====================
def _by_student(cells):
    """{student_id: [Grade, ...]} из cells вида {(student_id, column): [...]}."""
    result = {}
    for (student_id, _column), grades in (cells or {}).items():
        result.setdefault(student_id, []).extend(grades)
    return result


def _absent_dates(attendance, columns):
    """Даты, в которые кто-то из группы отсутствовал: {дата: True}.

    Нужна шапке сетки, чтобы пропущенные занятия не выглядели как
    «преподаватель ничего не выставил».
    """
    column_set = set(columns or ())
    return {date for (_student_id, date), records
            in (attendance or {}).items()
            if date in column_set and records}


def current_period_or_default(period_id=None):
    """Период из запроса, иначе тот, в который попадает сегодняшняя дата.

    Возвращает (period, error_response): error_response не None, если
    период не найден или в базе их нет вовсе.
    """
    if period_id:
        period = db.session.get(AcademicPeriod, period_id)
        if period is None:
            return None, (render_template(
                'error.html', code=404,
                message='Учебный период не найден.'), 404)
        return period, None

    today = date.today()
    period = (AcademicPeriod.query
              .filter(AcademicPeriod.start_date <= today,
                      AcademicPeriod.end_date >= today)
              .order_by(AcademicPeriod.sort_order.desc()).first())
    if period:
        return period, None

    # Дата вне учебного года (лето, каникулы) — показываем последний период
    period = (AcademicPeriod.query
              .order_by(AcademicPeriod.academic_year.desc(),
                        AcademicPeriod.sort_order.desc()).first())
    if period:
        return period, None

    return None, (render_template(
        'error.html', code=404,
        message='Сначала создайте хотя бы один учебный период.'), 404)


def journal_context(group_id, subject_id, period_id, mode):
    """Общие выборки для обоих режимов журнала.

    Возвращает (ctx, error_response). Все данные сетки берутся четырьмя
    запросами независимо от числа студентов и ячеек: иначе на группе из 25
    человек сетка даёт сотни SELECT.
    """
    groups_list = Group.query.order_by(Group.name).all()
    if not groups_list:
        return None, (render_template(
            'error.html', code=404,
            message='Сначала создайте хотя бы одну группу.'), 404)
    if group_id not in [g.id for g in groups_list]:
        group_id = groups_list[0].id
    group = next(g for g in groups_list if g.id == group_id)

    period, failure = current_period_or_default(period_id)
    if failure:
        return None, failure

    # Преподаватель видит только свои предметы: чужой журнал ему не нужен
    subjects_list = (visible_subjects_query()
                     .order_by(Subject.name).all())
    if not subjects_list:
        return None, (render_template(
            'error.html', code=403,
            message='У вас нет предметов для журнала.'), 403)
    if subject_id:
        # Явно запрошенный чужой предмет — это 403, а не тихая подмена на
        # первый доступный: иначе преподаватель с исправленной ссылкой
        # смотрел бы чужую сетку, не понимая why.
        if subject_id not in [s.id for s in subjects_list]:
            return None, (render_template(
                'error.html', code=403,
                message='Этот предмет ведёт другой преподаватель.'), 403)
        subject = next(s for s in subjects_list if s.id == subject_id)
    else:
        subject = subjects_list[0]

    periods_list = (AcademicPeriod.query
                    .filter_by(academic_year=period.academic_year)
                    .order_by(AcademicPeriod.sort_order).all())

    students = (Student.query
                .filter_by(group_id=group.id, status='active')
                .order_by(Student.last_name, Student.first_name).all())
    student_ids = [s.id for s in students]

    ctx = {
        'groups': groups_list,
        'group': group,
        'subjects': subjects_list,
        'subject': subject,
        'period': period,
        'periods': periods_list,
        'students': students,
        'mode': mode,
        'can_edit': current_user.is_admin or current_user.teaches(subject),
    }

    if not student_ids:
        ctx.update(columns=[], cells={}, attendance={}, column_titles=[],
                   summary={}, by_subject=False)
        return ctx, None

    # --- Режим 1: столбцы — даты занятий по этому предмету -----------------
    date_rows = (db.session.query(LessonDate.date)
                 .join(LessonDate.schedule_item)
                 .filter(ScheduleItem.group_id == group.id,
                         ScheduleItem.subject_id == subject.id,
                         LessonDate.date >= period.start_date,
                         LessonDate.date <= period.end_date)
                 .distinct()
                 .order_by(LessonDate.date).all())
    columns = [row[0] for row in date_rows]

    grade_rows = (Grade.query
                  .filter(Grade.student_id.in_(student_ids),
                          Grade.subject_id == subject.id,
                          Grade.date >= period.start_date,
                          Grade.date <= period.end_date)
                  .order_by(Grade.date, Grade.id).all())
    cells = {}
    for grade in grade_rows:
        cells.setdefault((grade.student_id, grade.date), []).append(grade)

    attendance_rows = (AttendanceRecord.query
                      .filter(AttendanceRecord.student_id.in_(student_ids),
                              AttendanceRecord.date >= period.start_date,
                              AttendanceRecord.date <= period.end_date)
                      .order_by(AttendanceRecord.date).all())
    attendance = {}
    for record in attendance_rows:
        attendance.setdefault((record.student_id, record.date), []).append(record)

    ctx.update(mode='dates', columns=columns, cells=cells,
               attendance=attendance, by_subject=False,
               column_titles=[d.strftime('%d.%m') for d in columns],
               summary={},
               totals=journal_totals(_by_student(cells)),
               column_totals={},
               absent_dates=_absent_dates(attendance, columns))
    return ctx, None


def journal_subject_mode(group_id, subject_id, period_id, mode):
    """Режим 2: столбцы — предметы, в ячейке все оценки за период."""
    ctx, failure = journal_context(group_id, subject_id, period_id,
                                   'subjects')
    if failure:
        return None, failure

    group = ctx['group']
    period = ctx['period']
    students = ctx['students']

    # Столбцы — предметы, которые реально стоят в расписании группы.
    # Если расписания нет (сетку смотрят до его заполнения), показываем все
    # доступные предметы, иначе журнал был бы пустым и непонятным.
    subject_ids = [row[0] for row in
                   (db.session.query(ScheduleItem.subject_id)
                    .filter_by(group_id=group.id).distinct().all())]
    available = {s.id: s for s in ctx['subjects']}
    column_ids = [sid for sid in subject_ids if sid in available]
    if not column_ids:
        column_ids = [s.id for s in ctx['subjects']]
    column_subjects = [available[sid] for sid in column_ids]

    rows = (Grade.query
            .filter(Grade.student_id.in_([s.id for s in students]),
                    Grade.subject_id.in_(column_ids),
                    Grade.period_id == period.id)
            .order_by(Grade.subject_id, Grade.date, Grade.id).all()) \
        if students else []
    cells = {}
    for grade in rows:
        cells.setdefault((grade.student_id, grade.subject_id),
                         []).append(grade)

    summary = {}
    column_totals = {}
    for sid in column_ids:
        column_grades = [g for (student_id, subject_id), group_ in cells.items()
                         if subject_id == sid for g in group_]
        summary[sid] = average_grade(column_grades)
        column_totals[sid] = grade_distribution(column_grades)

    ctx.update(mode='subjects', columns=column_ids, cells=cells,
               attendance={}, by_subject=True,
               column_titles=[s.name for s in column_subjects],
               summary=summary,
               totals=journal_totals(_by_student(cells)),
               column_totals=column_totals,
               absent_dates={})
    return ctx, None


def journal_export_frames(ctx):
    """Таблицы журнала в виде обычных списков словарей (10.4, 10.5).

    Собираем ровно из того же ctx, который уже отрисовал экран, иначе файл
    и таблица рано или поздно разойдутся: правило округления, состав
    колонок или фильтр по датам придётся менять в двух местах.

    Первая строка каждой таблицы — заголовок, дальше данные. Значение ячейки
    склеивается в строку, потому что в ячейке может быть несколько оценок и
    пропусков, а в Excel это должна быть одна ячейка.
    """
    students = ctx['students']
    columns = ctx['columns']
    mode = ctx['mode']

    def cell_text(student_id, column):
        """Содержимое ячейки так, как его видит преподаватель: оценки и пропуски."""
        parts = [str(grade.grade_value)
                 for grade in ctx['cells'].get((student_id, column), [])]
        parts += ['Н' for _ in ctx['attendance'].get((student_id, column), [])]
        return ' '.join(parts)

    grid = []
    header = ['Студент']
    for index, column in enumerate(columns):
        if mode == 'subjects':
            title = ctx['column_titles'][index]
            summary = ctx['summary'].get(column)
            header.append(f'{title} (ср. {summary})' if summary else title)
        else:
            header.append(column.strftime('%d.%m.%Y'))
    header.append('Средний')
    grid.append(header)

    for student in students:
        row = [f'{student.last_name} {student.first_name}']
        row += [cell_text(student.id, column) for column in columns]
        total = ctx['totals'].get(student.id)
        row.append(total['average'] if total and total['count'] else '')
        grid.append(row)

    # Сводная таблица: те же студенты, но предметной раскладкой.
    # В режиме «по датам» колонок-предметов в ctx нет, поэтому берём их из
    # распределения оценок по предметам, которое уже посчитано для сводки.
    summary_rows = [['Студент', 'Предмет', 'Оценок', 'Средний',
                     'Распределение']]
    if mode == 'subjects':
        pairs = [((student.id, column), student)
                 for student in students for column in columns]
    else:
        subject_id = ctx['subject'].id
        pairs = [((student.id, subject_id), student) for student in students]
    for (student_id, _column), student in pairs:
        grades = ctx['cells'].get((student_id, _column), [])
        if not grades and mode == 'subjects':
            continue
        distribution = grade_distribution(grades)
        summary_rows.append([
            f'{student.last_name} {student.first_name}',
            ctx['subject'].name if mode != 'subjects'
            else ctx['column_titles'][columns.index(_column)],
            len(grades),
            average_grade(grades) if grades else '',
            ', '.join(f'{key} — {count}'
                      for key, count in sorted(distribution.items())),
        ])

    # Пропуски отдельным листом: в сетке они сливаются с оценками, и
    # посчитать их по файлу нельзя.
    absence_rows = [['Студент', 'Дата', 'Предмет', 'Причина', 'Примечание']]
    for student in students:
        for column in columns:
            for record in ctx['attendance'].get((student.id, column), []):
                if mode == 'subjects':
                    name = ctx['column_titles'][columns.index(column)]
                else:
                    name = ctx['subject'].name
                absence_rows.append([
                    f'{student.last_name} {student.first_name}',
                    record.date.strftime('%d.%m.%Y'),
                    name,
                    record.reason_label,
                    record.note or '',
                ])

    return grid, summary_rows, absence_rows


def journal_export_csv_rows(ctx):
    """Плоская таблица для 1С/ФМС (10.5).

    Формат «одна строка — одна оценка» с заголовком из кодов предметов и
    групп: так выгрузку можно грузить без ручной переработки. Пропуски идут
    значением «Н», как в бумажном журнале, отдельной колонкой не нужны —
    они привязаны к той же дате.
    """
    header = ['Студент', 'Группа', 'Предмет', 'Дата', 'Оценка', 'Пропуск']
    rows = []
    subject_name = ctx['subject'].name
    for student in ctx['students']:
        for column in ctx['columns']:
            date_text = (column.strftime('%Y-%m-%d')
                         if hasattr(column, 'strftime') else '')
            grades = ctx['cells'].get((student.id, column), [])
            absences = ctx['attendance'].get((student.id, column), [])
            count = max(len(grades), len(absences), 1)
            for index in range(count):
                grade = grades[index] if index < len(grades) else None
                record = absences[index] if index < len(absences) else None
                if grade is None and record is None:
                    continue
                rows.append([
                    student.student_id,
                    ctx['group'].name,
                    subject_name,
                    date_text,
                    grade.grade_value if grade else '',
                    'Н' if record else '',
                ])
    return header, rows


def journal_export_filename(ctx, extension):
    """Имя файла из названия колонки, группы и периода — без слешей.

    Название предмета может содержать «/» или «:», а такие имена не
    переживают сохранение в Windows, поэтому приводим к безопасному виду.
    """
    subject = ctx['subject'].name
    parts = ['журнал', ctx['group'].name, subject, ctx['period'].name]
    safe = '-'.join(sanitize_filename(part) for part in parts)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return f'{safe}_{stamp}.{extension}'


def save_export(filepath, sheets):
    """Записать таблицы в xlsx или csv.

    xlsx умеет несколько листов, csv — только один, поэтому для csv берём
    первую таблицу: плоский формат для 1С всё равно не вместит три разных
    среза данных в один файл без потери структуры.
    """
    os.makedirs(EXPORT_DIR, exist_ok=True)
    if filepath.endswith('.csv'):
        # sheets[0] — это кортеж (имя листа, строки), а в csv попадают
        # только сами строки
        rows = sheets[0][1]
        pd.DataFrame(rows[1:], columns=rows[0]).to_csv(
            filepath, index=False, encoding='utf-8-sig')
        return filepath
    with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
        for name, rows in sheets:
            pd.DataFrame(rows[1:], columns=rows[0]).to_excel(
                writer, sheet_name=name[:31], index=False)
    return filepath


@main.route('/journal/export')
@staff_required
def journal_export():
    """Выгрузка журнала: Excel с тремя листами или плоский CSV (10.4, 10.5).

    Права те же, что у самого журнала: чужой предмет — 403, иначе файл
    оказался бы обходным путём мимо ограничения.
    """
    group_id = request.args.get('group_id', type=int)
    subject_id = request.args.get('subject_id', type=int)
    period_id = request.args.get('period_id', type=int)

    mode = request.args.get('mode', 'dates')
    if mode not in ('dates', 'subjects'):
        mode = 'dates'

    fmt = (request.args.get('fmt') or '').strip().lower()
    if fmt not in ('xlsx', 'csv'):
        flash_msg('error', 'Неизвестный формат выгрузки')
        return redirect(url_for('main.journal', group_id=group_id,
                                subject_id=subject_id, period_id=period_id,
                                mode=mode))

    # Плоская выгрузка для 1С всегда по датам, даже если на экране открыт
    # сводный режим: там колонка — предмет, и даты у ячейки просто нет.
    # Молча отдавать файл без дат хуже, чем проигнорировать переключатель.
    if fmt == 'csv':
        mode = 'dates'

    builder = (journal_subject_mode if mode == 'subjects' else journal_context)
    ctx, failure = builder(group_id, subject_id, period_id, mode)
    if failure:
        return failure

    if not ctx['students']:
        flash_msg('error', 'Нечего выгружать: в группе нет активных студентов')
        return redirect(url_for('main.journal', group_id=ctx['group'].id,
                                subject_id=ctx['subject'].id,
                                period_id=ctx['period'].id, mode=mode))

    if not ctx['columns']:
        flash_msg('error',
                  'Нечего выгружать: за период нет ни одного занятия')
        return redirect(url_for('main.journal', group_id=ctx['group'].id,
                                subject_id=ctx['subject'].id,
                                period_id=ctx['period'].id, mode=mode))

    if fmt == 'csv':
        header, rows = journal_export_csv_rows(ctx)
        sheets = [(ctx['subject'].name, [header] + rows)]
    else:
        grid, summary_rows, absence_rows = journal_export_frames(ctx)
        sheets = [('Журнал', grid), ('Сводная', summary_rows),
                  ('Пропуски', absence_rows)]

    filepath = journal_export_filename(ctx, fmt)
    save_export(os.path.join(EXPORT_DIR, filepath), sheets)
    log.info('Экспорт журнала %s, %s, %s (%s) — %s', ctx['group'].name,
             ctx['subject'].name, ctx['period'].name, fmt,
             current_user.username)
    return send_file(os.path.join(EXPORT_DIR, filepath), as_attachment=True)


@main.route('/journal')
@staff_required
def journal():
    """Сетка журнала: два режима — по датам занятий и сводная по предметам."""
    groups_list = Group.query.order_by(Group.name).all()
    if not groups_list:
        return render_template(
            'error.html', code=404,
            message='Сначала создайте хотя бы одну группу.'), 404

    mode = request.args.get('mode', 'dates')
    if mode not in ('dates', 'subjects'):
        mode = 'dates'
    group_id = request.args.get('group_id', type=int)
    subject_id = request.args.get('subject_id', type=int)
    period_id = request.args.get('period_id', type=int)

    builder = (journal_subject_mode if mode == 'subjects' else journal_context)
    ctx, failure = builder(group_id, subject_id, period_id, mode)
    if failure:
        return failure

    grade_form = JournalGradeForm()
    # Список занятий для выпадающего списка в сводном режиме. Заполняем
    # только для выбранного предмета: полный список по всем предметам
    # заставил бы преподавателя искать дату глазами среди сотни строк.
    grade_form.subject_id.choices = [(s.id, s.name) for s in ctx['subjects']]
    grade_form.lesson_date.choices = [
        (row.id, row.date.strftime('%d.%m.%Y (%a)'))
        for row in (LessonDate.query
                    .join(LessonDate.schedule_item)
                    .filter(ScheduleItem.group_id == ctx['group'].id,
                            ScheduleItem.subject_id == ctx['subject'].id,
                            LessonDate.date >= ctx['period'].start_date,
                            LessonDate.date <= ctx['period'].end_date,
                            LessonDate.date <= date.today())
                    .order_by(LessonDate.date).all())]

    absence_form = AttendanceMarkForm()
    absence_form.subject_id.choices = [(ctx['subject'].id, ctx['subject'].name)]
    absence_form.reason.choices = attendance_reason_choices()
    absence_edit_form = AttendanceEditForm()
    absence_edit_form.reason.choices = attendance_reason_choices()

    return render_template('journal.html', grade_form=grade_form,
                           edit_form=JournalEditForm(),
                           absence_form=absence_form,
                           absence_edit_form=absence_edit_form, **ctx)


@main.route('/journal/grade/add', methods=['POST'])
@staff_required
def journal_add_grade():
    """Выставление одной или нескольких оценок в ячейке журнала."""
    form = JournalGradeForm()
    # Список предметов заполняем до валидации: SelectField без choices падает
    # с TypeError, а попутно список не должен показывать преподавателю чужие
    # предметы.
    form.subject_id.choices = [(s.id, s.name)
                               for s in visible_subjects_query().all()]
    if not form.validate_on_submit():
        flash_form_errors(form)
        return _journal_back(request.form)

    subject, failure = teacher_owns_subject(form.subject_id.data)
    if failure:
        return failure

    student = db.session.get(Student, form.student_id.data)
    group_id = request.form.get('group_id', type=int)
    if student is None:
        flash_msg('error', 'Студент не найден')
        return _journal_back(request.form)
    if group_id and student.group_id != group_id:
        flash_msg('error', f'{student.full_name} не учится в выбранной группе')
        log.warning('Отклонена оценка: %s не в группе %s — %s',
                    student.full_name, group_id, current_user.username)
        return _journal_back(request.form)

    if form.period_id.data:
        period = db.session.get(AcademicPeriod, form.period_id.data)
        if period is None:
            flash_msg('error', 'Учебный период не найден')
            return _journal_back(request.form)
    else:
        period, failure = current_period_or_default()
        if failure:
            return failure

    group = db.session.get(Group, student.group_id)
    lesson_date, failure = _resolve_lesson_date(form, group, subject, period)
    if failure:
        return failure

    values = form.grades()
    for value in values:
        grade = Grade(student_id=student.id, subject_id=subject.id,
                      grade_value=value, grade_type=form.grade_type.data,
                      date=lesson_date, comments=form.comments.data or None,
                      period_id=period.id if period else None)
        db.session.add(grade)
        db.session.flush()
        db.session.add(GradeHistory(
            grade_id=grade.id, old_value=None, new_value=value,
            action=GradeHistory.ACTION_CREATED, changed_by=current_user.id,
            comment=form.comments.data or None))
    db.session.commit()

    log.info('Оценки выставлены: %s, предмет «%s», %s за %s — %s',
             student.full_name, subject.name, ', '.join(values),
             lesson_date.strftime('%d.%m.%Y'), current_user.username)
    log_audit('journal_grade_create', user=current_user, entity_type='grade',
              details=f'студент={student.full_name!r}, предмет={subject.name!r}, '
                      f'оценки={values!r}, '
                      f'дата={lesson_date.strftime("%d.%m.%Y")!r}')
    word = 'оценка' if len(values) == 1 else 'оценки'
    flash_msg('success',
              f'{student.full_name}: {", ".join(values)} — {word} выставлена')
    return _journal_back(request.form)


@main.route('/journal/grade/<int:grade_id>/edit', methods=['POST'])
@staff_required
def journal_edit_grade(grade_id):
    """Правка оценки. Старое и новое значения остаются в GradeHistory."""
    form = JournalEditForm()
    grade = db.session.get(Grade, grade_id)
    if grade is None:
        flash_msg('error', 'Оценка не найдена')
        return _journal_back(request.form)

    subject, failure = teacher_owns_subject(grade.subject_id)
    if failure:
        return failure
    if not form.validate_on_submit():
        flash_form_errors(form)
        return _journal_back(request.form)

    old_value = grade.grade_value
    new_value = form.value.data
    if old_value == new_value and (grade.comments or None) == \
            (form.comments.data or None):
        flash_msg('warning', 'Ничего не изменилось')
        return _journal_back(request.form)

    grade.grade_value = new_value
    grade.comments = form.comments.data or None
    db.session.add(GradeHistory(
        grade_id=grade.id, old_value=old_value, new_value=new_value,
        action=GradeHistory.ACTION_UPDATED, changed_by=current_user.id,
        comment=form.comments.data or None))
    db.session.commit()

    student = db.session.get(Student, grade.student_id)
    log.info('Оценка исправлена: %s, предмет «%s», %s → %s — %s',
             student.full_name if student else grade.student_id, subject.name,
             old_value, new_value, current_user.username)
    log_audit('journal_grade_edit', user=current_user, entity_type='grade',
              entity_id=grade.id,
              details=f'студент={student.full_name if student else grade.student_id!r}, '
                      f'предмет={subject.name!r}, было={old_value!r}, '
                      f'стало={new_value!r}')
    flash_msg('success', f'Оценка исправлена: {old_value} → {new_value}')
    return _journal_back(request.form)


@main.route('/journal/grade/<int:grade_id>/delete', methods=['POST'])
@staff_required
def journal_delete_grade(grade_id):
    """Удаление оценки с записью в историю (8.5)."""
    grade = db.session.get(Grade, grade_id)
    if grade is None:
        flash_msg('error', 'Оценка не найдена')
        return _journal_back(request.form)

    subject, failure = teacher_owns_subject(grade.subject_id)
    if failure:
        return failure

    student = db.session.get(Student, grade.student_id)
    value = grade.grade_value
    lesson_date = grade.date
    # Саму оценку удаляем, историю оставляем: иначе правка «было 3 → стало 5»
    # исчезла бы вместе с оценкой и журнал изменений врал бы
    db.session.add(GradeHistory(
        grade_id=grade.id, old_value=value, new_value=None,
        action=GradeHistory.ACTION_DELETED, changed_by=current_user.id,
        comment=request.form.get('comment')))
    db.session.delete(grade)
    db.session.commit()

    log.warning('Оценка удалена из журнала: %s, предмет «%s», значение %s — %s',
                student.full_name if student else grade.student_id,
                subject.name, value, current_user.username)
    # entity_id — id удалённой оценки: он уже не существует в таблице grade,
    # но в аудите остаётся как след удаления
    log_audit('journal_grade_delete', user=current_user, entity_type='grade',
              entity_id=grade_id,
              details=f'студент={student.full_name if student else grade.student_id!r}, '
                      f'предмет={subject.name!r}, значение={value!r}')
    flash_msg('success', f'Оценка {value} удалена')
    return _journal_back(request.form)


def _resolve_lesson_date(form, group, subject, period):
    """Дата занятия из формы либо подтверждённая LessonDate.

    В режиме «по датам» дата приходит скрытым полем из ячейки, в режиме
    «сводная» — id конкретного занятия. В обоих случаях проверяем, что
    занятие действительно есть у этой группы и предмета внутри периода:
    иначе через подделанную форму можно было бы выставить оценку за
    несуществующее занятие или за пределами периода.

    Возвращает (date|None, error_response|None).
    """
    if group is None or subject is None:
        return None, (render_template(
            'error.html', code=404, message='Группа или предмет не найден.'), 404)

    if form.lesson_date.data:
        lesson = db.session.get(LessonDate, form.lesson_date.data)
        if lesson is None:
            return None, (render_template(
                'error.html', code=404,
                message='Занятие не найдено. Обновите журнал.'), 404)
        if lesson.schedule_item.group_id != group.id or \
                lesson.schedule_item.subject_id != subject.id:
            log.warning('Отклонена оценка: занятие %s не относится к группе %s / предмету %s — %s',
                        lesson.id, group.id, subject.id, current_user.username)
            return None, (render_template(
                'error.html', code=403,
                message='Это занятие относится к другой группе или предмету.'), 403)
        if period and not (period.start_date <= lesson.date <= period.end_date):
            return None, (render_template(
                'error.html', code=400,
                message=f'Занятие {lesson.date.strftime("%d.%m.%Y")} вне периода '
                        f'«{period.name}».'), 400)
        if lesson.date > date.today():
            return None, (render_template(
                'error.html', code=400,
                message='Нельзя выставить оценку за будущее занятие.'), 400)
        return lesson.date, None

    lesson_date = _parse_form_date(form.date.data)
    if lesson_date is None:
        return None, (render_template(
            'error.html', code=400, message='Дата занятия указана неверно.'), 400)
    if period and not (period.start_date <= lesson_date <= period.end_date):
        return None, (render_template(
            'error.html', code=400,
            message=f'Дата {lesson_date.strftime("%d.%m.%Y")} вне периода '
                    f'«{period.name}».'), 400)
    if lesson_date > date.today():
        return None, (render_template(
            'error.html', code=400,
            message='Нельзя выставить оценку за будущее занятие.'), 400)
    exists = db.session.query(LessonDate.id).join(
        LessonDate.schedule_item).filter(
        ScheduleItem.group_id == group.id,
        ScheduleItem.subject_id == subject.id,
        LessonDate.date == lesson_date).first()
    if exists is None:
        log.warning('Отклонена оценка: нет отмеченного занятия %s для группы %s по предмету %s — %s',
                    lesson_date, group.id, subject.id, current_user.username)
        return None, (render_template(
            'error.html', code=400,
            message=f'Занятия {lesson_date.strftime("%d.%m.%Y")} не было. '
                    f'Отметьте его в расписании.'), 400)
    return lesson_date, None


def _parse_form_date(raw):
    """Дата из строки формы; None, если разобрать не удалось."""
    try:
        return datetime.strptime(str(raw), '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


def _journal_back(form):
    """Возврат на сетку с теми же фильтрами, откуда пришли."""
    args = {}
    for key in ('group_id', 'subject_id', 'period_id'):
        value = form.get(key, type=int)
        if value:
            args[key] = value
    if form.get('mode') in ('dates', 'subjects'):
        args['mode'] = form.get('mode')
    return redirect(url_for('main.journal', **args))


# ===================== ПРОПУСКИ =====================
def _attendance_back(form, fallback='main.attendance'):
    """Возврат на страницу пропусков или в сетку журнала.

    Отметка ставится из двух мест: из ячейки журнала и со страницы пропусков.
    Возвращать туда, откуда пришли, удобнее всего по полю mode.
    """
    if form.get('mode') in ('dates', 'subjects'):
        return _journal_back(form)
    args = {}
    for key in ('group_id', 'period_id', 'subject_id'):
        value = form.get(key, type=int)
        if value:
            args[key] = value
    return redirect(url_for(fallback, **args))


ATTENDANCE_FIELD_LABELS = {
    'student_id': 'студент', 'group_id': 'группа', 'subject_id': 'предмет',
    'date': 'дата занятия', 'reason': 'причина', 'note': 'комментарий',
    'period_id': 'период', 'mode': 'режим',
}

RESULT_FIELD_LABELS = {
    'student_id': 'студент', 'group_id': 'группа', 'subject_id': 'предмет',
    'period_id': 'период', 'final_value': 'итоговая оценка',
    'recommended': 'рекомендованный итог', 'justification': 'обоснование',
}


def flash_form_errors(form, labels=ATTENDANCE_FIELD_LABELS):
    """Показать ошибки формы по-русски.

    Без подписей пользователю выводилось «subject_id: Not a valid choice» —
    имя поля и текст WTForms о нём ничего не говорят.
    """
    for field, errors in form.errors.items():
        label = labels.get(field, field)
        flash_msg('error', f'{label}: {"; ".join(errors)}')


def attendance_subject_choices():
    """Предметы для форм пропусков.

    Администратору добавляем «весь день»: такая отметка честнее, чем ставить
    одинаковые пропуски по каждому предмету дня вручную. Преподавателю
    вариант не показываем — он затронул бы чужие предметы.
    """
    choices = [(s.id, s.name) for s in visible_subjects_query().all()]
    if current_user.is_admin:
        choices.insert(0, (ALL_DAY_SUBJECT, ALL_DAY_LABEL))
    return choices


def mark_attendance_allowed(subject_id):
    """Кто имеет право ставить пропуск по предмету.

    Преподаватель отмечает пропуск только по своему предмету. Отметка без
    предмета («отсутствовал весь день») задевает все пары дня, включая чужие
    предметы, поэтому доступна только администратору.

    Возвращает (subject|None, error_response|None).
    """
    if not subject_id:
        if not current_user.is_admin:
            log.warning('Доступ запрещён: %s (роль %s) хочет отметить пропуск '
                        'за весь день', current_user.username, current_user.role)
            return None, (render_template(
                'error.html', code=403,
                message='Отметку «отсутствовал весь день» может поставить только '
                        'администратор. Преподаватель отмечает пропуск по своему '
                        'предмету.'), 403)
        return None, None
    return teacher_owns_subject(subject_id)


def attendance_context(group_id, period_id):
    """Общие данные для журнала пропусков: фильтры, список, сводка.

    Сводка по группе и по студентам берётся одним проходом по записям
    (get_or_0), а не агрегатами на каждую причину: пять отдельных
    COUNT-запросов на группу расточительны и всё равно требуют второго
    прохода для списка.
    """
    groups_list = Group.query.order_by(Group.name).all()
    if not groups_list:
        return None, (render_template(
            'error.html', code=404,
            message='Сначала создайте хотя бы одну группу.'), 404)
    if group_id not in [g.id for g in groups_list]:
        group_id = groups_list[0].id
    group = next(g for g in groups_list if g.id == group_id)

    period, failure = current_period_or_default(period_id)
    if failure:
        return None, failure

    periods_list = (AcademicPeriod.query
                    .filter_by(academic_year=period.academic_year)
                    .order_by(AcademicPeriod.sort_order).all())

    students = (Student.query
                .filter_by(group_id=group.id, status='active')
                .order_by(Student.last_name, Student.first_name).all())
    reasons = attendance_reason_choices()

    records = []
    per_student = {s.id: {'total': 0, 'hours': 0,
                          'reasons': {reason: 0 for reason, _ in reasons}}
                   for s in students}
    if students:
        # Список отметок показывает студента, предмет и автора, поэтому
        # связи грузим сразу: иначе на каждую отметку по отдельному SELECT
        records = (AttendanceRecord.query
                   .options(joinedload(AttendanceRecord.student),
                            joinedload(AttendanceRecord.subject),
                            joinedload(AttendanceRecord.author))
                   .filter(AttendanceRecord.student_id.in_(
                       [s.id for s in students]),
                       AttendanceRecord.date >= period.start_date,
                       AttendanceRecord.date <= period.end_date)
                   .order_by(AttendanceRecord.date.desc(),
                             AttendanceRecord.student_id).all())

    for record in records:
        # hours_count считает пары по расписанию, поэтому суммируем по
        # студенту то, что он реально пропустил, а не число записей
        bucket = per_student.get(record.student_id)
        if bucket is None:
            continue
        bucket['total'] += 1
        bucket['hours'] += record.hours_count or 0
        if record.reason in bucket['reasons']:
            bucket['reasons'][record.reason] += 1

    summary = {
        'total': sum(b['total'] for b in per_student.values()),
        'hours': sum(b['hours'] for b in per_student.values()),
        'reasons': {reason: sum(b['reasons'][reason]
                                for b in per_student.values())
                    for reason, _ in reasons},
    }

    return {
        'groups': groups_list,
        'group': group,
        'period': period,
        'periods': periods_list,
        'students': students,
        'reasons': reasons,
        'records': records,
        'per_student': per_student,
        'summary': summary,
        'can_mark_any': current_user.is_admin,
    }, None


@main.route('/attendance')
@staff_required
def attendance():
    """Журнал пропусков по группе за период: список и сводка (9.1)."""
    group_id = request.args.get('group_id', type=int)
    period_id = request.args.get('period_id', type=int)
    ctx, failure = attendance_context(group_id, period_id)
    if failure:
        return failure

    mark_form = AttendanceMarkForm()
    mark_form.subject_id.choices = attendance_subject_choices()
    mark_form.reason.choices = ctx['reasons']
    bulk_form = AttendanceBulkForm()
    bulk_form.group_id.choices = [(g.id, g.name) for g in ctx['groups']]
    bulk_form.subject_id.choices = attendance_subject_choices()
    bulk_form.reason.choices = ctx['reasons']

    return render_template('attendance.html', mark_form=mark_form,
                           bulk_form=bulk_form,
                           edit_form=AttendanceEditForm(), **ctx)


@main.route('/attendance/mark', methods=['POST'])
@staff_required
def attendance_mark():
    """Отметка пропуска в ячейке журнала или на странице пропусков (9.2)."""
    form = AttendanceMarkForm()
    form.subject_id.choices = attendance_subject_choices()
    form.reason.choices = attendance_reason_choices()
    if not form.validate_on_submit():
        flash_form_errors(form)
        return _attendance_back(request.form)

    subject, failure = mark_attendance_allowed(
        form.subject_id.data or None)
    if failure:
        return failure

    student = db.session.get(Student, form.student_id.data)
    if student is None:
        flash_msg('error', 'Студент не найден')
        return _attendance_back(request.form)
    if student.group_id != form.group_id.data:
        flash_msg('error', f'{student.full_name} не учится в выбранной группе')
        log.warning('Отклонён пропуск: %s не в группе %s — %s',
                    student.full_name, form.group_id.data, current_user.username)
        return _attendance_back(request.form)

    lesson_date = _parse_form_date(form.date.data)
    if lesson_date is None:
        flash_msg('error', 'Дата занятия указана неверно')
        return _attendance_back(request.form)
    if lesson_date > date.today():
        flash_msg('error', 'Нельзя отметить пропуск за будущее занятие')
        return _attendance_back(request.form)

    failure = _check_lesson_happened(lesson_date, student.group_id, subject)
    if failure:
        return failure

    exists = (AttendanceRecord.query
              .filter_by(student_id=student.id, date=lesson_date,
                         subject_id=subject.id if subject else None)
              .first())
    if exists is not None:
        flash_msg('warning',
                  f'{student.full_name}: пропуск на {lesson_date.strftime("%d.%m.%Y")} '
                  f'уже отмечен ({exists.reason_label.lower()})')
        return _attendance_back(request.form)

    record = AttendanceRecord(
        student_id=student.id, date=lesson_date, reason=form.reason.data,
        subject_id=subject.id if subject else None,
        note=form.note.data or None, created_by=current_user.id)
    db.session.add(record)
    db.session.commit()

    scope = f'предмет «{subject.name}»' if subject else 'весь день'
    log.info('Отмечен пропуск: %s, %s, %s (%s) — %s',
             student.full_name, lesson_date.strftime('%d.%m.%Y'), scope,
             record.reason_label, current_user.username)
    log_audit('attendance_create', user=current_user,
              entity_type='attendance', entity_id=record.id,
              details=f'студент={student.full_name!r}, '
                      f'дата={lesson_date.strftime("%d.%m.%Y")!r}, '
                      f'причина={record.reason_label!r}')
    flash_msg('success',
              f'{student.full_name}: пропуск {lesson_date.strftime("%d.%m.%Y")} '
              f'отмечен — {record.reason_label.lower()}')
    return _attendance_back(request.form)


@main.route('/attendance/<int:record_id>/edit', methods=['POST'])
@staff_required
def attendance_edit(record_id):
    """Смена причины и комментария: пропуск уже записан, молча менять его
    вкладкой в базе нельзя."""
    form = AttendanceEditForm()
    form.reason.choices = attendance_reason_choices()
    record = db.session.get(AttendanceRecord, record_id)
    if record is None:
        flash_msg('error', 'Пропуск не найден')
        return _attendance_back(request.form)

    subject, failure = mark_attendance_allowed(record.subject_id)
    if failure:
        return failure
    if not form.validate_on_submit():
        flash_form_errors(form)
        return _attendance_back(request.form)

    old_reason = record.reason
    record.reason = form.reason.data
    record.note = form.note.data or None
    db.session.commit()

    student = db.session.get(Student, record.student_id)
    log.info('Пропуск изменён: %s, %s, %s → %s — %s',
             student.full_name if student else record.student_id,
             record.date.strftime('%d.%m.%Y'), old_reason, record.reason,
             current_user.username)
    log_audit('attendance_edit', user=current_user, entity_type='attendance',
              entity_id=record.id,
              details=f'студент={student.full_name if student else record.student_id!r}, '
                      f'дата={record.date.strftime("%d.%m.%Y")!r}, '
                      f'было={old_reason!r}, стало={record.reason!r}')
    flash_msg('success',
              f'{student.full_name}: причина изменена — {record.reason_label.lower()}')
    return _attendance_back(request.form)


@main.route('/attendance/<int:record_id>/delete', methods=['POST'])
@staff_required
def attendance_delete(record_id):
    """Удаление пропуска (9.4). Форма отправляется только из окна
    подтверждения, отдельной страницы удаления нет."""
    record = db.session.get(AttendanceRecord, record_id)
    if record is None:
        flash_msg('error', 'Пропуск не найден')
        return _attendance_back(request.form)

    subject, failure = mark_attendance_allowed(record.subject_id)
    if failure:
        return failure

    student = db.session.get(Student, record.student_id)
    name = student.full_name if student else record.student_id
    lesson_date = record.date
    reason_label = record.reason_label
    db.session.delete(record)
    db.session.commit()

    log.info('Пропуск удалён: %s, %s, %s — %s', name,
             lesson_date.strftime('%d.%m.%Y'), reason_label, current_user.username)
    log_audit('attendance_delete', user=current_user, entity_type='attendance',
              entity_id=record_id,
              details=f'студент={name!r}, '
                      f'дата={lesson_date.strftime("%d.%m.%Y")!r}, '
                      f'причина={reason_label!r}')
    flash_msg('success',
              f'{name}: пропуск {lesson_date.strftime("%d.%m.%Y")} удалён')
    return _attendance_back(request.form)


@main.route('/attendance/bulk', methods=['POST'])
@staff_required
def attendance_bulk():
    """Массовая отметка отсутствующих за дату (9.3)."""
    form = AttendanceBulkForm()
    form.group_id.choices = [(g.id, g.name) for g in Group.query.order_by(Group.name)]
    form.subject_id.choices = attendance_subject_choices()
    form.reason.choices = attendance_reason_choices()
    if not form.validate_on_submit():
        flash_form_errors(form)
        return _attendance_back(request.form)

    subject, failure = mark_attendance_allowed(form.subject_id.data or None)
    if failure:
        return failure

    failure = _check_lesson_happened(form.date.data, form.group_id.data, subject)
    if failure:
        return failure

    students = (Student.query
                .filter_by(group_id=form.group_id.data, status='active')
                .order_by(Student.id).all())
    if not students:
        flash_msg('warning', 'В группе нет активных студентов')
        return _attendance_back(request.form)

    # Уже отмеченные исключаем всегда, а оценки за день — по флажку:
    # пропуск и оценка в один день означают либо ошибку, либо отметку не по
    # тому предмету, но молча выбирать одно из двух нельзя
    already = {(row[0], row[1]) for row in
               (db.session.query(AttendanceRecord.student_id,
                                 AttendanceRecord.subject_id)
                .filter(AttendanceRecord.date == form.date.data)
                .all())}
    graded = set()
    if form.skip_graded.data:
        graded = {row[0] for row in
                  (db.session.query(Grade.student_id)
                   .filter(Grade.date == form.date.data,
                           Grade.subject_id == form.subject_id.data)
                   .all())}

    skipped_graded = []
    skipped_already = []
    created = 0
    for student in students:
        subject_key = subject.id if subject else None
        if (student.id, subject_key) in already:
            skipped_already.append(student)
            continue
        if student.id in graded:
            skipped_graded.append(student)
            continue
        db.session.add(AttendanceRecord(
            student_id=student.id, date=form.date.data, reason=form.reason.data,
            subject_id=subject_key, note=form.note.data or None,
            created_by=current_user.id))
        created += 1
    db.session.commit()

    scope = f'предмет «{subject.name}»' if subject else 'весь день'
    # .data, а не само поле: у DateField нет strftime
    log_date = form.date.data.strftime('%d.%m.%Y')
    log.info('Массовая отметка пропусков: группа %s, %s, %s — отмечено %s, '
             'пропущено с оценкой %s, уже отмечено %s — %s',
             form.group_id.data, scope, log_date, created,
             len(skipped_graded), len(skipped_already), current_user.username)
    log_audit('attendance_bulk', user=current_user, entity_type='attendance',
              details=f'группа={form.group_id.data}, {scope}, дата={log_date!r}, '
                      f'отмечено={created}, '
                      f'пропущено_с_оценкой={len(skipped_graded)}, '
                      f'уже_отмечено={len(skipped_already)}')
    flash_msg('success',
              f'Отмечено пропусков: {created} за {log_date}'
              + (f'; с оценкой за день пропущено: {len(skipped_graded)}'
                 if form.skip_graded.data else ''))
    return _attendance_back(request.form)


def dashboard_attendance_stats():
    """Сводка пропусков за текущий период для дашборда.

    Два запроса: агрегат по всем записям и пятёрка лидеров. Считать в шаблоне
    нельзя — там получится SELECT на каждую запись при обращении к student.
    """
    period, failure = current_period_or_default()
    if failure:
        return {'total': 0, 'unexcused': 0, 'top': [], 'period_name': ''}

    total, unexcused = (db.session.query(
        func.count(AttendanceRecord.id),
        func.sum(case((AttendanceRecord.reason ==
                       AttendanceRecord.REASON_UNEXCUSED, 1), else_=0)))
        .filter(AttendanceRecord.date >= period.start_date,
                AttendanceRecord.date <= period.end_date)
        .one())

    # Не «по числу пропусков», а по числу дней: иначе студент, сидящий на
    # парах по понедельникам, всегда был бы первым в списке
    rows = (db.session.query(
        AttendanceRecord.student_id,
        func.count(func.distinct(AttendanceRecord.date)).label('total'),
        func.sum(case((AttendanceRecord.reason ==
                       AttendanceRecord.REASON_UNEXCUSED, 1), else_=0)).label('unexcused'))
        .filter(AttendanceRecord.date >= period.start_date,
                AttendanceRecord.date <= period.end_date)
        .group_by(AttendanceRecord.student_id)
        .order_by(desc('total'), AttendanceRecord.student_id)
        .limit(5).all())

    top = []
    if rows:
        students = (Student.query
                    .options(joinedload(Student.group))
                    .filter(Student.id.in_([row[0] for row in rows])).all())
        by_id = {student.id: student for student in students}
        for student_id, count, without_reason in rows:
            student = by_id.get(student_id)
            if student is None:
                continue
            top.append({'id': student.id,
                        'last_name': student.last_name,
                        'first_name': student.first_name,
                        'group_name': student.group.name if student.group else '',
                        'total': count,
                        'unexcused': without_reason or 0})

    return {'total': total or 0,
            'unexcused': unexcused or 0,
            'top': top,
            'period_name': period.name}


def _check_lesson_happened(lesson_date, group_id, subject):
    """Пропуск отмечают только по состоявшемуся занятию.

    Без проверки можно было бы отметить пропуск на любую дату внутри
    периода, включая выходной, и сводка разошлась бы с расписанием.

    Возвращает error_response или None.
    """
    lesson_date = _parse_form_date(lesson_date) if lesson_date else None
    if lesson_date is None:
        return (render_template(
            'error.html', code=400, message='Дата занятия указана неверно.'), 400)

    query = (db.session.query(LessonDate.id)
             .join(LessonDate.schedule_item)
             .filter(ScheduleItem.group_id == group_id,
                     LessonDate.date == lesson_date))
    if subject is not None:
        query = query.filter(ScheduleItem.subject_id == subject.id)
    if query.first() is None:
        log.warning('Отклонён пропуск: занятия %s не было (группа %s, предмет %s) — %s',
                    lesson_date, group_id,
                    subject.name if subject else 'любой', current_user.username)
        return (render_template(
            'error.html', code=400,
            message=f'Занятия {lesson_date.strftime("%d.%m.%Y")} не было. '
                    f'Отметьте его в расписании.'), 400)
    return None


# ===================== СОТРУДНИКИ =====================
@main.route('/staff')
@admin_required
def staff():
    """Список сотрудников с их предметами и последним входом."""
    staff_list = (User.query
                  .filter(User.role.in_([User.ROLE_ADMIN, User.ROLE_TEACHER]))
                  .options(joinedload(User.subjects))
                  .order_by(User.role, User.username).all())
    # Форму сброса пароля делаем для каждой строки таблицы, иначе токен
    # пришлось бы подставлять вручную — и он молча пропадёт при Фазе 14
    reset_forms = {user.id: StaffPasswordResetForm() for user in staff_list}
    return render_template('staff.html', staff=staff_list,
                           reset_forms=reset_forms,
                           groups=Group.query.order_by(Group.name).all(),
                           roles=User.ROLE_LABELS)


@main.route('/staff/add', methods=['GET', 'POST'])
@admin_required
def add_staff():
    """Создание учётной записи сотрудника"""
    form = StaffForm()
    if form.validate_on_submit():
        try:
            user = User(username=normalize_username(form.username.data),
                        full_name=(form.full_name.data or '').strip() or None,
                        email=form.email.data or None,
                        role=form.role.data,
                        is_active=form.is_active.data,
                        created_by=current_user.id)
            # Пароль, заданный администратором, — временный: сотрудник обязан
            # сменить его при первом входе. Иначе через год этот пароль
            # останется в чужой переписке.
            user.set_temporary_password(form.password.data)
            db.session.add(user)
            db.session.commit()
            log.info('Создан сотрудник: %s (роль %s) — %s',
                     user.username, user.role, current_user.username)
            log_audit('staff_create', user=current_user, entity_type='user',
                      entity_id=user.id,
                      details=f'логин={user.username!r}, роль={user.role!r}, '
                              f'пароль выдан временный')
            flash_msg('success', f'Сотрудник {user.display_name} создан')
            return redirect(url_for('main.staff'))
        except IntegrityError:
            db.session.rollback()
            flash_msg('error', 'Такой логин уже существует')
        except Exception as e:
            db.session.rollback()
            log.exception('Ошибка создания сотрудника: %s', e)
            flash_msg('error', f'Ошибка создания сотрудника: {str(e)}')
    return render_template('staff_form.html', form=form, user=None,
                           title='Создание сотрудника', roles=User.ROLE_LABELS)


@main.route('/staff/<int:id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_staff(id):
    """Правка сотрудника: роль, ФИО, блокировка"""
    user = User.query.get_or_404(id)
    if user.id == current_user.id and user.role != User.ROLE_ADMIN:
        # Нельзя лишить себя прав в единственной сессии
        flash_msg('error', 'Нельзя изменить собственную роль на более низкую')
        return redirect(url_for('main.staff'))

    form = StaffForm(user=user)
    if form.validate_on_submit():
        demoting = user.is_admin and form.role.data != User.ROLE_ADMIN
        if demoting and User.query.filter_by(
                role=User.ROLE_ADMIN, is_active=True).count() <= 1:
            flash_msg('error', 'В системе должен остаться хотя бы один '
                               'активный администратор')
            return redirect(url_for('main.staff'))

        # Логин менять не даём: на него завязаны записи о действиях
        user.full_name = (form.full_name.data or '').strip() or None
        user.email = form.email.data or None
        user.role = form.role.data
        user.is_active = form.is_active.data
        try:
            db.session.commit()
            log.info('Изменён сотрудник %s: роль %s, активен %s — %s',
                     user.username, user.role, user.is_active,
                     current_user.username)
            log_audit('staff_edit', user=current_user, entity_type='user',
                      entity_id=user.id,
                      details=f'логин={user.username!r}, роль={user.role!r}, '
                              f'активен={user.is_active}')
            flash_msg('success', f'Данные сотрудника {user.display_name} сохранены')
            return redirect(url_for('main.staff'))
        except Exception as e:
            db.session.rollback()
            log.exception('Ошибка сохранения сотрудника: %s', e)
            flash_msg('error', f'Ошибка сохранения: {str(e)}')
    return render_template('staff_form.html', form=form, user=user,
                           title='Редактирование сотрудника', roles=User.ROLE_LABELS)


@main.route('/staff/<int:id>/reset-password', methods=['POST'])
@admin_required
def reset_staff_password(id):
    """Сброс пароля сотрудника"""
    user = User.query.get_or_404(id)
    form = StaffPasswordResetForm()
    if not form.validate_on_submit():
        flash_msg('error', 'Новый пароль не прошёл проверку')
        return redirect(url_for('main.staff'))
    try:
        user.set_temporary_password(form.password.data)
        db.session.commit()
        log.warning('Сброшен пароль сотрудника %s — %s',
                    user.username, current_user.username)
        log_audit('staff_password_reset', user=current_user, entity_type='user',
                  entity_id=user.id,
                  details=f'логин={user.username!r}, пароль сброшен')
        flash_msg('success', f'Пароль сотрудника {user.display_name} сброшен')
    except Exception as e:
        db.session.rollback()
        log.exception('Ошибка сброса пароля: %s', e)
        flash_msg('error', f'Ошибка сброса пароля: {str(e)}')
    return redirect(url_for('main.staff'))


@main.route('/staff/<int:id>/delete', methods=['POST'])
@admin_required
def delete_staff(id):
    """Удаление сотрудника.

    Удаляем только тех, у кого нет назначенных предметов; остальным
    предлагаем блокировку, чтобы не терять историю действий.
    """
    user = User.query.get_or_404(id)
    if user.id == current_user.id:
        flash_msg('error', 'Нельзя удалить собственную учётную запись')
        return redirect(url_for('main.staff'))
    if user.is_admin and User.query.filter_by(
            role=User.ROLE_ADMIN).count() <= 1:
        flash_msg('error', 'В системе должен остаться хотя бы один администратор')
        return redirect(url_for('main.staff'))
    if user.subjects:
        flash_msg('error', f'Сначала снимите с сотрудника {user.subjects} предметов')
        return redirect(url_for('main.staff'))

    username = user.username
    try:
        db.session.delete(user)
        db.session.commit()
        log.warning('Удалён сотрудник: %s — %s', username, current_user.username)
        log_audit('staff_delete', user=current_user, entity_type='user',
                  entity_id=id, details=f'логин={username!r}')
        flash_msg('success', f'Сотрудник {username} удалён')
    except Exception as e:
        db.session.rollback()
        log.exception('Ошибка удаления сотрудника: %s', e)
        flash_msg('error', f'Ошибка удаления: {str(e)}')
    return redirect(url_for('main.staff'))


# ===================== УЧЁТНЫЕ ЗАПИСИ СТУДЕНТОВ =====================
@main.route('/accounts')
@admin_required
def accounts():
    """Список ученических учётных записей (11.4).

    Пароля здесь нет и быть не может: в базе лежит только хеш, а
    восстановить из хеша исходный пароль нельзя. Поэтому у администратора
    есть только «выдать новый», а не «показать прежний».
    """
    group_id = request.args.get('group_id', type=int)
    search = (request.args.get('q') or '').strip()

    query = (User.query
             .filter(User.role == User.ROLE_STUDENT)
             .join(Student, Student.user_id == User.id)
             .options(joinedload(User.student_account)
                      .joinedload(Student.group)))
    if group_id:
        query = query.filter(Student.group_id == group_id)
    if search:
        pattern = f'%{search}%'
        query = query.filter(or_(User.username.ilike(pattern),
                                 User.full_name.ilike(pattern)))

    rows = query.order_by(User.username).all()

    # Студенты без учётной записи — их видно сразу, иначе «выдал всем» не
    # означает «все смогут войти»: номер зачётки мог занять сотрудник.
    no_account_filter = Student.query.filter(Student.user_id.is_(None))
    if group_id:
        no_account_filter = no_account_filter.filter(Student.group_id == group_id)
    without_account = (no_account_filter
                       .options(joinedload(Student.group))
                       .order_by(Student.last_name).all())

    return render_template('accounts.html', accounts=rows,
                           without_account=without_account,
                           groups=Group.query.order_by(Group.name).all(),
                           group_id=group_id, search=search)


@main.route('/accounts/issue', methods=['POST'])
@admin_required
def issue_accounts():
    """Массовая выдача учётных записей студентам группы (11.3).

    Логином служит номер зачётки, пароль — временный и одноразовый. Уже
    выданные записи не трогаются: сброс чужого пароля — отдельное
    осознанное действие, а не побочный эффект кнопки «выдать всем».
    """
    group_id = request.form.get('group_id', type=int)
    include_without = request.form.get('all_students') == '1'

    query = Student.query.filter(Student.user_id.is_(None))
    if group_id:
        query = query.filter(Student.group_id == group_id)
    if not include_without:
        query = query.filter(Student.status == 'active')

    students_list = (query.options(joinedload(Student.group))
                     .order_by(Student.last_name).all())
    if not students_list:
        flash_msg('info', 'Нет студентов без учётной записи')
        return redirect(url_for('main.accounts'))

    issued = []
    skipped = []
    for student in students_list:
        login_name = normalize_username(student.student_id)
        # Проверка по всей таблице User, без учёта регистра: номер зачётки
        # «ADMIN» не должен отдавать логин сотрудника-администратора.
        if username_taken(login_name):
            skipped.append({'name': student.full_name, 'login': login_name,
                            'reason': 'логин уже занят другой учётной записью'})
            continue
        if not login_name:
            skipped.append({'name': student.full_name, 'login': '—',
                            'reason': 'не заполнен номер зачётки'})
            continue

        temporary_password = generate_temporary_password()
        user = User(username=login_name,
                    full_name=student.full_name,
                    email=student.email or None,
                    role=User.ROLE_STUDENT,
                    is_active=True,
                    created_by=current_user.id)
        user.set_temporary_password(temporary_password)
        db.session.add(user)
        db.session.flush()
        student.user_id = user.id
        issued.append({'login': login_name, 'name': student.full_name,
                       'group': student.group.name if student.group else '',
                       'password': temporary_password})

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        log.exception('Ошибка массовой выдачи учётных записей: %s', e)
        flash_msg('error', f'Ошибка выдачи учётных записей: {str(e)}')
        return redirect(url_for('main.accounts'))

    # В журнал — только факт и число. Сами пароли в журнал событий не пишутся:
    # журнал читают при разборе инцидентов, и там им не место.
    log.info('Выдано учётных записей студентам: %d%s — %s',
             len(issued), f' (группа {group_id})' if group_id else '',
             current_user.username)
    # 13.2/13.4: в аудит — число выданных записей, но не сами пароли
    log_audit('accounts_issue', user=current_user, entity_type='user',
              entity_id=None,
              details=f'выдано={len(issued)}, группа={group_id}, '
                      f'пропущено={len(skipped)}, пароли временные')
    for row in skipped:
        log.warning('Логин «%s» занят, учётная запись не выдана: %s',
                    row['login'], row['name'])

    if not issued:
        flash_msg('info', 'Новые учётные записи не созданы')
        return redirect(url_for('main.accounts'))

    # Ведомость с паролями сохраняем файлом: её печатают и режут. На экране
    # пароли видны один раз, а распечатку администратор отдаёт руками.
    download_url = None
    try:
        filename = f'uchetnye_zapisi_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        filepath = os.path.join(EXPORT_DIR, filename)
        save_export(filepath, credentials_sheets(issued))
        download_url = url_for('main.download_credentials',
                               filename=filename)
    except Exception as e:
        # Не срываем выдачу из-за файла: логины и пароли уже показаны на
        # экране, администратор может перепечатать таблицу.
        log.exception('Не удалось сохранить ведомость с паролями: %s', e)
        flash_msg('warning', 'Ведомость не сохранилась для скачивания, '
                             'перепечатайте таблицу с экрана')

    return render_template('credentials_issued.html', issued=issued,
                           skipped=skipped, download_url=download_url)


@main.route('/accounts/credentials/<path:filename>')
@admin_required
def download_credentials(filename):
    """Скачать ведомость с выданными паролями.

    Файл лежит в exports/ и отдаётся только администратору: пароли на
    диске столько же опасны, сколько в журнале событий.
    """
    safe = sanitize_filename(os.path.basename(filename))
    if not safe.endswith('.xlsx') or '/' in filename or '\\' in filename:
        flash_msg('error', 'Недопустимое имя файла')
        return redirect(url_for('main.accounts'))
    # В файле лежат действующие пароли студентов: скачивание само по себе
    # должно быть видно в журнале действий
    log_audit('credentials_download', user=current_user,
              entity_type='credentials_file', details=f'файл={safe!r}')
    return send_from_directory(EXPORT_DIR, safe, as_attachment=True)


@main.route('/students/<int:id>/account', methods=['POST'])
@admin_required
def set_student_account(id):
    """Выдать или сбросить пароль одного студента (11.1).

    Логин не вводится: он равен номеру зачётки из карточки. Новый пароль
    показывается один раз, дальше — только хеш.
    """
    student = Student.query.get_or_404(id)
    form = StudentAccountForm()
    if not form.validate_on_submit():
        # Причина прямо в сообщении: «пароль не прошёл проверку» ничего
        # не говорит о том, что не сошлись два введённых пароля.
        flash_form_errors(form, {'password': 'Пароль',
                                 'confirm_password': 'Повтор пароля'})
        return redirect(url_for('main.edit_student', id=student.id))

    login_name = normalize_username(student.student_id)
    user = student.user
    was_new = user is None
    if user is None:
        if username_taken(login_name):
            flash_msg('error', f'Логин {login_name} уже занят другой '
                               f'учётной записью')
            return redirect(url_for('main.edit_student', id=student.id))
        user = User(username=login_name, role=User.ROLE_STUDENT,
                    is_active=True, created_by=current_user.id)
        db.session.add(user)
        db.session.flush()
        student.user_id = user.id

    user.full_name = student.full_name
    user.email = student.email or None
    user.is_active = True
    user.set_temporary_password(form.password.data)

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        log.exception('Ошибка выдачи учётной записи: %s', e)
        flash_msg('error', f'Ошибка выдачи учётной записи: {str(e)}')
        return redirect(url_for('main.edit_student', id=student.id))

    if was_new:
        log.info('Выдана учётная запись студенту %s (логин %s) — %s',
                 student.full_name, login_name, current_user.username)
        log_audit('student_account_create', user=current_user,
                  entity_type='user', entity_id=user.id,
                  details=f'студент={student.full_name!r}, логин={login_name!r}, '
                          f'пароль выдан временный')
    else:
        log.warning('Сброшен пароль ученической записи %s (логин %s) — %s',
                    student.full_name, login_name, current_user.username)
        log_audit('student_account_reset', user=current_user,
                  entity_type='user', entity_id=user.id,
                  details=f'студент={student.full_name!r}, логин={login_name!r}, '
                          f'пароль сброшен')

    # Один раз показываем пароль и отправляем в кабинет самого студента.
    return render_template('password_issued.html', rows=[{
        'login': login_name, 'name': student.full_name,
        'password': form.password.data,
    }], back_url=url_for('main.edit_student', id=student.id))


@main.route('/accounts/<int:id>/reset', methods=['POST'])
@admin_required
def reset_student_account(id):
    """Сброс пароля ученической записи — новый временный (11.5)."""
    user = User.query.get_or_404(id)
    if user.role != User.ROLE_STUDENT:
        flash_msg('error', 'Это не учётная запись студента')
        return redirect(url_for('main.accounts'))

    temporary_password = generate_temporary_password()
    user.set_temporary_password(temporary_password)
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        log.exception('Ошибка сброса пароля ученической записи: %s', e)
        flash_msg('error', f'Ошибка сброса пароля: {str(e)}')
        return redirect(url_for('main.accounts'))

    log.warning('Сброшен пароль ученической записи %s (логин %s) — %s',
                user.display_name, user.username, current_user.username)
    log_audit('student_account_reset', user=current_user, entity_type='user',
              entity_id=user.id,
              details=f'логин={user.username!r}, пароль сброшен')
    return render_template('password_issued.html', rows=[{
        'login': user.username, 'name': user.display_name,
        'password': temporary_password,
    }], back_url=url_for('main.accounts'))


@main.route('/accounts/<int:id>/toggle', methods=['POST'])
@admin_required
def toggle_student_account(id):
    """Блокировка и разблокировка ученической записи (11.5).

    Запись не удаляется: у отчисленного студента остаётся история оценок и
    входов, и удаление аккаунта обнулило бы её.
    """
    user = User.query.get_or_404(id)
    if user.role != User.ROLE_STUDENT:
        flash_msg('error', 'Это не учётная запись студента')
        return redirect(url_for('main.accounts'))

    user.is_active = not user.is_active
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        log.exception('Ошибка смены статуса учётной записи: %s', e)
        flash_msg('error', f'Ошибка смены статуса: {str(e)}')
        return redirect(url_for('main.accounts'))

    if user.is_active:
        log.info('Разблокирована учётная запись %s (логин %s) — %s',
                 user.display_name, user.username, current_user.username)
        log_audit('student_account_unblock', user=current_user,
                  entity_type='user', entity_id=user.id,
                  details=f'логин={user.username!r}')
        flash_msg('success', f'Запись {user.username} разблокирована')
    else:
        log.warning('Заблокирована учётная запись %s (логин %s) — %s',
                    user.display_name, user.username, current_user.username)
        log_audit('student_account_block', user=current_user,
                  entity_type='user', entity_id=user.id,
                  details=f'логин={user.username!r}')
        flash_msg('warning', f'Запись {user.username} заблокирована')
    return redirect(url_for('main.accounts'))


@main.route('/accounts/<int:id>/delete', methods=['POST'])
@admin_required
def delete_student_account(id):
    """Удаление ученической записи с сохранением студента (11.5).

    Студент остаётся в базе вместе с оценками: терять историю из-за
    забытого пароля нельзя, учётную запись можно создать заново одной кнопкой.
    """
    user = User.query.get_or_404(id)
    if user.role != User.ROLE_STUDENT:
        flash_msg('error', 'Это не учётная запись студента')
        return redirect(url_for('main.accounts'))

    login_name = user.username
    student = user.student_account
    student_name = student.full_name if student else user.display_name
    try:
        if student is not None:
            student.user_id = None
        db.session.delete(user)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        log.exception('Ошибка удаления учётной записи: %s', e)
        flash_msg('error', f'Ошибка удаления: {str(e)}')
        return redirect(url_for('main.accounts'))

    log.warning('Удалена учётная запись %s (логин %s), студент сохранён — %s',
                student_name, login_name, current_user.username)
    log_audit('student_account_delete', user=current_user, entity_type='user',
              entity_id=id,
              details=f'логин={login_name!r}, студент={student_name!r}')
    flash_msg('success', f'Запись {login_name} удалена, студент '
                         f'{student_name} сохранён')
    return redirect(url_for('main.accounts'))


# ===================== ЛИЧНЫЙ КАБИНЕТ СТУДЕНТА =====================
# Изоляция данных (12.8): все выборки кабинета строятся от current_student(),
# который берёт student_id из сессии. Ни один маршрут ниже не принимает
# student_id или group_id из URL — иначе студент подставил бы чужой id
# в адресную строку и увидел бы чужую успеваемость.

def portal_period_choice(period_id):
    """Период кабинета: из запроса, иначе тот, в который попадает сегодня.

    В отличие от current_period_or_default() не отвечает 404, когда периодов
    в базе нет: кабинет студента должен открыться и сказать, что периоды
    ещё не заведены, а не показывать страницу ошибки админ-раздела.
    """
    if period_id:
        period = db.session.get(AcademicPeriod, period_id)
        if period is not None:
            return period
    today = date.today()
    period = (AcademicPeriod.query
              .filter(AcademicPeriod.start_date <= today,
                      AcademicPeriod.end_date >= today)
              .order_by(AcademicPeriod.sort_order.desc()).first())
    if period:
        return period
    # Дата вне учебного года (лето, каникулы) — показываем последний период
    return (AcademicPeriod.query
            .order_by(AcademicPeriod.academic_year.desc(),
                      AcademicPeriod.sort_order.desc()).first())


def portal_periods(period):
    """Периоды того же учебного года и ближайший следующий (12.2).

    Период из сессии в список не всегда попадает: он может быть годовым или
    относиться к другому году, а список фильтруется по его academic_year.
    """
    if period is None:
        return [], None
    periods_list = (AcademicPeriod.query
                    .filter_by(academic_year=period.academic_year)
                    .order_by(AcademicPeriod.sort_order).all())
    if all(item.id != period.id for item in periods_list):
        periods_list = sorted(periods_list + [period],
                              key=lambda item: item.sort_order)
    index = next((i for i, item in enumerate(periods_list)
                  if item.id == period.id), None)
    following = periods_list[index + 1] if index is not None else None
    return periods_list, following


def portal_subjects(student):
    """Предметы студента: из расписания его группы плюс те, где у него оценки.

    Расписание нужно, чтобы в кабинете был предмет, по которому оценок ещё
    нет: иначе новый студент увидел бы пустую таблицу и решил бы, что ему
    ничего не преподавали. Оценки добавляются отдельно — предмет мог остаться
    в оценках после того, как его убрали из расписания.
    """
    subject_ids = {row[0] for row in
                   (db.session.query(Grade.subject_id)
                    .filter_by(student_id=student.id).distinct().all())}
    if student.group_id:
        subject_ids.update(
            row[0] for row in
            (db.session.query(ScheduleItem.subject_id)
             .filter_by(group_id=student.group_id).distinct().all()))
    if not subject_ids:
        return []
    return (Subject.query
            .filter(Subject.id.in_(subject_ids))
            .order_by(Subject.name).all())


def portal_grades_by_subject(student_id, period, subject_ids):
    """Оценки за период, разложенные по предметам: {subject_id: [Grade]}.

    Пустой subject_ids даёт пустой словарь, а не запрос с пустым IN (): такой
    SQL невалиден, и страница кабинета падала бы на группе без расписания.
    """
    if period is None or not subject_ids:
        return {}
    rows = (Grade.query
            .filter(Grade.student_id == student_id,
                    Grade.period_id == period.id,
                    Grade.subject_id.in_(subject_ids))
            .order_by(Grade.subject_id, Grade.date, Grade.id).all())
    grouped = {}
    for grade in rows:
        grouped.setdefault(grade.subject_id, []).append(grade)
    return grouped


def portal_final_grades(student_id, period, subject_ids):
    """Итоговые оценки за период: {(subject_id): PeriodResult}."""
    if period is None or not subject_ids:
        return {}
    rows = (PeriodResult.query
            .filter(PeriodResult.student_id == student_id,
                    PeriodResult.period_id == period.id,
                    PeriodResult.subject_id.in_(subject_ids)).all())
    return {row.subject_id: row for row in rows}


def portal_own_attendance(student, period):
    """Пропуски самого студента за период со сводкой по причинам (12.6).

    Число пар не берём из AttendanceRecord.hours_count: свойство делает запрос
    на каждую отметку, а отметок за четверть бывает несколько десятков.
    Расписание по дням считается один раз и переиспользуется.
    """
    empty = {'records': [], 'total': 0, 'hours': 0, 'reasons': {}}
    if period is None:
        return empty

    lessons_per_day = {}
    if student.group_id:
        rows = (db.session.query(ScheduleItem.day_of_week,
                                 func.count(ScheduleItem.id))
                .filter_by(group_id=student.group_id)
                .group_by(ScheduleItem.day_of_week).all())
        lessons_per_day = {day: count for day, count in rows}

    records = (AttendanceRecord.query
               .options(joinedload(AttendanceRecord.subject))
               .filter(AttendanceRecord.student_id == student.id,
                       AttendanceRecord.date >= period.start_date,
                       AttendanceRecord.date <= period.end_date)
               .order_by(AttendanceRecord.date.desc(),
                         AttendanceRecord.id.desc()).all())

    reasons = {reason: 0 for reason, _ in attendance_reason_choices()}
    total = 0
    hours = 0
    for record in records:
        total += 1
        # Отметка без предмета — «отсутствовал весь день»: это все пары дня
        hours += 1 if record.subject_id else lessons_per_day.get(
            record.date.weekday() + 1, 0)
        if record.reason in reasons:
            reasons[record.reason] += 1

    return {'records': records, 'total': total, 'hours': hours,
            'reasons': reasons}


def portal_context(period_id=None):
    """Общие данные кабинета: студент из сессии, период, периоды, предметы.

    Возвращает (ctx, failure). failure не None только если период запрошен
    несуществующий — молча подставлять другой период значило бы показать
    студенту не тот период, который он выбрал.
    """
    student = current_student()
    if student is None:
        return {'student': None}, None

    if period_id:
        period = db.session.get(AcademicPeriod, period_id)
        if period is None:
            return None, (render_template(
                'error.html', code=404,
                message='Учебный период не найден.'), 404)
    else:
        period = portal_period_choice(None)

    periods_list, following = portal_periods(period)
    subjects = portal_subjects(student)
    return {
        'student': student,
        'group': student.group,
        'period': period,
        'periods': periods_list,
        'next_period': following,
        'subjects': subjects,
    }, None


@main.route('/portal')
@student_required
def portal():
    """Кабинет студента: сводка по предметам, пропуски, ближайший период (12.2)."""
    ctx, failure = portal_context()
    if failure:
        return failure

    student = ctx['student']
    if student is None:
        return render_template('portal_dashboard.html', **ctx)

    period = ctx['period']
    subject_ids = [subject.id for subject in ctx['subjects']]
    cells = []
    all_grades = []
    if period is not None:
        grouped = portal_grades_by_subject(student.id, period, subject_ids)
        finals = portal_final_grades(student.id, period, subject_ids)
        for subject in ctx['subjects']:
            grades = grouped.get(subject.id, [])
            all_grades.extend(grades)
            result = finals.get(subject.id)
            cells.append({
                'subject': subject,
                'count': len(grades),
                'average': average_grade(grades),
                'grades': grades,
                'final': result.final_value if result else None,
            })

    return render_template(
        'portal_dashboard.html', **ctx,
        cells=cells,
        average=average_grade(all_grades),
        count=len(all_grades),
        attendance=portal_own_attendance(student, period),
        has_periods=bool(ctx['periods']),
    )


@main.route('/portal/grades')
@student_required
def portal_grades():
    """Свои оценки по предметам и периодам; сводная по группе — read-only (12.3–12.5)."""
    ctx, failure = portal_context(request.args.get('period_id', type=int))
    if failure:
        return failure

    student = ctx['student']
    if student is None:
        return render_template('portal_grades.html', **ctx, view='mine',
                               cells=[], rows=[], average=0, count=0,
                               classmates=[], has_periods=False)

    # Режим приходит из URL, но проверяется по белому списку: иначе в шаблон
    # попадёт произвольная строка, а вместе с ней — лишний запрос
    view = request.args.get('view', 'mine')
    if view not in ('mine', 'group'):
        view = 'mine'

    period = ctx['period']
    subject_ids = [subject.id for subject in ctx['subjects']]
    cells = []
    all_grades = []
    if period is not None and view == 'mine':
        grouped = portal_grades_by_subject(student.id, period, subject_ids)
        finals = portal_final_grades(student.id, period, subject_ids)
        for subject in ctx['subjects']:
            grades = grouped.get(subject.id, [])
            all_grades.extend(grades)
            result = finals.get(subject.id)
            cells.append({
                'subject': subject,
                'count': len(grades),
                'average': average_grade(grades),
                'grades': grades,
                'final': result.final_value if result else None,
            })

    # Сводная по группе: только своя группа, только чтение. Среднего балла
    # группы и места в рейтинге здесь нет намеренно (12.4, 12.9) — по среднему
    # баллу группы любой список студентов читается как рейтинг, даже если
    # места не пронумерованы.
    classmates = []
    rows = []
    if view == 'group' and student.group_id and period is not None \
            and subject_ids:
        classmates = (Student.query
                      .filter_by(group_id=student.group_id, status='active')
                      .order_by(Student.last_name, Student.first_name).all())
        if classmates:
            rows_map = {}
            for grade in (Grade.query
                          .filter(Grade.student_id.in_(
                                      [mate.id for mate in classmates]),
                                  Grade.period_id == period.id,
                                  Grade.subject_id.in_(subject_ids))
                          .order_by(Grade.student_id, Grade.subject_id,
                                    Grade.date).all()):
                rows_map.setdefault((grade.student_id, grade.subject_id),
                                    []).append(grade)
            finals_map = {(row.student_id, row.subject_id): row for row in
                          (PeriodResult.query
                           .filter(PeriodResult.student_id.in_(
                                       [mate.id for mate in classmates]),
                                   PeriodResult.period_id == period.id,
                                   PeriodResult.subject_id.in_(subject_ids))
                           .all())}
            for mate in classmates:
                mate_cells = []
                for subject in ctx['subjects']:
                    grades = rows_map.get((mate.id, subject.id), [])
                    result = finals_map.get((mate.id, subject.id))
                    mate_cells.append({
                        'subject': subject,
                        'count': len(grades),
                        'average': average_grade(grades),
                        'grades': grades,
                        'final': result.final_value if result else None,
                    })
                rows.append({'student': mate, 'cells': mate_cells,
                             'is_me': mate.id == student.id})

    return render_template(
        'portal_grades.html', **ctx,
        view=view, cells=cells, rows=rows,
        average=average_grade(all_grades), count=len(all_grades),
        classmates=classmates, has_periods=bool(ctx['periods']),
    )


@main.route('/portal/attendance')
@student_required
def portal_attendance():
    """Свои пропуски с причинами и итогом за период (12.6)."""
    ctx, failure = portal_context(request.args.get('period_id', type=int))
    if failure:
        return failure

    student = ctx['student']
    if student is None:
        return render_template('portal_attendance.html', **ctx, summary=None,
                               has_periods=False)

    return render_template(
        'portal_attendance.html', **ctx,
        summary=portal_own_attendance(student, ctx['period']),
        reasons=attendance_reason_choices(),
        has_periods=bool(ctx['periods']),
    )


@main.route('/my/account')
@login_required
def my_account():
    """Старый адрес кабинета. Оставлен редиректом: закладки и ссылки из
    писем не должны превращаться в 404 после появления /portal."""
    if not current_user.is_student:
        return redirect(url_for('main.dashboard'))
    return redirect(url_for('main.portal'))


@main.route('/my/password', methods=['GET', 'POST'])
@login_required
def change_password():
    """Смена собственного пароля.

    Студенту показывается форма в оформлении кабинета, сотруднику — общая
    страница админ-панели. Логика одна, чтобы правила паролей не расходились.
    """
    form = AccountPasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            flash_msg('error', 'Текущий пароль указан неверно')
        elif form.new_password.data == form.current_password.data:
            # Молча принимать такой пароль нельзя: человек уходит, уверенный,
            # что сменил пароль, а старый продолжает работать. И это же —
            # самый частый пароль, если человек не понял задачу.
            flash_msg('error', 'Новый пароль совпадает с текущим. '
                                'Придумайте другой.')
        else:
            current_user.set_password(form.new_password.data)
            # Свой пароль вместо выданного — значит, человек его запомнил.
            # Список учётных записей перестаёт считать этот пароль временным.
            current_user.password_temporary = False
            db.session.commit()
            log.info('Пользователь %s сменил пароль', current_user.username)
            # Факт смены — да; сам пароль и его хеш — нет (13.4)
            log_audit('password_change_self', user=current_user,
                      entity_type='user', entity_id=current_user.id,
                      details='пароль изменён')
            flash_msg('success', 'Пароль успешно изменён')
            return redirect(request.referrer or home_url())

    if current_user.is_student:
        return render_template('portal_password.html', form=form,
                               student=current_student())
    return render_template('password_form.html', form=form,
                           title='Смена пароля')


# ===================== ИТОГИ, СВОД, ЭКСПОРТ =====================
def results_context(group_id, period_id):
    """Данные страницы итогов: свод по группе и рейтинг (10.2, 10.3).

    Собираем всё одним проходом по таблице оценок: отдельный запрос на
    каждый предмет и на каждого студента превратил бы страницу в десятки
    SELECT, а считать в шаблоне нельзя — там будет запрос на каждую ячейку.
    """
    groups_list = Group.query.order_by(Group.name).all()
    if not groups_list:
        return None, (render_template(
            'error.html', code=404,
            message='Сначала создайте хотя бы одну группу.'), 404)
    if group_id not in [g.id for g in groups_list]:
        group_id = groups_list[0].id
    group = next(g for g in groups_list if g.id == group_id)

    period, failure = current_period_or_default(period_id)
    if failure:
        return None, failure
    periods_list = (AcademicPeriod.query
                    .filter_by(academic_year=period.academic_year)
                    .order_by(AcademicPeriod.sort_order).all())

    students = (Student.query
                .filter_by(group_id=group.id, status='active')
                .order_by(Student.last_name, Student.first_name).all())

    # Предметы — те, что стоят в расписании группы: итог по предмету без
    # пар в группе не имеет смысла
    subject_ids = [row[0] for row in
                   (db.session.query(ScheduleItem.subject_id)
                    .filter_by(group_id=group.id).distinct().all())]
    subjects = []
    if subject_ids:
        subjects = (Subject.query
                    .filter(Subject.id.in_(subject_ids))
                    .order_by(Subject.name).all())
    subject_by_id = {s.id: s for s in subjects}
    editable = {s.id for s in subjects if current_user.teaches(s)}

    grades = []
    results = {}
    attendance = {}
    if students:
        grades = (Grade.query
                  .filter(Grade.student_id.in_([s.id for s in students]),
                          Grade.period_id == period.id)
                  .order_by(Grade.subject_id, Grade.date).all())
        results = {(row.student_id, row.subject_id): row for row in
                   (PeriodResult.query
                    .filter(PeriodResult.student_id.in_(
                        [s.id for s in students]),
                        PeriodResult.period_id == period.id,
                        PeriodResult.subject_id.in_(subject_ids or [-1]))
                    .all())}
        records = (AttendanceRecord.query
                   .options(joinedload(AttendanceRecord.subject))
                   .filter(AttendanceRecord.student_id.in_([s.id for s in students]),
                           AttendanceRecord.date >= period.start_date,
                           AttendanceRecord.date <= period.end_date).all())
        for record in records:
            # Отметка без предмета — «отсутствовал весь день»: в свод по
            # предмету она не попадает, иначе одна запись размазывалась бы
            # по всем предметам дня и портила число пропусков у каждого
            if record.subject_id is None:
                continue
            attendance.setdefault((record.student_id, record.subject_id), []).append(record)

    by_student_subject = {}
    for grade in grades:
        by_student_subject.setdefault(
            (grade.student_id, grade.subject_id), []).append(grade)

    rows = []
    for student in students:
        cells = []
        all_values = []
        for subject in subjects:
            subject_grades = by_student_subject.get((student.id, subject.id), [])
            all_values.extend(subject_grades)
            result = results.get((student.id, subject.id))
            cells.append({
                'subject': subject,
                'count': len(subject_grades),
                'average': average_grade(subject_grades),
                'recommended': recommend_final_grade(subject_grades),
                'final': result.final_value if result else None,
                'result': result,
                'absences': len(attendance.get((student.id, subject.id), [])),
                'can_edit': subject.id in editable,
            })
        stats = grade_quality_stats(all_values)
        rows.append({
            'student': student,
            'cells': cells,
            'average': average_grade(all_values),
            'count': stats['count'],
            'quality': stats['quality'],
            'progress': stats['progress'],
            'absences': sum(c['absences'] for c in cells),
        })

    # Рейтинг: по среднему, при равенстве — по пропускам и по фамилии.
    # Сортировка по фамилии последней, иначе список «прыгал» бы при пересчёте
    # средних у нескольких студентов с одинаковым баллом.
    rating = sorted(rows, key=lambda row: (-row['average'], row['absences'],
                                           row['student'].last_name))
    for position, row in enumerate(rating, start=1):
        row['place'] = position

    return {
        'groups': groups_list,
        'group': group,
        'period': period,
        'periods': periods_list,
        'students': students,
        'subjects': subjects,
        'subject_by_id': subject_by_id,
        'rows': rows,
        'rating': rating,
        'can_set_results': current_user.is_admin,
    }, None


@main.route('/results')
@staff_required
def results():
    """Свод по группе за период и рейтинг (10.2, 10.3)."""
    ctx, failure = results_context(request.args.get('group_id', type=int),
                                   request.args.get('period_id', type=int))
    if failure:
        return failure

    form = PeriodResultForm()
    return render_template('results.html', form=form, **ctx)


@main.route('/results/final', methods=['POST'])
@staff_required
def results_set_final():
    """Поставить или снять итоговую оценку (10.1)."""
    form = PeriodResultForm()
    if not form.validate_on_submit():
        flash_form_errors(form, RESULT_FIELD_LABELS)
        return redirect(url_for('main.results', group_id=request.form.get('group_id'),
                                period_id=form.period_id.data))

    subject, failure = teacher_owns_subject(form.subject_id.data)
    if failure:
        return failure

    student = db.session.get(Student, form.student_id.data)
    period = db.session.get(AcademicPeriod, form.period_id.data)
    if student is None or period is None:
        flash_msg('error', 'Студент или период не найден')
        return redirect(url_for('main.results'))

    # Итог выставляется только по тому предмету, который реально преподавали
    # этой группе: иначе в ведомость попадёт оценка по несуществующей паре
    taught = (db.session.query(ScheduleItem.id)
              .filter(ScheduleItem.group_id == student.group_id,
                      ScheduleItem.subject_id == subject.id)
              .first())
    if taught is None:
        flash_msg('error',
                  f'Предмет «{subject.name}» не входит в расписание группы '
                  f'{student.group.name if student.group else ""}')
        log.warning('Отклонён итог: %s не преподаётся группе %s — %s',
                    subject.name, student.group_id, current_user.username)
        return redirect(url_for('main.results', group_id=student.group_id,
                                period_id=period.id))

    result = PeriodResult.query.filter_by(
        student_id=student.id, subject_id=subject.id,
        period_id=period.id).first()
    value = (form.final_value.data or '').strip().lower()

    # Пустое значение означает «снять итог». Проверка обоснования на этом
    # шаге ещё не применялась, и это правильно: снятие не требует
    # объяснения, объяснение нужно только чтобы поставить чужую оценку.
    if not value:
        if result is not None:
            db.session.delete(result)
            db.session.commit()
            log.info('Итог снят: %s, %s, %s — %s', student.full_name,
                     subject.name, period.name, current_user.username)
            log_audit('result_remove', user=current_user,
                      entity_type='period_result', entity_id=result.id,
                      details=f'студент={student.full_name!r}, '
                              f'предмет={subject.name!r}, период={period.name!r}')
        flash_msg('success', 'Итоговая оценка снята')
        return redirect(url_for('main.results', group_id=student.group_id,
                                period_id=period.id))

    # Рекомендация считается заново на сервере: значение из формы не
    # доверяем, иначе можно было бы подсунуть «рекомендацию 5» и обойти
    # требование обоснования
    grades = (Grade.query
              .filter(Grade.student_id == student.id,
                      Grade.subject_id == subject.id,
                      Grade.period_id == period.id).all())
    recommended = recommend_final_grade(grades)
    justification = (form.justification.data or '').strip() or None
    if value != recommended and not justification:
        flash_msg('error',
                  f'Оценка «{value}» отличается от рекомендованной '
                  f'«{recommended}» — нужно обоснование')
        return redirect(url_for('main.results', group_id=student.group_id,
                                period_id=period.id))

    if result is None:
        result = PeriodResult(student_id=student.id, subject_id=subject.id,
                              period_id=period.id, created_by=current_user.id)
        db.session.add(result)
        is_new = True
    else:
        is_new = False
    result.final_value = value
    result.justification = justification
    db.session.commit()

    note = f' (рекомендация была «{recommended}»)' if value != recommended else ''
    log.info('Итог %s: %s, %s, %s%s — %s', 'поставлен' if is_new else 'изменён',
             student.full_name, subject.name, value, note, current_user.username)
    # Обоснование в аудит дублируем: по нему видно, чем оценка отличается
    # от рекомендованной, а в самой таблице PeriodResult его легко потерять
    details = (f'студент={student.full_name!r}, '
               f'предмет={subject.name!r}, период={period.name!r}, '
               f'итог={value!r}, рекомендация={recommended!r}, '
               f'обоснование={justification!r}')
    if is_new:
        log_audit('result_set', user=current_user, entity_type='period_result',
                  entity_id=result.id, details=details)
    else:
        log_audit('result_edit', user=current_user, entity_type='period_result',
                  entity_id=result.id, details=details)
    flash_msg('success',
              f'{student.full_name}, {subject.name}: итог {value}{note}')
    return redirect(url_for('main.results', group_id=student.group_id,
                            period_id=period.id))


@main.route('/results/export')
@staff_required
def results_export():
    """Выгрузка свода по группе в Excel или CSV."""
    fmt = (request.args.get('fmt') or 'xlsx').strip().lower()
    if fmt not in ('xlsx', 'csv'):
        fmt = 'xlsx'

    ctx, failure = results_context(request.args.get('group_id', type=int),
                                   request.args.get('period_id', type=int))
    if failure:
        return failure
    if not ctx['rows']:
        flash_msg('error', 'Нечего выгружать: в группе нет активных студентов')
        return redirect(url_for('main.results', group_id=ctx['group'].id,
                                period_id=ctx['period'].id))

    # Плоский формат: одна строка — пара «студент + предмет». Такой файл
    # годится и для 1С, и для обычной таблицы, где свод читают глазами.
    header = ['Студент', 'Номер зачетки', 'Группа', 'Предмет', 'Оценок',
              'Средний', 'Рекомендованный итог', 'Итог', 'Пропуски']
    data = []
    for row in ctx['rows']:
        for cell in row['cells']:
            data.append([
                row['student'].full_name,
                row['student'].student_id,
                ctx['group'].name,
                cell['subject'].name,
                cell['count'],
                cell['average'] or '',
                cell['recommended'],
                cell['final'] or '',
                cell['absences'],
            ])

    # Рейтинг — вторым листом: в своде по предметам его нет, а сравнивать
    # студентов между собой обычно именно для этого.
    rating_header = ['Место', 'Студент', 'Номер зачетки', 'Средний', 'Оценок',
                     'Пропуски', 'Качество, %', 'Успеваемость, %']
    rating_data = [[row['place'], row['student'].full_name,
                    row['student'].student_id, row['average'] or '',
                    row['count'], row['absences'], row['quality'],
                    row['progress']] for row in ctx['rating']]

    prefix = f'свод_{ctx["group"].name}_{ctx["period"].name}'
    sheets = [(ctx['group'].name, [header] + data)]
    if fmt == 'xlsx':
        sheets.append(('Рейтинг', [rating_header] + rating_data))

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe = sanitize_filename(prefix)
    os.makedirs(EXPORT_DIR, exist_ok=True)
    filepath = os.path.join(EXPORT_DIR, f'{safe}_{timestamp}.{fmt}')
    save_export(filepath, sheets)

    log.info('Экспорт свода %s, %s (%s) — %s', ctx['group'].name,
             ctx['period'].name, fmt, current_user.username)
    return send_file(filepath, as_attachment=True)


# ===================== ОТЧЕТЫ =====================
@main.route('/reports')
@staff_required
def reports():
    groups_list = Group.query.order_by(Group.name).all()
    return render_template('reports.html', groups=groups_list,
                           student_counts=group_student_counts(groups_list),
                           total_grades=db.session.query(
                               db.func.count(Grade.id)).scalar() or 0,
                           subject_count=Subject.query.count())

@main.route('/reports/generate', methods=['POST'])
@staff_required
def generate_report():
    report_type = request.form.get('report_type')
    group_id = request.form.get('group_id')
    start_date = request.form.get('start_date')
    end_date = request.form.get('end_date')
    try:
        if report_type == 'students':
            query = Student.query
            if group_id:
                query = query.filter_by(group_id=group_id)
            
            data = [{
                'Номер зачетки': s.student_id,
                'ФИО': s.full_name,
                'Группа': s.group.name if s.group else '',
                'Дата рождения': format_date(s.birth_date),
                'Email': s.email or '',
                'Телефон': s.phone or '',
                'Статус': s.status
            } for s in query.all()]
            
            filepath = export_to_excel(data, 'students_report')
            log.info('Экспорт отчёта: студенты (%s строк) — %s',
                     len(data), current_user.username)
            # Выгрузка уводит персональные данные из системы в файл, который
            # потом гуляет по почте: в журнал — вид отчёта и число строк,
            # но не сами ФИО
            log_audit('report_export', user=current_user, entity_type='report',
                      entity_id='students',
                      details=f'вид=студенты, строк={len(data)}, '
                              f'группа={group_id!r}')
            return send_file(filepath, as_attachment=True)
        
        elif report_type == 'grades':
            query = Grade.query
            
            if group_id:
                query = query.join(Student).filter(Student.group_id == group_id)
            
            if start_date:
                query = query.filter(Grade.date >= datetime.strptime(start_date, '%Y-%m-%d').date())
            
            if end_date:
                query = query.filter(Grade.date <= datetime.strptime(end_date, '%Y-%m-%d').date())
            
            data = [{
                'Студент': g.student.full_name if g.student else '',
                'Группа': g.student.group.name if g.student and g.student.group else '',
                'Предмет': g.subject.name if g.subject else '',
                'Оценка': g.grade_value,
                'Тип оценки': g.grade_type,
                'Дата': format_date(g.date),
                'Комментарий': g.comments or ''
            } for g in query.all()]
            
            filepath = export_to_excel(data, 'grades_report')
            log.info('Экспорт отчёта: оценки (%s строк) — %s',
                     len(data), current_user.username)
            log_audit('report_export', user=current_user, entity_type='report',
                      entity_id='grades',
                      details=f'вид=оценки, строк={len(data)}, группа={group_id!r}, '
                              f'период={start_date!r}..{end_date!r}')
            return send_file(filepath, as_attachment=True)
        
        flash_msg('error', 'Неверный тип отчета')
        return redirect(url_for('main.reports'))
        
    except Exception as e:
        log.exception('Ошибка генерации отчёта: %s', e)
        flash_msg('error', f'Ошибка генерации отчета: {str(e)}')
        return redirect(url_for('main.reports'))

@main.route('/reports/students')
@staff_required
def report_students():
    """Экспорт всех студентов"""
    try:
        students = Student.query.all()
        data = []
        for student in students:
            data.append({
                'Номер зачетки': student.student_id,
                'ФИО': student.full_name,
                'Группа': student.group.name if student.group else '',
                'Дата рождения': format_date(student.birth_date),
                'Email': student.email or '',
                'Телефон': student.phone or '',
                'Статус': student.status
            })
        filepath = export_to_excel(data, 'students_report')
        log.info('Экспорт всех студентов (%s строк) — %s', len(data), current_user.username)
        log_audit('report_export', user=current_user, entity_type='report',
                  entity_id='students', details=f'вид=все студенты, строк={len(data)}')
        return send_file(filepath, as_attachment=True)
    except Exception as e:
        log.exception('Ошибка экспорта студентов: %s', e)
        flash_msg('error', f'Ошибка генерации отчета: {str(e)}')
        return redirect(url_for('main.reports'))

@main.route('/reports/group/<int:group_id>')
@staff_required
def report_group(group_id):
    """Экспорт студентов группы"""
    try:
        group = Group.query.get_or_404(group_id)
        students = Student.query.filter_by(group_id=group_id).all()
        data = []
        for student in students:
            data.append({
                'Номер зачетки': student.student_id,
                'ФИО': student.full_name,
                'Группа': group.name,
                'Дата рождения': format_date(student.birth_date),
                'Email': student.email or '',
                'Телефон': student.phone or '',
                'Статус': student.status
            })
        
        filepath = export_to_excel(data, f'group_{group.name}_report')
        log.info('Экспорт группы «%s» (%s строк) — %s',
                 group.name, len(data), current_user.username)
        log_audit('report_export', user=current_user, entity_type='report',
                  entity_id=f'group_{group.id}',
                  details=f'вид=студенты группы, строк={len(data)}, '
                          f'группа={group.name!r}')
        return send_file(filepath, as_attachment=True)
    except Exception as e:
        flash_msg('error', f'Ошибка генерации отчета: {str(e)}')
        return redirect(url_for('main.reports'))

# ===================== НАСТРОЙКИ =====================
@main.route('/settings', methods=['GET', 'POST'])
@admin_required
def settings():
    """Настройки системы: сохранение, смена пароля, сброс к умолчаниям"""
    current = get_settings()
    form = SettingsForm(obj=current)

    # Сброс обрабатываем до validate_on_submit: сброшенная форма невалидна,
    # потому что поля основных настроек обязательны
    if form.reset.data and request.method == 'POST':
        SystemSettings.reset_to_default()
        log.warning('Настройки сброшены к значениям по умолчанию — %s', current_user.username)
        flash_msg('success', 'Настройки сброшены к значениям по умолчанию')
        return redirect(url_for('main.settings'))

    if form.validate_on_submit():
        changed = []
        for field in ('college_name', 'academic_year', 'max_students_per_group',
                      'period_kind',
                      'theme_color', 'items_per_page', 'export_format',
                      'auto_backup', 'backup_frequency',
                      'enable_system_notifications', 'enable_email_notifications'):
            new_value = getattr(form, field).data
            if getattr(current, field) != new_value:
                changed.append(field)
                setattr(current, field, new_value)

        if form.new_password.data:
            current_user.set_password(form.new_password.data)
            changed.append('пароль')

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            log.exception('Ошибка сохранения настроек: %s', e)
            flash_msg('error', f'Не удалось сохранить настройки: {e}')
            return render_template('settings.html', form=form, settings=current,
                                   **_settings_stats())

        if changed:
            log.info('Настройки изменены (%s) — %s', ', '.join(changed), current_user.username)
            # Список изменённых полей, не их значения: в настройках есть
            # и такие, что меняют поведение для всех сразу
            log_audit('settings_update', user=current_user,
                      entity_type='settings', entity_id=current.id,
                      details=f'поля={changed!r}')
            message = 'Настройки сохранены'
            if 'пароль' in changed:
                message += ', пароль обновлён'
            flash_msg('success', message)
        else:
            flash_msg('info', 'Изменений не было')
        return redirect(url_for('main.settings'))

    if form.errors:
        log.warning('Форма настроек не прошла валидацию: %s', ', '.join(form.errors))
        flash_msg('error', 'Проверьте правильность заполнения полей')

    return render_template('settings.html', form=form, settings=current,
                           **_settings_stats())


def _settings_stats():
    """Счётчики для блока «Сведения» на странице настроек."""
    return {'stats': {
        'students': Student.query.count(),
        'groups': Group.query.count(),
        'subjects': Subject.query.count(),
    }}

# ===================== РЕЗЕРВНОЕ КОПИРОВАНИЕ =====================
@main.route('/settings/backup', methods=['GET', 'POST'])
@admin_required
def settings_backup():
    """Страница управления резервными копиями"""
    form = BackupForm()
    backups = list_backups()

    if form.validate_on_submit():
        try:
            filename = create_backup(description=form.description.data or '')
            log.info('Создана резервная копия «%s» — %s',
                     filename, current_user.username)
            log_audit('backup_create', user=current_user, entity_type='backup',
                      details=f'файл={filename!r}, '
                              f'описание={form.description.data!r}')
            flash_msg('success', f'Резервная копия «{filename}» создана')
            return redirect(url_for('main.settings_backup'))
        except Exception as e:
            log.exception('Ошибка создания резервной копии: %s', e)
            flash_msg('error', f'Ошибка создания резервной копии: {str(e)}')
            return redirect(url_for('main.settings_backup'))

    return render_template('settings_backup.html', form=form, backups=backups)

@main.route('/settings/backup/<filename>/download')
@admin_required
def download_backup_file(filename):
    """Скачивание резервной копии"""
    path = os.path.join(get_backup_dir(), os.path.basename(sanitize_filename(filename)))
    if not os.path.isfile(path):
        log.warning('Скачивание несуществующей копии «%s» — %s', filename, current_user.username)
        flash_msg('error', 'Файл резервной копии не найден')
        return redirect(url_for('main.settings_backup'))
    log.info('Скачана резервная копия «%s» — %s', filename, current_user.username)
    log_audit('backup_download', user=current_user, entity_type='backup',
              details=f'файл={filename!r}')
    return send_file(path, as_attachment=True)

@main.route('/settings/backup/<filename>/restore', methods=['POST'])
@admin_required
def restore_backup_file(filename):
    """Восстановление базы данных из резервной копии"""
    path = os.path.join(get_backup_dir(), os.path.basename(sanitize_filename(filename)))
    if not os.path.isfile(path):
        log.warning('Восстановление несуществующей копии «%s» — %s', filename, current_user.username)
        flash_msg('error', 'Файл резервной копии не найден')
        return redirect(url_for('main.settings_backup'))

    try:
        restore_backup(path)
        log.warning('База восстановлена из копии «%s» — %s', filename, current_user.username)
        # Восстановление заменяет college.db целиком, вместе с таблицей
        # audit_log. Запись о нём делается после восстановления — иначе она
        # исчезла бы вместе с той базой, откуда её записали.
        log_audit('backup_restore', user=current_user, entity_type='backup',
                  details=f'файл={filename!r}')
        flash_msg('success', 'Данные восстановлены из резервной копии')
    except Exception as e:
        log.exception('Ошибка восстановления из «%s»: %s', filename, e)
        flash_msg('error', f'Ошибка восстановления: {str(e)}')

    return redirect(url_for('main.settings_backup'))

@main.route('/settings/backup/<filename>/delete', methods=['POST'])
@admin_required
def delete_backup_file(filename):
    """Удаление файла резервной копии"""
    if delete_backup(filename):
        log.warning('Удалена резервная копия «%s» — %s', filename, current_user.username)
        log_audit('backup_delete', user=current_user, entity_type='backup',
                  details=f'файл={filename!r}')
        flash_msg('success', 'Резервная копия удалена')
    else:
        log.warning('Не удалось удалить резервную копию «%s» — %s', filename, current_user.username)
        flash_msg('error', 'Не удалось удалить резервную копию')
    return redirect(url_for('main.settings_backup'))

# ===================== ИМПОРТ ДАННЫХ =====================
@main.route('/settings/import', methods=['GET', 'POST'])
@admin_required
def settings_import():
    """Страница импорта данных из Excel/CSV"""
    form = ImportForm()
    result = None

    if form.validate_on_submit():
        file = form.file.data
        if file and file.filename:
            import_type = form.import_type.data
            import_mode = form.import_mode.data
            try:
                result = import_from_file(file, import_type=import_type,
                                          import_mode=import_mode)
                log.info('Импорт выполнен: тип %s, режим %s, записей %s, '
                         'пропущено %s — %s',
                         import_type, import_mode, result.get('count'),
                         result.get('skipped', 0), current_user.username)
                # Имя файла в аудит не пишем: в нём может быть ФИО
                log_audit('import', user=current_user,
                          entity_type=import_type,
                          details=f'режим={import_mode!r}, '
                                  f'записей={result.get("count")}, '
                                  f'пропущено={result.get("skipped", 0)}, '
                                  f'ошибок={result.get("errors_total", 0)}')
                if result.get('errors_total'):
                    # Часть строк не прошла проверку — отчёт показываем
                    # на странице, а не теряем среди прочих flash-сообщений
                    result['fatal'] = None
                    flash_msg('warning', result['message'])
                else:
                    flash_msg('success', result['message'])
            except ValueError as e:
                # Ошибки самого файла: показываем на странице, чтобы можно
                # было понять, что исправлять
                log.warning('Файл импорта отклонён (%s): %s',
                            import_type, e)
                db.session.rollback()
                result = {'type': import_type, 'mode': import_mode, 'count': 0,
                          'skipped': 0, 'columns': [],
                          'errors': [str(e)], 'errors_total': 1,
                          'fatal': str(e),
                          'message': f'Импорт не выполнен: {e}'}
                flash_msg('error', f'Импорт не выполнен: {e}')
            except Exception as e:
                log.exception('Ошибка импорта: %s', e)
                db.session.rollback()
                flash_msg('error', f'Ошибка импорта: {str(e)}')
        else:
            log.warning('Импорт без выбранного файла — %s', current_user.username)
            flash_msg('error', 'Выберите файл для импорта')

    return render_template('settings_import.html', form=form, result=result)


@main.route('/settings/export-template/<import_type>')
@admin_required
def export_import_template(import_type):
    """Скачивание .xlsx-шаблона с правильными названиями столбцов"""
    try:
        filepath, filename = build_import_template(import_type)
    except ValueError as e:
        log.warning('Запрошен неизвестный шаблон импорта: %s', import_type)
        flash_msg('error', str(e))
        return redirect(url_for('main.settings_import'))

    log.info('Скачан шаблон импорта «%s» — %s', filename, current_user.username)
    return send_file(filepath, as_attachment=True,
                     download_name=filename)

# ===================== ЖУРНАЛ СОБЫТИЙ =====================
LOG_LEVELS = ('INFO', 'WARNING', 'ERROR', 'DEBUG')
LOG_PAGE_SIZE = 200


@main.route('/settings/logs')
@admin_required
def view_logs():
    """Просмотр журнала событий с фильтрацией по уровню и поиском"""
    raw_levels = request.args.getlist('level') or list(LOG_LEVELS[:3])
    levels = [lvl for lvl in LOG_LEVELS if lvl in raw_levels] or list(LOG_LEVELS)

    search = (request.args.get('q') or '').strip()[:200]
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (TypeError, ValueError):
        page = 1

    filtered = read_log_lines(limit=0, level=levels,
                              search=search or None)
    total = len(filtered)
    pages = max(1, (total + LOG_PAGE_SIZE - 1) // LOG_PAGE_SIZE)
    page = min(page, pages)
    offset = (page - 1) * LOG_PAGE_SIZE
    logs = filtered[offset:offset + LOG_PAGE_SIZE]

    return render_template(
        'view_logs.html', logs=logs, levels=levels, all_levels=list(LOG_LEVELS),
        search=search, page=page, pages=pages, total=total,
        clear_form=ClearLogsForm(),
        from_index=offset + 1 if total else 0,
        to_index=offset + len(logs))


@main.route('/api/system/download-logs')
@admin_required
def download_logs():
    """Скачивание текущего журнала как .txt"""
    path = get_log_file_path()
    if not os.path.isfile(path):
        flash_msg('error', 'Файл журнала ещё не создан')
        return redirect(url_for('main.view_logs'))

    log.info('Скачан журнал событий — %s', current_user.username)
    log_audit('logs_download', user=current_user, entity_type='log_file',
              details=f'файл={os.path.basename(path)!r}')
    return send_file(path, as_attachment=True, mimetype='text/plain',
                     download_name=os.path.basename(path))


@main.route('/api/system/clear-logs', methods=['POST'])
@admin_required
def clear_logs():
    """Очистка журнала.

    Глобального CSRFProtect в проекте нет, поэтому токен проверяет форма:
    вызывающий обязан передать csrf_token в теле или в заголовке X-CSRFToken.
    """
    form = ClearLogsForm()
    # validate(), а не validate_on_submit(): эндпоинт вызывается из JS без
    # кнопки отправки, поэтому поля submit в теле запроса нет
    if not form.validate():
        log.warning('Очистка журнала отклонена: недействительный CSRF-токен — %s',
                    current_user.username)
        return {'success': False, 'error': 'Недействительный CSRF-токен'}, 400

    if not clear_log_file():
        log.warning('Попытка очистить несуществующий журнал — %s',
                    current_user.username)
        return {'success': False, 'error': 'Файл журнала ещё не создан'}, 404

    # Запись после очистки: сам факт очистки тоже должен попасть в журнал
    log.warning('Журнал событий очищен вручную — %s', current_user.username)
    # Аудит переживает очистку app.log: он лежит в базе, а не в файле
    log_audit('logs_clear', user=current_user, entity_type='log_file',
              details='файл журнала очищен')
    return {'success': True}


# ===================== ЖУРНАЛ ДЕЙСТВИЙ (АУДИТ) =====================
AUDIT_PAGE_SIZE = 50


def _audit_date_arg(name):
    """Дата из строки запроса в формате YYYY-MM-DD, иначе None.

    Некорректную дату молча игнорируем, а не показываем ошибку: фильтр в
    журнале не должен ломать страницу из-за опечатки в адресной строке.
    """
    raw = (request.args.get(name) or '').strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date()
    except ValueError:
        return None


@main.route('/audit')
@staff_required
def audit_logs():
    """Журнал действий пользователей (13.3).

    Администратор видит все записи, преподаватель — только свои: чужую
    выгрузку оценок или сброс чужого пароля ему знать не нужно, а личность
    остальных сотрудников — тоже. Студент к журналу не допускается вовсе
    (staff_required отдаёт ему 403), и в списке записанных действий для него
    есть только вход, выход и смена собственного пароля.

    Записи не удаляются: это единственный след, когда учётная запись
    нарушителя уже стёрта.
    """
    query = AuditLog.query

    if not current_user.is_admin:
        # Фильтр по автору на сервере, а не «прячем чужие строки в шаблоне»:
        # иначе количество записей в пагинации показывало бы чужое
        query = query.filter(AuditLog.user_id == current_user.id)

    action = (request.args.get('action') or '').strip()
    if action:
        query = query.filter(AuditLog.action == action)

    entity = (request.args.get('entity') or '').strip()
    if entity:
        query = query.filter(AuditLog.entity_type == entity)

    username = (request.args.get('user') or '').strip()
    if username:
        query = query.filter(AuditLog.username.ilike(f'%{username}%'))

    date_from = _audit_date_arg('date_from')
    if date_from:
        query = query.filter(AuditLog.timestamp >= datetime.combine(
            date_from, datetime.min.time()))
    date_to = _audit_date_arg('date_to')
    if date_to:
        # Конец дня включительно: без этого «по 15.03» терял бы записи
        # самой даты после 00:00
        query = query.filter(AuditLog.timestamp <= datetime.combine(
            date_to, datetime.max.time()))

    search = (request.args.get('q') or '').strip()[:200]
    if search:
        pattern = f'%{search}%'
        query = query.filter(or_(AuditLog.details.ilike(pattern),
                                AuditLog.username.ilike(pattern),
                                AuditLog.ip_address.ilike(pattern)))

    page = request.args.get('page', 1, type=int) or 1
    entries = (query.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
               .paginate(page=max(1, page), per_page=AUDIT_PAGE_SIZE,
                         error_out=False))

    # Списки для фильтров — из того же множества записей, которое видит
    # пользователь: иначе в выпадающем списке были бы чужие значения
    scope = (AuditLog.query.filter(AuditLog.user_id == current_user.id)
             if not current_user.is_admin else AuditLog.query)
    actions = sorted({row[0] for row in
                      scope.with_entities(AuditLog.action).distinct()
                      .order_by(AuditLog.action).all()})
    entities = sorted({row[0] for row in
                       scope.with_entities(AuditLog.entity_type).distinct()
                       .order_by(AuditLog.entity_type).all()
                       if row[0]})

    return render_template(
        'audit_logs.html', entries=entries, actions=actions, entities=entities,
        action=action, entity=entity, username=username, search=search,
        date_from=date_from, date_to=date_to,
        date_from_raw=request.args.get('date_from', ''),
        date_to_raw=request.args.get('date_to', ''),
        labels=AuditLog.ACTION_LABELS, entity_labels=AuditLog.ENTITY_LABELS,
        is_admin=current_user.is_admin)

# ===================== API =====================
@main.route('/health')
def health_check():
    return {'status': 'ok', 'timestamp': datetime.now().isoformat()}
