FROM python:3.12-slim

RUN pip install --no-cache-dir edward-guard

# pi coding agent (optional, for full RPC monitoring of pi tasks):
# RUN npm install -g --ignore-scripts @earendil-works/pi-coding-agent

ENTRYPOINT ["edward"]
