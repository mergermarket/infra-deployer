"""
Thin wrapper over terraform, expecting exactly the same syntax as terraform itself.

Extracts the values of the terraform vars <required_vars>, and uses them for it's own purposes

"""

import os
import sys
import re
from subprocess import check_call
import shutil
import boto3
import hashlib
import pdb
import util


s3_bucket_prefix = "terraform-tfstate-"
required_vars = ['env', 'component', 'state_aws_region', 'aws_region', 'account_id']
optional_vars = ['state_aws_region']


def generate_terraform_config(parsed_args):
    region = parsed_args.get('state_aws_region', parsed_args['aws_region'])
    environment = parsed_args['env']
    component_name = parsed_args['component']
    account_id = parsed_args['account_id']

    s3_bucket_suffix = hashlib.md5(account_id.encode('utf-8')).hexdigest()[:6]
    s3_bucket_name = s3_bucket_prefix + s3_bucket_suffix

    """Generates terraform config as per documentation"""
    state_file_id = "{env}-{component}".format(env=environment, component=component_name)

    state_config_template = """
terraform {{
  backend "s3" {{
    bucket = "{s3_bucket}"
    key    = "{env}/{component}/terraform.tfstate"
    region = "{region}"
    dynamodb_table = "terraform_locks"
  }}
}}
"""


    with open('infra/state.tf', 'w') as f:
        t=state_config_template.format(
            region=region,
            s3_bucket=s3_bucket_name,
            env=environment,
            component=component_name
        )
        f.write(t)


def assume_role(parsed_args):
    admin_role_arn = "arn:aws:iam::" + parsed_args['account_id'] + ":role/admin"

    unpriv_client = boto3.client('sts')

    priv_role = unpriv_client.assume_role(
        RoleArn=admin_role_arn,
        RoleSessionName='terraform'
    )
    return priv_role


def terraform(all_args, parsed_args, role_creds):

    priv_creds = role_creds['Credentials']

    env = os.environ.copy()
    env['AWS_ACCESS_KEY_ID'] = priv_creds['AccessKeyId']
    env['AWS_SECRET_ACCESS_KEY'] = priv_creds['SecretAccessKey']
    env['AWS_SESSION_TOKEN'] = priv_creds['SessionToken']
    env['AWS_DEFAULT_REGION'] = parsed_args['aws_region']

    tmp_secrets = util.Credstash()._generate_decrypted_credentials('platform',parsed_args['component'],parsed_args['env'],env)
    # Required, as we want this in a predictable place for external use
    os.symlink(tmp_secrets, '/tmp/secrets.json')

    check_call(["terraform", "init"], env=env, cwd='infra')
    check_call(["terraform"] + all_args, env=env, cwd='infra')


def cleanup():
    try:
        os.remove("infra/state.tf")
    except:
        pass
    try:
        shutil.rmtree("infra/.terraform")
    except:
        pass


def parse_args(args):
    found_vars = {}
    for var in required_vars:
        regex = re.compile('^' + var + '=')
        for arg in args:
            if regex.search(arg):
                short_arg = arg.split("=")
                found_vars[var] = short_arg[1]
    for var in optional_vars:
        regex = re.compile('^' + var + '=')
        for arg in args:
            if regex.search(arg):
                short_arg = arg.split("=")
                found_vars[var] = short_arg[1]
    for var in required_vars:
        if var in found_vars:
            pass
        else:
            sys.stderr.write("Error: Please specify missing variable: -var " + var + "=<value>")
            sys.exit(1)

    return found_vars


def main():
    all_args = sys.argv[1:]
    parsed_args = parse_args(all_args)

    cleanup()
    role_creds = assume_role(parsed_args)

    generate_terraform_config(parsed_args)
    terraform(all_args, parsed_args, role_creds)
    cleanup()


if __name__ == '__main__':
    main()
