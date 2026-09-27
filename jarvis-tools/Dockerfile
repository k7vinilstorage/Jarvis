FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY testes/ ./testes/

# Usuário sem privilégios; o compose ainda deixa o sistema de arquivos só de leitura
RUN useradd --system --uid 10001 --no-create-home jarvis
USER jarvis

EXPOSE 8000
CMD ["python", "-m", "app.servidor"]
