FROM localhost/pytalib:0.0.1

WORKDIR /usr/local/app
COPY ./pyproject.toml ./uv.lock ./
RUN pip install --no-cache-dir uv
RUN uv sync

CMD ["python", "run.py"]
