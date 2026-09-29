from datetime import datetime
import json
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.init_ import db, login_manager


def now():
    """Текущее локальное время без таймзоны.

    datetime.utcnow() помечен устаревшим начиная с Python 3.12, и вдобавок
    в базу писалось время на несколько часов раньше местного — из-за чего
    в отчётах и в поле «последний вход» съезжали даты.
    """
    return datetime.now()


class User(UserMixin, db.Model):
    ROLE_ADMIN = 'admin'
    ROLE_TEACHER = 'teacher'
    ROLE_STUDENT = 'student'
    ROLES = (ROLE_ADMIN, ROLE_TEACHER, ROLE_STUDENT)

    ROLE_LABELS = {
        ROLE_ADMIN: 'Администратор',
        ROLE_TEACHER: 'Преподаватель',
        ROLE_STUDENT: 'Студент',
    }

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256))
    role = db.Column(db.String(20), default=ROLE_ADMIN)
    created_at = db.Column(db.DateTime, default=now)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    password_changed_at = db.Column(db.DateTime)
    last_login_at = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    full_name = db.Column(db.String(200))
    email = db.Column(db.String(100))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        self.password_changed_at = now()

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == self.ROLE_ADMIN

    @property
    def is_teacher(self):
        return self.role == self.ROLE_TEACHER

    @property
    def is_student(self):
        return self.role == self.ROLE_STUDENT

    @property
    def can_manage_data(self):
        """Администратор и преподаватель; студент только читает своё."""
        return self.role in (self.ROLE_ADMIN, self.ROLE_TEACHER)

    @property
    def display_name(self):
        return self.full_name or self.username

    @property
    def role_label(self):
        return self.ROLE_LABELS.get(self.role, self.role)

    def teaches(self, subject):
        """Преподаватель ведёт этот предмет.

        Администратор считается имеющим доступ ко всем предметам: он не
        ведёт журналы, но должен видеть и править их.
        """
        if self.is_admin:
            return True
        if not self.is_teacher or subject is None:
            return False
        return subject.teacher_id == self.id

    def __repr__(self):
        return f'<User {self.username} ({self.role})>'

class SystemSettings(db.Model):
    """Модель для хранения системных настроек"""
    id = db.Column(db.Integer, primary_key=True)
    college_name = db.Column(db.String(200), default='Технический колледж')
    academic_year = db.Column(db.String(50), default='2024-2025')
    max_students_per_group = db.Column(db.Integer, default=25)
    export_format = db.Column(db.String(20), default='excel')
    enable_email_notifications = db.Column(db.Boolean, default=False)
    enable_system_notifications = db.Column(db.Boolean, default=True)
    theme_color = db.Column(db.String(20), default='purple')
    items_per_page = db.Column(db.Integer, default=20)
    auto_backup = db.Column(db.Boolean, default=True)
    backup_frequency = db.Column(db.String(20), default='daily')  # daily, weekly, monthly
    updated_at = db.Column(db.DateTime, default=now, onupdate=now)
    
    def to_dict(self):
        """Преобразуем настройки в словарь"""
        return {
            'college_name': self.college_name,
            'academic_year': self.academic_year,
            'max_students_per_group': self.max_students_per_group,
            'export_format': self.export_format,
            'enable_email_notifications': self.enable_email_notifications,
            'enable_system_notifications': self.enable_system_notifications,
            'theme_color': self.theme_color,
            'items_per_page': self.items_per_page,
            'auto_backup': self.auto_backup,
            'backup_frequency': self.backup_frequency,
            'updated_at': self.updated_at
        }
    
    @classmethod
    def get_settings(cls):
        """Получаем текущие настройки (синглтон)"""
        settings = cls.query.first()
        if not settings:
            settings = cls()
            db.session.add(settings)
            db.session.commit()
        return settings
    
    def __repr__(self):
        return f'<SystemSettings {self.college_name}>'
    
    @classmethod
    def reset_to_default(cls):
        """Сброс настроек к значениям по умолчанию"""
        settings = cls.get_settings()
        settings.college_name = 'Технический колледж'
        settings.academic_year = '2024-2025'
        settings.max_students_per_group = 25
        settings.export_format = 'excel'
        settings.enable_email_notifications = False
        settings.enable_system_notifications = True
        settings.theme_color = 'purple'
        settings.items_per_page = 20
        settings.auto_backup = True
        settings.backup_frequency = 'daily'
        db.session.commit()
        return settings

@login_manager.user_loader
def load_user(id):
    return User.query.get(int(id))

class Group(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    specialty = db.Column(db.String(200))
    year = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=now)
    
    students = db.relationship('Student', backref='group', lazy=True, cascade='all, delete-orphan')
    
    def student_count(self):
        return Student.query.filter_by(group_id=self.id, status='active').count()
    
    def __repr__(self):
        return f'<Group {self.name}>'

class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(20), unique=True, nullable=False, index=True)
    last_name = db.Column(db.String(50), nullable=False, index=True)
    first_name = db.Column(db.String(50), nullable=False)
    patronymic = db.Column(db.String(50))
    gender = db.Column(db.String(1), nullable=False)
    birth_date = db.Column(db.Date)
    email = db.Column(db.String(100))
    phone = db.Column(db.String(20))
    
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'))
    
    status = db.Column(db.String(20), default='active')
    enrollment_date = db.Column(db.Date, default=now().date())
    created_at = db.Column(db.DateTime, default=now)
    # Связь с учётной записью. nullable=True: у старых студентов из сида
    # аккаунта нет, и они не должны ломать импорт и сид.
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True)
    
    grades = db.relationship('Grade', backref='student', lazy=True, cascade='all, delete-orphan')
    
    @property
    def full_name(self):
        return f'{self.last_name} {self.first_name} {self.patronymic or ""}'.strip()
    
    @property
    def short_name(self):
        patronymic_initial = f'{self.patronymic[0]}.' if self.patronymic else ''
        return f'{self.last_name} {self.first_name[0]}.{patronymic_initial}'
    
    def average_grade(self):
        """Средний балл студента. Расчёт общий с журналом — в utils.average_grade."""
        from app.utils import average_grade
        return average_grade(self.grades)
    
    def __repr__(self):
        return f'<Student {self.full_name}>'

class Subject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    hours = db.Column(db.Integer, default=72)
    # Преподаватель, ведущий предмет. NULL — предмет ещё не распределён.
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=now)
    
    teacher = db.relationship('User', backref='subjects', lazy=True)
    grades = db.relationship('Grade', backref='subject', lazy=True, cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<Subject {self.name}>'

class Grade(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id'), nullable=False)
    grade_value = db.Column(db.String(20), nullable=False)
    grade_type = db.Column(db.String(50))
    date = db.Column(db.Date, default=now().date())
    comments = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=now)
    
    def get_grade_color(self):
        """Возвращает класс Bootstrap для цвета оценки"""
        grade_str = str(self.grade_value)
        if grade_str in ['5', 'зачет']:
            return 'bg-success'
        elif grade_str == '4':
            return 'bg-primary'
        elif grade_str == '3':
            return 'bg-warning'
        else:
            return 'bg-danger'
    
    def __repr__(self):
        return f'<Grade {self.grade_value} for student {self.student_id}>'