FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PORT=3000
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd -r -u 10001 app
USER app
EXPOSE 3000
CMD ["python", "-m", "vapi_lokutor_adapter.app"]
