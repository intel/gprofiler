# pinned python:3.10-slim - If this is updated make sure to update tests/test_app_metadata.py with new metadata.
FROM python@sha256:6a59e30de500f8ea1e673b88cd7a558f4d642ed4ec8e574d79380380adc3aed4

WORKDIR /app
ADD lister.py /app
# Install some package so we can test that its info appears in the collapsed
RUN pip install pyyaml==6.0

CMD ["python", "lister.py"]
