# Official Playwright image: Chromium and its system libraries are preinstalled.
# The pip playwright version below must match the image tag.
FROM mcr.microsoft.com/playwright/python:v1.55.0-noble

WORKDIR /app
RUN pip install --no-cache-dir playwright==1.55.0 "PyYAML>=6.0"
COPY lazada_bot ./lazada_bot

# Mount config.yaml and a data dir (browser profile + state.json) at /data.
# Containers have no screen: set `headless: true` in config.yaml.
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "lazada_bot"]
CMD ["run", "-c", "/data/config.yaml"]
