FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt pyproject.toml README.md ./
COPY app ./app
COPY cli ./cli
COPY examples ./examples
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir .
EXPOSE 8000
# Dashboard/API: uvicorn app.main:app
# CLI:         specagent --help  (or: python -m cli.specagent --help)
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]
