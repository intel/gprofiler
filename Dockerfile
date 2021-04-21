FROM ubuntu:20.04 as bcc-builder

RUN apt-get update

RUN DEBIAN_FRONTEND=noninteractive apt-get install -y git build-essential iperf llvm-9-dev libclang-9-dev \
  cmake python3 flex bison libelf-dev libz-dev

WORKDIR /bcc

# git clone --depth 1 git@github.com:Granulate/bcc.git -b pyperf
COPY bcc /bcc/bcc
RUN mkdir bcc/build && cd bcc/build && \
  cmake -DPYTHON_CMD=python3 -DINSTALL_CPP_EXAMPLES=y -DCMAKE_INSTALL_PREFIX=/bcc/root .. && \
  make -C examples/cpp/pyperf -j -l VERBOSE=1 install


FROM ubuntu:20.04

WORKDIR /app

# kmod - for modprobe kheaders if it's available
RUN apt-get update && apt-get install -y curl python3-pip kmod

COPY --from=bcc-builder /bcc/root/share/bcc/examples/cpp/PyPerf gprofiler/resources/python/PyPerf

COPY build.sh build.sh
RUN ./build.sh

COPY requirements.txt requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt

COPY LICENSE.md MANIFEST.in README.md setup.py ./
COPY gprofiler gprofiler
RUN python3 setup.py install

STOPSIGNAL SIGINT

ENTRYPOINT [ "python3", "-m", "gprofiler", "-v" ]
