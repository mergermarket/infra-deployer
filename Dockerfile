# Used to create docker image for running the commands invoked by the stubs in ../scripts/.

FROM python:3-alpine

ENV TERRAFORM_VERSION=0.7.7
ENV TERRAGRUNT_VERSION=v0.1.2

RUN apk update && \
    apk add bash ca-certificates curl docker gawk git git openssl py-pip unzip wget

RUN cd /tmp && \
    curl -sSLO https://github.com/gruntwork-io/terragrunt/releases/download/${TERRAGRUNT_VERSION}/terragrunt_linux_amd64 && \
    mv terragrunt_linux_amd64 /usr/local/bin/terragrunt && \
    curl -sSLO https://releases.hashicorp.com/terraform/$TERRAFORM_VERSION/terraform_${TERRAFORM_VERSION}_linux_amd64.zip && \
    unzip terraform_*_linux_amd64.zip -d /usr/bin && \
    rm -rf /tmp/* && \
    rm -rf /var/tmp/* && \
    chmod 755 /usr/local/bin/terragrunt

ADD ./requirements.txt /infra/requirements.txt
RUN pip install -r /infra/requirements.txt

ADD . /infra
