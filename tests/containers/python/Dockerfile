# pinned python:3.10-slim
FROM python@sha256:fc113a90d0a9a41b52f87c973de5b0970ddcccd726d5cd07885ddb5f1f31dd15

WORKDIR /app
ADD lister.py /app
# Install some package so we can test that its info appears in the collapsed
RUN pip install pyyaml==6.0

CMD ["python", "lister.py"]
