FROM localhost/pytalib:0.0.1

WORKDIR /usr/local/app
COPY ./requirements.txt .
RUN pip install -r requirements.txt

CMD ["python", "main.py"]
