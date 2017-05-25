from subprocess import check_call
from collections import namedtuple
from base64 import b64decode
import json

from botocore.exceptions import ClientError


ReleaseConfig = namedtuple('ReleaseConfig', [
    'dev_account_id',
    'prod_account_id',
    'aws_region',
])


class Release(object):

    def __init__(self, config, boto_ecr_client, component_name, version=None):
        self._dev_account_id = config.dev_account_id
        self._prod_account_id = config.prod_account_id
        self._aws_region = config.aws_region
        self._boto_ecr_client = boto_ecr_client
        self._component_name = component_name
        self._version = version

    def create(self):
        check_call(['docker', 'build', '-t', self._image_name])

        if self._version:
            self._ensure_ecr_repo_exists()
            self._ensure_ecr_policy_set()
            self._docker_login()
            self._docker_push()

    @property
    def _image_name(self):
        return '{}.dkr.ecr.{}.amazonaws.com/{}:{}'.format(
            self._dev_account_id,
            self._aws_region,
            self._component_name,
            self._version or 'dev'
        )

    def _ensure_ecr_repo_exists(self):
        try:
            self._boto_ecr_client.describe_repositories(
                repositoryNames=[self._component_name]
            )
        except ClientError as e:
            if e.response['Error']['Code'] != 'RepositoryNotFoundException':
                raise
            self._boto_ecr_client.create_repository(
                repositoryName=self._component_name
            )

    def _ensure_ecr_policy_set(self):
        self._boto_ecr_client.set_repository_policy(
            repositoryName=self._component_name,
            policyText=json.dumps({
                'Version': '2008-10-17',
                'Statement': [{
                    'Sid': 'allow production',
                    'Effect': 'Allow',
                    'Principal': {'AWS': 'arn:aws:iam::{}:root'.format(
                         self._prod_account_id
                    )},
                    'Action': [
                        'ecr:GetDownloadUrlForLayer',
                        'ecr:BatchGetImage',
                        'ecr:BatchCheckLayerAvailability'
                    ]
                }]
            }, sort_keys=True)
        )

    def _docker_login(self):
        response = self._boto_ecr_client.get_authorization_details()

        username, password = b64decode(
            response['authorizationData'][0]['authorizationToken']
        ).split(':')

        proxy_endpoint = response['authorizationData'][0]['proxyEndpoint']

        check_call(
            ['docker', 'login', '-u', username, '-p', password, proxy_endpoint]
        )

    def _docker_push(self):
        check_call(['docker', 'push', self._image_name])
