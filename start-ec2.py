# MIT License
# Copyright (c) 2024 Cavit Erginsoy

import boto3
import logging
import sys
import yaml
import subprocess
import time
import shutil
import botocore.config
import threading
import requests
import base64
import traceback
import socket

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
try:
    with open('config.yaml', 'r') as f:
        config = yaml.safe_load(f)
        user_data = base64.b64encode(config['user_data'].encode()).decode()
except FileNotFoundError:
    logging.error("Configuration file not found. Please ensure 'config.yaml' exists.")
    raise SystemExit("Exiting due to missing configuration file.")

# Use the configuration values
REMOTE_PORT_NUMBER = config['remote_port']
LOCAL_PORT_NUMBER = config['local_port']
aws_region = config['region']
aws_key_name = config['key_name']
aws_ami = config['ami']
aws_availability_zone = config['availability_zone']
aws_tag_key = config['tag_key']
aws_tag_value = config['tag_value']
aws_iam_instance_profile = config['iam_instance_profile']
aws_instance_type = config['instance_type']
aws_availability_zone = config['availability_zone']
aws_security_groups = config['security_groups']
aws_max_spot_price = config['max_spot_price']

# Ask for user confirmation before proceeding
#confirmation = input("Press Enter to continue or type q to exit: ")
#if confirmation:
#    logging.info("Exiting...")
#    raise SystemExit("User chose to exit.")

ready_event = threading.Event()


def is_connected(host="8.8.8.8", port=53, timeout=3):
    """
    Check if the internet connection is available by attempting to connect to a DNS server.
    
    Args:
        host (str): The host to connect to. Default is Google's public DNS server.
        port (int): The port to connect to. Default is 53.
        timeout (int): The timeout for the connection attempt in seconds. Default is 3 seconds.
    
    Returns:
        bool: True if the connection is successful, False otherwise.
    """
    try:
        socket.setdefaulttimeout(timeout)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((host, port))
        return True
    except socket.error:
        return False

ec2_client = boto3.client('ec2')

# Create a new instance 
def create_spot_instance_request(ec2_client) -> dict:
    try:
        response = ec2_client.request_spot_instances(
            SpotPrice=aws_max_spot_price,
            InstanceCount=1,
            Type="one-time",
            LaunchSpecification={
                'ImageId': aws_ami,
                'KeyName': aws_key_name,
                'SecurityGroupIds': aws_security_groups,
                'InstanceType': aws_instance_type,
                'Placement': {
                    'AvailabilityZone': aws_availability_zone,
                },
                'IamInstanceProfile': {
                    'Arn': aws_iam_instance_profile
                },
            }
        )[0]
        return response
    except ClientError as e:
        logging.error(f"An AWS client error occurred while creating the instance: {e}")
        logging.error(traceback.format_exc()) 
        raise
    except Exception as e:
        logging.error(f"An error occurred while creating the instance: {e}")
        logging.error(traceback.format_exc())  
        raise

def add_tags_to_instance(ec2_client, instance_id: str):
    try:
        ec2_client.create_tags(
            Resources=[instance_id],
            Tags=[
                {
                    'Key': aws_tag_key,
                    'Value': aws_tag_value
                },
            ]
        )
        logging.info(f"Tags successfully added to the instance {instance_id}")
    except ClientError as e:
        logging.error(f"Failed to add tags to the instance {instance_id}: {e}")
        raise

def wait_for_instance_status_ok(ec2_client, instance_id: str):
    try:
        waiter = ec2_client.get_waiter('instance_status_ok')
        waiter.wait(InstanceIds=[instance_id])
        logging.info(f"Instance {instance_id} has passed status checks.")
    except ClientError as e:
        logging.error(f"An AWS client error occurred while waiting for the instance status: {e}")
        raise

def run_instance(ec2_client) -> str:
    logging.info("Creating a new instance...")
    try:
        response = create_spot_instance_request(ec2_client)
        instance_id = response['SpotInstanceRequests'][0]['InstanceId']
        add_tags_to_instance(ec2_client, instance_id)
        wait_for_instance_status_ok(ec2_client, instance_id)
        return instance_id
    except Exception as e:
        logging.error(f"An error occurred while running the instance: {e}")
        logging.error(traceback.format_exc())  # Add this line
        return None
    
