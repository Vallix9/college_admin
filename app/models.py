from datetime import datetime
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
    enrollment_date = db.Column(db.Date, default=datetime.utcnow)
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
        grades = [g for g in self.grades if g.grade_value.isdigit()]
        if not grades:
            return 0
        return sum(int(g.grade_value) for g in grades) / len(grades)
    
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
    grade_value = db.Column(db.String(2), nullable=False)
    grade_type = db.Column(db.String(20))
    date = db.Column(db.Date, default=datetime.utcnow)
    comments = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('student_id', 'subject_id', 'date', name='unique_grade'),
    )
    
    def __repr__(self):
        return f'<Grade {self.grade_value} для {self.student_id}>'