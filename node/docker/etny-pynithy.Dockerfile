FROM localhost:5000/iosif/etny-pynithy:latest

RUN pip3 install --upgrade pip
RUN pip3 install --upgrade setuptools
RUN pip3 install web3
COPY etny-result.py /etny-result.py
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
#CMD ["--help"]