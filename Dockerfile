FROM python:3.11-slim

# Dépendances système pour pypdfium2 et pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Installer les dépendances Python en premier (layer mis en cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copier le code source
COPY app.py layer_parser.py extract_pdf_layers.py ./

# Dossier de dépôt des PDFs (monté en volume en production)
RUN mkdir -p uploads

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "app.py", \
     "--server.address", "0.0.0.0", \
     "--server.port", "8501", \
     "--server.headless", "true", \
     "--server.maxUploadSize", "200"]
