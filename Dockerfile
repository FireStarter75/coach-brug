# Coach-brug. Bewust kaal: geen dashboardcode, geen data, alleen deze dienst.
# De Vibe CLI komt uit het image zelf (pip, zie requirements.txt), dus een
# update van de CLI vraagt wel een rebuild — dat is bewust: een nieuwe
# CLI-versie moet niet stiekem meekomen met een herstart.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY coach/ /app/
# Lege werkmap voor de CLI: hij heeft een cwd nodig en mag niets anders zien.
# En de home/VIBE_HOME van de coach: daar mounted start.sh de gesloten
# configuratie in; zonder mount zou de CLI er zijn eigen standaarden zetten.
RUN mkdir -p /work /home/coach/.vibe
EXPOSE 8099
CMD ["gunicorn","-b","0.0.0.0:8099","--workers","1","--threads","4","--timeout","200","server:app"]
