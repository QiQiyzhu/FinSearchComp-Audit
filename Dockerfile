FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements-workbench.txt ./
RUN pip install --no-cache-dir -r requirements-workbench.txt && useradd --create-home appuser
COPY research_workbench ./research_workbench
COPY site/workbench ./site/workbench
COPY site/terminal ./site/terminal
RUN mkdir -p /app/build && chown -R appuser:appuser /app
USER appuser
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/api/health', timeout=3)"
CMD ["python", "-m", "uvicorn", "research_workbench.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8090"]
