FROM python:3.9.19-alpine3.20

RUN apk update && \
    apk add --no-cache \
        gcc \
        g++ \
        make \
        bash \
        wget \
        vim

RUN wget http://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz && \
    tar -xzf ta-lib-0.4.0-src.tar.gz && \
    cd ta-lib && \
    ./configure --prefix=/usr && \
    make && \
    make install && \
    cd .. && \
    rm -rf ta-lib ta-lib-0.4.0-src.tar.gz

RUN pip3 install ta-lib

WORKDIR /usr/local/app
COPY ./requirements.txt .
RUN pip install -r requirements.txt

CMD ["python", "main.py"]