def get_instance_id_by_tag(ec2_resource, tag_key: str, tag_value: str) -> str:
    instances = ec2_resource.instances.filter(
        Filters=[
            {'Name': f'tag:{tag_key}', 'Values': [tag_value]},
            {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping', 'stopped']}
        ])
    for instance in instances:
        return instance.id
    logging.info(f"No instances with tag key '{tag_key}' and value '{tag_value}' found.")
    return ""

def wait_for_instance(ec2_client, instance_id: str, status_type: str) -> None:
    waiter = ec2_client.get_waiter(status_type)
    waiter.wait(InstanceIds=[instance_id])
    logging.info(f"Instance {instance_id} has passed {status_type.replace('_', ' ')}.")

def start_instance_if_stopped(ec2_resource, ec2_client, instance_id: str) -> None:
    instance = ec2_resource.Instance(instance_id)
    if instance.state['Name'] != 'running':
        logging.info(f"Starting instance {instance_id}...")
        instance.start()
        wait_for_instance(ec2_client, instance_id, 'instance_running')
        wait_for_instance(ec2_client, instance_id, 'instance_status_ok')
        wait_for_instance(ec2_client, instance_id, 'system_status_ok')

def check_existing_ssm(ssm, instance_id: str, aws_region: str) -> dict:
    try:
        response = ssm.describe_sessions(
            Filters=[
                {
                    'key': 'Target',
                    'value': instance_id
                }
            ]
        )
        sessions = response.get('Sessions', [])
        shell_sessions = []
        port_forwarding_sessions = []
        for session in sessions:
            if session['DocumentName'] == 'AWS-StartSSHSession':
                shell_sessions.append(session)
            elif session['DocumentName'] == 'AWS-StartPortForwardingSession':
                port_forwarding_sessions.append(session)
        
        # Log the number of shell and port forwarding sessions
        logging.info(f"Found {len(shell_sessions)} shell session(s) for instance {instance_id}.")
        logging.info(f"Found {len(port_forwarding_sessions)} port forwarding session(s) for instance {instance_id}.")

        return {
            'shell_sessions': shell_sessions,
            'port_forwarding_sessions': port_forwarding_sessions
        }
    except ClientError as e:
        logging.error(f"Error checking for existing SSM sessions: {e}")
        return {'shell_sessions': [], 'port_forwarding_sessions': []}
    
def initiate_ssm_session(ssm, instance_id: str, aws_region: str) -> bool:
    logging.info("SSM agent is correctly configured. Attempting to start session...")

    # Check if AWS CLI is installed
    if not shutil.which("aws"):
        logging.error("AWS CLI is not installed or not found in PATH.")
        return False

    # Use the AWS CLI 'aws ssm start-session' command to start an interactive shell session
    start_session_command = [
        "aws",
        "ssm",
        "start-session",
        "--target",
        instance_id,
        "--region",
        aws_region
    ]

    logging.info(f"Running command: {' '.join(start_session_command)}")

    try:
        logging.info("About to start the SSM session...")
        result = subprocess.run(start_session_command, stderr=subprocess.PIPE, check=True)
        logging.info("SSM session started.")
        
        if result.returncode != 0:
            logging.error(f"Failed to start SSM session: {result.stderr}")
            return False

        logging.info(f"SSM session started successfully.")
        return True

    except subprocess.TimeoutExpired:
        logging.error("SSM session command timed out.")
        return False

    except subprocess.CalledProcessError as e:
        logging.error(f"An error occurred while starting the SSM session: {e}")
        return False

    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        return False
        
def ensure_ssm_session(ssm, instance_id: str, aws_region: str) -> bool:
    existing_sessions = check_existing_ssm(ssm, instance_id, aws_region)
    
    if existing_sessions['shell_sessions']:
        logging.info(f"Terminating active SSM shell session for instance {instance_id}...")
        for session in existing_sessions['shell_sessions']:
            ssm.terminate_session(SessionId=session['SessionId'])
        time.sleep(2)

    shell_session_started = start_ssm_shell_session(ssm, instance_id, aws_region)
    logging.info(f"SSM shell session started: {shell_session_started}")
    if not shell_session_started:
        return False

    if existing_sessions['port_forwarding_sessions']:
        logging.info(f"Terminating active SSM port forwarding session for instance {instance_id}...")
        for session in existing_sessions['port_forwarding_sessions']:
            ssm.terminate_session(SessionId=session['SessionId'])
        time.sleep(2)

    port_forwarding_session_started = start_ssm_port_forwarding_session(ssm, instance_id, aws_region, REMOTE_PORT_NUMBER, LOCAL_PORT_NUMBER)
    logging.info(f"SSM port forwarding session started: {port_forwarding_session_started}")

    return port_forwarding_session_started

def is_ssm_agent_configured(ssm, instance_id: str) -> bool:
    try:
        response = ssm.describe_instance_information(
            InstanceInformationFilterList=[{'key': 'InstanceIds', 'valueSet': [instance_id]}]
        )
        return bool(response['InstanceInformationList'])
    except ClientError as e:
        logging.error(f"ClientError occurred while checking SSM agent configuration: {e}")
        raise

def start_ssm_shell_session(instance_id: str, aws_region: str) -> subprocess.Popen:
    logging.info("Attempting to start an SSM shell session...")
    shell_session_command = [
        "aws", "ssm", "start-session",
        "--target", instance_id,
        "--region", aws_region
    ]

    try:
        process = subprocess.Popen(shell_session_command)
        logging.info(f"SSM session started successfully.")
        return process
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        return None


def handle_output(process, ready_event):
    try:
        while True:
            output = process.stdout.readline().decode().strip()
            if output == '' and process.poll() is not None:
                break
            if output:
                if "Waiting for connections..." in output:
                    logging.info("Port forwarding session is ready.")
                    ready_event.set()
                elif "Starting session with SessionId:" in output:
                    session_id = output.split(":")[1].strip()
                    logging.info(f"Port forwarding session {session_id} has started.")
                elif "Port opened for sessionId" in output:
                    port, session_id = output.split("opened for sessionId")
                    logging.info(f"Port {port.strip()} opened for session {session_id.strip()}.")
                elif "Exiting session with sessionId:" in output:
                    session_id = output.split(":")[1].strip()
                    logging.info(f"Port forwarding session {session_id} has ended.")
                else:
                    logging.info(f"Port forwarding session output: {output}")
    except Exception as e:
        logging.error(f"An error occurred while handling the process output: {e}")
        logging.error("Terminating the port forwarding session due to the error.")
        process.terminate()

def start_ssm_port_forwarding_session(instance_id: str, aws_region: str, remote_port: str, local_port: str) -> subprocess.Popen:
    logging.info("Attempting to start an SSM port forwarding session in the background...")
    port_forwarding_command = [
        "aws", "ssm", "start-session",
        "--target", instance_id,
        "--region", aws_region,
        "--document-name", "AWS-StartPortForwardingSession",
        "--parameters", f"portNumber={remote_port},localPortNumber={local_port}"
    ]
    try:
        process = subprocess.Popen(port_forwarding_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # Start a new thread to handle the process's output
        threading.Thread(target=handle_output, args=(process, ready_event)).start()
        return process
    except subprocess.SubprocessError as e:
        logging.error(f"An error occurred while starting the SSM port forwarding session: {e}")
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred while starting the SSM port forwarding session: {e}")
        return None
    
def terminate_ssm_session(ssm, session_id: str) -> None:
    try:
        ssm.terminate_session(SessionId=session_id)
        logging.info(f"Terminated SSM session {session_id}")
    except ClientError as e:
        logging.error(f"Error terminating SSM session {session_id}: {e}")

def terminate_port_forwarding_session(port_forwarding_process, ssm, instance_id):
    try:
        # Terminate the port forwarding session
        port_forwarding_process.terminate()
        port_forwarding_process.wait(timeout=5)
        logging.info("SSM port forwarding session terminated successfully.")
    except subprocess.TimeoutExpired:
        logging.error("Port forwarding session did not terminate in a timely manner.")
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
    finally:
        try:
            logging.info("Terminating SSM sessions...")
            response = ssm.describe_sessions(
                State='Active', Filters=[{'key': 'Target', 'value': instance_id}]
            )
            sessions = response.get('Sessions', [])
            for session in sessions:
                ssm.terminate_session(SessionId=session['SessionId'])
                logging.info(f"Terminated SSM session {session['SessionId']}")
        except Exception as e:
            logging.error(f"Failed to terminate SSM sessions: {e}")

        logging.info("Script execution finished.")

def get_aws_session() -> boto3.Session:
    if not is_connected():
        logging.error("No internet connection. Please check your connection and try again.")
        return None

    try:
        session = boto3.Session()
    except NoCredentialsError:
        logging.error("AWS is not configured. Please enter your AWS credentials.")
        return None

    logging.info("AWS credentials are configured, proceeding.")
    return session

def get_ec2_resources(session, aws_region):
    # Create a Config object with custom settings
    custom_config = botocore.config.Config(
        read_timeout=900,
        connect_timeout=900,
        retries={'max_attempts': 0}
    )

    ec2_resource = session.resource('ec2', region_name=aws_region, config=custom_config)
    ec2_client = session.client('ec2', region_name=aws_region, config=custom_config)
    ssm = session.client('ssm', region_name=aws_region, config=custom_config)

    return ec2_resource, ec2_client, ssm

def get_instance(ec2_resource, ec2_client, aws_tag_value):
    existing_instance_id = get_instance_id_by_tag(ec2_resource, aws_tag_key, aws_tag_value)
    if existing_instance_id:
        logging.info(f"An instance with tag value '{aws_tag_value}' exists. Instance ID: {existing_instance_id}")
        try:
            start_instance_if_stopped(ec2_resource, ec2_client, existing_instance_id)
        except ClientError as e:
            if 'UnauthorizedOperation' in str(e):
                logging.error("You do not have the necessary permissions to start instances. Please check your IAM policies.")
            else:
                logging.error(f"Failed to start instance: {e}")
            return None
        instance_id = existing_instance_id
    else:
        try:
            instance_id = run_instance(ec2_client)  # Only pass ec2_client
        except ClientError as e:
            if 'UnauthorizedOperation' in str(e):
                logging.error("You do not have the necessary permissions to create instances. Please check your IAM policies.")
            else:
                logging.error(f"Failed to create instance: {e}")
            return None
    return instance_id

def start_ssm_sessions(ssm, instance_id, aws_region):
    port_forwarding_process = None  # Initialize the variable at the start of the function

    # Start the SSM port forwarding session in the background
    if REMOTE_PORT_NUMBER and LOCAL_PORT_NUMBER:
        if port_forwarding_process is not None:  # Check if the variable is not None before using it
            terminate_port_forwarding_session(port_forwarding_process, ssm, instance_id)
        logging.info("About to start the SSM port forwarding session...")
        # Start a new port forwarding session       
        port_forwarding_process = start_ssm_port_forwarding_session(ssm, instance_id, aws_region, REMOTE_PORT_NUMBER, LOCAL_PORT_NUMBER)
        if port_forwarding_process is None:
            logging.error("Unable to start the SSM port forwarding session. Exiting.")
            return None, None
        else:
            logging.info("SSM port forwarding session started successfully.")
        # Give the port forwarding session a moment to start
        time.sleep(3)

    # Start the SSM shell session
    logging.info("About to start the SSM shell session...")
    shell_session_process = start_ssm_shell_session(ssm, instance_id, aws_region)
    if shell_session_process is None:
        logging.error("Unable to start the SSM shell session. Exiting.")
        return None, None

    return shell_session_process, port_forwarding_process

def cleanup(port_forwarding_process, shell_session_process, ssm, instance_id):
    try:
        if port_forwarding_process:
            terminate_port_forwarding_session(port_forwarding_process, ssm, instance_id)
    except Exception as e:
        logging.error(f"Error terminating port forwarding session: {e}")
    try:
        if shell_session_process:
            shell_session_process.terminate()  # Terminate the shell session process
    except Exception as e:
        logging.error(f"Error terminating shell session: {e}")

def main() -> None:
    # Initialize the shell and port forwarding processes to None
    shell_session_process = None
    port_forwarding_process = None
    instance_id = None  # Initialize instance_id to None
    try:
        session = get_aws_session()
        if session is None:
            return
        
        ec2_resource, ec2_client, ssm = get_ec2_resources(session, aws_region)
        
        instance_id = get_instance(ec2_resource, ec2_client, aws_tag_value)
        if instance_id is None:
            logging.error("Failed to get instance. Exiting.")
            return

        # Start the SSM sessions
        logging.info("Starting SSM sessions")
        shell_session_process, port_forwarding_process = start_ssm_sessions(ssm, instance_id, aws_region)
        if shell_session_process is None:
            return

        # Wait for the shell session to finish
        shell_session_process.wait()
        
        # Ask the user whether to terminate the EC2 instance, default is no
        terminate = input("Do you want to terminate the EC2 instance? (yes/no, default is no): ")
        if terminate.lower() == 'yes':
            ec2_client.terminate_instances(InstanceIds=[instance_id])
            logging.info(f"Instance {instance_id} is being terminated.")

            # Wait for the instance to be terminated
            waiter = ec2_client.get_waiter('instance_terminated')
            waiter.wait(InstanceIds=[instance_id])

            # Verify the instance is terminated
            response = ec2_client.describe_instances(InstanceIds=[instance_id])
            if response['Reservations'][0]['Instances'][0]['State']['Name'] == 'terminated':
                logging.info(f"Instance {instance_id} has been terminated.")
            else:
                logging.error(f"Failed to terminate instance {instance_id}.")
        else:
            logging.info("Instance termination skipped.")

    # If the script is interrupted, log the interruption and clean up
    except KeyboardInterrupt:
        logging.info("Script interrupted by user. Cleaning up...")
        cleanup(port_forwarding_process, shell_session_process, ssm, instance_id)
        raise SystemExit("Script interrupted by user") from None
    
    # If an unexpected error occurs, log the error
    except Exception as e:
        logging.error(f"An unexpected error occurred in main: {e}")
        logging.error(traceback.format_exc()) 
        
    # Regardless of how the script exits, clean up the resources
    finally:
        cleanup(port_forwarding_process, shell_session_process, ssm, instance_id)

if __name__ == "__main__":
    main()