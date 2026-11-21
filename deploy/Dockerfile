# Obraz aplikacji ODWOŁANIA2JSON (reguły + OCR; model językowy w osobnej usłudze "llm").
# UWAGA: nieprzetestowany - na komputerze deweloperskim nie było Dockera. Sprawdzić przed wdrożeniem.
FROM python:3.13-slim

# Tesseract z językiem polskim (OCR skanów); w obrazie, więc nie trzeba instalować go osobno.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-pol \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY silnik/ silnik/
COPY typy/ typy/
COPY run.py ocena.py llm.yaml ./

ENV PYTHONUNBUFFERED=1 \
    LLM_ADRES=http://llm:11434/v1 \
    LLM_MODEL=bielik-11b-6k

ENTRYPOINT ["python", "run.py"]
CMD ["--wejscie", "/dane/input", "--wyjscie", "/dane/output", "--raport", "/dane/raport"]
