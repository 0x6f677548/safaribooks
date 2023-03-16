FROM python:3.6

ADD requirements.txt /safaribooks/requirements.txt
ADD safaribooks.py /safaribooks/safaribooks.py
ADD sso_cookies.py /safaribooks/sso_cookies.py
ADD cookies.json /safaribooks/cookies.json

WORKDIR /safaribooks
RUN apt-get update
RUN pip3 install --upgrade pip
RUN pip3 install -r requirements.txt