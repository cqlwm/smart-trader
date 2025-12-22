FROM pytalib:0.0.2

WORKDIR /usr/local/app
COPY ./pyproject.toml ./uv.lock ./
RUN pip install --no-cache-dir uv
RUN source .venv/bin/activate & uv sync

CMD ["python", "run.py"]
