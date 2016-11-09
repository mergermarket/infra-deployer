"""
Build and release container for deployment to MMG ECS infrastructure

Usage:
    infra/release [<version>] [-c <component-name>] [-l <leg>]

Options:
    # Override component name (default from repo name)
    -c <component-name>, --component-name <component-name>
    # Set a leg postfix for the service name
    -l <leg>, --leg <leg>
"""

from os import environ, path, access, X_OK
import sys
from subprocess import check_call
from docopt import docopt
import botocore.exceptions
from base64 import b64decode

import util

PREFIX = 'infra/release: '


def executable(f):
    return path.isfile(f) and access(f, X_OK)


class Release:

    """
    Manages the creation of a release.
    """

    def __init__(self, argv, environ, shell_runner=None, service_json_loader=None):
        arguments = docopt(__doc__, argv=argv)
        self.shell_runner = shell_runner if shell_runner is not None else util.ShellRunner()
        self.version = arguments.get('<version>')
        self.component_name = util.get_component_name(arguments, environ, self.shell_runner)
        if not service_json_loader:
            service_json_loader = util.ServiceJsonLoader()
        self.metadata = util.apply_metadata_defaults(
            service_json_loader.load(),
            self.component_name
        )
        self.platform_config = util.load_platform_config(self.metadata['REGION'])
        self.aws = None

    def _get_aws(self):
        """
        Gets an AWS session.
        """
        if self.aws is None:
            self.aws = util.assume_role(self.metadata['REGION'], self.platform_config)
        return self.aws

    def _set_aws(self, aws):
        """
        Sets an AWS session - used to inject dependency in tests.
        """
        self.aws = aws

    def _ecr(self):
        """
        Get an ECR client.
        """
        return self._get_aws().client('ecr')

    def _build_slug(self):
        """
        Builds a slug in target/slug.tgz in preparation for the docker build.
        """
        print(PREFIX + 'building slug')
        self.shell_runner.run("mkdir -p target")
        self.shell_runner.run("""
            tar -cf - $(
                    ls -A |
                    grep -v '^.git/\\{0,1\\}$' |
                    grep -v '^build/\\{0,1\\}$' |
                    grep -v '^target/\\{0,1\\}$' |
                    grep -v '^infrastructure/\\{0,1\\}$' |
                    grep -v '^node_modules/\\{0,1\\}$'
                ) | docker run -v /tmp/cache:/tmp/cache:rw --rm -i -a stdin -a stdout -a stderr
        """ + self.metadata['SLUG_BUILDER_DOCKER_OPTS'] + """ flynn/slugbuilder - > target/slug.tgz
        """)
        self.shell_runner.run('cp /infra/Dockerfile_slug target/Dockerfile')
        self.metadata['DOCKER_BUILD_DIR'] = 'target'

    def ecr_registry(self):
        return util.ecr_registry(self.platform_config, self.metadata['REGION'])

    def create(self):
        """
        Create the release.
        """
        if self.metadata['TYPE'] == 'slug':
            self._build_slug()

        if executable('prepare-docker-build'):
            version = self.version if self.version is not None else 'dev'
            command = "./prepare-docker-build %s-%s %s" % (self.component_name, version,
                                                           self.metadata['DOCKER_BUILD_DIR'])
            print(PREFIX + "running " + command)
            check_call(command, shell=True)

        # generate container image name
        image = util.container_image_name(self.ecr_registry(), self.component_name, self.version)

        print(PREFIX + 'building docker image %s' % (image))
        check_call("docker build -t %s %s" % (image, self.metadata['DOCKER_BUILD_DIR']), shell=True)
        print(PREFIX + 'image ' + image + ' successfully built')

        if executable('on-docker-build'):
            command = './on-docker-build %s' % (image)
            print(PREFIX + 'running %s' % (command))
            check_call(command, shell=True)

        if self.version is None:
            print(PREFIX + 'no version supplied, push skipped')
        else:
            self._push_to_ecr(image)

        print(PREFIX + 'done.')

    def _ensure_ecr_repository_exists(self):
        """
        Create the ECR repository if it doesn't exist.
        """
        print(PREFIX + 'checking ECR repository')
        try:
            self._ecr().describe_repositories(
                repositoryNames=[self.component_name],
            )
        except botocore.exceptions.ClientError as err:
            if err.response['Error']['Code'] != 'RepositoryNotFoundException':
                raise
            self._ecr().create_repository(
                repositoryName=self.component_name,
            )

    def _push_to_ecr(self, image):
        """
        Push the image to ECR.
        """
        self._ensure_ecr_repository_exists()

        print(PREFIX + 'logging into ECR')
        auth_data = self._ecr().get_authorization_token()['authorizationData']
        for details in auth_data:
            username, password = b64decode(details['authorizationToken'].encode('utf-8')).decode('utf-8').split(':')
            check_call("docker login -u '%s' -p '%s' %s" % (username, password, details['proxyEndpoint']), shell=True)

        print(PREFIX + 'pushing image %s' % (image))
        check_call('docker push %s' % (image), shell=True)

        latest_tag = '%s/%s:latest' % (self.ecr_registry(), self.component_name)
        print(PREFIX + 'pushing tag %s' % (latest_tag))
        check_call('docker tag %s %s' % (image, latest_tag), shell=True)
        check_call('docker push %s' % (latest_tag), shell=True)


def main():
    """
    Entry-point for script.
    """
    try:
        release = Release(sys.argv[1:], environ)
        release.create()
    except util.UserError as e:
        print('error: ' + str(e))
        exit(1)

if __name__ == '__main__':
    main()
