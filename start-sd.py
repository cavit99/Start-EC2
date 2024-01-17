import boto3
import logging
import os
import getpass
from botocore.exceptions import NoCredentialsError, ClientError

# Set up logging
logging.basicConfig(filename='start-sd.log', level=logging.INFO, format='%(asctime)s %(message)s')

# Define variables
LAUNCH_TEMPLATE_ID = 'lt-0e096fddfc03f20b6'
REMOTE_PORT_NUMBER = '8188'
LOCAL_PORT_NUMBER = '8188'
awsregion = 'eu-north-1'
awstagvalue = 'sd'
user_data = '''#!/bin/bash
cd /home/ubuntu/stable-diffusion-webui && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull
cd /home/ubuntu/kohya_ss && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull
cd /home/ubuntu/ComfyUI && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull
find /home/ubuntu/stable-diffusion-webui/extensions -maxdepth 2 -type d -exec sh -c 'cd {} && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull' \;
find /home/ubuntu/ComfyUI/custom_nodes -maxdepth 2 -type d -exec sh -c 'cd {} && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull' \;
sudo apt-get update
nohup fio --filename=/dev/nvme0n1 --rw=read --bs=1M --iodepth=32 --ioengine=libaio --direct=1 --name=volume-initialize &
'''

def is_aws_configured() -> bool:
    try:
        session = boto3.Session()
        session.get_credentials()
        return True
    except NoCredentialsError:
        print("AWS is not configured. Please enter your AWS credentials.\nPlease note the credentials will not be stored. In the future you should configure AWS-CLI instead to ensure that the credentials persist")
        aws_access_key_id = getpass.getpass("Enter your AWS Access Key ID: ")
        aws_secret_access_key = getpass.getpass("Enter your AWS Secret Access Key: ")
        region_name = input("Enter your AWS region (e.g., eu-north-1): ") or 'eu-north-1'
        
        # Set environment variables
        os.environ['AWS_ACCESS_KEY_ID'] = aws_access_key_id
        os.environ['AWS_SECRET_ACCESS_KEY'] = aws_secret_access_key
        os.environ['AWS_DEFAULT_REGION'] = region_name

        return True
    
def run_instance(ec2, launch_template_id: str, user_data: str) -> str:
    print("Creating a new instance...")
    instance = ec2.create_instances(
        LaunchTemplate={'LaunchTemplateId': launch_template_id},
        UserData=user_data,
        MaxCount=1,
        MinCount=1
    )[0]
    instance.wait_until_running()
    print(f"Instance {instance.id} is now running.")
    return instance.id

def get_instance_id(ec2, name_tag: str) -> str:
    instances = ec2.instances.filter(
        Filters=[{'Name': 'tag:Name', 'Values': [name_tag]}])
    for instance in instances:
        return instance.id
    return ""

def start_instance_if_stopped(ec2, instance_id: str) -> None:
    instance = ec2.Instance(instance_id)
    if instance.state['Name'] != 'running':
        print(f"Starting instance {instance_id}...")
        instance.start()
        instance.wait_until_running()
        print(f"Instance {instance_id} is now running.")

def start_ssm_session(ssm, instance_id: str) -> bool:
    print(f"Starting a session with instance {instance_id}...")
    try:
        response = ssm.describe_instance_information(InstanceInformationFilterList=[{'key': 'InstanceIds', 'valueSet': [instance_id]}])
        if not response['InstanceInformationList']:
            logging.error(f"SSM agent is not correctly configured on the instance: {instance_id}")
            return False
        ssm.start_session(Target=instance_id, DocumentName='AWS-StartPortForwardingSession',
                          Parameters={'portNumber': [REMOTE_PORT_NUMBER], 'localPortNumber': [LOCAL_PORT_NUMBER]})
        print("Session started successfully.")
        return True
    except ClientError as e:
        logging.error(f"Failed to start a session with the instance: {e}")
        return False

def main() -> None:

    if not is_aws_configured():
        logging.error("AWS is not configured. Please configure it and try again.")
        return
    print("AWS credentials are configured, proceeding.")

    session = boto3.Session(region_name=awsregion)
    ec2 = session.resource('ec2')
    ssm = session.client('ssm')

    existing_instance_id = get_instance_id(ec2, awstagvalue)

    if existing_instance_id:
        logging.info(f"An instance with tag value '{awstagvalue}' exists. Instance ID: {existing_instance_id}")
        start_instance_if_stopped(ec2, existing_instance_id)
        instance_id = existing_instance_id
    else:
        try:
            instance_id = run_instance(ec2, LAUNCH_TEMPLATE_ID, user_data)
            logging.info(f"Instance {instance_id} is running.")
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