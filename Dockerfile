FROM localhost/pytalib:0.0.1

WORKDIR /usr/local/app
COPY ./pyproject.toml ./uv.lock ./
RUN uv sync --frozen --no-install-project

CMD ["python", "run.py"]
