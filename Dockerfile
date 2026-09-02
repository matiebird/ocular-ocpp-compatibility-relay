FROM python:3.12-alpine
LABEL org.opencontainers.image.title="Ocular OCPP Compatibility Relay" \
      org.opencontainers.image.version="0.3.7"
RUN pip install --no-cache-dir websockets==12.0 && \
    addgroup -S proxy && adduser -S -G proxy proxy
WORKDIR /app
COPY --chown=proxy:proxy proxy ./proxy
EXPOSE 9000
ENTRYPOINT ["python", "-m", "proxy.main"]
CMD ["--options", "/data/options.json"]
