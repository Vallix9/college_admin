from datetime import datetime
import json
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.init_ import db, login_manager

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256))
    role = db.Column(db.String(20), default='admin')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def __repr__(self):
        return f'<User {self.username}>'

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
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
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

@login_manager.user_loader
def load_user(id):
    return User.query.get(int(id))

class Group(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    specialty = db.Column(db.String(200))
    year = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
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
    enrollment_date = db.Column(db.Date, default=datetime.utcnow().date())
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    grades = db.relationship('Grade', backref='student', lazy=True, cascade='all, delete-orphan')
    
    @property
    def full_name(self):
        return f'{self.last_name} {self.first_name} {self.patronymic or ""}'.strip()
    
    @property
    def short_name(self):
        patronymic_initial = f'{self.patronymic[0]}.' if self.patronymic else ''
        return f'{self.last_name} {self.first_name[0]}.{patronymic_initial}'
    
    def average_grade(self):
        if not self.grades:
            return 0
        
        total = 0
        count = 0
        
        for grade in self.grades:
            value = grade.grade_value
            if value is not None:
                try:
                    # Преобразуем значение в число
                    if isinstance(value, (int, float)):
                        total += float(value)
                        count += 1
                    elif isinstance(value, str):
                        # Пробуем преобразовать строку в число
                        total += float(value)
                        count += 1
                except (ValueError, TypeError):
                    # Пропускаем некорректные значения
                    continue
        
        return round(total / count, 2) if count > 0 else 0
    
    def __repr__(self):
        return f'<Student {self.full_name}>'

class Subject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    hours = db.Column(db.Integer, default=72)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    grades = db.relationship('Grade', backref='subject', lazy=True, cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<Subject {self.name}>'

class Grade(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id'), nullable=False)
    grade_value = db.Column(db.Integer, nullable=False)
    grade_type = db.Column(db.String(50))
    date = db.Column(db.Date, default=datetime.utcnow().date())
    comments = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def get_grade_color(self):
        """Возвращает класс Bootstrap для цвета оценки"""
        if self.grade_value >= 4:
            return 'bg-success'
        elif self.grade_value == 3:
            return 'bg-warning'
        else:
            return 'bg-danger'
    
    def __repr__(self):
        return f'<Grade {self.grade_value} for student {self.student_id}>'