FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV DATA_DIR=/data
ENV OUTPUT_MD=/docs/data_profile.md
ENV OUTPUT_JSON=/app/output/profile.json

CMD ["python", "profile.py"]
