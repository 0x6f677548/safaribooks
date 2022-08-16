FROM python:3
RUN apt-get update
RUN apt-get install -y git
RUN git clone https://github.com/0x6f677548/safaribooks.git

WORKDIR /safaribooks
RUN pip3 install -r requirements.txt