# syntax=docker/dockerfile:1.2

# FIRST STAGE
FROM python:3.11-slim-bullseye AS intermediate

RUN apt-get update && apt-get install -y --no-install-recommends git &&  \
    apt-get install -y --no-install-recommends openssh-client && apt-get autoremove -y && apt-get clean all -y
RUN apt-get -y install gcc
RUN pip install wheel
RUN mkdir /wheels
WORKDIR /wheels
COPY requirements.txt /wheels
RUN pip wheel -r /wheels/requirements.txt

RUN mkdir -p -m 0600 ~/.ssh && ssh-keyscan github.com >> ~/.ssh/known_hosts

RUN --mount=type=ssh,id=zmq_python pip wheel --no-deps git+ssh://git@github.com/perrytec/zmq_python.git@0.0.1

# SECOND STAGE
FROM python:3.11-slim-bullseye
COPY --from=intermediate /wheels /wheels
RUN pip install -r /wheels/requirements.txt -f /wheels
RUN pip install zmq_python -f /wheels

WORKDIR .
COPY . .
ENTRYPOINT [ "python3", "run.py" ]