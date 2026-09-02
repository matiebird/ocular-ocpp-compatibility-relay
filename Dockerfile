FROM python:3.12-alpine AS test
WORKDIR /src
RUN pip install --no-cache-dir websockets==12.0
COPY proxy ./proxy
COPY tests/test_main.py tests/test_server.py tests/test_timing.py ./tests/
RUN python -m unittest discover -s tests -p 'test_main.py' -v && \
    python -m unittest discover -s tests -p 'test_server.py' -v && \
    python -m unittest discover -s tests -p 'test_timing.py' -v

FROM python:3.12-alpine
LABEL org.opencontainers.image.title="Ocular OCPP Compatibility Relay" \
      org.opencontainers.image.version="0.3.10"
RUN pip install --no-cache-dir websockets==12.0 && \
    addgroup -S proxy && adduser -S -G proxy proxy
WORKDIR /app
COPY --from=test --chown=proxy:proxy /src/proxy ./proxy
EXPOSE 9000
ENTRYPOINT ["python", "-m", "proxy.main"]
CMD ["--options", "/data/options.json"]
