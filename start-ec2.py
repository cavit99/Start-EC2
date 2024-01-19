# MIT License
# Copyright (c) 2024 Cavit Erginsoy

import boto3
import logging
import sys
import requests
import yaml
import subprocess
import time
import shutil
import botocore.config
import threading
import requests

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
        user_data = config['user_data']
except FileNotFoundError:
    logging.error("Configuration file not found. Please ensure 'config.yaml' exists.")
    raise SystemExit("Exiting due to missing configuration file.")

# Use the configuration values
LAUNCH_TEMPLATE_ID = config['template']
REMOTE_PORT_NUMBER = config['remote_port']
LOCAL_PORT_NUMBER = config['local_port']
aws_region = config['region']
aws_tag_value = config['tag_value']

# Log the configuration values for debugging purposes
logging.info(f"Using Launch Template ID: {LAUNCH_TEMPLATE_ID}")
logging.info(f"Using Remote Port Number: {REMOTE_PORT_NUMBER}")
logging.info(f"Using Local Port Number: {LOCAL_PORT_NUMBER}")
logging.info(f"Using AWS region: {aws_region}")
logging.info(f"Using AWS tag value: {aws_tag_value}")

# Ask for user confirmation before proceeding
confirmation = input("Press Enter to continue or type q to exit: ")
if confirmation:
    logging.info("Exiting...")
    raise SystemExit("User chose to exit.")

ready_event = threading.Event()

def get_instance_metadata():
    base_url = "http://169.254.169.254/latest/meta-data/"
    instance_type = requests.get(base_url + "instance-type").text
    availability_zone = requests.get(base_url + "placement/availability-zone").text
    return instance_type, availability_zone

def get_spot_price(ec2_client):
    instance_type, availability_zone = get_instance_metadata()
    response = ec2_client.describe_spot_price_history(
        InstanceTypes=[instance_type],
        ProductDescriptions=['Linux/UNIX'],
        AvailabilityZone=availability_zone,
        MaxResults=1
    )

    for spot_price in response['SpotPriceHistory']:
        logging.info(f"Current spot price for {instance_type} in {availability_zone}: {spot_price['SpotPrice']}")
        return spot_price['SpotPrice']

def is_connected():
    try:
        requests.get('http://www.google.com', timeout=5)
        return True
    except requests.exceptions.RequestException:
        return False

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

# Create a new instance using the specified launch template
def run_instance(ec2_resource, ec2_client, launch_template_id: str) -> str:
    logging.info("Creating a new instance...")

    try:
        instance = ec2_resource.create_instances(
            LaunchTemplate={'LaunchTemplateId': launch_template_id},
            MaxCount=1,
            MinCount=1,
            UserData=user_data
        )[0]
    except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code'] == 'InvalidLaunchTemplateId.NotFound':
            logging.error(f"Launch template {launch_template_id} does not exist: {e}")
            return None
        else:
            logging.error(f"An unexpected error occurred: {e}")
            return None

    logging.info(f"Waiting for instance {instance.id} to start...")
    instance.wait_until_running()
    logging.info(f"Instance {instance.id} is now running. Waiting for initialization...")
    
    waiter = ec2_client.get_waiter('instance_status_ok')
    waiter.wait(InstanceIds=[instance.id])
    logging.info(f"Instance {instance.id} has passed status checks.")
    
    waiter = ec2_client.get_waiter('system_status_ok')
    waiter.wait(InstanceIds=[instance.id])
    logging.info(f"System on instance {instance.id} has passed status checks.")
    
    return instance.id

def get_instance_id_by_tag(ec2_resource, name_tag: str) -> str:
    instances = ec2_resource.instances.filter(
        Filters=[
            {'Name': 'tag:Name', 'Values': [name_tag]},
            {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping', 'stopped']}
        ])
    for instance in instances:
        return instance.id
    logging.info(f"No instances with tag value '{name_tag}' found.")
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

def start_ssm_shell_session(ssm, instance_id: str, aws_region: str) -> subprocess.Popen:
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

def start_ssm_port_forwarding_session(ssm, instance_id: str, aws_region: str, remote_port: str, local_port: str) -> subprocess.Popen:
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

def get_instance(ec2_resource, ec2_client, launch_template_id, aws_tag_value):
    if not does_launch_template_exist(ec2_client, launch_template_id):
        logging.error(f"Launch template {launch_template_id} does not exist. Exiting.")
        return None

    existing_instance_id = get_instance_id_by_tag(ec2_resource, aws_tag_value)

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
            instance_id = run_instance(ec2_resource, ec2_client, launch_template_id)
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
    try:
        session = get_aws_session()
        if session is None:
            return
        
        ec2_resource, ec2_client, ssm = get_ec2_resources(session, aws_region)
        
        try:
            spot_price = get_spot_price(ec2_client)
        except Exception as e:
            logging.error(f"An error occurred while getting the spot price: {e}")
            raise SystemExit("An error occurred while getting the spot price.") 

        try:
            confirmation = input(f"The current spot price is {spot_price}. Do you want to continue? (yes/no): ")
        except Exception as e:
            logging.error(f"An error occurred while getting user confirmation: {e}")
            raise SystemExit("An error occurred while getting user confirmation.") 

        if confirmation.lower() != 'yes':
            logging.info("User chose not to continue due to the spot price. Exiting...")
            raise SystemExit("User chose to exit.") 
        
        instance_id = get_instance(ec2_resource, ec2_client, LAUNCH_TEMPLATE_ID, aws_tag_value)
        if instance_id is None:
            return

        # Start the SSM sessions
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
        
    # Regardless of how the script exits, clean up the resources
    finally:
        cleanup(port_forwarding_process, shell_session_process, ssm, instance_id)

if __name__ == "__main__":
    main()