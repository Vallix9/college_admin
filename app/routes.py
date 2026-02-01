from flask import Blueprint, render_template, redirect, url_for, flash, request, send_file, session, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.urls import url_parse
from sqlalchemy.exc import IntegrityError
from datetime import datetime, date
from sqlalchemy import or_, func, desc
import functools
import traceback
import os
import json
import secrets

from app.init_ import db
from app.models import User, Student, Group, Subject, Grade, SystemSettings
from app.forms import LoginForm, StudentForm, GroupForm, GradeForm, SubjectForm, SettingsForm, BackupForm, ImportForm
from app.utils import export_to_excel, format_date, create_backup, restore_backup, import_from_file

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

def get_pagination_args():
    return {
        'page': request.args.get('page', 1, type=int),
        'per_page': get_settings().items_per_page or 20
    }

def apply_filters(query, model):
    filters = {}
    
    if 'search' in request.args:
        search = request.args.get('search', '')
        if search:
            search_term = f'%{search}%'
            if model == Student:
                query = query.filter(or_(
                    Student.last_name.ilike(search_term),
                    Student.first_name.ilike(search_term),
                    Student.student_id.ilike(search_term)
                ))
    
    if 'group' in request.args and request.args.get('group') != 'all':
        group_id = request.args.get('group')
        query = query.filter_by(group_id=group_id)
        filters['group'] = group_id
    
    if 'status' in request.args and request.args.get('status') != 'all':
        status = request.args.get('status')
        query = query.filter_by(status=status)
        filters['status'] = status
    
    return query, filters

