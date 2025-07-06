FROM python:3.10 AS requirements_stage

COPY ./docker/pip.conf /root/.config/pip/pip.conf

WORKDIR /wheel

COPY ./pyproject.toml \
  ./requirements.txt \
  /wheel/

RUN python -m pip wheel --wheel-dir=/wheel --no-cache-dir --requirement ./requirements.txt


FROM python:3.10-slim

COPY ./docker/pip.conf /root/.config/pip/pip.conf

WORKDIR /app

ENV TZ=Asia/Shanghai
ENV PYTHONPATH=/app

COPY ./docker/gunicorn_conf.py ./docker/start.sh /
RUN chmod +x /start.sh

ENV APP_MODULE=main:app
ENV MAX_WORKERS=1

COPY ./docker/main.py /app
COPY --from=requirements_stage /wheel /wheel

RUN pip install --no-cache-dir gunicorn uvicorn[standard] nonebot2 \
  && pip install --no-cache-dir --no-index --force-reinstall --find-links=/wheel -r /wheel/requirements.txt && rm -rf /wheel
COPY . /app/

COPY ./docker/prestart.sh /app/
RUN chmod +x /app/prestart.sh

CMD ["/start.sh"]