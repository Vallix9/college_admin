FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Колледж хранит данные в SQLite — каталог монтируется в volumes,
# чтобы college.db переживал пересборку образа.
# Владельцем назначается непривилегированный пользователь: именованный
# том наследует права каталога из образа, иначе приложение, запущенное
# не от root, не сможет создать файл базы.
RUN mkdir -p /app/data /app/logs /app/backups /app/exports /app/uploads \
    && useradd --create-home --uid 1000 college \
    && chown -R college:college /app

ENV FLASK_APP=run.py \
    PYTHONUNBUFFERED=1 \
    WAITRESS=1 \
    FLASK_DEBUG=0 \
    HOST=0.0.0.0 \
    PORT=5000 \
    DATABASE_URL=sqlite:////app/data/college.db

EXPOSE 5000

USER college

# В контейнере dev-сервер Werkzeug не используется — только waitress
CMD ["python", "run.py"]