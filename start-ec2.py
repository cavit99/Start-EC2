# MIT License
# Copyright (c) 2024 Cavit Erginsoy

import boto3
import logging
import sys
import requests
import yaml
from botocore.exceptions import NoCredentialsError, ClientError

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(message)s',
    handlers=[
        logging.FileHandler('start-ec2.log'),
        logging.StreamHandler(sys.stdout)  # This will output to the console
    ]
)

# Load the configuration file
with open('config.yaml', 'r') as f:
    config = yaml.safe_load(f)

# Use the configuration values
LAUNCH_TEMPLATE_ID = config['template']
REMOTE_PORT_NUMBER = config['remote_port']
LOCAL_PORT_NUMBER = config['local_port']
awsregion = config['region']
awstagvalue = config['tag_value']


logging.info(f"Using Launch Template ID: {LAUNCH_TEMPLATE_ID}")
logging.info(f"Using Remote Port Number: {REMOTE_PORT_NUMBER}")
logging.info(f"Using Local Port Number: {LOCAL_PORT_NUMBER}")
logging.info(f"Using AWS region: {awsregion}")
logging.info(f"Using AWS tag value: {awstagvalue}")

confirmation = input("Press Enter to continue or type q to exit: ")
if confirmation:
    logging.info("Exiting...")
    sys.exit()

def is_connected():
    try:
        requests.get('http://www.google.com', timeout=5)
        return True
    except requests.exceptions.RequestException:
        return False

def is_aws_configured() -> boto3.Session:
    try:
        session = boto3.Session()
        credentials = session.get_credentials()
        if credentials is None:
            return None
        return session
    except NoCredentialsError:
        return None
 
def does_launch_template_exist(ec2_client, launch_template_id: str) -> bool:
    try:
        response = ec2_client.describe_launch_templates(
            LaunchTemplateIds=[launch_template_id]
        )
        return bool(response['LaunchTemplates'])
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'InvalidLaunchTemplateId.NotFound':
            logging.error(f"Launch template {launch_template_id} does not exist: {e}")
        elif error_code == 'InvalidLaunchTemplateId.Malformed':
            logging.error(f"The launch template ID {launch_template_id} is malformed: {e}")
        else:
            logging.error(f"An unexpected error occurred: {e}")
        return False
    except Exception as e:
        logging.error(f"An error occurred: {e}")
        return False

# Create a new instance
def run_instance(ec2_resource, launch_template_id: str) -> str:
    logging.info("Creating a new instance...")
    instance = ec2_resource.create_instances(
        LaunchTemplate={'LaunchTemplateId': launch_template_id},
        MaxCount=1,
        MinCount=1
    )[0]
    logging.info(f"Waiting for instance {instance.id} to start...")
    instance.wait_until_running()
    logging.info(f"Instance {instance.id} is now running.")
    return instance.id

def get_instance_id(ec2_resource, name_tag: str) -> str:
    instances = ec2_resource.instances.filter(
        Filters=[
            {'Name': 'tag:Name', 'Values': [name_tag]},
            {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping', 'stopped']}
        ])
    for instance in instances:
        return instance.id
    logging.info(f"No instances with tag value '{name_tag}' found.")
    return ""

def start_instance_if_stopped(ec2_resource, instance_id: str) -> None:
    instance = ec2_resource.Instance(instance_id)
    if instance.state['Name'] != 'running':
        logging.info(f"Starting instance {instance_id}...")
        try:
            instance.start()
            delay = 15
            max_attempts = 20
            logging.info(f"Waiting for instance {instance_id} to start...\nChecking every {delay} seconds, max {max_attempts} attempts")
            instance.wait_until_running(WaiterConfig={
                'Delay': delay,
                'MaxAttempts': max_attempts
            })
            logging.info(f"Instance {instance_id} is now running.")
        except ClientError as e:
            if e.response['Error']['Code'] == 'UnauthorizedOperation':
                logging.error("You are not authorized to start the instance. Please check your AWS permissions.")
                sys.exit(1)  # Exit the script with an error code
            else:
                logging.error(f"An error occurred while starting the instance: {e}")
                sys.exit(1)  # Exit the script with an error code

def start_ssm_session(ssm, instance_id: str) -> bool:
    logging.info(f"Starting a session with instance {instance_id}...")
    try:
        response = ssm.describe_instance_information(InstanceInformationFilterList=[{'key': 'InstanceIds', 'valueSet': [instance_id]}])
        if not response['InstanceInformationList']:
            logging.error(f"SSM agent is not correctly configured on the instance: {instance_id}")
            return False
        logging.info("SSM agent is correctly configured. Attempting to start session...")
        ssm.start_session(Target=instance_id, DocumentName='AWS-StartPortForwardingSession',
                          Parameters={'portNumber': [REMOTE_PORT_NUMBER], 'localPortNumber': [LOCAL_PORT_NUMBER]})
        return True
    except ClientError as e:
        return False

def main() -> None:

    if not is_connected():
        logging.error("No internet connection. Please check your connection and try again.")
        return
    
    session = is_aws_configured()
    if session is None:
        logging.error("AWS is not configured. Please enter your AWS credentials.")
        return
    
    logging.info("AWS credentials are configured, proceeding.")
    ec2_resource = session.resource('ec2', region_name=awsregion)
    ec2_client = session.client('ec2', region_name=awsregion)
    ssm = session.client('ssm', region_name=awsregion)


    if not does_launch_template_exist(ec2_client, LAUNCH_TEMPLATE_ID):
        return

    existing_instance_id = get_instance_id(ec2_resource, awstagvalue)

    if existing_instance_id:
        logging.info(f"An instance with tag value '{awstagvalue}' exists. Instance ID: {existing_instance_id}")
        try:
            start_instance_if_stopped(ec2_resource, existing_instance_id)
        except ClientError as e:
            if 'UnauthorizedOperation' in str(e):
                logging.error("You do not have the necessary permissions to start instances. Please check your IAM policies.")
            else:
                logging.error(f"Failed to start instance: {e}")
            return
        instance_id = existing_instance_id
    else:
        try:
            instance_id = run_instance(ec2_resource, LAUNCH_TEMPLATE_ID)
        except ClientError as e:
            if 'UnauthorizedOperation' in str(e):
                logging.error("You do not have the necessary permissions to create instances. Please check your IAM policies.")
            else:
                logging.error(f"Failed to create instance: {e}")
            return

    if start_ssm_session(ssm, instance_id):
        logging.info("Successfully started a session with the instance.")
    else:
        logging.error("Failed to start a session with the instance.")


if __name__ == "__main__":
    main()