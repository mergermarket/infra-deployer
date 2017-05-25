import unittest

from string import uppercase, lowercase, digits
from base64 import b64encode
import json

from mock import patch, Mock
from hypothesis import given, assume, settings
from hypothesis.strategies import text
from botocore.exceptions import ClientError

from cdflow_commands.release import Release, ReleaseConfig


IDENTIFIER_ALPHABET = uppercase + lowercase + digits + '-_'


class TestRelease(unittest.TestCase):

    def setUp(self):
        self._boto_ecr_client = Mock()
        self._boto_ecr_client.get_authorization_details.return_value = {
            'authorizationData': [
                {
                    'authorizationToken': b64encode('{}:{}'.format(
                        'dummy-username', 'dummy-password'
                    )),
                    'proxyEndpoint': 'dummy-proxy-endpoint'
                }
            ]
        }

    @given(text(alphabet=lowercase + digits + '-', min_size=1, max_size=10))
    @given(text(alphabet=digits, min_size=12, max_size=12))
    @given(text(alphabet=IDENTIFIER_ALPHABET, min_size=1, max_size=10))
    @settings(max_examples=10)
    def test_builds_container(
        self, component_name, dev_account_id, aws_region
    ):
        config = ReleaseConfig(dev_account_id, 'dummy-account-id', aws_region)
        boto_ecr_client = Mock()
        release = Release(config, boto_ecr_client, component_name)
        with patch('cdflow_commands.release.check_call') as check_call:
            release.create()

            image_name = '{}.dkr.ecr.{}.amazonaws.com/{}:{}'.format(
                dev_account_id,
                aws_region,
                component_name,
                'dev'
            )

            check_call.assert_called_once_with(
                ['docker', 'build', '-t', image_name]
            )

    @given(text(alphabet=IDENTIFIER_ALPHABET, min_size=1, max_size=12))
    def test_tags_container_with_version(self, version):
        component_name = 'dummy-component'
        dev_account_id = 'dummy-account-id'
        aws_region = 'dummy-region'

        config = ReleaseConfig(dev_account_id, 'dummy-account-id', aws_region)
        release = Release(
            config, self._boto_ecr_client, component_name, version
        )

        with patch('cdflow_commands.release.check_call') as check_call:
            release.create()

            image_name = '{}.dkr.ecr.{}.amazonaws.com/{}:{}'.format(
                dev_account_id,
                aws_region,
                component_name,
                version
            )

            check_call.assert_any_call(
                ['docker', 'build', '-t', image_name]
            )

    @given(text(alphabet=IDENTIFIER_ALPHABET + ':/', min_size=8, max_size=16))
    @given(text(alphabet=IDENTIFIER_ALPHABET, min_size=8, max_size=16))
    @given(text(alphabet=IDENTIFIER_ALPHABET, min_size=8, max_size=16))
    @settings(max_examples=10)
    def test_build_with_version_pushes_to_repo(
        self, proxy_endpoint, username, password
    ):
        dev_account_id = 'dummy-account-id'
        aws_region = 'dummy-region'
        component_name = 'dummy-component'
        version = '1.2.3'

        boto_ecr_client = Mock()
        boto_ecr_client.get_authorization_details.return_value = {
            'authorizationData': [
                {
                    'authorizationToken': b64encode('{}:{}'.format(
                        username, password
                    )),
                    'proxyEndpoint': proxy_endpoint
                }
            ]
        }

        config = ReleaseConfig(dev_account_id, 'dummy-account-id', aws_region)
        release = Release(config, boto_ecr_client, component_name, version)

        with patch('cdflow_commands.release.check_call') as check_call:
            release.create()

            boto_ecr_client.describe_repositories.assert_called_once_with(
                repositoryNames=[component_name]
            )

            boto_ecr_client.get_authorization_details.assert_called_once()

            check_call.assert_any_call([
                'docker', 'login',
                '-u', username, '-p', password, proxy_endpoint
            ])

            image_name = '{}.dkr.ecr.{}.amazonaws.com/{}:{}'.format(
                dev_account_id,
                aws_region,
                component_name,
                version
            )

            check_call.assert_any_call([
                'docker', 'push', image_name
            ])

    @given(text(alphabet=IDENTIFIER_ALPHABET, min_size=8, max_size=16))
    def test_repo_created_when_it_does_not_exist(self, component_name):
        dev_account_id = 'dummy-account-id'
        aws_region = 'dummy-region'
        version = '1.2.3'

        boto_ecr_client = Mock()
        boto_ecr_client.get_authorization_details.return_value = {
            'authorizationData': [
                {
                    'authorizationToken': b64encode('{}:{}'.format(
                        'dummy-username', 'dummy-password'
                    )),
                    'proxyEndpoint': 'dummy-proxy-endpoint'
                }
            ]
        }
        boto_ecr_client.describe_repositories.side_effect = ClientError(
            {'Error': {'Code': 'RepositoryNotFoundException'}},
            None
        )

        config = ReleaseConfig(dev_account_id, 'dummy-account-id', aws_region)
        release = Release(
            config, boto_ecr_client, component_name, version
        )

        with patch('cdflow_commands.release.check_call'):
            release.create()

            boto_ecr_client.create_repository.assert_called_once_with(
                repositoryName=component_name
            )

    @given(text(alphabet=lowercase + uppercase, min_size=8, max_size=16))
    def test_exception_re_raised(self, error_code):
        assume(error_code != 'RepositoryNotFoundException')

        dev_account_id = 'dummy-account-id'
        aws_region = 'dummy-region'
        component_name = 'dummy-component'
        version = '1.2.3'

        boto_ecr_client = Mock()
        boto_ecr_client.get_authorization_details.return_value = {
            'authorizationData': [
                {
                    'authorizationToken': b64encode('{}:{}'.format(
                        'dummy-username', 'dummy-password'
                    )),
                    'proxyEndpoint': 'dummy-proxy-endpoint'
                }
            ]
        }
        boto_ecr_client.describe_repositories.side_effect = ClientError(
            {'Error': {'Code': error_code}},
            None
        )

        config = ReleaseConfig(dev_account_id, 'dummy-account-id', aws_region)
        release = Release(
            config, boto_ecr_client, component_name, version
        )

        with patch('cdflow_commands.release.check_call'):
            self.assertRaises(ClientError, release.create)

    @given(text(alphabet=IDENTIFIER_ALPHABET, min_size=8, max_size=16))
    @given(text(alphabet=digits, min_size=12, max_size=12))
    @settings(max_examples=50)
    def test_policy_set_on_repo(self, prod_account_id, component_name):
        dev_account_id = 'dummy-account-id'
        aws_region = 'dummy-region'
        version = '1.2.3'

        boto_ecr_client = Mock()
        boto_ecr_client.get_authorization_details.return_value = {
            'authorizationData': [
                {
                    'authorizationToken': b64encode('{}:{}'.format(
                        'dummy-username', 'dummy-password'
                    )),
                    'proxyEndpoint': 'dummy-proxy-endpoint'
                }
            ]
        }

        config = ReleaseConfig(
            dev_account_id,
            prod_account_id,
            aws_region
        )
        release = Release(
            config, boto_ecr_client, component_name, version
        )

        with patch('cdflow_commands.release.check_call'):
            release.create()

            boto_ecr_client.set_repository_policy.assert_called_once_with(
                repositoryName=component_name,
                policyText=json.dumps({
                    'Version': '2008-10-17',
                    'Statement': [{
                        'Sid': 'allow production',
                        'Effect': 'Allow',
                        'Principal': {
                            'AWS': 'arn:aws:iam::{}:root'.format(
                                prod_account_id
                            )
                        },
                        'Action': [
                            'ecr:GetDownloadUrlForLayer',
                            'ecr:BatchGetImage',
                            'ecr:BatchCheckLayerAvailability'
                        ]
                    }]
                }, sort_keys=True)
            )
