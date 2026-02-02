from app.init_ import db, create_app
from app.models import StudentSubject

app = create_app()

with app.app_context():
    # Создаем таблицу для связи студент-предмет
    db.create_all()
    print("Таблицы успешно созданы")