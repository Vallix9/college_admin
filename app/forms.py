from datetime import datetime
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField, SelectField, DateField, TextAreaField, IntegerField
from wtforms.validators import DataRequired, Email, Length, Optional, ValidationError, NumberRange
from app.models import Group, Student, Subject

class LoginForm(FlaskForm):
    username = StringField('Логин', validators=[DataRequired()])
    password = PasswordField('Пароль', validators=[DataRequired()])
    remember_me = BooleanField('Запомнить меня')
    submit = SubmitField('Войти')

class StudentForm(FlaskForm):
    student_id = StringField('Номер зачетки*', validators=[DataRequired(), Length(max=20)])
    last_name = StringField('Фамилия*', validators=[DataRequired(), Length(max=50)])
    first_name = StringField('Имя*', validators=[DataRequired(), Length(max=50)])
    patronymic = StringField('Отчество', validators=[Optional(), Length(max=50)])
    gender = SelectField('Пол*', choices=[
        ('M', '👨 Мужской'),
        ('F', '👩 Женский')
    ], validators=[DataRequired()])
    birth_date = DateField('Дата рождения', format='%Y-%m-%d', validators=[Optional()])
    email = StringField('Email', validators=[Optional(), Email(), Length(max=100)])
    phone = StringField('Телефон', validators=[Optional(), Length(max=20)])
    group_id = SelectField('Группа*', coerce=int, validators=[DataRequired()])
    status = SelectField('Статус*', choices=[
        ('active', '✅ Активный'),
        ('academic_leave', '⏸️ Академический отпуск'),
        ('expelled', '❌ Отчислен'),
        ('graduated', '🎓 Выпускник')
    ], validators=[DataRequired()])
    enrollment_date = DateField('Дата поступления', format='%Y-%m-%d', default=datetime.now, validators=[Optional()])
    submit = SubmitField('Сохранить')
    
    def __init__(self, *args, **kwargs):
        super(StudentForm, self).__init__(*args, **kwargs)
        # Динамическое заполнение списка групп
        self.group_id.choices = [(-1, '-- Выберите группу --')] + [(g.id, f"{g.name} ({g.specialty[:30]}...)") for g in Group.query.order_by(Group.name).all()]
        
        # Установим текущую дату по умолчанию, если не задана
        if not self.enrollment_date.data:
            self.enrollment_date.data = datetime.now().date()
    
    def validate_student_id(self, field):
        """Проверка уникальности номера зачетки"""
        student = Student.query.filter_by(student_id=field.data).first()
        # Если редактируем существующего студента, пропускаем проверку для него самого
        if hasattr(self, 'obj') and self.obj:
            if student and student.id != self.obj.id:
                raise ValidationError('Студент с таким номером зачетки уже существует.')
        else:
            # При создании нового студента
            if student:
                raise ValidationError('Студент с таким номером зачетки уже существует.')

class GroupForm(FlaskForm):
    name = StringField('Название группы*', 
                      validators=[DataRequired(), Length(max=50)],
                      description="Например: ИСП-204, ПКС-101")
    specialty = StringField('Специальность*', 
                           validators=[DataRequired(), Length(max=200)],
                           description="Полное название специальности")
    year = SelectField('Год поступления*', 
                      coerce=int, 
                      validators=[DataRequired()],
                      description="Год, когда группа начала обучение")
    submit = SubmitField('✅ Сохранить')
    
    def __init__(self, *args, **kwargs):
        super(GroupForm, self).__init__(*args, **kwargs)
        current_year = datetime.now().year
        
        # Динамический список годов: от 2018 до текущего + 5 лет
        # Например, если сейчас 2026, то будут года: 2018-2031
        start_year = 2018
        end_year = current_year + 6  # +6 чтобы включить текущий год и 5 лет вперед
        
        # Создаем список годов в обратном порядке (новые сверху)
        years = list(range(start_year, end_year))
        years.reverse()  # От новых к старым
        
        self.year.choices = [(y, str(y)) for y in years]
        
        # Установим текущий год как значение по умолчанию
        if not self.year.data:
            self.year.data = current_year
    
    def validate_name(self, field):
        """Проверка уникальности имени группы"""
        group = Group.query.filter_by(name=field.data).first()
        
        # Если редактируем существующую группу
        if hasattr(self, 'obj') and self.obj:
            # Пропускаем проверку для текущей группы
            if group and group.id != self.obj.id:
                raise ValidationError('❌ Группа с таким названием уже существует!')
        else:
            # При создании новой группы
            if group:
                raise ValidationError('❌ Группа с таким названием уже существует!')

class GradeForm(FlaskForm):
    student_id = SelectField('Студент*', coerce=int, validators=[DataRequired()])
    subject_id = SelectField('Предмет*', coerce=int, validators=[DataRequired()])
    grade_value = SelectField('Оценка*', choices=[
        ('5', '5️⃣ - Отлично'),
        ('4', '4️⃣ - Хорошо'),
        ('3', '3️⃣ - Удовлетворительно'),
        ('2', '2️⃣ - Неудовлетворительно'),
        ('зачет', '✅ Зачет'),
        ('незачет', '❌ Незачет'),
        ('н/я', '📝 Не явился')
    ], validators=[DataRequired()])
    grade_type = SelectField('Тип оценки', choices=[
        ('экзамен', '📚 Экзамен'),
        ('зачет', '📋 Зачет'),
        ('лабораторная', '🔬 Лабораторная работа'),
        ('практика', '🛠️ Практическая работа'),
        ('тест', '📝 Тест'),
        ('курсовая', '📄 Курсовая работа'),
        ('диплом', '🎓 Дипломная работа')
    ], default='экзамен', validators=[Optional()])
    date = DateField('Дата оценки*', format='%Y-%m-%d', default=datetime.now, validators=[DataRequired()])
    comments = TextAreaField('Комментарии', 
                           validators=[Optional(), Length(max=500)],
                           description="Дополнительные заметки по оценке",
                           render_kw={"rows": 3})
    submit = SubmitField('✅ Сохранить оценку')
    
    def __init__(self, *args, **kwargs):
        super(GradeForm, self).__init__(*args, **kwargs)
        # Динамическое заполнение списка студентов
        self.student_id.choices = [(-1, '-- Выберите студента --')] + [
            (s.id, f"{s.last_name} {s.first_name[0]}. {s.patronymic[0] if s.patronymic else ''}. ({s.student_id})") 
            for s in Student.query.order_by(Student.last_name, Student.first_name).all()
        ]
        
        # Динамическое заполнение списка предметов
        self.subject_id.choices = [(-1, '-- Выберите предмет --')] + [
            (s.id, f"{s.name}") 
            for s in Subject.query.order_by(Subject.name).all()
        ]

class SubjectForm(FlaskForm):
    name = StringField('Название предмета*', 
                      validators=[DataRequired(), Length(max=100)],
                      description="Например: Программирование на Python")
    hours = IntegerField('Количество часов*', 
                        validators=[DataRequired(), NumberRange(min=1, max=500)],
                        default=72,
                        description="Общее количество учебных часов")
    submit = SubmitField('✅ Сохранить')
    
    def validate_name(self, field):
        """Проверка уникальности названия предмета"""
        subject = Subject.query.filter_by(name=field.data).first()
        
        if hasattr(self, 'obj') and self.obj:
            if subject and subject.id != self.obj.id:
                raise ValidationError('Предмет с таким названием уже существует!')
        else:
            if subject:
                raise ValidationError('Предмет с таким названием уже существует!')