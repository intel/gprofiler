# eclipse-temurin:8u442-b06-jdk
ARG JAVA_BASE_IMAGE=eclipse-temurin@sha256:3a980a2ea5d03252cc2fb84d6bb6f6fd2a0305a15f01a4185077b9fe26bad8f7
FROM ${JAVA_BASE_IMAGE}

WORKDIR /app
ADD Fibonacci.java /app
ADD MANIFEST.MF /app
RUN javac Fibonacci.java
RUN jar cvmf MANIFEST.MF Fibonacci.jar *.class
# create a java binary with different basename, see test_java_different_basename
RUN cp `which java` `which java`-notjava

CMD ["sh", "-c", "java -jar Fibonacci.jar; sleep 10000"]
