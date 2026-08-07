@echo off

REM Production WSGI server for MY-SHOP

python -m gunicorn myshop.wsgi:application -c gunicorn.conf.py