# ===================== ДЕКОРАТОРЫ =====================
def handle_db_exceptions(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except IntegrityError:
            db.session.rollback()
            flash_msg('error', 'Запись с такими данными уже существует')
            return None
        except Exception as e:
            db.session.rollback()
            flash_msg('error', f'Ошибка операции: {str(e)}')
            return None
    return wrapper

# ===================== АУТЕНТИФИКАЦИЯ =====================
@main.route('/')
@main.route('/dashboard')
@login_required
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
        recent_grades = Grade.query.order_by(desc(Grade.created_at)).limit(10).all()
        
        return render_template('dashboard.html', 
                             stats=stats, 
                             recent_students=recent_students, 
                             recent_grades=recent_grades,
                             settings=settings)
    except Exception as e:
        flash_msg('error', f'Ошибка загрузки дашборда: {str(e)}')
        return render_template('dashboard.html', stats={}, recent_students=[], recent_grades=[])

@main.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        
        if user and user.check_password(form.password.data):
            login_user(user, remember=form.remember_me.data)
            next_page = request.args.get('next')
            
            if not next_page or url_parse(next_page).netloc != '':
                next_page = url_for('main.dashboard')
            
            flash_msg('success', f'Добро пожаловать, {user.username}!')
            return redirect(next_page)
        
        flash_msg('error', 'Неверное имя пользователя или пароль')
    
    return render_template('login.html', form=form)

@main.route('/logout')
@login_required
def logout():
    logout_user()
    flash_msg('success', 'Вы успешно вышли из системы')
    return redirect(url_for('main.login'))

# ===================== СТУДЕНТЫ =====================
@main.route('/students')
@login_required
def students():
    query = Student.query
    query, filters = apply_filters(query, Student)
    
    page_args = get_pagination_args()
    students_paginated = query.order_by(Student.last_name).paginate(
        page=page_args['page'], per_page=page_args['per_page'], error_out=False)
    
    return render_template('students.html', 
                         students=students_paginated,
                         groups=Group.query.all(),
                         current_filters=filters)

@main.route('/students/add', methods=['GET', 'POST'])
@login_required
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
            flash_msg('success', f'Студент {student.full_name} успешно добавлен')
            return redirect(url_for('main.students'))
        except IntegrityError:
            db.session.rollback()
            flash_msg('error', 'Запись с такими данными уже существует')
            return None
        except Exception as e:
            db.session.rollback()
            flash_msg('error', f'Ошибка добавления студента: {str(e)}')
            return None
    
    return render_template('student_form.html', form=form, title='Добавить студента', student=None)

@main.route('/students/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_student(id):
    student = Student.query.get_or_404(id)
    form = StudentForm(obj=student)
    form.group_id.choices = [(0, 'Без группы')] + [(g.id, g.name) for g in Group.query.all()]
    
    if form.validate_on_submit():
        try:
            form.populate_obj(student)
            student.group_id = form.group_id.data if form.group_id.data != 0 else None
            db.session.commit()
            flash_msg('success', f'Данные студента {student.full_name} обновлены')
            return redirect(url_for('main.students'))
        except IntegrityError:
            db.session.rollback()
            flash_msg('error', 'Запись с такими данными уже существует')
            return None
        except Exception as e:
            db.session.rollback()
            flash_msg('error', f'Ошибка обновления студента: {str(e)}')
            return None
    
    return render_template('student_form.html', form=form, title='Редактировать студента', student=student)

@main.route('/students/<int:id>/delete', methods=['POST'])
@login_required
def delete_student(id):
    student = Student.query.get_or_404(id)
    try:
        db.session.delete(student)
        db.session.commit()
        flash_msg('success', f'Студент {student.full_name} удален')
    except Exception as e:
        db.session.rollback()
        flash_msg('error', f'Ошибка удаления студента: {str(e)}')
    
    return redirect(url_for('main.students'))

# ===================== ГРУППЫ =====================
@main.route('/groups')
@login_required
def groups():
    return render_template('groups.html', groups=Group.query.all())

@main.route('/groups/add', methods=['GET', 'POST'])
@login_required
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
            flash_msg('success', f'Группа {group.name} успешно добавлена')
            return redirect(url_for('main.groups'))
        except IntegrityError:
            db.session.rollback()
            flash_msg('error', 'Группа с таким названием уже существует')
            return None
        except Exception as e:
            db.session.rollback()
            flash_msg('error', f'Ошибка добавления группы: {str(e)}')
            return None
    
    return render_template('group_form.html', form=form, title='Добавить группу', group=None)

@main.route('/groups/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_group(id):
    group = Group.query.get_or_404(id)
    form = GroupForm(obj=group)
    
    if form.validate_on_submit():
        try:
            form.populate_obj(group)
            db.session.commit()
            flash_msg('success', f'Группа {group.name} обновлена')
            return redirect(url_for('main.groups'))
        except IntegrityError:
            db.session.rollback()
            flash_msg('error', 'Группа с таким названием уже существует')
            return None
        except Exception as e:
            db.session.rollback()
            flash_msg('error', f'Ошибка обновления группы: {str(e)}')
            return None
    
    return render_template('group_form.html', form=form, title='Редактировать группу', group=group)

@main.route('/groups/<int:id>/delete', methods=['POST'])
@login_required
def delete_group(id):
    group = Group.query.get_or_404(id)
    try:
        db.session.delete(group)
        db.session.commit()
        flash_msg('success', f'Группа {group.name} удалена')
    except Exception as e:
        db.session.rollback()
        flash_msg('error', f'Ошибка удаления группы: {str(e)}')
    
    return redirect(url_for('main.groups'))

# ===================== ОЦЕНКИ =====================
@main.route('/grades')
@login_required
def grades():
    query = Grade.query
    
    group_filter = request.args.get('group', 'all')
    student_filter = request.args.get('student', 'all')
    
    if group_filter != 'all':
        query = query.join(Student).filter(Student.group_id == group_filter)
    
    if student_filter != 'all':
        query = query.filter_by(student_id=student_filter)
    
    page_args = get_pagination_args()
    grades_paginated = query.order_by(Grade.date.desc()).paginate(
        page=page_args['page'], per_page=page_args['per_page'], error_out=False)
    
    return render_template('grades.html', 
                         grades=grades_paginated,
                         groups=Group.query.all(),
                         students=Student.query.all(),
                         current_filters={'group': group_filter, 'student': student_filter})

@main.route('/grades/add', methods=['GET', 'POST'])
@login_required
def add_grade():
    form = GradeForm()
    form.student_id.choices = [(s.id, f'{s.last_name} {s.first_name}') for s in Student.query.all()]
    form.subject_id.choices = [(s.id, s.name) for s in Subject.query.all()]
    
    if form.validate_on_submit():
        try:
            grade = Grade(
                student_id=form.student_id.data,
                subject_id=form.subject_id.data,
                grade_value=form.grade_value.data,
                grade_type=form.grade_type.data,
                date=form.date.data,
                comments=form.comments.data
            )
            db.session.add(grade)
            db.session.commit()
            flash_msg('success', 'Оценка успешно добавлена')
            return redirect(url_for('main.grades'))
        except Exception as e:
            db.session.rollback()
            flash_msg('error', f'Ошибка добавления оценки: {str(e)}')
            return None
    
    return render_template('grade_form.html', form=form, title='Добавить оценку')

@main.route('/grades/<int:id>/delete', methods=['POST'])
@login_required
def delete_grade(id):
    grade = Grade.query.get_or_404(id)
    try:
        db.session.delete(grade)
        db.session.commit()
        flash_msg('success', 'Оценка удалена')
    except Exception as e:
        db.session.rollback()
        flash_msg('error', f'Ошибка удаления оценки: {str(e)}')
    
    return redirect(url_for('main.grades'))

# ===================== ОТЧЕТЫ =====================
@main.route('/reports')
@login_required
def reports():
    return render_template('reports.html', groups=Group.query.all())

@main.route('/reports/generate', methods=['POST'])
@login_required
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
            return send_file(filepath, as_attachment=True)
        
        flash_msg('error', 'Неверный тип отчета')
        return redirect(url_for('main.reports'))
        
    except Exception as e:
        flash_msg('error', f'Ошибка генерации отчета: {str(e)}')
        return redirect(url_for('main.reports'))

@main.route('/reports/students')
@login_required
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
        return send_file(filepath, as_attachment=True)
    except Exception as e:
        flash_msg('error', f'Ошибка генерации отчета: {str(e)}')
        return redirect(url_for('main.reports'))

@main.route('/reports/group/<int:group_id>')
@login_required
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
        return send_file(filepath, as_attachment=True)
    except Exception as e:
        flash_msg('error', f'Ошибка генерации отчета: {str(e)}')
        return redirect(url_for('main.reports'))

# ===================== НАСТРОЙКИ =====================
@main.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    settings_obj = get_settings()
    form = SettingsForm(obj=settings_obj)
    
    if form.validate_on_submit():
        if form.reset.data:
            settings_obj.college_name = 'Технический колледж'
            settings_obj.academic_year = '2024-2025'
            settings_obj.max_students_per_group = 25
            settings_obj.export_format = 'excel'
            settings_obj.enable_email_notifications = False
            settings_obj.enable_system_notifications = True
            settings_obj.theme_color = 'purple'
            settings_obj.items_per_page = 20
            settings_obj.auto_backup = True
            settings_obj.backup_frequency = 'daily'
            
            db.session.commit()
            flash_msg('success', 'Настройки сброшены к значениям по умолчанию!')
            return redirect(url_for('main.settings'))
        
        try:
            form.populate_obj(settings_obj)
            
            if form.new_password.data:
                current_user.set_password(form.new_password.data)
                flash_msg('success', 'Пароль успешно изменен!')
            
            db.session.commit()
            flash_msg('success', 'Настройки успешно сохранены!')
            session['theme_color'] = settings_obj.theme_color
            
            return redirect(url_for('main.settings'))
            
        except Exception as e:
            db.session.rollback()
            flash_msg('error', f'Ошибка сохранения настроек: {str(e)}')
    
    return render_template('settings.html', form=form, settings=settings_obj)

@main.route('/settings/backup', methods=['GET', 'POST'])
@login_required
def settings_backup():
    """Управление резервными копиями"""
    form = BackupForm()
    
    if form.validate_on_submit():
        try:
            backup_file = create_backup(
                backup_type=form.backup_type.data,
                include_files=form.include_files.data,
                description=form.description.data
            )
            flash_msg('success', f'Резервная копия создана: {backup_file}')
            return redirect(url_for('main.settings_backup'))
        except Exception as e:
            flash_msg('error', f'Ошибка создания резервной копии: {str(e)}')
    
    backup_dir = 'backups'
    backups = []
    if os.path.exists(backup_dir):
        files = os.listdir(backup_dir)
        for f in files:
            if f.endswith('.backup'):
                filepath = os.path.join(backup_dir, f)
                backups.append({
                    'filename': f,
                    'size': os.path.getsize(filepath),
                    'size_mb': round(os.path.getsize(filepath) / 1024 / 1024, 2),
                    'date': datetime.fromtimestamp(os.path.getmtime(filepath)).strftime('%Y-%m-%d %H:%M'),
                    'type': f.split('_')[2].split('.')[0] if '_' in f else 'full'
                })
        backups.sort(key=lambda x: x['date'], reverse=True)
    
    return render_template('settings_backup.html', form=form, backups=backups)

@main.route('/settings/backup/<filename>/restore', methods=['POST'])
@login_required
def restore_backup_file(filename):
    """Восстановление из резервной копии"""
    try:
        backup_path = os.path.join('backups', filename)
        if not os.path.exists(backup_path):
            flash_msg('error', 'Файл резервной копии не найден')
            return redirect(url_for('main.settings_backup'))
        
        restore_backup(backup_path)
        flash_msg('success', 'Резервная копия успешно восстановлена!')
    except Exception as e:
        flash_msg('error', f'Ошибка восстановления: {str(e)}')
    
    return redirect(url_for('main.settings_backup'))

@main.route('/settings/backup/<filename>/delete', methods=['POST'])
@login_required
def delete_backup_file(filename):
    """Удаление резервной копии"""
    try:
        backup_path = os.path.join('backups', filename)
        if os.path.exists(backup_path):
            os.remove(backup_path)
            flash_msg('success', 'Резервная копия удалена')
        else:
            flash_msg('error', 'Файл не найден')
    except Exception as e:
        flash_msg('error', f'Ошибка удаления: {str(e)}')
    
    return redirect(url_for('main.settings_backup'))

@main.route('/settings/import', methods=['GET', 'POST'])
@login_required
def settings_import():
    """Импорт данных"""
    form = ImportForm()
    
    if form.validate_on_submit():
        try:
            file = form.file.data
            result = import_from_file(
                file=file,
                import_type=form.import_type.data,
                import_mode=form.import_mode.data
            )
            flash_msg('success', f'Импорт завершен: {result}')
            return redirect(url_for('main.settings_import'))
        except Exception as e:
            flash_msg('error', f'Ошибка импорта: {str(e)}')
    
    return render_template('settings_import.html', form=form)

@main.route('/settings/export-template/<template_type>')
@login_required
def download_import_template(template_type):
    """Скачивание шаблона для импорта"""
    templates = {
        'students': 'templates/import_students_template.xlsx',
        'grades': 'templates/import_grades_template.xlsx',
        'groups': 'templates/import_groups_template.xlsx'
    }
    
    if template_type in templates and os.path.exists(templates[template_type]):
        return send_file(templates[template_type], as_attachment=True)
    
    flash_msg('error', 'Шаблон не найден')
    return redirect(url_for('main.settings_import'))

@main.route('/settings/logs')
@login_required
def view_logs():
    """Просмотр логов системы"""
    log_file = 'app.log'
    logs = []
    
    if os.path.exists(log_file):
        with open(log_file, 'r', encoding='utf-8') as f:
            logs = f.readlines()[-100:]
    
    return render_template('view_logs.html', logs=logs)

@main.route('/settings/api')
@login_required
def api_settings():
    """Настройки API"""
    return render_template('api_settings.html')

@main.route('/settings/api/generate-key', methods=['POST'])
@login_required
def generate_api_key():
    """Генерация API ключа"""
    api_key = secrets.token_urlsafe(32)
    session['api_key'] = api_key
    flash_msg('success', 'Новый API ключ сгенерирован')
    return redirect(url_for('main.api_settings'))

# ===================== API =====================
@main.route('/api/settings', methods=['GET'])
@login_required
def get_settings_api():
    settings = get_settings()
    return jsonify(settings.to_dict())

@main.route('/api/settings/update', methods=['POST'])
@login_required
def update_settings_api():
    try:
        data = request.get_json()
        settings = get_settings()
        
        for key, value in data.items():
            if hasattr(settings, key):
                setattr(settings, key, value)
        
        settings.updated_at = datetime.utcnow()
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Настройки обновлены'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@main.route('/api/system/health')
def system_health():
    health = {
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'database': 'connected',
        'tables': {
            'users': User.query.count(),
            'students': Student.query.count(),
            'groups': Group.query.count(),
            'grades': Grade.query.count()
        }
    }
    return jsonify(health)

@main.route('/health')
def health_check():
    return {'status': 'ok', 'timestamp': datetime.utcnow().isoformat()}

@main.route('/api/system/clear-logs', methods=['POST'])
@login_required
def clear_logs():
    """Очистка логов"""
    try:
        log_file = 'app.log'
        if os.path.exists(log_file):
            open(log_file, 'w').close()
        return jsonify({'success': True, 'message': 'Логи очищены'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@main.route('/api/system/download-logs')
@login_required
def download_logs():
    """Скачивание логов"""
    log_file = 'app.log'
    
    if os.path.exists(log_file):
        return send_file(log_file, as_attachment=True, download_name=f'logs_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt')
    else:
        flash_msg('error', 'Файл логов не найден')
        return redirect(url_for('main.view_logs'))

@main.route('/api/system/logs/recent')
@login_required
def get_recent_logs():
    """Получение последних логов (API)"""
    log_file = 'app.log'
    logs = []
    
    if os.path.exists(log_file):
        with open(log_file, 'r', encoding='utf-8') as f:
            logs = f.readlines()[-50:]
    
    return jsonify({'logs': logs})

# ===================== ДОПОЛНИТЕЛЬНЫЕ API МАРШРУТЫ =====================
@main.route('/api/system/api-usage')
@login_required
def api_usage():
    """Статистика использования API"""
    return jsonify({
        'total_requests': 0,
        'requests_by_endpoint': {},
        'last_30_days': []
    })

@main.route('/api/settings/integrations', methods=['GET', 'POST'])
@login_required
def integrations_settings():
    """Настройки интеграций"""
    if request.method == 'GET':
        return jsonify({
            'enableWebhooks': False,
            'webhookUrl': '',
            'events': {
                'newStudent': False,
                'newGrade': False,
                'statusChange': False
            }
        })
    else:
        data = request.get_json()
        return jsonify({'success': True, 'message': 'Настройки сохранены'})

@main.route('/api/system/revoke-api-key', methods=['POST'])
@login_required
def revoke_api_key():
    """Отзыв API ключа"""
    session.pop('api_key', None)
    return jsonify({'success': True, 'message': 'API ключ отозван'})