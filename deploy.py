"""
Deploy to MMG ECS infrastructure

Usage:
    infra/deploy <environment> <version> [-c <component-name>] [-l <leg>]

Options:
    # Override component name (default from repo name)
    -c <component-name>, --component-name <component-name>
    # Set a leg postfix for the service name
    -l <leg>, --leg <leg>
"""


from docopt import docopt
from subprocess import check_call

import hashlib
import logging
import util
import os
import sys
import shutil


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def get_nested_key(data, keys, default=None):
    """
    Takes a nested dict and array of keys and returns the corresponding value, or the passed
    in default (default None) if it does not exist.
    """
    if keys[0] not in data:
        return default
    elif len(data) == 1:
        return data[keys[0]]
    else:
        return get_nested_key(data[keys[0]], keys[1:], default)


class Deployment:

    """
    Manages the process of deployment.
    """

    def __init__(self, argv, environ, shell_runner=None, service_json_loader=None):
        arguments = docopt(__doc__, argv=argv)
        self.shell_runner = shell_runner if shell_runner is not None else util.ShellRunner()
        self.environment = arguments.get('<environment>')
        self.version = arguments.get('<version>')
        self.component_name = util.get_component_name(arguments, environ, self.shell_runner)
        self.leg = arguments.get('--leg')
        if not service_json_loader:
            service_json_loader = util.ServiceJsonLoader()
        self.config = None
        self.metadata = util.apply_metadata_defaults(
            service_json_loader.load(),
            self.component_name
        )
        self.platform_config = util.load_platform_config(self.metadata['REGION'])
        self.aws = None

    def run(self):
        """
        Run the deployment.
        """
        print('deploying %s version %s to %s' %
              (self.component_name, self.version, self.environment))

        # initialise Boto Session
        self.get_aws()

        # construct local env
        env = os.environ.copy()
        (aws_access_key, aws_secret_key, aws_session_token) = self._get_aws_credentials(self.aws)
        env['AWS_ACCESS_KEY_ID'] = aws_access_key
        env['AWS_SECRET_ACCESS_KEY'] = aws_secret_key
        env['AWS_SESSION_TOKEN'] = aws_session_token

        account_id = self.get_account_id()

        # generate container image name
        image = util.container_image_name(util.ecr_registry(self.platform_config, self.metadata['REGION']), self.component_name, self.version)

        print("Preparing S3 bucket for terragrunt...")
        s3_bucket_name = self.terragrunt_s3_bucket_name(account_id.encode('utf-8'))
        self.s3_bucket_prep(s3_bucket_name)

        print("Generating terragrunt config...")
        self.generate_terragrunt_config(self.metadata['REGION'], s3_bucket_name, self.environment,
                                        self.component_name)

        # get all the relevant modules
        check_call("terraform get infra", env=env, shell=True)

        self.terragrunt('plan', self.environment, image, self.component_name, self.metadata['REGION'],
                             self.metadata['TEAM'], self.version, env)
        self.terragrunt('apply', self.environment, image, self.component_name, self.metadata['REGION'],
                              self.metadata['TEAM'], self.version, env)

        # clean up all irrelevant files
        self.cleanup()

    def get_account_id(self):
        """
        Get the account id of the current AWS account.
        """
        return self.get_aws().client('sts').get_caller_identity()['Account']

    def get_aws(self):
        """
        Gets an AWS session.
        """
        if self.aws is None:
            self.aws = util.assume_role(self.metadata['REGION'], self.platform_config, self.prod())
        return self.aws

    def _get_aws_credentials(self, session):
        """
        Gets temporary AWS credentials based on the passed boto3.session.Session

        Params:
            boto3.session.Session: Boto3 session to get the credentials against

        Returns:
            array: [access_key, secret_key]
        """
        try:
            aws_access_key = session.get_credentials().access_key
            aws_secret_key = session.get_credentials().secret_key
            aws_session_token = session.get_credentials().token
        except:
            logger.error("Exception caught while trying to get temporary AWS credentials!")

        return (aws_access_key, aws_secret_key, aws_session_token)

    def set_aws(self, aws):
        """
        Sets an AWS session - used to inject dependency in tests.
        """
        self.aws = aws

    def terragrunt_s3_bucket_name(self, account_id):
        """Generates S3 bucket name Terragrunt will use based on account
        ID.

        Takes the ID and hashes it to make it smaller (6 characters)

        Returns:
            string: The return value.  Non-empty for success

        """
        return "terraform-tfstate-{}".format(hashlib.md5(account_id).hexdigest()[:6])

    def s3_bucket_prep(self, s3_bucket_name):
        """Checks whether given S3 Bucket exists and if not, it creates
        it

        Arguments:
            s3_bucket_name - name of the bucket (string)

        Returns:
            bool: True if successful; False otherwise
        """
        s3 = self.aws.resource('s3')

        if not s3.Bucket(s3_bucket_name) in s3.buckets.all():
            logger.info("Bucket %s doesn't exist... Trying to create...", s3_bucket_name)
            try:
                s3.create_bucket(Bucket=s3_bucket_name,
                                 CreateBucketConfiguration={'LocationConstraint': 'eu-west-1'})
            except Exception as e:
                logger.exception("Error while trying to create bucket %s (%s)",
                    s3_bucket_name, str(e))

    def generate_terragrunt_config(self, region, s3_bucket_name, environment, component_name):
        """Generates terragrunt config as per terragrunt documentation"""
        state_file_id = "{env}-{component}".format(env=environment, component=component_name)

        grunt_config_template = """lock = {{
  backend = "dynamodb"
  config {{
    state_file_id = "{state_file_id}"
    aws_region = "{region}"
    table_name = "terragrunt_locks"
    max_lock_retries = 360
  }}
}}
remote_state = {{
  backend = "s3"
  config {{
    encrypt = "true"
    bucket = "{s3_bucket}"
    key = "{env}/{component}/terraform.tfstate"
    region = "{region}"
  }}
}}"""

        with open('.terragrunt', 'w') as f:
            f.write(grunt_config_template.format(
                state_file_id=state_file_id,
                region=region,
                s3_bucket=s3_bucket_name,
                env=environment,
                component=component_name
            ))

    def terragrunt(self, action, environment, image, component, region, team, version, exec_env):
        """
        Runs terragrunt.
        """
        configfile = "config/%s.json" % environment
        if os.path.exists(configfile):
            environmentconfig = " -var-file=%s" % configfile
        else:
            environmentconfig = ""

        t = ("terragrunt {action} -var aws.region={region}"
             " -var component={component}"
             " -var env={environment}"
             " -var image={image}"
             " -var team={team}"
             " -var 'version=\"{version}\"'"
             " {environmentconfig}"
             " infra")

        try:
            check_call(
                t.format(
                    action=action,
                    component=component,
                    environmentconfig=environmentconfig,
                    environment=environment,
                    image=image,
                    region=region,
                    team=team,
                    version=version,
                ),
                env=exec_env,
                shell=True
            )
        except Exception:
            logger.exception('Exception caught while executing terragrunt %s!', action)

        return True

    def cleanup(self):
        # clean up after terraform run
        try:
            shutil.rmtree(".terraform")
            os.remove(".terragrunt")
        except:
            pass

    def prod(self):
        """
        Returns True if the prod account should be used.
        """
        return self.environment == 'live' or self.environment == 'debug'

    def account(self):
        """
        Returns the account identifier for the deployment.
        """
        if self.prod():
            return self.metadata['ACCOUNT_PREFIX'] + 'prod'
        else:
            return self.metadata['ACCOUNT_PREFIX'] + 'dev'

    def cluster(self):
        """
        Returns the cluster name.
        """
        if self.environment == 'live':
            return self.metadata.get('PRODUCTION_CLUSTER', 'production')
        else:
            return self.metadata.get('NON_PRODUCTION_CLUSTER', 'non-production')

    def dns_zone(self):
        """
        Returns the DNS zone for the deployment.
        """
        domain = self.metadata['DOMAIN']
        if domain is None:
            raise util.UserError('cannot infer dns zone when DOMAIN set to null')
        return ('' if self.environment == 'live' else 'dev.') + domain + '.'

    def hostname(self):
        """
        Returns the hostname for the service.
        """
        if self.environment == "live":
            return "%s.%s" % (self.metadata['DNS_NAME'], self.dns_zone())
        else:
            return "%s-%s.%s" % (self.environment, self.metadata['DNS_NAME'], self.dns_zone())


def main():
    """
    Entry-point for script.
    """
    try:
        deployment = Deployment(sys.argv[1:], os.environ)
        deployment.run()
    except util.UserError as err:
        print('error: %s' % str(err), file=sys.stderr)
        exit(1)


if __name__ == '__main__':
    main()
