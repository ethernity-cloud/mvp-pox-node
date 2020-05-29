FROM localhost:5000/iosif/etny-pynithy:latest

RUN pip3 install --upgrade pip
RUN pip3 install --upgrade setuptools
RUN pip3 install web3

ENTRYPOINT ["python"]
CMD ["--help"]
