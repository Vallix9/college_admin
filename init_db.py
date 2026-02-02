from app.init_ import create_app, db
from app.models import User, Group, Student, Subject, Grade, SystemSettings
from datetime import date, datetime

app = create_app()

with app.app_context():
    # Создаем таблицы
    db.create_all()
    
    # Создаем администратора, если его нет
    if not User.query.first():
        admin = User(username='admin')
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
        print("Администратор создан: admin / admin123")
    
    # Создаем настройки по умолчанию
    if not SystemSettings.query.first():
        settings = SystemSettings()
        db.session.add(settings)
        db.session.commit()
        print("Настройки системы созданы")
    
    # Создаем тестовые группы
    if not Group.query.first():
        group1 = Group(name='ИСП-204', specialty='Информационные системы и программирование', year=2023)
        group2 = Group(name='ПКС-101', specialty='Программное обеспечение', year=2024)
        group3 = Group(name='СПО-302', specialty='Сетевое и системное администрирование', year=2022)
        db.session.add_all([group1, group2, group3])
        db.session.commit()
        print("Тестовые группы созданы")
    
    # Создаем тестовые предметы
    if not Subject.query.first():
        subjects = [
            Subject(name='Математика', hours=144),
            Subject(name='Физика', hours=108),
            Subject(name='Информатика', hours=180),
            Subject(name='Программирование', hours=216),
            Subject(name='Базы данных', hours=144),
            Subject(name='Веб-разработка', hours=180),
            Subject(name='Английский язык', hours=144),
            Subject(name='Экономика', hours=72)
        ]
        db.session.add_all(subjects)
        db.session.commit()
        print("Тестовые предметы созданы")
    
    # Создаем тестовых студентов
    if not Student.query.first():
        from datetime import date as dt_date
        students = [
            Student(
                student_id='STD0001',
                last_name='Иванов',
                first_name='Иван',
                patronymic='Иванович',
                gender='M',
                birth_date=dt_date(2003, 5, 15),
                email='ivanov@college.edu',
                phone='+79991234567',
                group_id=1,
                status='active',
                enrollment_date=dt_date(2023, 9, 1)
            ),
            Student(
                student_id='STD0002',
                last_name='Петрова',
                first_name='Мария',
                patronymic='Сергеевна',
                gender='F',
                birth_date=dt_date(2004, 3, 22),
                email='petrova@college.edu',
                phone='+79992345678',
                group_id=1,
                status='active',
                enrollment_date=dt_date(2023, 9, 1)
            ),
            Student(
                student_id='STD0003',
                last_name='Сидоров',
                first_name='Алексей',
                patronymic='Петрович',
                gender='M',
                birth_date=dt_date(2002, 11, 30),
                email='sidorov@college.edu',
                phone='+79993456789',
                group_id=2,
                status='active',
                enrollment_date=dt_date(2024, 9, 1)
            )
        ]
        db.session.add_all(students)
        db.session.commit()
        print("Тестовые студенты созданы")
    
    print("База данных успешно создана!")