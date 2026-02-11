FROM alpine:3.23.0 AS gprofiler

ARG ARCH
ARG EXE_PATH=build/${ARCH}/gprofiler
# lets gProfiler know it is running in a container
ENV GPROFILER_IN_CONTAINER=1
COPY ${EXE_PATH} /gprofiler
RUN chmod +x /gprofiler

ENTRYPOINT ["/gprofiler"]
