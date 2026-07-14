FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ src/
COPY models/ models/
ENV PYTHONPATH=/app/src
USER nobody
# Обучение: docker run ... -m training.train_winprob
# Инференс-CLI: docker run ... -m predictors.win_probability MATCH_ID
ENTRYPOINT ["python"]
CMD ["-m", "predictors.win_probability", "--help"]
