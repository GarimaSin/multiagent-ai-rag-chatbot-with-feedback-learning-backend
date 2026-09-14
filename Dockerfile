FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY sample-data ./sample-data
RUN pip install '.[postgres]' && useradd --create-home --uid 10001 chatbot && mkdir -p /app/data && chown -R chatbot:chatbot /app
USER chatbot
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
