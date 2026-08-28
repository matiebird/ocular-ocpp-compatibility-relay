FROM python:3.12-alpine AS test
WORKDIR /src
RUN pip install --no-cache-dir websockets==12.0
COPY proxy ./proxy
COPY tests ./tests
COPY Dockerfile config.yaml install.sh uninstall.sh ha-timing-script.yaml README.md ./
RUN python -m unittest discover -s tests -v

FROM python:3.12-alpine
LABEL org.opencontainers.image.title="Ocular OCPP Compatibility Relay" \
      org.opencontainers.image.version="0.3.2"
RUN pip install --no-cache-dir websockets==12.0 && \
    addgroup -S proxy && adduser -S -G proxy proxy
WORKDIR /app
COPY --from=test --chown=proxy:proxy /src/proxy ./proxy
EXPOSE 9000
ENTRYPOINT ["python", "-m", "proxy.main"]
CMD ["--options", "/data/options.json"]
